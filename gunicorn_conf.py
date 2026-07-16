"""Gunicorn config for Dhaka Exclusive Bot.

Uses gthread worker class so the Telegram polling background thread
can run concurrently with HTTP request handling in the same worker.
"""
import os
import sys
import fcntl
import tempfile
import logging
import atexit

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger("gunicorn.error")

LOCK_PATH = os.path.join(tempfile.gettempdir(), "dhaka_bot_telegram.lock")
_lock_fd = None

# Worker settings
worker_class = "gthread"
threads = 2  # Allow 2 threads per worker: 1 for HTTP, 1 for polling
workers = 1  # Just 1 worker since polling needs to be in the same process
bind = "0.0.0.0:5000"
timeout = 120
loglevel = "info"


def _acquire_lock():
    """Acquire an exclusive file lock. Non-blocking — return None if held."""
    global _lock_fd
    try:
        _lock_fd = open(LOCK_PATH, "w")
        fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fd.write(str(os.getpid()))
        _lock_fd.flush()
        return True
    except (BlockingIOError, OSError):
        if _lock_fd is not None:
            _lock_fd.close()
            _lock_fd = None
        return False


def _release_lock():
    global _lock_fd
    if _lock_fd is not None:
        try:
            fcntl.flock(_lock_fd.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        _lock_fd.close()
        _lock_fd = None
    atexit.unregister(_release_lock)


def on_starting(server):
    """Called once in the master process before workers spawn."""
    logger.info("Dhaka Exclusive gunicorn master starting...")


def post_worker_init(worker):
    """Called just after a worker has initialized.

    Uses a file lock to ensure only ONE worker across all gunicorn
    worker processes starts Telegram long-polling.
    """
    if not _acquire_lock():
        logger.info("Telegram polling already owned by another worker — skipping in worker %s", worker.pid)
        return

    atexit.register(_release_lock)
    logger.info("This worker (pid %s) acquired the Telegram lock — starting polling", worker.pid)

    try:
        from app import _start_telegram
        started = _start_telegram()
        if started:
            logger.info("✅ Telegram bot polling started in worker %s", worker.pid)
        else:
            logger.warning("Telegram bot not started (check TELEGRAM_BOT_TOKEN)")
    except Exception as e:
        logger.error("Failed to start Telegram bot: %s", e)


def worker_exit(server, worker):
    """Release the lock on worker exit so another worker can take over."""
    _release_lock()
    try:
        from telegram_connector import _telegram_bot
        if _telegram_bot is not None:
            _telegram_bot.stop_polling()
    except Exception:
        pass
