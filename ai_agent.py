"""
Dhaka Exclusive - AI Sales Agent
Advanced conversational AI with tool calling, memory, and multi-provider support
Integrates ai_core.py with the WhatsApp bot
"""
import os
import json
import re
import time
import logging
from typing import Optional, Dict, List, Any, Tuple

logger = logging.getLogger(__name__)

# Lazy imports to avoid circular deps
_llm: Any = None
_db_query: Any = None
_improve: Any = None

def _get_improve():
    global _improve
    if _improve is None:
        from self_improve import get_improvement_engine
        _improve = get_improvement_engine(db_query_fn=_get_db())
    return _improve

def _get_llm():
    global _llm
    if _llm is None:
        from ai_core import get_llm_engine, _register_default_tools
        _llm = get_llm_engine()
    return _llm

def _get_db():
    global _db_query
    if _db_query is None:
        from app import db_query as dq
        _db_query = dq
        # Register tools once DB is available
        from ai_core import _register_default_tools
        _register_default_tools(_db_query)
    return _db_query

def _get_memory(phone: str):
    from ai_core import get_memory
    return get_memory(phone)

def _create_system_prompt(intent: str, customer_phone: str = "") -> str:
    """Build comprehensive system prompt for the AI sales agent"""
    from ai_core import IntentClassifier
    
    db = _get_db()
    
    # Get products
    products = db("SELECT name, price, stock, description, category FROM products ORDER BY id DESC LIMIT 30", fetchall=True) or []
    product_lines = []
    for p in products:
        stock = "In Stock ✅" if p.get('stock', 0) > 5 else f"Only {p.get('stock', 0)} left! ⚠️"
        desc = f" - {p['description'][:60]}" if p.get('description') else ""
        cat = f" [{p.get('category', '')}]" if p.get('category') else ""
        product_lines.append(f"• {p['name']}{cat} — {p['price']}৳ | {stock}{desc}")
    
    products_text = "\n".join(product_lines[:25]) if product_lines else "No products"
    
    # Hot deals
    hot = db("""
        SELECT p.name, p.price, COUNT(o.id) as sold 
        FROM products p LEFT JOIN orders o ON p.name = o.product_name 
        WHERE o.created_at > datetime('now', '-14 days') 
        GROUP BY p.id ORDER BY sold DESC LIMIT 5
    """, fetchall=True)
    if not hot:
        hot = db("SELECT name, price FROM products ORDER BY id DESC LIMIT 5", fetchall=True) or []
    hot_text = "\n".join([f"• {h['name']} — {h['price']}৳" for h in hot])
    
    # Customer context
    customer_ctx = ""
    if customer_phone and customer_phone not in ("group_team", "group_orders"):
        orders = db("SELECT id, product_name, total, status FROM orders WHERE phone=? ORDER BY id DESC LIMIT 5", (customer_phone,), fetchall=True) or []
        if orders:
            total_spent = sum(o.get('total', 0) for o in orders)
            customer_ctx = f"""\nCUSTOMER CONTEXT:
- Previous Orders: {len(orders)}
- Total Spent: {total_spent}৳  
- Last Order: {orders[0].get('product_name', 'N/A')} ({orders[0].get('status', 'N/A')})
- Loyal Customer: {'Yes ⭐' if len(orders) > 2 else 'New' if len(orders) == 0 else 'Returning'}"""
        else:
            customer_ctx = "\nCUSTOMER CONTEXT: New customer - no previous orders"
    
    intent_guide = IntentClassifier.get_prompt_for_intent(intent)
    
    return f"""You are Dhaka Exclusive's AI Sales Assistant — a premium Bangladeshi fashion/lifestyle e-commerce brand.

## YOUR ROLE
You help customers place orders, answer questions, and provide excellent service in BANGLA. Be friendly, professional, and persuasive — but never pushy. You represent a trusted brand.

## PRODUCT CATALOG
{products_text}

## 🔥 TODAY'S HOT DEALS
{hot_text}

## BUSINESS INFO
- Delivery: Dhaka Metro 24 hours | Outside Dhaka 48-72 hours
- Payment: Cash on Delivery (COD) + bKash + Nagad
- Returns: 7 days for damaged/wrong items
- Free delivery on orders above 2000৳
- Location: Online-based, serving all Bangladesh
{customer_ctx}

## CURRENT INTENT: {intent}
{intent_guide}

## RESPONSE RULES
1. **ALWAYS respond in Bangla** (Bengali) — use natural, conversational Bangla
2. Be warm and friendly — use "ভাইয়া/আপু" ONLY for first-time customers
3. Keep responses concise (2-4 sentences max for simple queries)
4. Always mention COD option and fast delivery
5. When product stock is low (< 5), create urgency: "শেষ হওয়ার আগেই অর্ডার করুন!"
6. For catalog requests: list products with prices clearly
7. For orders: confirm details before finalizing
8. Never make false promises about delivery times
9. If you don't know something, offer to connect with human support
10. Use emojis sparingly (1-2 per response max)
11. End order-related responses with a call to action
12. Mention "ফ্রি ডেলিভারি" for orders above 2000৳

## TOOLS AVAILABLE
You have access to these tools — use them when needed:
- check_stock: Check product availability
- get_catalog: Get full product catalog  
- create_order: Place a new order (get customer confirmation first)
- get_order_status: Check customer's order status
- get_delivery_info: Get delivery/payment information
- get_hot_deals: Get today's best deals

When a customer wants to order, first check stock, then confirm details, then create the order.
When a customer wants to see products, use get_catalog or check_stock.
"""


class AdvancedSalesAgent:
    """Advanced AI Sales Agent for Dhaka Exclusive"""
    
    def __init__(self):
        self._tool_registry = None
        self._memory_db_wired = False
    
    def _init_tools(self):
        if self._tool_registry is None:
            from ai_core import tool_registry
            self._tool_registry = tool_registry
    
    def _wire_memory_db(self, phone: str):
        """Ensure memory has DB access for hydration on first use"""
        if not self._memory_db_wired:
            from ai_core import _memory_store, get_memory
            db = _get_db()
            mem = get_memory(phone, db_query_fn=db)
            self._memory_db_wired = True
            return mem
        from ai_core import get_memory
        db = _get_db()
        return get_memory(phone, db_query_fn=db)
    
    def process_message(
        self, 
        user_message: str, 
        customer_phone: str = "",
        chat_history: List[dict] = None,
        image_path: str = None,
        voice_path: str = None
    ) -> str:
        """
        Process incoming customer message and return AI response.
        This is the main entry point replacing get_optimized_gemini_reply.
        """
        llm = _get_llm()
        db = _get_db()
        self._init_tools()
        # Wire DB into memory for auto-hydration
        memory = self._wire_memory_db(customer_phone)
        
        # Handle media
        if voice_path:
            return self._handle_voice(voice_path, customer_phone)
        
        if image_path:
            return self._handle_image(image_path, customer_phone)
        
        # Team/Orders group mode
        if customer_phone == "group_team":
            return self._team_group_reply(user_message, llm)
        if customer_phone == "group_orders":
            return self._orders_group_reply(user_message)
        
        # Classify intent
        from ai_core import IntentClassifier
        intent_info = IntentClassifier.classify(user_message, llm)
        intent = intent_info["intent"]
        
        logger.info(f"Intent: {intent} (confidence: {intent_info['confidence']}) for {customer_phone}")
        
        # Fast-path: handle common intents without AI for speed
        fast_reply = self._fast_path(user_message, intent, customer_phone)
        if fast_reply:
            return fast_reply
        
        # Build system prompt | Inject learned improvements
        system_prompt = _create_system_prompt(intent, customer_phone)
        try:
            improve = _get_improve()
            system_prompt = improve.inject_learned_context(system_prompt)
        except Exception as e:
            logger.debug(f"Improve inject skipped: {e}")
        
        # Build messages with memory
        messages = []
        
        # Add conversation history from memory
        memory_context = memory.get_context(max_recent=8)
        for m in memory_context:
            messages.append({"role": m["role"], "content": m["content"]})
        
        # Add current message
        messages.append({"role": "user", "content": user_message})
        
        # Add chat history if provided (from DB)
        if chat_history:
            for msg in chat_history[-6:]:
                role = "user" if msg.get("direction") == "inbound" else "assistant"
                content = msg.get("content", "")
                if content and len(content) < 500:
                    # Avoid duplicates
                    if not messages or messages[-1]["content"] != content:
                        messages.insert(-1, {"role": role, "content": content})
        
        # Call LLM
        response = llm.chat(
            messages=messages,
            system_prompt=system_prompt,
            preferred_provider="gemini"
        )
        
        reply_text = response.get("text", "")
        
        # Handle tool calls if any
        if response.get("tool_calls"):
            results = []
            for tc in response["tool_calls"]:
                # Handle different tool call formats
                if isinstance(tc, dict):
                    tool_name = tc.get("name") or tc.get("function", {}).get("name", "")
                    tool_args = tc.get("args") or tc.get("arguments", {})
                    if isinstance(tool_args, str):
                        try:
                            tool_args = json.loads(tool_args)
                        except:
                            tool_args = {}
                else:
                    continue
                
                if tool_name:
                    result = self._tool_registry.execute(tool_name, tool_args)
                    results.append(f"[Tool: {tool_name}] {result}")
            
            if results:
                # Run a follow-up to incorporate tool results
                follow_up_messages = messages.copy()
                follow_up_messages.append({"role": "assistant", "content": f"[Tools executed: {'; '.join(results)}]"})
                follow_up_messages.append({"role": "user", "content": "Based on the tool results above, provide a natural response in Bangla."})
                
                fu_response = llm.chat(
                    messages=follow_up_messages,
                    system_prompt=system_prompt,
                    preferred_provider="gemini"
                )
                reply_text = fu_response.get("text", reply_text)
        
        # Update memory
        memory.add("user", user_message)
        memory.add("assistant", reply_text)
        
        # ---- SELF-IMPROVEMENT PIPELINE ----
        try:
            improve = _get_improve()
            # 1. Infer outcome from this interaction
            outcome = improve.infer_outcome(user_message, reply_text, intent)
            latency = response.get("latency_ms", 0) if isinstance(response.get("latency_ms"), (int, float)) else 0
            
            # 2. Track conversation outcome
            improve.track_outcome(
                customer_phone=customer_phone,
                intent=intent,
                resolution=outcome,
                response_time_ms=latency,
                ai_provider=response.get("provider", ""),
                product_mentioned="",
                message_count=1
            )
            
            # 3. Learn from successful interactions
            if outcome in ("sale", "resolved"):
                improve.learn_from_interaction(user_message, reply_text, intent, outcome)
            
            # 4. Track unresolved as unknown queries (potential FAQ gaps)
            if outcome == "unresolved" and intent_info["confidence"] < 0.6:
                improve.track_unknown_query(user_message, customer_phone)
            
            # 5. Periodic analysis trigger
            analysis = improve.maybe_analyze()
            if analysis and analysis.get("ready"):
                logger.info(f"Self-improvement analysis: {analysis['metrics']}")
                if analysis.get("suggested_actions"):
                    for action in analysis["suggested_actions"]:
                        logger.info(f"Improvement action: {action}")
        except Exception as e:
            logger.debug(f"Self-improvement pipeline: {e}")
        
        # Log provider stats periodically
        if response.get("provider") and response["provider"] != "fallback":
            logger.info(f"AI: {response['provider']}/{response['model']} ({response.get('latency_ms', 0)}ms)")
        
        return reply_text or "ধন্যবাদ! আমাদের টিম শীঘ্রই আপনাকে সাহায্য করবে।"
    
    def _fast_path(self, user_message: str, intent: str, phone: str) -> Optional[str]:
        """Handle common queries directly without AI for speed"""
        db = _get_db()
        
        if intent == "catalog_request":
            products = db("SELECT name, price, stock FROM products ORDER BY id DESC LIMIT 20", fetchall=True) or []
            if not products:
                return "🎯 আমাদের নতুন কালেকশন শীঘ্রই আসছে! কিছুক্ষণ পর আবার চেক করুন।"
            
            lines = ["📦 *Dhaka Exclusive — প্রোডাক্ট ক্যাটালগ*\n"]
            for i, p in enumerate(products, 1):
                stock = "✅" if p.get('stock', 0) > 5 else f"⚠️ মাত্র {p.get('stock', 0)} টি"
                lines.append(f"{i}. {p['name']}\n   💰 {p['price']}৳ | {stock}")
            
            lines.append(f"\n🛒 মোট {len(products)} টি প্রোডাক্ট")
            lines.append("🚚 ঢাকা ২৪ ঘণ্টা | বাইরে ৪৮-৭২ ঘণ্টা")
            lines.append("💵 ক্যাশ অন ডেলিভারি")
            lines.append("\n✍️ অর্ডার করতে প্রোডাক্টের নাম ও ঠিকানা লিখুন!")
            return "\n".join(lines)
        
        if intent == "order_status":
            orders = db("SELECT id, product_name, total, status, created_at FROM orders WHERE phone=? ORDER BY id DESC LIMIT 5", (phone,), fetchall=True) or []
            if not orders:
                return "আপনার কোনো অর্ডার পাওয়া যায়নি। 💁 অর্ডার করতে প্রোডাক্টের নাম লিখুন!"
            
            status_emoji = {
                "pending": "⏳ প্রসেসিং",
                "confirmed": "✅ কনফার্মড",
                "shipped": "📦 শিপড",
                "delivered": "🏠 ডেলিভার্ড",
                "cancelled": "❌ ক্যান্সেলড"
            }
            
            lines = ["📋 *আপনার অর্ডার স্ট্যাটাস*\n"]
            for o in orders:
                st = status_emoji.get(o.get('status', 'pending'), o.get('status', 'pending'))
                lines.append(f"🔹 #{o['id']} — {o['product_name']}")
                lines.append(f"   {st} | {o['total']}৳ | {str(o.get('created_at', ''))[:10]}")
                lines.append("")
            
            lines.append("❓ কোনো প্রশ্ন থাকলে জানাবেন!")
            return "\n".join(lines)
        
        if intent == "payment_info":
            methods = db("SELECT name, type, number FROM payment_methods WHERE is_active=1", fetchall=True) or []
            lines = ["💳 *পেমেন্ট পদ্ধতি*\n"]
            lines.append("1️⃣ ক্যাশ অন ডেলিভারি (COD) — সবচেয়ে সহজ!")
            for m in methods:
                lines.append(f"• {m['name']} ({m['type']}): {m.get('number', 'N/A')}")
            lines.append("\n✅ ডেলিভারির সময় পেমেন্ট করুন।")
            return "\n".join(lines)
        
        if intent == "greeting":
            greetings = [
                "আসসালামু আলাইকুম! 🤲 Dhaka Exclusive-এ আপনাকে স্বাগতম!\n\nআমাদের আজকের হট ডিলস দেখুন! কোনো প্রোডাক্ট প্রয়োজন হলে জানাবেন। লিস্ট দেখতে 'লিস্ট' লিখুন।",
                "হ্যালো! 😊 Dhaka Exclusive-এ স্বাগতম!\n\nআমাদের সেরা কালেকশন থেকে পছন্দের প্রোডাক্ট অর্ডার করুন। ক্যাটালগ দেখতে 'লিস্ট' লিখুন।",
                "স্বাগতম! 🎯 Dhaka Exclusive — আপনার বিশ্বস্ত অনলাইন শপ!\n\nকী প্রোডাক্ট লাগবে বলুন, অথবা 'লিস্ট' লিখে ক্যাটালগ দেখুন।"
            ]
            import random
            return random.choice(greetings)
        
        return None  # Let AI handle it
    
    def _handle_image(self, image_path: str, phone: str) -> str:
        """Handle image analysis with AI"""
        from app import GEMINI_API_KEY, base64
        if not GEMINI_API_KEY:
            return "📸 ছবি পেয়েছি! কিন্তু ইমেজ অ্যানালাইসিস ফিচার অ্যাক্টিভেট করতে GEMINI_KEY সেট করুন।"
        
        try:
            import requests as req
            with open(image_path, "rb") as f:
                image_bytes = f.read()
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            
            ext = os.path.splitext(image_path)[1].lower()
            mime = "image/jpeg" if ext in [".jpg", ".jpeg"] else "image/png" if ext == ".png" else "image/webp"
            
            db = _get_db()
            products = db("SELECT name, price FROM products LIMIT 30", fetchall=True) or []
            product_list = "\n".join([f"- {p['name']} ({p['price']}৳)" for p in products])
            
            prompt = (
                "তুমি Dhaka Exclusive-এর AI সেলস অ্যাসিস্ট্যান্ট। ছবিটি দেখো।\n"
                "যদি এটি প্রোডাক্টের ছবি হয়, আমাদের ক্যাটালগের সাথে মিলিয়ে দেখো।\n"
                f"আমাদের ক্যাটালগ:\n{product_list}\n\n"
                "সংক্ষেপে, বন্ধুত্বপূর্ণ বাংলায় উত্তর দাও। প্রোডাক্ট চিনতে পারলে দাম বলো এবং অর্ডার নিতে উৎসাহিত করো।"
            )
            
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent?key={GEMINI_API_KEY}"
            payload = {
                "contents": [{
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": mime, "data": image_b64}}
                    ]
                }],
                "generationConfig": {"temperature": 0.4, "maxOutputTokens": 500}
            }
            r = req.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=45)
            res = r.json()
            
            if res.get("candidates"):
                parts = res["candidates"][0].get("content", {}).get("parts", [])
                for part in parts:
                    if "text" in part:
                        return part["text"].strip()
            
            return "📸 ছবি পেয়েছি! প্রোডাক্টের নাম লিখে পাঠান, আমি দাম ও স্টক চেক করে জানাচ্ছি।"
        except Exception as e:
            logger.error(f"Image handling error: {e}")
            return "📸 ছবি পেয়েছি! প্রযুক্তিগত সমস্যার কারণে ছবি বিশ্লেষণ করা যাচ্ছে না। অনুগ্রহ করে প্রোডাক্টের নাম লিখুন।"
    
    def _handle_voice(self, voice_path: str, phone: str) -> str:
        """Handle voice message - placeholder for future"""
        return "🎤 ভয়েস মেসেজ পেয়েছি। আপাতত টেক্সট মেসেজ পাঠান, আমরা দ্রুত রেসপন্স দিচ্ছি।"
    
    def _team_group_reply(self, user_message: str, llm) -> str:
        """Reply for internal team group"""
        prompt = f"""তুমি Dhaka Exclusive টিমের মেম্বার। টিম গ্রুপে বন্ধুর মতো উত্তর দাও।
        
Business context:
- তুমি Dhaka Exclusive (ফ্যাশন/লাইফস্টাইল ই-কমার্স)-এর টিম মেম্বার
- বাংলায় বন্ধুসুলভ ভাবে উত্তর দাও
- টিমের মোরাল বাড়াও, প্রফেশনাল কিন্তু ফ্রেন্ডলি থাকো
- ১-২ লাইনে সংক্ষিপ্ত উত্তর দাও

Team message: {user_message}

Reply (বাংলায়, informal, 1-3 lines):"""
        
        response = llm.chat(
            messages=[{"role": "user", "content": user_message}],
            system_prompt=prompt,
            preferred_provider="gemini"
        )
        return response.get("text", "ভাইয়া, দারুণ! চলতে থাকুক 💪")
    
    def _orders_group_reply(self, user_message: str) -> str:
        """Extract order details from orders group"""
        return f"""NAME: 
PHONE: 
ADDRESS: 
PRODUCT: {user_message}
QUANTITY: 1
PRICE: 0

If NOT an order, reply: NOT_AN_ORDER"""


# Singleton
_sales_agent: Optional[AdvancedSalesAgent] = None

def get_sales_agent() -> AdvancedSalesAgent:
    global _sales_agent
    if _sales_agent is None:
        _sales_agent = AdvancedSalesAgent()
    return _sales_agent


# =====================================================================
# COMPATIBILITY WRAPPER - Drop-in replacement for get_optimized_gemini_reply
# =====================================================================

def get_advanced_ai_reply(user_message: str, customer_phone: str = "", 
                           chat_history: List[dict] = None, 
                           image_path: str = None, 
                           voice_path: str = None) -> str:
    """
    Drop-in replacement for get_optimized_gemini_reply.
    Uses the advanced AI agent with multi-provider support, tool calling, and memory.
    """
    try:
        agent = get_sales_agent()
        return agent.process_message(
            user_message=user_message,
            customer_phone=customer_phone,
            chat_history=chat_history,
            image_path=image_path,
            voice_path=voice_path
        )
    except Exception as e:
        logger.error(f"Advanced AI error: {e}", exc_info=True)
        # Fallback to original function
        try:
            from app import get_optimized_gemini_reply
            return get_optimized_gemini_reply(user_message, customer_phone, chat_history, image_path, voice_path)
        except:
            return "ধন্যবাদ! আমরা শীঘ্রই আপনার সাথে যোগাযোগ করবো। ✨"
