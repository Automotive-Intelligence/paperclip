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


def notify_cost_alert(message: str):
    """Send cost budget alert — logs + Twilio when configured."""
    logger = _get_hot_lead_logger("cost_alerts")
    logger.warning(f"COST ALERT | {message}")

    # Twilio alert when credentials are available
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_number = os.environ.get("TWILIO_FROM")
    michael_phone = os.environ.get("MICHAEL_PHONE")

    if all([account_sid, auth_token, from_number, michael_phone]):
        try:
            import requests
            url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
            requests.post(url, auth=(account_sid, auth_token), data={
                "From": from_number,
                "To": michael_phone,
                "Body": message,
            })
            logger.info("COST ALERT sent via Twilio to Michael")
        except Exception as e:
            logger.error(f"Twilio cost alert failed: {e}")


def notify_axiom_directive(target_agent: str, directive: str, triggered_by: str):
    """Log Axiom CEO directive — for traceability."""
    logger = _get_hot_lead_logger("axiom")
    logger.info(f"AXIOM DIRECTIVE | to={target_agent} | trigger={triggered_by} | {directive[:100]}")
