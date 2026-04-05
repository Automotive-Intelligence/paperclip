"""Unified logging for all Project Paperclip rivers."""

import logging
import os
from datetime import datetime

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
os.makedirs(LOG_DIR, exist_ok=True)

_loggers = {}


def _get_river_logger(river: str) -> logging.Logger:
    if river in _loggers:
        return _loggers[river]
    logger = logging.getLogger(f"paperclip.{river}")
    logger.setLevel(logging.INFO)
    fh = logging.FileHandler(os.path.join(LOG_DIR, f"{river}_enrollments.log"))
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(message)s"))
    logger.addHandler(sh)
    _loggers[river] = logger
    return logger


def log_enrollment(river: str, contact_id: str, contact_name: str, track: str):
    logger = _get_river_logger(river)
    logger.info(f"ENROLLED | {contact_id} | {contact_name} | track={track}")


def log_sequence_event(river: str, contact_id: str, event_type: str, step: str):
    logger = _get_river_logger(river)
    logger.info(f"SEQUENCE | {contact_id} | {event_type} | step={step}")


def log_hot_lead(river: str, contact_id: str, trigger: str):
    logger = _get_river_logger(river)
    logger.info(f"HOT LEAD | {contact_id} | trigger={trigger}")


def log_error(river: str, message: str):
    logger = _get_river_logger(river)
    logger.error(f"ERROR | {message}")


def log_info(river: str, message: str):
    logger = _get_river_logger(river)
    logger.info(message)
