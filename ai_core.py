"""
Dhaka Exclusive - Advanced AI Core Engine
Multi-LLM Provider System with Tool Calling & Memory
Inspired by Hermes Agent architecture (NousResearch/hermes-agent)
"""
import os
import json
import time
import hashlib
import logging
import threading
from typing import Optional, Dict, List, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
from collections import OrderedDict

import requests

logger = logging.getLogger(__name__)

# =====================================================================
# PROVIDER TYPES
# =====================================================================

class ProviderType(Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    DEEPSEEK = "deepseek"
    GROQ = "groq"
    LOCAL = "local"

@dataclass
class ProviderConfig:
    provider: ProviderType
    api_key: str
    base_url: str = ""
    models: List[str] = field(default_factory=list)
    default_model: str = ""
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout: int = 30

# =====================================================================
# TOOL SYSTEM (Function Calling)
# =====================================================================

class ToolRegistry:
    """Registry for AI-callable tools (hermes-agent inspired)"""
    
    def __init__(self):
        self._tools: Dict[str, dict] = {}
        self._handlers: Dict[str, Callable] = {}
    
    def register(self, name: str, description: str, parameters: dict, handler: Callable):
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters
        }
        self._handlers[name] = handler
        logger.info(f"Tool registered: {name}")
    
    def get_definitions(self, provider: ProviderType = ProviderType.GEMINI) -> list:
        """Return tool definitions in provider-specific format"""
        if provider in (ProviderType.GEMINI,):
            # Gemini format
            declarations = []
            for name, tool in self._tools.items():
                declarations.append({
                    "name": name,
                    "description": tool["description"],
                    "parameters": tool["parameters"]
                })
            return declarations
        elif provider in (ProviderType.OPENAI, ProviderType.DEEPSEEK, ProviderType.GROQ):
            # OpenAI function format
            return [{
                "type": "function",
                "function": {
                    "name": name,
                    "description": t["description"],
                    "parameters": t["parameters"]
                }
            } for name, t in self._tools.items()]
        elif provider == ProviderType.ANTHROPIC:
            # Anthropic tool format
            return [{
                "name": name,
                "description": t["description"],
                "input_schema": {
                    "type": "object",
                    "properties": t["parameters"].get("properties", {}),
                    "required": t["parameters"].get("required", [])
                }
            } for name, t in self._tools.items()]
        return []
    
    def execute(self, name: str, args: dict) -> str:
        handler = self._handlers.get(name)
        if not handler:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            result = handler(**args)
            return json.dumps(result, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Tool {name} error: {e}")
            return json.dumps({"error": str(e)})

# Global tool registry
tool_registry = ToolRegistry()

# =====================================================================
# CONVERSATION MEMORY (Context Compression)
# =====================================================================

@dataclass
class ConversationTurn:
    role: str  # "user" or "assistant" or "system"
    content: str
    timestamp: float = field(default_factory=time.time)
    tokens: int = 0

class ConversationMemory:
    """Manages conversation history with compression + DB hydration (hermes-agent inspired)
    
    Lifecycle:
    1. Active session: keeps recent turns in memory for fast response
    2. Session expires (TTL): auto-hydrates from DB on next message
    3. Old conversations: automatically loaded from DB when customer returns
    """
    
    def __init__(self, max_turns: int = 50, max_tokens: int = 8000, 
                 ttl_hours: int = 1,  # Short in-memory TTL — DB is source of truth
                 db_query_fn=None, customer_phone: str = ""):
        self.turns: List[ConversationTurn] = []
        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.ttl_hours = ttl_hours
        self.db_query = db_query_fn
        self.customer_phone = customer_phone
        self.summary: str = ""
        self._last_db_sync: float = 0
        self._db_sync_interval: float = 300  # Sync with DB every 5 min
        self._lock = threading.Lock()
    
    def set_db(self, db_query_fn, phone: str = ""):
        """Inject DB function and customer phone for hydration"""
        self.db_query = db_query_fn
        if phone:
            self.customer_phone = phone
    
    def _load_from_db(self, limit: int = 30) -> int:
        """Hydrate memory from database message history.
        Returns number of turns loaded."""
        if not self.db_query or not self.customer_phone:
            return 0
        try:
            msgs = self.db_query(
                "SELECT content, direction FROM messages WHERE from_number=? ORDER BY id DESC LIMIT ?",
                (self.customer_phone, limit),
                fetchall=True
            )
            if not msgs:
                return 0
            
            loaded = 0
            # Messages come in DESC order, reverse for chronological
            for msg in reversed(msgs):
                role = "user" if msg.get("direction") == "inbound" else "assistant"
                content = msg.get("content", "")
                if content and len(content) > 2:
                    # Skip system messages and media placeholders
                    if content.startswith("[") and content.endswith("]"):
                        continue
                    turn = ConversationTurn(
                        role=role, 
                        content=content[:500],  # Truncate long messages
                        tokens=len(content) // 3
                    )
                    # Avoid consecutive duplicates
                    if not self.turns or self.turns[-1].content != content[:500]:
                        self.turns.append(turn)
                        loaded += 1
            
            if loaded > 0:
                logger.info(f"Memory hydrated from DB: {loaded} turns for {self.customer_phone}")
            return loaded
        except Exception as e:
            logger.error(f"Memory DB hydration error: {e}")
            return 0
    
    def _maybe_hydrate(self):
        """Check if memory needs hydration from DB"""
        now = time.time()
        ttl_seconds = self.ttl_hours * 3600
        
        # Check if in-memory turns are expired (older than TTL)
        if self.turns:
            newest_turn_age = now - self.turns[-1].timestamp
            if newest_turn_age < ttl_seconds:
                return  # Still fresh — no hydration needed
        
        # Memory expired or empty — hydrate from DB
        self._load_from_db()
        self._last_db_sync = now
    
    def add(self, role: str, content: str, tokens: int = 0, persist_to_db: bool = False):
        with self._lock:
            if not tokens:
                tokens = len(content) // 3  # rough estimate
            turn = ConversationTurn(role=role, content=content, tokens=tokens)
            self.turns.append(turn)
            self._maybe_compress()
            # Optionally save to DB (for bot responses that aren't saved by webhook)
            if persist_to_db and self.db_query and self.customer_phone:
                try:
                    direction = "outbound" if role == "assistant" else "inbound"
                    self.db_query(
                        "INSERT INTO messages (from_number, content, direction) VALUES (?, ?, ?)",
                        (self.customer_phone, content, direction),
                        commit=True
                    )
                except Exception as e:
                    logger.error(f"Memory persist error: {e}")
    
    def _maybe_compress(self):
        """Compress old conversations into a summary when exceeding limits"""
        total_tokens = sum(t.tokens for t in self.turns)
        
        # Compress if over limits (no TTL purge — DB is source of truth)
        while len(self.turns) > self.max_turns or total_tokens > self.max_tokens:
            if len(self.turns) <= 6:  # Keep at least 6 recent turns
                break
            # Merge oldest 2 turns into summary
            old = self.turns.pop(0)
            if old.role == "user":
                self.summary += f"👤: {old.content[:150]}... | "
            else:
                self.summary += f"🤖: {old.content[:150]}... | "
            total_tokens = sum(t.tokens for t in self.turns)
        
        # Trim summary if too long
        if len(self.summary) > 3000:
            self.summary = "..." + self.summary[-3000:]
    
    def get_context(self, max_recent: int = 12) -> List[dict]:
        """Return conversation context for API calls — hydrates from DB if needed"""
        with self._lock:
            self._maybe_hydrate()
            
            # Periodic DB sync for active conversations every 5 min
            now = time.time()
            if self.db_query and (now - self._last_db_sync) > self._db_sync_interval:
                self._load_from_db(limit=10)  # Load recent msgs we might have missed
                self._last_db_sync = now
            
            context = []
            if self.summary:
                context.append({
                    "role": "system",
                    "content": f"[পূর্ববর্তী কথোপকথন: {self.summary}]"
                })
            recent = self.turns[-max_recent:] if max_recent else self.turns
            for t in recent:
                context.append({
                    "role": t.role,
                    "content": t.content
                })
            return context
    
    def to_messages_list(self) -> List[dict]:
        """Legacy format for existing code"""
        self._maybe_hydrate()
        return [{"role": t.role, "content": t.content} for t in self.turns]
    
    def clear(self):
        with self._lock:
            self.turns.clear()
            self.summary = ""
            self._last_db_sync = 0

# =====================================================================
# MULTI-LLM PROVIDER ENGINE
# =====================================================================

class LLMEngine:
    """Multi-provider LLM engine with failover (hermes-agent inspired)"""
    
    def __init__(self):
        self.providers: Dict[str, ProviderConfig] = {}
        self._setup_providers()
        self.stats: Dict[str, dict] = {}  # per-provider stats
        # Try to import DB query function
        self._db = None
    
    def set_db(self, db_query_fn):
        """Inject database query function"""
        self._db = db_query_fn
    
    def _setup_providers(self):
        """Initialize all available providers from environment"""
        
        # Gemini (primary - already used)
        gemini_key = os.environ.get("GEMINI_KEY", "")
        if gemini_key:
            self.providers["gemini"] = ProviderConfig(
                provider=ProviderType.GEMINI,
                api_key=gemini_key,
                base_url="https://generativelanguage.googleapis.com/v1beta",
                models=["gemini-2.5-flash", "gemini-2.5-pro", "gemini-1.5-flash-latest"],
                default_model="gemini-2.5-flash",
                max_tokens=4096,
                temperature=0.7,
                timeout=30
            )
        
        # OpenAI
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if openai_key:
            self.providers["openai"] = ProviderConfig(
                provider=ProviderType.OPENAI,
                api_key=openai_key,
                base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                models=["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"],
                default_model="gpt-4o-mini",
                max_tokens=4096,
                temperature=0.7,
                timeout=30
            )
        
        # Anthropic
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if anthropic_key:
            self.providers["anthropic"] = ProviderConfig(
                provider=ProviderType.ANTHROPIC,
                api_key=anthropic_key,
                base_url="https://api.anthropic.com/v1",
                models=["claude-3-5-haiku-latest", "claude-3-5-sonnet-latest", "claude-3-haiku-20240307"],
                default_model="claude-3-5-haiku-latest",
                max_tokens=4096,
                temperature=0.7,
                timeout=45
            )
        
        # DeepSeek
        deepseek_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if deepseek_key:
            self.providers["deepseek"] = ProviderConfig(
                provider=ProviderType.DEEPSEEK,
                api_key=deepseek_key,
                base_url="https://api.deepseek.com/v1",
                models=["deepseek-chat", "deepseek-reasoner"],
                default_model="deepseek-chat",
                max_tokens=4096,
                temperature=0.7,
                timeout=60
            )
        
        # Groq (fast inference)
        groq_key = os.environ.get("GROQ_API_KEY", "")
        if groq_key:
            self.providers["groq"] = ProviderConfig(
                provider=ProviderType.GROQ,
                api_key=groq_key,
                base_url="https://api.groq.com/openai/v1",
                models=["llama-3.3-70b-versatile", "mixtral-8x7b-32768", "gemma2-9b-it"],
                default_model="llama-3.3-70b-versatile",
                max_tokens=4096,
                temperature=0.7,
                timeout=20
            )
    
    def get_available_providers(self) -> List[str]:
        return list(self.providers.keys())
    
    def _call_gemini(self, cfg: ProviderConfig, system_prompt: str, 
                     messages: list, tools: list = None) -> dict:
        """Call Gemini API"""
        model = cfg.default_model
        url = f"{cfg.base_url}/models/{model}:generateContent?key={cfg.api_key}"
        
        # Build contents from messages
        contents = []
        for msg in messages:
            role = "user" if msg["role"] in ("user", "system") else "model"
            contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })
        
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": cfg.temperature,
                "maxOutputTokens": cfg.max_tokens,
                "topP": 0.95
            }
        }
        
        if system_prompt:
            payload["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }
        
        if tools:
            payload["tools"] = [{"functionDeclarations": tools}]
        
        resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=cfg.timeout)
        return resp.json()
    
    def _call_openai_compatible(self, cfg: ProviderConfig, system_prompt: str,
                                 messages: list, tools: list = None) -> dict:
        """Call OpenAI-compatible API (OpenAI, DeepSeek, Groq)"""
        url = f"{cfg.base_url}/chat/completions"
        
        api_messages = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        api_messages.extend(messages)
        
        payload = {
            "model": cfg.default_model,
            "messages": api_messages,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens
        }
        
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json"
        }
        
        # Anthropic uses different auth header
        if cfg.provider == ProviderType.ANTHROPIC:
            headers = {
                "x-api-key": cfg.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json"
            }
            url = f"{cfg.base_url}/messages"
            # Anthropic format differs
            payload = {
                "model": cfg.default_model,
                "max_tokens": cfg.max_tokens,
                "temperature": cfg.temperature,
                "system": system_prompt,
                "messages": [m for m in api_messages if m["role"] != "system"]
            }
            if tools:
                payload["tools"] = tools
        
        resp = requests.post(url, json=payload, headers=headers, timeout=cfg.timeout)
        return resp.json()
    
    def _extract_text(self, response: dict, provider: ProviderType) -> str:
        """Extract text from provider-specific response"""
        try:
            if provider == ProviderType.GEMINI:
                if response.get("candidates"):
                    parts = response["candidates"][0].get("content", {}).get("parts", [])
                    for part in parts:
                        if "text" in part:
                            return part["text"].strip()
                        if "functionCall" in part:
                            return json.dumps({"function_call": part["functionCall"]})
                if "error" in response:
                    return f"[GEMINI_ERROR: {response['error'].get('message', 'unknown')}]"
            
            elif provider in (ProviderType.OPENAI, ProviderType.DEEPSEEK, ProviderType.GROQ):
                if response.get("choices"):
                    choice = response["choices"][0]
                    msg = choice.get("message", {})
                    # Check for tool calls
                    if msg.get("tool_calls"):
                        return json.dumps({"tool_calls": msg["tool_calls"]})
                    return msg.get("content", "").strip()
                if "error" in response:
                    return f"[{provider.value.upper()}_ERROR: {response['error'].get('message', 'unknown')}]"
            
            elif provider == ProviderType.ANTHROPIC:
                if response.get("content"):
                    for block in response["content"]:
                        if block.get("type") == "text":
                            return block["text"].strip()
                        if block.get("type") == "tool_use":
                            return json.dumps({"tool_use": block})
                if "error" in response:
                    return f"[ANTHROPIC_ERROR: {response['error'].get('message', 'unknown')}]"
            
            return ""
        except Exception as e:
            logger.error(f"Text extraction error: {e}")
            return ""

    def chat(self, messages: list, system_prompt: str = "", 
             tools: list = None, preferred_provider: str = None,
             fallback: bool = True) -> dict:
        """
        Primary chat interface with multi-provider failover.
        Returns: {"text": str, "provider": str, "model": str, "tool_calls": list}
        """
        provider_order = []
        
        if preferred_provider and preferred_provider in self.providers:
            provider_order.append(preferred_provider)
        
        # Default order: Gemini > OpenAI > Anthropic > DeepSeek > Groq
        default_order = ["gemini", "openai", "anthropic", "deepseek", "groq"]
        for p in default_order:
            if p not in provider_order and p in self.providers:
                provider_order.append(p)
        
        if not provider_order:
            return {"text": "⚠️ No AI providers configured. Set GEMINI_KEY, OPENAI_API_KEY, or ANTHROPIC_API_KEY.", "provider": "none", "model": "none"}
        
        last_error = None
        
        for pname in provider_order:
            cfg = self.providers[pname]
            try:
                t0 = time.time()
                
                # Get tool definitions in correct format
                tool_defs = tools if tools else tool_registry.get_definitions(cfg.provider)
                
                if cfg.provider == ProviderType.GEMINI:
                    response = self._call_gemini(cfg, system_prompt, messages, tool_defs)
                else:
                    response = self._call_openai_compatible(cfg, system_prompt, messages, tool_defs)
                
                elapsed = time.time() - t0
                text = self._extract_text(response, cfg.provider)
                
                if text and not text.startswith("[") and "ERROR" not in text:
                    self._update_stats(pname, True, elapsed)
                    result = {
                        "text": text,
                        "provider": pname,
                        "model": cfg.default_model,
                        "latency_ms": int(elapsed * 1000),
                        "tool_calls": None
                    }
                    
                    # Parse tool calls if present
                    if text.startswith("{"):
                        try:
                            parsed = json.loads(text)
                            if "tool_calls" in parsed:
                                result["tool_calls"] = parsed["tool_calls"]
                            elif "function_call" in parsed:
                                result["tool_calls"] = [parsed["function_call"]]
                            elif "tool_use" in parsed:
                                result["tool_calls"] = [parsed["tool_use"]]
                        except json.JSONDecodeError:
                            pass
                    
                    return result
                
                # Check for specific errors
                if "ERROR" in text:
                    last_error = text
                    logger.warning(f"Provider {pname} returned error: {text[:100]}")
                    self._update_stats(pname, False, elapsed)
                    if not fallback:
                        break
                    continue
                
                if not text:
                    last_error = f"{pname}: empty response"
                    self._update_stats(pname, False, elapsed)
                    continue
                    
            except requests.Timeout:
                last_error = f"{pname}: timeout"
                self._update_stats(pname, False, cfg.timeout * 1000)
                logger.warning(f"Provider {pname} timed out")
            except Exception as e:
                last_error = f"{pname}: {str(e)[:100]}"
                self._update_stats(pname, False, 0)
                logger.error(f"Provider {pname} exception: {e}")
        
        # All providers failed
        return {
            "text": f"⚠️ AI service temporarily unavailable. Please try again. ({last_error[:80]})",
            "provider": "fallback",
            "model": "none",
            "error": last_error
        }
    
    def _update_stats(self, provider: str, success: bool, latency_ms: float):
        if provider not in self.stats:
            self.stats[provider] = {"calls": 0, "successes": 0, "failures": 0, "total_latency": 0}
        s = self.stats[provider]
        s["calls"] += 1
        if success:
            s["successes"] += 1
        else:
            s["failures"] += 1
        s["total_latency"] += latency_ms
    
    def get_stats(self) -> dict:
        return {k: {
            **v,
            "avg_latency_ms": int(v["total_latency"] / v["calls"]) if v["calls"] else 0,
            "success_rate": int(v["successes"] / v["calls"] * 100) if v["calls"] else 0
        } for k, v in self.stats.items()}

# =====================================================================
# SINGLETON ENGINE
# =====================================================================

_llm_engine: Optional[LLMEngine] = None
_memory_store: Dict[str, ConversationMemory] = {}  # phone -> memory

def get_llm_engine() -> LLMEngine:
    global _llm_engine
    if _llm_engine is None:
        _llm_engine = LLMEngine()
    return _llm_engine

def get_memory(phone: str, db_query_fn=None) -> ConversationMemory:
    """Get or create per-customer memory with auto DB hydration.
    - TTL=1hr in-memory (expired memory auto-reloads from DB)
    - DB is source of truth for long-term history
    - Max 50 turns / 8000 tokens in memory"""
    if phone not in _memory_store:
        mem = ConversationMemory(
            max_turns=50, 
            max_tokens=8000, 
            ttl_hours=1,  # Short TTL — DB is source of truth
            db_query_fn=db_query_fn,
            customer_phone=phone
        )
        # Hydrate from DB on first creation
        if db_query_fn:
            mem._load_from_db(limit=30)
        _memory_store[phone] = mem
    else:
        # Update DB reference if provided (handles case where db was set later)
        mem = _memory_store[phone]
        if db_query_fn and not mem.db_query:
            mem.set_db(db_query_fn, phone)
    return _memory_store[phone]

def clear_memory(phone: str):
    if phone in _memory_store:
        _memory_store[phone].clear()
        # Don't delete — just clear turns; hydration will reload from DB on next use
        logger.info(f"Memory cleared for {phone} (will reload from DB on next message)")

# =====================================================================
# REGISTER BUSINESS TOOLS
# =====================================================================

def _register_default_tools(db_query_fn):
    """Register all business tools for AI to call"""
    
    def check_stock(product_name: str = "") -> dict:
        """Check product stock levels"""
        try:
            if product_name:
                products = db_query_fn(
                    "SELECT name, price, stock, category FROM products WHERE name LIKE ? LIMIT 10",
                    (f"%{product_name}%",), fetchall=True
                )
            else:
                products = db_query_fn(
                    "SELECT name, price, stock, category FROM products WHERE stock > 0 ORDER BY id DESC LIMIT 10",
                    fetchall=True
                )
            if not products:
                return {"message": "কোনো প্রোডাক্ট পাওয়া যায়নি", "products": []}
            return {
                "message": f"{len(products)} টি প্রোডাক্ট পাওয়া গেছে",
                "products": [{"name": p["name"], "price": p["price"], "stock": p["stock"], "category": p.get("category", "")} for p in products]
            }
        except Exception as e:
            return {"error": str(e)}
    
    def create_order(phone: str = "", name: str = "", product: str = "", 
                     quantity: int = 1, address: str = "", total: int = 0) -> dict:
        """Create a new order"""
        try:
            if not phone or not product:
                return {"error": "Phone and product are required"}
            db_query_fn(
                "INSERT INTO orders (phone, name, product_name, quantity, address, total, status) VALUES (?, ?, ?, ?, ?, ?, 'pending')",
                (phone, name or "Customer", product, quantity, address or "Dhaka", total),
                commit=True
            )
            # Update user
            db_query_fn(
                "INSERT INTO users (phone, name, last_active) VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT(phone) DO UPDATE SET name=COALESCE(?, name), last_active=CURRENT_TIMESTAMP",
                (phone, name or "Customer", name or "Customer"), commit=True
            )
            return {"success": True, "message": f"অর্ডার কনফার্ম হয়েছে! প্রোডাক্ট: {product} x{quantity}, মোট: {total}৳"}
        except Exception as e:
            return {"error": str(e)}
    
    def get_catalog(category: str = "") -> dict:
        """Get product catalog"""
        try:
            if category:
                products = db_query_fn(
                    "SELECT name, price, stock, description, category FROM products WHERE category LIKE ? AND stock > 0 LIMIT 20",
                    (f"%{category}%",), fetchall=True
                )
            else:
                products = db_query_fn(
                    "SELECT name, price, stock, description, category FROM products WHERE stock > 0 ORDER BY id DESC LIMIT 20",
                    fetchall=True
                )
            if not products:
                return {"message": "ক্যাটালগ খালি", "products": []}
            return {
                "message": f"মোট {len(products)} টি প্রোডাক্ট",
                "products": [{"name": p["name"], "price": p["price"], "stock": p["stock"], "category": p.get("category", ""), "description": (p.get("description") or "")[:100]} for p in products]
            }
        except Exception as e:
            return {"error": str(e)}
    
    def get_delivery_info() -> dict:
        """Get delivery and payment information"""
        return {
            "delivery": {
                "dhaka": "24 ঘণ্টা",
                "outside_dhaka": "48-72 ঘণ্টা",
                "cod": True,
                "free_delivery_above": 2000
            },
            "payment_methods": ["ক্যাশ অন ডেলিভারি (COD)", "bKash", "Nagad"],
            "return_policy": "7 দিন (ড্যামেজ/ভুল প্রোডাক্ট হলে)"
        }
    
    def get_order_status(phone: str = "") -> dict:
        """Check order status for a customer"""
        try:
            if not phone:
                return {"error": "Phone number required"}
            orders = db_query_fn(
                "SELECT id, product_name, total, status, created_at FROM orders WHERE phone=? ORDER BY id DESC LIMIT 5",
                (phone,), fetchall=True
            )
            if not orders:
                return {"message": "আপনার কোনো অর্ডার পাওয়া যায়নি", "orders": []}
            return {
                "message": f"আপনার {len(orders)} টি অর্ডার আছে",
                "orders": [{"id": o["id"], "product": o["product_name"], "total": o["total"], "status": o["status"], "date": str(o.get("created_at", ""))[:10]} for o in orders]
            }
        except Exception as e:
            return {"error": str(e)}
    
    def get_hot_deals() -> dict:
        """Get today's hot deals and discounts"""
        try:
            hot = db_query_fn(
                """SELECT p.name, p.price, p.stock, COUNT(o.id) as sold 
                   FROM products p LEFT JOIN orders o ON p.name = o.product_name 
                   WHERE o.created_at > datetime('now', '-7 days') 
                   GROUP BY p.id ORDER BY sold DESC LIMIT 5""",
                fetchall=True
            )
            if not hot or len(hot) == 0:
                hot = db_query_fn("SELECT name, price, stock FROM products ORDER BY id DESC LIMIT 5", fetchall=True)
            return {
                "message": "আজকের হট ডিলস!",
                "deals": [{"name": h["name"], "price": h["price"], "stock": h["stock"]} for h in (hot or [])]
            }
        except Exception as e:
            return {"error": str(e)}
    
    tool_registry.register(
        "check_stock",
        "Check product stock and availability in Dhaka Exclusive catalog",
        {
            "type": "object",
            "properties": {
                "product_name": {"type": "string", "description": "Product name to search (optional, leave empty for all)"}
            },
            "required": []
        },
        check_stock
    )
    
    tool_registry.register(
        "create_order",
        "Create a new order for a customer",
        {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "Customer phone number"},
                "name": {"type": "string", "description": "Customer name"},
                "product": {"type": "string", "description": "Product name to order"},
                "quantity": {"type": "integer", "description": "Quantity (default 1)"},
                "address": {"type": "string", "description": "Delivery address"},
                "total": {"type": "integer", "description": "Total price in BDT"}
            },
            "required": ["phone", "product"]
        },
        create_order
    )
    
    tool_registry.register(
        "get_catalog",
        "Get Dhaka Exclusive product catalog with prices",
        {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Category filter (optional)"}
            },
            "required": []
        },
        get_catalog
    )
    
    tool_registry.register(
        "get_delivery_info",
        "Get delivery time, payment methods, and return policy",
        {
            "type": "object",
            "properties": {},
            "required": []
        },
        get_delivery_info
    )
    
    tool_registry.register(
        "get_order_status",
        "Check order status for a customer by phone number",
        {
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "Customer phone number"}
            },
            "required": ["phone"]
        },
        get_order_status
    )
    
    tool_registry.register(
        "get_hot_deals",
        "Get today's hot selling products and deals",
        {
            "type": "object",
            "properties": {},
            "required": []
        },
        get_hot_deals
    )
    
    logger.info(f"Registered {len(tool_registry._tools)} business tools")

# =====================================================================
# ADVANCED INTENT CLASSIFIER (AI-Powered)
# =====================================================================

class IntentClassifier:
    """AI-powered intent classification with keyword fallback"""
    
    INTENTS = {
        "place_order": "Customer wants to place/buy/order a product",
        "catalog_request": "Customer wants to see product list/catalog",
        "price_inquiry": "Customer asking about price/cost",
        "order_status": "Customer wants to check order/delivery status",
        "payment_info": "Customer asking about payment methods",
        "delivery_info": "Customer asking about delivery time/location",
        "complaint": "Customer has a complaint/problem with product/order",
        "return_request": "Customer wants to return/exchange product",
        "greeting": "Customer saying hello/hi/salam",
        "general_inquiry": "General question about the shop/business",
        "product_recommendation": "Customer wants product suggestions",
        "stock_check": "Customer asking if specific product is available/in stock"
    }
    
    @classmethod
    def classify(cls, message: str, engine: LLMEngine = None) -> dict:
        """Classify intent with AI if available, fallback to keywords"""
        msg_lower = message.lower().strip()
        
        # Fast keyword pre-check for common intents
        if any(w in msg_lower for w in ["লিস্ট", "list", "ক্যাটালগ", "catalog", "কী আছে", "ki ace", "প্রোডাক্ট লিস্ট"]):
            return {"intent": "catalog_request", "confidence": 0.95, "method": "keyword"}
        
        if any(w in msg_lower for w in ["দাম", "price", "কত", "tk", "taka", "টাকা"]):
            return {"intent": "price_inquiry", "confidence": 0.85, "method": "keyword"}
        
        if any(w in msg_lower for w in ["অর্ডার", "order", "status", "কবে", "ডেলিভারি", "delivery", "পাঠানো"]):
            return {"intent": "order_status", "confidence": 0.85, "method": "keyword"}
        
        if any(w in msg_lower for w in ["কিনব", "buy", "কনফার্ম", "confirm", "নিব", "চাই", "book", "অর্ডার করব"]):
            return {"intent": "place_order", "confidence": 0.85, "method": "keyword"}
        
        if any(w in msg_lower for w in ["পেমেন্ট", "payment", "বিকাশ", "bkash", "নগদ", "nagad", "ক্যাশ"]):
            return {"intent": "payment_info", "confidence": 0.9, "method": "keyword"}
        
        if any(w in msg_lower for w in ["হাই", "hello", "hi", "আসসালামু", "salam", "কেমন", "আছেন", "আছো"]):
            return {"intent": "greeting", "confidence": 0.9, "method": "keyword"}
        
        if any(w in msg_lower for w in ["খারাপ", "bad", "problem", "সমস্যা", "complain", "অভিযোগ", "ঠিক না"]):
            return {"intent": "complaint", "confidence": 0.8, "method": "keyword"}
        
        if any(w in msg_lower for w in ["রিটার্ন", "return", "ফেরত", "change", "বদল"]):
            return {"intent": "return_request", "confidence": 0.8, "method": "keyword"}
        
        if any(w in msg_lower for w in ["ঠিকানা", "address", "লোকেশন", "কোথায়", "দোকান"]):
            return {"intent": "delivery_info", "confidence": 0.8, "method": "keyword"}
        
        if any(w in msg_lower for w in ["আছে", "available", "stock", "in stock"]):
            return {"intent": "stock_check", "confidence": 0.75, "method": "keyword"}
        
        # Default
        return {"intent": "general_inquiry", "confidence": 0.5, "method": "keyword"}
    
    @classmethod
    def get_prompt_for_intent(cls, intent: str) -> str:
        prompts = {
            "place_order": "Customer wants to order. Extract order details and confirm.",
            "catalog_request": "Customer wants to see products. List best items with prices.",
            "price_inquiry": "Answer price questions clearly. Suggest value deals.",
            "order_status": "Check order and reassure customer about delivery.",
            "payment_info": "Explain payment methods: COD, bKash, Nagad.",
            "delivery_info": "Explain delivery: 24h Dhaka, 48-72h outside. COD available.",
            "complaint": "Apologize sincerely. Offer resolution. Escalate if needed.",
            "return_request": "Explain 7-day return policy. Offer exchange first.",
            "greeting": "Warm welcome. Mention today's offers.",
            "product_recommendation": "Suggest relevant products based on popularity.",
            "stock_check": "Check and report stock. If low, create urgency.",
            "general_inquiry": "Be helpful. Guide toward placing an order."
        }
        return prompts.get(intent, prompts["general_inquiry"])
