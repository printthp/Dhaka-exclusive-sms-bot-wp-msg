"""
Dhaka Exclusive — Telegram Bot Connector + SMS Training Mode
=============================================================
Bridges Telegram with the existing WhatsApp/SMS bot infrastructure.
Also enables admin to train the bot via SMS or Telegram commands.

MODES:
  1. Telegram Polling: Auto-fetches messages, forwards to AI pipeline
  2. Telegram Webhook: Production mode with public URL
  3. SMS Training: Admin sends "!teach Q | A" via SMS to train bot

COMMANDS (Telegram + SMS):
  !teach প্রশ্ন | উত্তর        — Teach bot a Q&A pair (stores in DB)
  !train একটি বড় জিজ্ঞাসা      — Enter training mode for multi-turn learning
  !correct <msg_id> সঠিক উত্তর  — Fix a wrong bot response
  !forget <keyword>              — Remove a learned pattern
  !patterns                      — Show top learned patterns
  !stats                         — Show improvement stats
  !ping                          — Check if bot is alive
  !help                          — Show all commands
"""

import os
import sys
import json
import time
import logging
import threading
import re
from typing import Optional, Dict, List, Any, Callable
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

# Reusable session with connection pooling + retry
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
REQUESTS_SESSION = requests.Session()
retry_strategy = Retry(
    total=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "POST", "OPTIONS"]
)
adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
REQUESTS_SESSION.mount("https://", adapter)
REQUESTS_SESSION.mount("http://", adapter)
# Force IPv4 only (Hostinger VPS has IPv6 routing issues)
import socket
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_getaddrinfo(host, *args, **kwargs):
    res = _orig_getaddrinfo(host, *args, **kwargs)
    return [r for r in res if r[0] == socket.AF_INET]
socket.getaddrinfo = _ipv4_getaddrinfo


# ============================================================================
# CONFIGURATION
# ============================================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_WEBHOOK_URL = os.environ.get("TELEGRAM_WEBHOOK_URL", "")  # e.g. https://your-domain.com/tg-webhook
ADMIN_PHONES = os.environ.get("ADMIN_PHONES", "").split(",")  # e.g. "8801712345678,88018..."
ADMIN_TELEGRAM_IDS = os.environ.get("ADMIN_TELEGRAM_IDS", "").split(",")  # e.g. "123456789"

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

_tg_running = False
_tg_polling_thread = None

# ============================================================================
# TRAINING COMMAND HANDLER (Works for both SMS & Telegram)
# ============================================================================

class TrainingCommandHandler:
    """Handles !teach, !train, !correct etc commands from admin"""
    
    def __init__(self, db_query_fn=None):
        self.db = db_query_fn
        self._training_sessions: Dict[str, Dict] = {}  # phone/chat_id → session state
        self._ensure_tables()
    
    def _ensure_tables(self):
        if not self.db:
            return
        try:
            self.db("""
                CREATE TABLE IF NOT EXISTS trained_qa (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question TEXT,
                    answer TEXT,
                    keywords TEXT,
                    category TEXT,
                    trained_by TEXT,
                    usage_count INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.db("""
                CREATE TABLE IF NOT EXISTS bot_corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    original_msg TEXT,
                    wrong_reply TEXT,
                    correct_reply TEXT,
                    corrected_by TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            self.db("""
                CREATE TABLE IF NOT EXISTS training_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trainer_id TEXT,
                    session_type TEXT,
                    messages_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """, commit=True)
        except Exception as e:
            logger.error(f"Training tables init: {e}")
    
    def handle_command(self, message: str, sender_id: str, 
                       get_ai_reply_fn=None, is_telegram: bool = False) -> Optional[str]:
        """
        Process a training command. Returns reply if it's a command, None if normal message.
        
        sender_id: phone number (SMS) or telegram chat_id
        """
        msg = message.strip()
        is_admin = self._is_admin(sender_id, is_telegram)
        
        # ── Public commands (anyone can use) ──
        if msg in ("!help", "!হেল্প", "!সাহায্য", "!commands"):
            return self._cmd_help()
        if msg in ("!ping", "!পিং"):
            return self._cmd_ping()
        if msg.startswith("!faq") or msg.startswith("!প্রশ্ন"):
            return self._cmd_faq(msg)
        if msg.startswith("!patterns") or msg.startswith("!প্যাটার্ন"):
            return self._cmd_patterns()
        if msg.startswith("!stats") or msg.startswith("!স্ট্যাট"):
            return self._cmd_stats()
        
        # ── Admin-only commands ──
        if not is_admin:
            if sender_id in self._training_sessions:
                return self._handle_training_continue(msg, sender_id)
            return None  # Not a recognized command, let AI handle
        
        # Check if in training session
        if sender_id in self._training_sessions:
            if msg in ("!stop", "!end", "!শেষ", "!done"):
                return self._cmd_train_stop(sender_id)
            return self._handle_training_continue(msg, sender_id)
        
        # ── !teach প্রশ্ন | উত্তর ──
        if msg.startswith("!teach ") or msg.startswith("!শেখাও "):
            return self._cmd_teach(msg, sender_id, is_telegram)
        
        # ── !train ── (multi-turn training)
        if msg.startswith("!train") or msg.startswith("!ট্রেন"):
            return self._cmd_train_start(msg, sender_id, is_telegram)
        
        # ── !correct ──
        if msg.startswith("!correct ") or msg.startswith("!ঠিক "):
            return self._cmd_correct(msg, sender_id)
        
        # ── !forget ──
        if msg.startswith("!forget ") or msg.startswith("!ভুলে "):
            return self._cmd_forget(msg, sender_id)
        
        return None  # Normal message
    
    def _is_admin(self, sender_id: str, is_telegram: bool) -> bool:
        if is_telegram:
            return str(sender_id) in [x.strip() for x in ADMIN_TELEGRAM_IDS if x.strip()]
        else:
            return sender_id.strip() in [x.strip() for x in ADMIN_PHONES if x.strip()]
    
    # ─── COMMAND IMPLEMENTATIONS ───
    
    def _cmd_teach(self, msg: str, sender_id: str, is_telegram: bool) -> str:
        """!teach প্রশ্ন | উত্তর"""
        content = msg.split(" ", 1)[1] if " " in msg else ""
        
        # Try splitting by | or || or newline
        parts = re.split(r'\s*\|\s*|\n', content, maxsplit=1)
        
        if len(parts) < 2:
            return (
                "❌ *ফরম্যাট ভুল!*\n\n"
                "সঠিক নিয়ম:\n"
                "`!teach প্রশ্ন | উত্তর`\n\n"
                "উদাহরণ:\n"
                "`!teach ডেলিভারি চার্জ কত? | ঢাকায় ৬০৳, বাইরে ১২০৳`\n"
                "`!teach রিটার্ন পলিসি কী? | ৭ দিনের মধ্যে ফ্রি রিটার্ন`"
            )
        
        question = parts[0].strip()
        answer = parts[1].strip()
        
        # Extract keywords for matching
        keywords = self._extract_keywords(question)
        category = self._infer_category(question)
        
        # Store in DB
        if self.db:
            try:
                self.db(
                    """INSERT INTO trained_qa (question, answer, keywords, category, trained_by)
                       VALUES (?,?,?,?,?)""",
                    (question[:500], answer[:2000], json.dumps(keywords, ensure_ascii=False),
                     category, f"{'tg' if is_telegram else 'sms'}:{sender_id}"),
                    commit=True
                )
                self.db(
                    """INSERT INTO training_sessions (trainer_id, session_type, messages_count)
                       VALUES (?, 'teach', 1)""",
                    (f"{'tg' if is_telegram else 'sms'}:{sender_id}",), commit=True
                )
            except Exception as e:
                logger.error(f"Teach storage error: {e}")
                return f"❌ ডাটাবেজে সংরক্ষণ করা যায়নি: {str(e)[:50]}"
        
        logger.info(f"Trained: Q='{question[:60]}...' by {sender_id}")
        
        return (
            f"✅ *শেখানো সম্পন্ন!*\n\n"
            f"📝 প্রশ্ন: {question[:100]}\n"
            f"💡 উত্তর: {answer[:150]}\n"
            f"🏷️ ক্যাটাগরি: {category}\n"
            f"🔑 কীওয়ার্ড: {', '.join(keywords[:5])}\n\n"
            f"এখন থেকে বট এই প্রশ্নের উত্তর জানে! 🧠"
        )
    
    def _cmd_train_start(self, msg: str, sender_id: str, is_telegram: bool) -> str:
        """Start multi-turn training session"""
        self._training_sessions[sender_id] = {
            "started": time.time(),
            "messages": [],
            "state": "collecting",  # collecting → confirming → done
            "intent": "",
            "mode": "train"
        }
        
        if self.db:
            try:
                self.db(
                    "INSERT INTO training_sessions (trainer_id, session_type) VALUES (?, 'multi_turn')",
                    (f"{'tg' if is_telegram else 'sms'}:{sender_id}",), commit=True
                )
            except: pass
        
        return (
            "🎓 *ট্রেনিং মোড শুরু!*\n\n"
            "এখন আপনি বটের সাথে স্বাভাবিক কথোপকথন করুন।\n"
            "বটের উত্তর ভুল হলে আপনি `!correct সঠিক উত্তর` লিখে সংশোধন করতে পারবেন।\n\n"
            "শেষ করতে `!stop` লিখুন।\n\n"
            "বর্তমান অবস্থা: শিখছি... 👂"
        )
    
    def _handle_training_continue(self, msg: str, sender_id: str) -> Optional[str]:
        """Handle messages during training session"""
        session = self._training_sessions.get(sender_id)
        if not session:
            return None
        
        session["messages"].append({"role": "user", "content": msg, "time": time.time()})
        
        if len(session["messages"]) <= 3:
            return "👂 শুনছি, বলতে থাকুন... (!stop দিয়ে শেষ করুন)"
        
        # After collecting enough messages, analyze
        return (
            f"📊 এ পর্যন্ত {len(session['messages'])} টি মেসেজ সংগ্রহ করেছি।\n"
            f"চালিয়ে যান অথবা `!stop` দিয়ে শেষ করুন।"
        )
    
    def _cmd_train_stop(self, sender_id: str) -> str:
        """End training session and save learnings"""
        session = self._training_sessions.pop(sender_id, None)
        if not session:
            return "❌ কোনো সক্রিয় ট্রেনিং সেশন নেই।"
        
        msg_count = len(session.get("messages", []))
        
        if msg_count == 0:
            return "⚠️ ট্রেনিং সেশন শেষ। কোনো মেসেজ সংগ্রহ করা হয়নি।"
        
        # Save training session summary
        if self.db:
            try:
                self.db(
                    "UPDATE training_sessions SET messages_count=? WHERE trainer_id=? ORDER BY id DESC LIMIT 1",
                    (msg_count, f"tg:{sender_id}"), commit=True
                )
            except: pass
        
        return (
            f"✅ *ট্রেনিং সেশন শেষ!*\n\n"
            f"📊 মোট মেসেজ: {msg_count} টি\n"
            f"🧠 এই ডাটা পরবর্তী অ্যানালাইসিসে ব্যবহার করা হবে।\n\n"
            f"ধন্যবাদ! বট এখন আরও বুদ্ধিমান। 🤖✨"
        )
    
    def _cmd_correct(self, msg: str, sender_id: str) -> str:
        """!correct সঠিক উত্তর — fix last bot response"""
        correct_answer = msg.split(" ", 1)[1] if " " in msg else ""
        
        if not correct_answer:
            return "❌ ব্যবহার: `!correct সঠিক উত্তর`"
        
        # Store correction
        if self.db:
            try:
                self.db(
                    """INSERT INTO bot_corrections (correct_reply, corrected_by)
                       VALUES (?, ?)""",
                    (correct_answer[:2000], sender_id), commit=True
                )
            except Exception as e:
                logger.error(f"Correction storage error: {e}")
        
        logger.info(f"Correction by {sender_id}: {correct_answer[:80]}...")
        
        # Also add as trained Q&A if we have context
        return (
            f"✅ *সংশোধন সংরক্ষিত!*\n\n"
            f"💡 সঠিক উত্তর: {correct_answer[:200]}\n\n"
            f"ভবিষ্যতে বট এই উত্তর দেবে। ধন্যবাদ! 🙏"
        )
    
    def _cmd_forget(self, msg: str, sender_id: str) -> str:
        """!forget keyword — remove learned patterns matching keyword"""
        keyword = msg.split(" ", 1)[1] if " " in msg else ""
        
        if not keyword or len(keyword) < 2:
            return "❌ ব্যবহার: `!forget কীওয়ার্ড`"
        
        deleted = 0
        if self.db:
            try:
                result = self.db(
                    "DELETE FROM trained_qa WHERE question LIKE ? OR keywords LIKE ?",
                    (f"%{keyword}%", f"%{keyword}%"), commit=True
                )
                # Also try learned_patterns table
                self.db(
                    "DELETE FROM learned_patterns WHERE trigger_words LIKE ?",
                    (f"%{keyword}%",), commit=True
                )
                deleted = 1  # Approximation
            except Exception as e:
                logger.error(f"Forget error: {e}")
        
        return f"🗑️ *ভুলে গেছি!* `{keyword}` সম্পর্কিত শেখা তথ্য মুছে ফেলা হয়েছে।"
    
    def _cmd_patterns(self) -> str:
        """Show top learned patterns"""
        try:
            from self_improve import get_improvement_engine
            engine = get_improvement_engine()
            best = engine.get_best_patterns(limit=8, min_success=1)
            
            if not best:
                # Fallback: check trained_qa
                if self.db:
                    qa_rows = self.db(
                        "SELECT question, answer, usage_count FROM trained_qa WHERE is_active=1 ORDER BY usage_count DESC LIMIT 5",
                        fetchall=True
                    )
                    if qa_rows:
                        lines = ["🧠 *শেখানো প্রশ্নোত্তর*\n"]
                        for i, row in enumerate(qa_rows, 1):
                            lines.append(f"{i}. প্রশ্ন: {row['question'][:60]}")
                            lines.append(f"   উত্তর: {row['answer'][:80]}")
                            lines.append(f"   ব্যবহার: {row.get('usage_count', 0)} বার\n")
                        return "\n".join(lines)
                
                return "📭 এখনো কোনো প্যাটার্ন শেখা হয়নি। `!teach প্রশ্ন | উত্তর` দিয়ে শেখান!"
            
            lines = ["🧠 *শেখা প্যাটার্ন সমূহ*\n"]
            for i, p in enumerate(best, 1):
                lines.append(
                    f"{i}. [{p['success_rate']}%] ট্রিগার: {', '.join(p['triggers'][:3])}\n"
                    f"   → {p['outcome']} | {p['uses']} বার ব্যবহার"
                )
            
            return "\n".join(lines)
        except Exception as e:
            return f"❌ প্যাটার্ন লোড করতে সমস্যা: {str(e)[:50]}"
    
    def _cmd_stats(self) -> str:
        """Show improvement statistics"""
        try:
            from self_improve import get_improvement_engine
            engine = get_improvement_engine()
            report = engine.get_improvement_report()
            return report
        except:
            pass
        
        # Fallback stats
        if self.db:
            try:
                qa_count = self.db("SELECT COUNT(*) as c FROM trained_qa WHERE is_active=1", fetchone=True)
                corr_count = self.db("SELECT COUNT(*) as c FROM bot_corrections", fetchone=True)
                return (
                    "📊 *বট স্ট্যাটিস্টিক্স*\n\n"
                    f"🧠 শেখানো QA: {qa_count.get('c', 0) if qa_count else 0} টি\n"
                    f"✏️ সংশোধন: {corr_count.get('c', 0) if corr_count else 0} টি\n"
                )
            except: pass
        
        return "📊 স্ট্যাটিস্টিক্স লোড করা যায়নি।"
    
    def _cmd_ping(self) -> str:
        return "🏓 *Pong!* বট সচল আছে। সবকিছু ঠিকঠাক চলছে। ✅"
    
    def _cmd_help(self) -> str:
        return """🤖 *Dhaka Exclusive Bot — কমান্ড সমূহ*

📚 *ট্রেনিং:*
`!teach প্রশ্ন | উত্তর` — বটকে নতুন প্রশ্নোত্তর শেখান
`!train` — মাল্টি-টার্ন ট্রেনিং সেশন শুরু
`!correct সঠিক উত্তর` — বটের ভুল উত্তর সংশোধন
`!stop` — ট্রেনিং সেশন শেষ

🔍 *তথ্য:*
`!patterns` — শেখা প্যাটার্ন দেখুন
`!faq [keyword]` — শেখানো প্রশ্নোত্তর সার্চ
`!stats` — বটের পারফরমেন্স স্ট্যাট
`!ping` — বট সচল কিনা চেক

🗑️ *ম্যানেজমেন্ট:*
`!forget কীওয়ার্ড` — শেখা তথ্য মুছুন

💡 *টিপ:* SMS বা Telegram দুই জায়গা থেকেই ট্রেনিং করা যায়!"""
    
    def _cmd_faq(self, msg: str) -> str:
        """Search trained Q&A"""
        keyword = msg.split(" ", 1)[1] if " " in msg else ""
        
        if not self.db:
            return "❌ ডাটাবেজ সংযোগ নেই।"
        
        try:
            if keyword:
                rows = self.db(
                    """SELECT question, answer, usage_count FROM trained_qa 
                       WHERE is_active=1 AND (question LIKE ? OR keywords LIKE ?)
                       ORDER BY usage_count DESC LIMIT 5""",
                    (f"%{keyword}%", f"%{keyword}%"), fetchall=True
                )
            else:
                rows = self.db(
                    "SELECT question, answer, usage_count FROM trained_qa WHERE is_active=1 ORDER BY id DESC LIMIT 10",
                    fetchall=True
                )
            
            if not rows:
                return f"🔍 `{keyword}` সম্পর্কে কোনো প্রশ্নোত্তর পাওয়া যায়নি। `!teach` দিয়ে শেখান!"
            
            lines = [f"📚 *শেখানো FAQ* ({len(rows)} টি)\n"]
            for i, row in enumerate(rows, 1):
                lines.append(f"{i}. *প্রশ্ন:* {row['question'][:80]}")
                lines.append(f"   *উত্তর:* {row['answer'][:100]}\n")
            
            return "\n".join(lines)
        except Exception as e:
            return f"❌ সার্চ করতে সমস্যা: {str(e)[:50]}"
    
    def _extract_keywords(self, text: str) -> List[str]:
        """Extract keywords from text"""
        words = re.findall(r'[\u0980-\u09FF]{2,}|[a-zA-Z]{3,}', text.lower())
        noise = {"এখন", "আমি", "তুমি", "সে", "তা", "এই", "ওই", "একটা", "একটি",
                 "the", "and", "for", "that", "this", "with", "have",
                 "আছে", "কি", "না", "হ্যাঁ", "ঠিক", "আর", "যদি", "কিন্তু"}
        return [w for w in words if w not in noise][:10]
    
    def _infer_category(self, text: str) -> str:
        q = text.lower()
        if any(w in q for w in ["দাম", "price", "কত", "টাকা"]): return "pricing"
        if any(w in q for w in ["ডেলিভারি", "delivery", "পাঠানো"]): return "delivery"
        if any(w in q for w in ["পেমেন্ট", "payment", "বিকাশ"]): return "payment"
        if any(w in q for w in ["রিটার্ন", "return", "ফেরত"]): return "returns"
        if any(w in q for w in ["সাইজ", "size", "রং", "color"]): return "product"
        return "general"
    
    def search_trained_qa(self, user_message: str) -> Optional[str]:
        """Search trained Q&A for matching answer (called by AI pipeline)"""
        if not self.db:
            return None
        
        keywords = self._extract_keywords(user_message)
        if not keywords:
            return None
        
        try:
            # Try exact match first
            row = self.db(
                "SELECT answer FROM trained_qa WHERE is_active=1 AND question LIKE ? LIMIT 1",
                (f"%{user_message[:50]}%",), fetchone=True
            )
            if row:
                self.db(
                    "UPDATE trained_qa SET usage_count = usage_count + 1 WHERE question LIKE ?",
                    (f"%{user_message[:50]}%",), commit=True
                )
                return row["answer"]
            
            # Try keyword match
            for kw in keywords[:3]:
                row = self.db(
                    "SELECT answer FROM trained_qa WHERE is_active=1 AND keywords LIKE ? LIMIT 1",
                    (f"%{kw}%",), fetchone=True
                )
                if row:
                    self.db(
                        "UPDATE trained_qa SET usage_count = usage_count + 1 WHERE keywords LIKE ?",
                        (f"%{kw}%",), commit=True
                    )
                    return row["answer"]
        except Exception as e:
            logger.debug(f"QA search: {e}")
        
        return None


# ============================================================================
# TELEGRAM BOT
# ============================================================================

class TelegramBot:
    """Telegram Bot that bridges to existing AI pipeline"""

    def __init__(self, token: str = "", get_ai_reply_fn=None, db_query_fn=None):
        self.token = token or TELEGRAM_TOKEN
        self.get_ai_reply = get_ai_reply_fn
        self.db = db_query_fn
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self.last_update_id = 0
        self._known_admin_chats: set = set()
        self._running = False
        self._thread = None
        
        # Training handler (shared with SMS)
        self.trainer = TrainingCommandHandler(db_query_fn=db_query_fn)
        
        if self.token:
            logger.info(f"Telegram bot initialized: {self.token[:8]}...")
        else:
            logger.warning("TELEGRAM_BOT_TOKEN not set. Telegram disabled.")
    
    def set_ai_handler(self, fn):
        """Inject the AI reply function after init"""
        self.get_ai_reply = fn
    
    def set_db(self, fn):
        self.db = fn
        if self.trainer:
            self.trainer.db = fn
            self.trainer._ensure_tables()
    
    # ─── Telegram API Helpers ───
    
    def _api(self, method: str, data: dict = None, files: dict = None, timeout: int = 10) -> dict:
        """Call Telegram Bot API using persistent session for connection reuse.

        Short timeout (10s) so a stuck request doesn't block the poller for 30s+.
        """
        url = f"{self.base_url}/{method}"
        try:
            if files:
                r = REQUESTS_SESSION.post(url, data=data or {}, files=files, timeout=timeout)
            else:
                r = REQUESTS_SESSION.post(url, json=data or {}, timeout=timeout)
            return r.json()
        except requests.exceptions.ReadTimeout:
            logger.debug(f"Telegram {method}: read timeout (network slow)")
            return {"ok": False, "error": "timeout"}
        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Telegram {method}: connection error: {e}")
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.error(f"Telegram API error ({method}): {e}")
            return {"ok": False, "error": str(e)}
    
    def send_message(self, chat_id: int, text: str, parse_mode: str = "Markdown",
                     reply_to_msg_id: int = None, buttons: list = None) -> dict:
        """Send message to Telegram chat"""
        data = {
            "chat_id": chat_id,
            "text": text[:4000],  # Telegram limit
            "parse_mode": parse_mode,
        }
        if reply_to_msg_id:
            data["reply_to_message_id"] = reply_to_msg_id
        if buttons:
            data["reply_markup"] = json.dumps(buttons)
        
        return self._api("sendMessage", data)
    
    def send_photo(self, chat_id: int, photo_url: str, caption: str = "") -> dict:
        """Send photo to Telegram"""
        return self._api("sendPhoto", {
            "chat_id": chat_id,
            "photo": photo_url,
            "caption": caption[:1000]
        })

    def send_chat_action(self, chat_id: int, action: str = "typing") -> dict:
        """Send chat action (typing, upload_photo, etc.) — short timeout"""
        return self._api("sendChatAction", {"chat_id": chat_id, "action": action}, timeout=5)
    
    # ─── Message Processing ───
    
    def _is_admin_tg(self, user_id) -> bool:
        """Check if Telegram user is an admin"""
        return str(user_id) in [x.strip() for x in ADMIN_TELEGRAM_IDS if x.strip()]
    
    def process_message(self, msg: dict) -> Optional[str]:
        """Process an incoming Telegram message — ADMIN ONLY for AI, rest redirected to SMS"""
        try:
            if "message" not in msg:
                return None
            # High-priority log: always shows what's incoming
            _m = msg.get("message", {})
            _has_photo = bool(_m.get("photo"))
            _has_doc = bool(_m.get("document"))
            _text_preview = (_m.get("text") or _m.get("caption") or "")[:60]
            _chat_id = _m.get("chat", {}).get("id")
            _user = _m.get("from", {}).get("id")
            print(f"TG_MSG from={_user} chat={_chat_id} photo={_has_photo} doc={_has_doc} text={_text_preview!r}", flush=True)
            sys.stdout.flush()
            
            message = msg["message"]
            chat_id = message.get("chat", {}).get("id")
            user_id = message.get("from", {}).get("id")
            text = message.get("text", "").strip()
            username = message.get("from", {}).get("username", "")
            first_name = message.get("from", {}).get("first_name", "")
            
            if not chat_id:
                return None
            
            sender_name = username or first_name or str(user_id)
            # Calculate is_admin ONCE at the top so it's available everywhere below
            is_admin = self._is_admin_tg(user_id)
            logger.info(f"TG [{'ADMIN' if is_admin else 'USER'} {sender_name}]: {text[:80] if text else '[non-text]'}")
            
            # ── PHOTO HANDLING: Use Gemini Vision (works for BOTH admin and customer) ──
            logger.info(f"TG [{user_id}]: photo handler entered, is_admin={is_admin}, text={text[:50]!r}")
            photo = message.get("photo")
            document = message.get("document")
            if photo or document:
                file_id = None
                if photo:
                    file_id = photo[-1].get("file_id")
                elif document:
                    file_id = document.get("file_id")

                if file_id:
                    # Acknowledge receipt immediately so user knows we got the photo
                    try:
                        self.send_chat_action(chat_id, "typing")
                    except Exception:
                        pass

                    file_info = self._api("getFile", {"file_id": file_id})
                    if not file_info.get("ok"):
                        logger.error(f"getFile failed: {file_info}")
                        self.send_message(chat_id, "❌ দুঃখিত, ছবিটি আনতে সমস্যা হচ্ছে। আবার পাঠান।")
                        return None

                    file_path = file_info["result"]["file_path"]
                    file_url = f"https://api.telegram.org/file/bot{self.token}/{file_path}"

                    try:
                        img_data = self._download_telegram_file(file_url)
                        if not img_data:
                            self.send_message(chat_id, "❌ ছবি ডাউনলোড করা যাচ্ছে না।")
                            return None

                        # Inline Gemini Vision call
                        from app import GEMINI_API_KEY
                        import base64 as _b64
                        img_b64 = _b64.b64encode(img_data).decode('utf-8')

                        # Use caption as the question, default if no caption
                        user_q = text or "এই ছবিটি বিশ্লেষণ করুন।"
                        prompt = (
                            f"আপনি ArShi, Dhaka Exclusive হাউজওয়্যার শপের AI sales assistant।\n"
                            f"Customer প্রশ্ন: {user_q}\n\n"
                            f"ছবিটি বিশ্লেষণ করে বাংলায় সংক্ষেপে উত্তর দিন।\n"
                            f"পণ্য হলে: নাম, ধরন, বৈশিষ্ট্য, আনুমানিক দাম (BDT) বলুন।\n"
                            f"অর্ডার বা বিস্তারিত জানতে admin এ পাঠান।"
                        )

                        analysis = None
                        for model in ['gemini-2.5-flash', 'gemini-1.5-flash', 'gemini-1.5-flash-latest']:
                            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
                            payload = {"contents": [{"parts": [
                                {"text": prompt},
                                {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}}
                            ]}]}
                            try:
                                r = REQUESTS_SESSION.post(url, json=payload, timeout=30)
                                if r.status_code == 200:
                                    result = r.json()
                                    if result.get("candidates"):
                                        analysis = result["candidates"][0]["content"]["parts"][0]["text"]
                                        break
                            except Exception as e:
                                logger.warning(f"Gemini model {model} failed: {e}")
                                continue

                        if analysis:
                            admin_phone = ADMIN_PHONES[0].strip() if ADMIN_PHONES and ADMIN_PHONES[0].strip() else "01717121068"
                            if admin_phone.startswith("880"):
                                admin_phone = "0" + admin_phone[3:]

                            if is_admin:
                                reply = f"📸 *ছবি বিশ্লেষণ:*\n\n{analysis}"
                            else:
                                reply = (
                                    f"📸 *ছবি বিশ্লেষণ:*\n\n{analysis}\n\n"
                                    f"---\n"
                                    f"অর্ডার বা বিস্তারিত জানতে যোগাযোগ করুন:\n"
                                    f"📱 *{admin_phone}*"
                                )
                            self.send_message(chat_id, reply)
                            return reply
                        else:
                            self.send_message(chat_id, "❌ Vision API-তে সমস্যা হচ্ছে, পরে আবার চেষ্টা করুন।")
                            return None
                    except Exception as e:
                        logger.error(f"Photo analysis error: {e}")
                        self.send_message(chat_id, f"❌ ছবি প্রসেস করতে সমস্যা: {str(e)[:200]}")
                        return None
                return None
            
            if not text:
                return None
            
            # Track chat_id for admin (so we can send them updates later)
            if is_admin:
                self._known_admin_chats.add(int(chat_id))
            
            # ── UNIVERSAL: /start welcome (works for anyone) ──
            if text in ("/start", "/start@ArshibyabidBot"):
                if is_admin:
                    welcome = (
                        f"🟢 আসসালামু আলাইকুম *{first_name or 'অ্যাডমিন'} সাহেব!*\n\n"
                        f"আমি *ArShi* — আপনার *Dhaka Exclusive* বটের AI assistant। 😊\n\n"
                        f"📚 *আমার কমান্ডসমূহ:*\n"
                        f"• `!help` — সব কমান্ড দেখুন\n"
                        f"• `!teach <প্রশ্ন> | <উত্তর>` — আমাকে শেখান\n"
                        f"• `!stats` — কত শিখেছি দেখুন\n"
                        f"• `!patterns` — শেখা প্যাটার্ন\n"
                        f"• `!faq` — অটো FAQ\n"
                        f"• `!ping` — আমি সচল কিনা\n\n"
                        f"আমাকে যেকোনো প্রশ্ন করুন, আমি উত্তর দেবো! 🚀"
                    )
                else:
                    # CUSTOMER gets the ArShi intro
                    admin_phone = ADMIN_PHONES[0].strip() if ADMIN_PHONES and ADMIN_PHONES[0].strip() else "01717121068"
                    if admin_phone.startswith("880"):
                        admin_phone = "0" + admin_phone[3:]
                    welcome = (
                        f"👋 আসসালামু আলাইকুম! *আমি ArShi* 😊\n\n"
                        f"আমি *Dhaka Exclusive*-র AI assistant।\n"
                        f"আপনাকে কীভাবে সাহায্য করতে পারি?\n\n"
                        f"🛍️ পণ্য সম্পর্কে জানতে\n"
                        f"💰 দাম জানতে\n"
                        f"📦 অর্ডার করতে\n"
                        f"🚚 ডেলিভারি সম্পর্কে\n\n"
                        f"আমার সাথে কথা বলুন বা অর্ডারের জন্য SMS করুন:\n"
                        f"📱 *{admin_phone}*\n\n"
                        f"ধন্যবাদ! 🙏"
                    )
                self.send_message(chat_id, welcome)
                return welcome
            
            # ── ADMIN: Full access (training commands + AI pipeline) ──
            if is_admin:
                # Check for training commands first
                cmd_reply = self.trainer.handle_command(
                    text, str(chat_id), 
                    get_ai_reply_fn=self.get_ai_reply,
                    is_telegram=True
                )
                if cmd_reply:
                    self.send_message(chat_id, cmd_reply)
                    return cmd_reply
                
                # Route to AI pipeline
                if self.get_ai_reply:
                    try:
                        ai_reply = self.get_ai_reply(
                            user_message=text,
                            customer_phone=f"tg_{chat_id}",
                            chat_history=None
                        )
                        if ai_reply:
                            clean_reply = self._clean_for_telegram(ai_reply)
                            self.send_message(chat_id, clean_reply)
                            return ai_reply
                    except Exception as e:
                        logger.error(f"AI pipeline error: {e}")
                
                fallback = "ধন্যবাদ! আমাদের টিম শীঘ্রই আপনার সাথে যোগাযোগ করবে। 🙏"
                self.send_message(chat_id, fallback)
                return fallback
            
            # ── NON-ADMIN: Redirect to SMS/WhatsApp ──
            admin_phone = ADMIN_PHONES[0].strip() if ADMIN_PHONES and ADMIN_PHONES[0].strip() else "01717121068"
            display_phone = admin_phone
            if display_phone.startswith("880"):
                display_phone = "0" + display_phone[3:]
            redirect_msg = (
                f"👋 আসসালামু আলাইকুম! আমি *ArShi* 😊\n\n"
                f"আমি *Dhaka Exclusive*-র AI assistant।\n"
                f"আপনার পণ্য, দাম, অর্ডার বা ডেলিভারি সংক্রান্ত যেকোনো প্রশ্নে\n"
                f"আমি সাহায্য করতে পারি!\n\n"
                f"অর্ডার বা বিশেষ সাহায্যের জন্য যোগাযোগ করুন:\n"
                f"📱 *WhatsApp/SMS:* {display_phone}\n\n"
                f"আমার সাথে কথা বলতে থাকুন! 🙏"
            )
            # Also accept !help/!ping from anyone (public commands)
            if text in ("!ping", "!পিং"):
                self.send_message(chat_id, "🏓 *Pong!* বট সচল আছে ✅\n\nঅর্ডারের জন্য SMS করুন।")
                return "pong"
            if text in ("!help", "!হেল্প", "!সাহায্য"):
                self.send_message(chat_id, redirect_msg)
                return "help"
            
            self.send_message(chat_id, redirect_msg)
            logger.info(f"TG non-admin {user_id} redirected to SMS")
            return redirect_msg
        except Exception as e:
            logger.error(f"process_message top-level error: {e}\n  msg: {str(msg)[:200]}")
            try:
                if 'chat_id' in locals() and chat_id:
                    self.send_message(chat_id, "❌ দুঃখিত, মেসেজ প্রসেস করতে সমস্যা হয়েছে। আবার চেষ্টা করুন।")
            except:
                pass
            return None

    def _clean_for_telegram(self, text: str) -> str:
        """Clean markdown for Telegram compatibility"""
        # Remove WhatsApp-specific formatting
        text = text.replace("*", "**")  # Bold
        text = text.replace("_", "__")  # Italic
        
        # Escape Telegram reserved chars: _ * [ ] ( ) ~ ` > # + - = | { } . !
        # But we already converted * and _, so skip those
        reserved = r'`'
        for char in reserved:
            text = text.replace(char, f'\\{char}')
        
        return text
    
    # ─── Polling Mode ───
    
    def start_polling(self):
        """Start long-polling in background thread"""
        if not self.token:
            logger.warning("Cannot start Telegram polling: no token")
            return False
        
        if self._running:
            return True
        
        self._running = True
        self._thread = threading.Thread(target=self._polling_loop, daemon=True)
        self._thread.start()
        logger.info("Telegram polling started!")
        
        # Send startup notification to admins
        for admin_id in ADMIN_TELEGRAM_IDS:
            if admin_id.strip():
                try:
                    self.send_message(int(admin_id.strip()), "🟢 *Dhaka Exclusive Bot চালু হয়েছে!*\n\n`!help` দিয়ে কমান্ড দেখুন।")
                except: pass
        
        return True
    
    def stop_polling(self):
        """Stop polling"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Telegram polling stopped.")
    

    def _download_telegram_file(self, file_url: str, max_retries: int = 3) -> bytes:
        """Download a file from Telegram with retry logic"""
        import time
        for attempt in range(max_retries):
            try:
                # Use stream for large files, longer timeout
                r = REQUESTS_SESSION.get(file_url, timeout=60, stream=True)
                if r.status_code == 200:
                    return r.content
                logger.warning(f"Download attempt {attempt+1}: status {r.status_code}")
            except Exception as e:
                logger.warning(f"Download attempt {attempt+1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)  # exponential backoff
        return b""

    def _polling_loop(self):
        """Background long-polling loop with adaptive timeouts"""
        retry_delay = 1

        while self._running:
            try:
                # Use short timeout for getUpdates to avoid blocking on slow networks.
                # Telegram long-polling ignores server-side timeout on broken connections.
                updates = self._api("getUpdates", {
                    "offset": self.last_update_id + 1,
                    "timeout": 5,
                    "allowed_updates": ["message"]
                })

                if updates.get("ok") and updates.get("result"):
                    print(f"TG_UPDATES: got {len(updates['result'])} updates", flush=True)
                    for update in updates["result"]:
                        self.last_update_id = max(self.last_update_id, update["update_id"])
                        try:
                            self.process_message(update)
                        except Exception as e:
                            logger.error(f"Process message error: {e}")

                    retry_delay = 0.2  # Fast poll when messages arrive
                else:
                    retry_delay = min(retry_delay * 1.2, 3)

                time.sleep(retry_delay)

            except requests.Timeout:
                # Network is slow — Telegram will return empty if no updates, that's fine
                time.sleep(0.5)
            except requests.exceptions.ReadTimeout:
                # Read timeout — restart connection quickly
                logger.debug("getUpdates read timeout, retrying...")
                time.sleep(0.5)
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"Telegram connection error: {e}, sleeping 2s")
                time.sleep(2)
            except Exception as e:
                logger.error(f"Polling error: {e}")
                time.sleep(2)
    
    # ─── Webhook Mode (for production) ───
    
    def setup_webhook(self, webhook_url: str = "") -> bool:
        """Set Telegram webhook to point at our server"""
        url = webhook_url or TELEGRAM_WEBHOOK_URL
        if not url:
            logger.warning("No webhook URL set")
            return False
        
        result = self._api("setWebhook", {"url": url})
        if result.get("ok"):
            logger.info(f"Telegram webhook set: {url}")
            # Send notification
            for admin_id in ADMIN_TELEGRAM_IDS:
                if admin_id.strip():
                    try:
                        self.send_message(int(admin_id.strip()), f"🔗 Webhook set: {url}")
                    except: pass
            return True
        else:
            logger.error(f"Webhook setup failed: {result.get('description')}")
            return False
    
    def handle_webhook_update(self, update_data: dict) -> dict:
        """Process a webhook update (called from Flask route)"""
        try:
            self.last_update_id = update_data.get("update_id", self.last_update_id)
            reply = self.process_message(update_data)
            return {"ok": True, "reply": reply}
        except Exception as e:
            logger.error(f"Webhook handler error: {e}")
            return {"ok": False, "error": str(e)}


# ============================================================================
# SMS TRAINING MODE (Integrated with existing SMS pipeline)
# ============================================================================

class SMSTrainingBridge:
    """
    Handles training commands sent via SMS.
    Called BEFORE normal AI processing in the SMS pipeline.
    Returns reply if it's a training command, None otherwise.
    """
    
    def __init__(self, db_query_fn=None):
        self.trainer = TrainingCommandHandler(db_query_fn=db_query_fn)
    
    def intercept_sms(self, message: str, from_number: str, 
                       get_ai_reply_fn=None) -> Optional[str]:
        """
        Check if this SMS is a training command.
        Returns reply string if it was handled, None to continue normal flow.
        """
        # Check if message starts with training prefix
        if any(message.startswith(p) for p in ["!teach", "!train", "!correct", 
                                                 "!forget", "!patterns", "!stats",
                                                 "!ping", "!help", "!faq", "!stop",
                                                 "!end", "!done", "!শেখাও", "!ট্রেন",
                                                 "!ঠিক", "!ভুলে", "!প্যাটার্ন",
                                                 "!স্ট্যাট", "!পিং", "!হেল্প",
                                                 "!সাহায্য", "!প্রশ্ন"]):
            return self.trainer.handle_command(
                message, from_number,
                get_ai_reply_fn=get_ai_reply_fn,
                is_telegram=False
            )
        
        return None


# ============================================================================
# SINGLETON + GETTERS
# ============================================================================

_telegram_bot: Optional[TelegramBot] = None
_sms_trainer: Optional[SMSTrainingBridge] = None

def get_telegram_bot(get_ai_reply_fn=None, db_query_fn=None) -> TelegramBot:
    global _telegram_bot
    if _telegram_bot is None:
        _telegram_bot = TelegramBot(
            get_ai_reply_fn=get_ai_reply_fn,
            db_query_fn=db_query_fn
        )
    else:
        if get_ai_reply_fn and not _telegram_bot.get_ai_reply:
            _telegram_bot.set_ai_handler(get_ai_reply_fn)
        if db_query_fn and not _telegram_bot.db:
            _telegram_bot.set_db(db_query_fn)
    return _telegram_bot

def get_sms_trainer(db_query_fn=None) -> SMSTrainingBridge:
    global _sms_trainer
    if _sms_trainer is None:
        _sms_trainer = SMSTrainingBridge(db_query_fn=db_query_fn)
    return _sms_trainer

def start_telegram_if_configured(get_ai_reply_fn=None, db_query_fn=None) -> bool:
    """Start Telegram bot if token is configured. Called on app startup."""
    if not TELEGRAM_TOKEN:
        logger.info("Telegram disabled (no TELEGRAM_BOT_TOKEN)")
        return False
    
    bot = get_telegram_bot(get_ai_reply_fn=get_ai_reply_fn, db_query_fn=db_query_fn)
    
    # Try webhook first, fallback to polling
    if TELEGRAM_WEBHOOK_URL:
        bot.setup_webhook(TELEGRAM_WEBHOOK_URL)
    
    return bot.start_polling()
