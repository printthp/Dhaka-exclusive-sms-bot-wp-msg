"""Gunicorn config for Dhaka Exclusive Bot.

Hooks into the worker lifecycle to start the Telegram bot polling
on the first worker boot (only once across all workers).
"""
import os
import sys
import logging

# Ensure project root is importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger("gunicorn.error")

_started = False


def on_starting(server):
    """Called once in the master process before workers spawn."""
    logger.info("Dhaka Exclusive gunicorn master starting...")


def post_worker_init(worker):
    """Called just after a worker has initialized.

    Telegram polling is started only in the first worker to avoid
    multiple bot instances fighting over the same Telegram updates.
    """
    global _started
    if _started:
        return
    _started = True
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
    """Stop Telegram polling on worker exit."""
    try:
        from telegram_connector import _telegram_bot
        if _telegram_bot is not None:
            _telegram_bot.stop_polling()
    except Exception:
        pass
