"""
Dhaka Exclusive — Auto Self-Improvement Engine
================================================
Learns from every conversation to continuously improve:
• Tracks conversation outcomes (sale, resolved, dead-end)
• Discovers successful response patterns
• Auto-generates FAQ entries from unresolved queries
• Learns effective sales tactics per customer segment
• Periodically tunes the system prompt with learned insights

Architecture:
  Every message → outcome tracked → patterns extracted → improvements applied
  Runs in background: periodic analysis every N conversations
"""

import os
import json
import time
import re
import logging
import threading
from typing import Optional, Dict, List, Any, Tuple, Set
from dataclasses import dataclass, field
from collections import Counter, defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ============================================================================
# DATA MODELS
# ============================================================================

@dataclass
class ConversationOutcome:
    """Tracks result of a customer interaction"""
    customer_phone: str
    intent: str
    resolution: str  # "sale", "resolved", "unresolved", "escalated", "abandoned"
    confidence_before: float  # Bot's confidence before the interaction
    confidence_after: float   # Bot's confidence after
    response_time_ms: int
    ai_used: bool
    ai_provider: str = ""
    message_count: int = 0
    product_mentioned: str = ""
    timestamp: float = field(default_factory=time.time)

@dataclass 
class LearnedPattern:
    """A successful interaction pattern the bot discovered"""
    trigger_words: List[str]      # What user said
    response_template: str        # What worked
    outcome: str                  # What happened (sale/resolved)
    success_count: int = 0
    total_uses: int = 0
    confidence: float = 0.0       # success_count / total_uses
    last_used: float = field(default_factory=time.time)

# ============================================================================
# SELF IMPROVEMENT ENGINE
# ============================================================================

class SelfImprovementEngine:
    """
    Continuously learns from conversations and improves bot responses.
    
    Learning loops:
    1. Outcome Tracking: Every conversation → labeled outcome
    2. Pattern Mining: Successful patterns → learned templates
    3. FAQ Generation: Repeated unresolved queries → auto FAQ
    4. Prompt Tuning: Aggregated learnings → optimised system prompt
    5. Gap Detection: Questions bot can't answer → human escalation
    """
    
    def __init__(self, db_query_fn=None):
        self.db = db_query_fn
        self.outcomes: List[ConversationOutcome] = []
        self.patterns: Dict[str, LearnedPattern] = {}  # hash → pattern
        self.auto_faqs: Dict[str, Dict] = {}
        self.unknown_queries: List[Dict] = []
        self.conversation_count: int = 0
        self.last_analysis: float = 0
        self.analysis_interval: int = 50  # Analyze every 50 conversations
        self._lock = threading.Lock()
        
        # Performance metrics
        self.sales_from_convos: int = 0
        self.total_convos: int = 0
        self.resolution_rate: float = 0.0
        
        # Initialize DB tables
        self._init_db()
        
        # Load existing knowledge
        self._load_persisted()
        
        logger.info("SelfImprovementEngine initialized")
    
    def _init_db(self):
        """Create tables for self-improvement data"""
        if not self.db:
            return
        try:
            self.db("""
                CREATE TABLE IF NOT EXISTS conversation_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_phone TEXT,
                    intent TEXT,
                    resolution TEXT,
                    ai_provider TEXT,
                    response_time_ms INTEGER,
                    product_mentioned TEXT,
                    message_count INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.db("""
                CREATE TABLE IF NOT EXISTS learned_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pattern_hash TEXT UNIQUE,
                    trigger_words TEXT,  -- JSON array
                    response_template TEXT,
                    outcome TEXT,
                    success_count INTEGER DEFAULT 0,
                    total_uses INTEGER DEFAULT 0,
                    confidence REAL DEFAULT 0.0,
                    last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.db("""
                CREATE TABLE IF NOT EXISTS auto_faq (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT,
                    answer TEXT,
                    category TEXT,
                    source_count INTEGER DEFAULT 1,
                    confidence REAL DEFAULT 0.5,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.db("""
                CREATE TABLE IF NOT EXISTS unknown_queries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT,
                    customer_phone TEXT,
                    frequency INTEGER DEFAULT 1,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    suggested_answer TEXT,
                    is_resolved INTEGER DEFAULT 0
                )
            """)
            self.db("""
                CREATE TABLE IF NOT EXISTS improvement_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    action TEXT,
                    details TEXT,
                    impact_score REAL DEFAULT 0.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """, commit=True)
        except Exception as e:
            logger.error(f"SelfImprovement DB init error: {e}")
    
    # ========================================================================
    # OUTCOME TRACKING
    # ========================================================================
    
    def track_outcome(self, customer_phone: str, intent: str, resolution: str,
                      response_time_ms: int = 0, ai_provider: str = "",
                      product_mentioned: str = "", message_count: int = 1):
        """Record the outcome of a conversation turn"""
        with self._lock:
            outcome = ConversationOutcome(
                customer_phone=customer_phone,
                intent=intent,
                resolution=resolution,
                confidence_before=0.5,
                confidence_after=0.0,
                response_time_ms=response_time_ms,
                ai_used=bool(ai_provider),
                ai_provider=ai_provider,
                message_count=message_count,
                product_mentioned=product_mentioned
            )
            self.outcomes.append(outcome)
            self.total_convos += 1
            
            if resolution in ("sale", "order_placed"):
                self.sales_from_convos += 1
            
            # Persist to DB
            if self.db:
                try:
                    self.db(
                        """INSERT INTO conversation_outcomes 
                           (customer_phone, intent, resolution, ai_provider, 
                            response_time_ms, product_mentioned, message_count)
                           VALUES (?,?,?,?,?,?,?)""",
                        (customer_phone, intent, resolution, ai_provider,
                         response_time_ms, product_mentioned, message_count),
                        commit=True
                    )
                except Exception as e:
                    logger.error(f"Track outcome DB error: {e}")
            
            # Trim outcomes list (keep last 1000)
            if len(self.outcomes) > 1000:
                self.outcomes = self.outcomes[-500:]
    
    def infer_outcome(self, user_message: str, bot_response: str, intent: str) -> str:
        """Auto-detect conversation outcome from message content"""
        msg = (user_message + " " + bot_response).lower()
        
        # Sale indicators
        if any(w in msg for w in ["অর্ডার কনফার্ম", "order confirmed", "সফলভাবে", "পেমেন্ট", "order placed",
                                    "কনফার্মড", "confirmed", "ঠিক আছে অর্ডার", "অর্ডারটি নেওয়া"]):
            return "sale"
        
        # Resolved indicators
        if any(w in msg for w in ["ধন্যবাদ", "thank", "ঠিক আছে", "ok", "okay", "জানিয়ে দিব", 
                                    "thanks", "ঠিকাছে", "বুঝলাম", "আচ্ছা"]):
            return "resolved"
        
        # Escalation indicators
        if any(w in msg for w in ["টিম", "কল", "call", "agent", "এজেন্ট", "মানব", "অফিসার",
                                    "কাস্টমার কেয়ার", "helpline"]):
            return "escalated"
        
        # Abandonment / dead-end
        if len(user_message.strip()) <= 2:
            return "abandoned"
        
        return "unresolved"
    
    # ========================================================================
    # PATTERN LEARNING
    # ========================================================================
    
    def learn_from_interaction(self, user_message: str, bot_response: str,
                                intent: str, outcome: str):
        """Extract patterns from successful interactions"""
        if outcome not in ("sale", "resolved"):
            return
        
        # Extract trigger keywords from user message
        triggers = self._extract_keywords(user_message)
        if not triggers:
            return
        
        # Create pattern hash
        pattern_hash = self._hash_pattern(intent, triggers)
        
        # Update or create pattern
        with self._lock:
            if pattern_hash in self.patterns:
                p = self.patterns[pattern_hash]
                p.success_count += 1
                p.total_uses += 1
                p.last_used = time.time()
            else:
                self.patterns[pattern_hash] = LearnedPattern(
                    trigger_words=triggers,
                    response_template=bot_response[:500],
                    outcome=outcome,
                    success_count=1,
                    total_uses=1
                )
            
            p = self.patterns[pattern_hash]
            p.confidence = p.success_count / max(p.total_uses, 1)
            
            # Persist
            if self.db and p.total_uses % 5 == 0:  # Every 5 uses
                self._persist_pattern(pattern_hash, p)
    
    def _extract_keywords(self, text: str) -> List[str]:
        """Extract meaningful keywords from Bengali/English text"""
        text = text.lower().strip()
        words = re.findall(r'[\u0980-\u09FF]{2,}|[a-zA-Z]{3,}', text)
        
        # Filter noise words
        noise = {"এখন", "আমি", "তুমি", "সে", "তা", "এই", "ওই", "একটা", "একটি",
                 "the", "and", "for", "that", "this", "with", "have", "from",
                 "আছে", "কি", "না", "হ্যাঁ", "ঠিক", "আর", "যদি", "কিন্তু"}
        
        return [w for w in words if w not in noise][:5]
    
    def _hash_pattern(self, intent: str, triggers: List[str]) -> str:
        """Create a stable hash for pattern deduplication"""
        key = f"{intent}:{'+'.join(sorted(triggers)[:3])}"
        import hashlib
        return hashlib.md5(key.encode()).hexdigest()[:12]
    
    def _persist_pattern(self, pattern_hash: str, pattern: LearnedPattern):
        """Save pattern to DB"""
        try:
            self.db(
                """INSERT OR REPLACE INTO learned_patterns 
                   (pattern_hash, trigger_words, response_template, outcome,
                    success_count, total_uses, confidence, last_used)
                   VALUES (?,?,?,?,?,?,?,datetime('now'))""",
                (pattern_hash, json.dumps(pattern.trigger_words, ensure_ascii=False),
                 pattern.response_template, pattern.outcome,
                 pattern.success_count, pattern.total_uses, pattern.confidence),
                commit=True
            )
        except Exception as e:
            logger.error(f"Persist pattern error: {e}")
    
    def get_best_patterns(self, intent: str = None, limit: int = 5, min_success: int = 2) -> List[Dict]:
        """Get top performing patterns"""
        with self._lock:
            candidates = [p for p in self.patterns.values() 
                         if p.success_count >= min_success and p.confidence >= 0.5]
            if intent:
                candidates = [p for p in candidates if intent in str(p.trigger_words)]
            
            candidates.sort(key=lambda p: (p.confidence, p.success_count), reverse=True)
            
            return [{
                "triggers": p.trigger_words,
                "template": p.response_template[:300],
                "success_rate": int(p.confidence * 100),
                "uses": p.total_uses,
                "outcome": p.outcome
            } for p in candidates[:limit]]
    
    # ========================================================================
    # AUTO FAQ GENERATION
    # ========================================================================
    
    def track_unknown_query(self, query: str, customer_phone: str):
        """Track questions the bot couldn't answer well"""
        with self._lock:
            # Check if similar query exists
            for existing in self.unknown_queries:
                if self._similarity(query, existing["query"]) > 0.7:
                    existing["frequency"] += 1
                    existing["last_seen"] = time.time()
                    
                    # Auto-generate FAQ if frequency threshold reached
                    if existing["frequency"] >= 3 and not existing.get("is_resolved"):
                        self._auto_generate_faq(existing)
                    return
            
            self.unknown_queries.append({
                "query": query,
                "customer_phone": customer_phone,
                "frequency": 1,
                "last_seen": time.time(),
                "suggested_answer": "",
                "is_resolved": False
            })
            
            # Persist
            if self.db:
                try:
                    self.db(
                        "INSERT INTO unknown_queries (query, customer_phone) VALUES (?,?)",
                        (query[:300], customer_phone), commit=True
                    )
                except Exception as e:
                    logger.error(f"Unknown query DB error: {e}")
    
    def _similarity(self, a: str, b: str) -> float:
        """Simple word-overlap similarity for Bengali/English"""
        words_a = set(re.findall(r'[\u0980-\u09FF]{2,}|[a-zA-Z]{3,}', a.lower()))
        words_b = set(re.findall(r'[\u0980-\u09FF]{2,}|[a-zA-Z]{3,}', b.lower()))
        if not words_a or not words_b:
            return 0.0
        intersection = words_a & words_b
        return len(intersection) / max(len(words_a), len(words_b))
    
    def _auto_generate_faq(self, query_entry: Dict):
        """Auto-generate FAQ entry from repeated unknown queries"""
        query = query_entry["query"]
        category = self._infer_faq_category(query)
        
        # Check if FAQ already exists
        if self.db:
            existing = self.db(
                "SELECT id FROM auto_faq WHERE question LIKE ?",
                (f"%{query[:30]}%",), fetchone=True
            )
            if existing:
                return
        
        faq_entry = {
            "question": query[:200],
            "answer": f"[AUTO] এই বিষয়ে আমাদের টিম কাজ করছে। বিস্তারিত জানতে কল করুন।",  # Placeholder
            "category": category,
            "source_count": query_entry["frequency"],
            "confidence": min(0.3 + (query_entry["frequency"] * 0.1), 0.8)
        }
        
        with self._lock:
            faq_key = self._hash_pattern("faq", [query[:50]])
            self.auto_faqs[faq_key] = faq_entry
        
        if self.db:
            try:
                self.db(
                    """INSERT OR IGNORE INTO auto_faq 
                       (question, answer, category, source_count, confidence)
                       VALUES (?,?,?,?,?)""",
                    (faq_entry["question"], faq_entry["answer"], faq_entry["category"],
                     faq_entry["source_count"], faq_entry["confidence"]),
                    commit=True
                )
                self.db(
                    """INSERT INTO improvement_log (action, details, impact_score)
                       VALUES ('auto_faq_generated', ?, 0.3)""",
                    (f"FAQ: {query[:100]}",), commit=True
                )
                logger.info(f"Auto FAQ generated: {query[:60]}...")
            except Exception as e:
                logger.error(f"Auto FAQ DB error: {e}")
    
    def _infer_faq_category(self, query: str) -> str:
        """Infer FAQ category from query content"""
        q = query.lower()
        if any(w in q for w in ["দাম", "price", "কত", "টাকা"]):
            return "pricing"
        if any(w in q for w in ["ডেলিভারি", "delivery", "পাঠানো", "কবে"]):
            return "delivery"
        if any(w in q for w in ["সাইজ", "size", "রং", "color"]):
            return "product_specs"
        if any(w in q for w in ["পেমেন্ট", "payment", "বিকাশ", "bkash"]):
            return "payment"
        if any(w in q for w in ["রিটার্ন", "return", "ফেরত"]):
            return "returns"
        if any(w in q for w in ["অর্ডার", "order", "status"]):
            return "orders"
        return "general"
    
    def get_pending_faqs(self, min_frequency: int = 3) -> List[Dict]:
        """Get auto-generated FAQs pending human review"""
        return [{"question": f["question"], "category": f["category"],
                 "frequency": f["source_count"], "confidence": f["confidence"]}
                for f in self.auto_faqs.values() 
                if f["source_count"] >= min_frequency]
    
    # ========================================================================
    # PROMPT TUNING
    # ========================================================================
    
    def generate_prompt_improvements(self) -> Dict[str, Any]:
        """
        Analyze all learnings and suggest prompt improvements.
        Called periodically (every ~200 conversations).
        """
        with self._lock:
            outcomes = self.outcomes[-200:]  # Last 200 interactions
            if len(outcomes) < 20:
                return {"ready": False, "message": "Not enough data yet"}
        
        # Calculate metrics
        total = len(outcomes)
        sales = sum(1 for o in outcomes if o.resolution == "sale")
        resolved = sum(1 for o in outcomes if o.resolution == "resolved")
        unresolved = sum(1 for o in outcomes if o.resolution in ("unresolved", "abandoned"))
        
        resolution_rate = (sales + resolved) / max(total, 1)
        sale_rate = sales / max(total, 1)
        
        # Find problematic intents
        intent_stats = defaultdict(lambda: {"total": 0, "resolved": 0, "unresolved": 0})
        for o in outcomes:
            intent_stats[o.intent]["total"] += 1
            if o.resolution in ("sale", "resolved"):
                intent_stats[o.intent]["resolved"] += 1
            else:
                intent_stats[o.intent]["unresolved"] += 1
        
        weak_intents = []
        for intent, stats in intent_stats.items():
            if stats["total"] >= 5:
                rate = stats["resolved"] / max(stats["total"], 1)
                if rate < 0.5:
                    weak_intents.append({"intent": intent, "resolution_rate": int(rate * 100)})
        
        # Top performing patterns
        top_patterns = self.get_best_patterns(limit=3)
        
        # Frequent unknown queries
        top_unknowns = sorted(self.unknown_queries, 
                             key=lambda x: x["frequency"], reverse=True)[:5]
        
        improvements = {
            "ready": True,
            "metrics": {
                "total_conversations": total,
                "resolution_rate": int(resolution_rate * 100),
                "sale_rate": int(sale_rate * 100),
                "analysis_timestamp": datetime.now().isoformat()
            },
            "weak_intents": weak_intents,
            "top_patterns": top_patterns,
            "top_unknown_queries": [u["query"][:100] for u in top_unknowns],
            "suggested_prompt_additions": self._build_prompt_suggestions(
                weak_intents, top_patterns, top_unknowns
            ),
            "suggested_actions": []
        }
        
        # Generate action suggestions
        if weak_intents:
            improvements["suggested_actions"].append(
                f"Improve handling for: {', '.join(i['intent'] for i in weak_intents[:3])}"
            )
        if top_unknowns:
            improvements["suggested_actions"].append(
                f"Create manual FAQ for {len(top_unknowns)} frequent unknown queries"
            )
        if sale_rate < 0.1 and total > 50:
            improvements["suggested_actions"].append(
                "Sales rate low — consider more aggressive product recommendations"
            )
        
        # Log the analysis
        if self.db:
            try:
                self.db(
                    """INSERT INTO improvement_log (action, details, impact_score)
                       VALUES ('prompt_analysis', ?, ?)""",
                    (json.dumps(improvements["metrics"], ensure_ascii=False),
                     resolution_rate),
                    commit=True
                )
            except Exception:
                pass
        
        return improvements
    
    def _build_prompt_suggestions(self, weak_intents: List[Dict],
                                   top_patterns: List[Dict],
                                   top_unknowns: List[Dict]) -> str:
        """Build concrete prompt improvement suggestions"""
        suggestions = []
        
        if weak_intents:
            for wi in weak_intents[:2]:
                suggestions.append(
                    f"Intent '{wi['intent']}' has low resolution ({wi['resolution_rate']}%). "
                    f"Add clearer instructions for handling this intent."
                )
        
        if top_patterns:
            best = top_patterns[0]
            suggestions.append(
                f"Pattern with triggers {best['triggers']} works well "
                f"({best['success_rate']}% success). Use similar style for related queries."
            )
        
        if top_unknowns:
            suggestions.append(
                f"Common unanswered question: '{top_unknowns[0]['query'][:80]}'. "
                f"Add this to product knowledge base."
            )
        
        return "\n".join(suggestions) if suggestions else "No specific suggestions yet."
    
    # ========================================================================
    # GAP DETECTION
    # ========================================================================
    
    def detect_knowledge_gaps(self) -> List[Dict]:
        """Find what the bot consistently fails at"""
        with self._lock:
            if len(self.outcomes) < 50:
                return []
        
        # Analyze by intent
        intent_gaps = defaultdict(lambda: {"failures": 0, "total": 0})
        for o in self.outcomes[-200:]:
            intent_gaps[o.intent]["total"] += 1
            if o.resolution in ("unresolved", "abandoned"):
                intent_gaps[o.intent]["failures"] += 1
        
        gaps = []
        for intent, stats in intent_gaps.items():
            if stats["total"] >= 10:
                fail_rate = stats["failures"] / stats["total"]
                if fail_rate > 0.4:
                    gaps.append({
                        "area": intent,
                        "failure_rate": int(fail_rate * 100),
                        "total_attempts": stats["total"],
                        "suggestion": f"Train AI on {intent} scenarios"
                    })
        
        return sorted(gaps, key=lambda g: g["failure_rate"], reverse=True)
    
    # ========================================================================
    # PERIODIC ANALYSIS TRIGGER
    # ========================================================================
    
    def maybe_analyze(self) -> Optional[Dict]:
        """Check if enough conversations have passed for analysis"""
        self.conversation_count += 1
        now = time.time()
        
        if (self.conversation_count - self.last_analysis) >= self.analysis_interval:
            self.last_analysis = self.conversation_count
            logger.info(f"Running periodic analysis at {self.conversation_count} conversations")
            return self.generate_prompt_improvements()
        
        return None
    
    # ========================================================================
    # PERSISTENCE
    # ========================================================================
    
    def _load_persisted(self):
        """Load existing patterns and FAQs from DB"""
        if not self.db:
            return
        try:
            patterns = self.db("SELECT * FROM learned_patterns ORDER BY confidence DESC", fetchall=True)
            if patterns:
                for row in patterns:
                    try:
                        triggers = json.loads(row.get("trigger_words", "[]"))
                    except:
                        triggers = []
                    self.patterns[row["pattern_hash"]] = LearnedPattern(
                        trigger_words=triggers,
                        response_template=row.get("response_template", ""),
                        outcome=row.get("outcome", ""),
                        success_count=row.get("success_count", 0),
                        total_uses=row.get("total_uses", 0),
                        confidence=row.get("confidence", 0.0)
                    )
                logger.info(f"Loaded {len(patterns)} learned patterns from DB")
            
            faqs = self.db("SELECT * FROM auto_faq WHERE is_active=1", fetchall=True)
            if faqs:
                for row in faqs:
                    key = self._hash_pattern("faq", [row.get("question", "")[:50]])
                    self.auto_faqs[key] = {
                        "question": row.get("question", ""),
                        "answer": row.get("answer", ""),
                        "category": row.get("category", "general"),
                        "source_count": row.get("source_count", 1),
                        "confidence": row.get("confidence", 0.5)
                    }
                logger.info(f"Loaded {len(faqs)} auto FAQs from DB")
        except Exception as e:
            logger.error(f"Load persisted error: {e}")
    
    def get_improvement_report(self) -> str:
        """Generate a human-readable improvement report"""
        metrics = self.generate_prompt_improvements()
        gaps = self.detect_knowledge_gaps()
        
        lines = ["📊 *Self-Improvement Report*\n"]
        
        if metrics.get("ready"):
            m = metrics["metrics"]
            lines.append(f"📈 Resolution Rate: {m['resolution_rate']}%")
            lines.append(f"💰 Sale Rate: {m['sale_rate']}%")
            lines.append(f"💬 Conversations: {m['total_conversations']}")
        
        if gaps:
            lines.append("\n⚠️ *Knowledge Gaps:*")
            for g in gaps[:3]:
                lines.append(f"  • {g['area']}: {g['failure_rate']}% failure ({g['total_attempts']} attempts)")
        
        if metrics.get("suggested_actions"):
            lines.append("\n🔧 *Suggested Actions:*")
            for a in metrics["suggested_actions"][:3]:
                lines.append(f"  • {a}")
        
        top_patterns = self.get_best_patterns(limit=3)
        if top_patterns:
            lines.append("\n✅ *Top Patterns:*")
            for p in top_patterns:
                lines.append(f"  • [{p['success_rate']}%] Triggers: {', '.join(p['triggers'][:3])}")
        
        return "\n".join(lines)
    
    def inject_learned_context(self, system_prompt: str) -> str:
        """Augment system prompt with learned insights"""
        improvements = []
        
        # Add top patterns
        top = self.get_best_patterns(limit=3)
        if top:
            pattern_text = "\n".join(
                f"- When user says [{', '.join(p['triggers'][:3])}]: respond like '{p['template'][:100]}...'"
                for p in top
            )
            improvements.append(f"PROVEN RESPONSE PATTERNS (learned from successful interactions):\n{pattern_text}")
        
        # Add common gaps
        gaps = self.detect_knowledge_gaps()
        if gaps:
            gap_text = "\n".join(f"- Be extra careful with '{g['area']}' queries ({g['failure_rate']}% failure rate)"
                                for g in gaps[:2])
            improvements.append(f"AREAS NEEDING IMPROVEMENT:\n{gap_text}")
        
        if improvements:
            return system_prompt + "\n\n[LEARNED IMPROVEMENTS]\n" + "\n\n".join(improvements)
        
        return system_prompt


# ============================================================================
# SINGLETON
# ============================================================================

_improvement_engine: Optional[SelfImprovementEngine] = None

def get_improvement_engine(db_query_fn=None) -> SelfImprovementEngine:
    global _improvement_engine
    if _improvement_engine is None:
        _improvement_engine = SelfImprovementEngine(db_query_fn=db_query_fn)
    elif db_query_fn and not _improvement_engine.db:
        _improvement_engine.db = db_query_fn
        _improvement_engine._init_db()
        _improvement_engine._load_persisted()
    return _improvement_engine
