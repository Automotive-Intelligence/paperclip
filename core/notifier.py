"""Notifications for Project Paperclip — logs only."""

import os
import logging
from datetime import datetime

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_hot_lead_loggers: dict = {}


def _get_hot_lead_logger(river: str) -> logging.Logger:
    if river in _hot_lead_loggers:
        return _hot_lead_loggers[river]
    logger = logging.getLogger(f"paperclip.hot_leads.{river}")
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(os.path.join(LOG_DIR, f"{river}_hot_leads.log"))
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(message)s"))
    logger.addHandler(sh)
    _hot_lead_loggers[river] = logger
    return logger


def notify_hot_lead(river: str, contact_name: str, business: str, phone: str, action: str):
    logger = _get_hot_lead_logger(river)
    logger.info(f"HOT LEAD | {contact_name} | {business} | {phone} | {action}")


def notify_error(river: str, error_message: str):
    logger = _get_hot_lead_logger(river)
    logger.error(f"ERROR | {error_message}")


def notify_daily_summary(stats: dict):
    logger = _get_hot_lead_logger("summary")
    lines = [f"DAILY SUMMARY — {len(stats)} rivers"]
    for river, data in stats.items():
        lines.append(f"  {river}: {data.get('enrolled', 0)} enrolled, {data.get('hot_leads', 0)} hot leads")
    logger.info("\n".join(lines))
