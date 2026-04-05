"""Twilio SMS notifications to Michael."""

import os
from twilio.rest import Client
from core.logger import log_info, log_error

TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_FROM = os.environ.get("TWILIO_FROM")
MICHAEL_PHONE = os.environ.get("MICHAEL_PHONE")


def _send_sms(body: str):
    if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM, MICHAEL_PHONE]):
        log_error("notifier", "Twilio credentials not configured — skipping SMS")
        print(f"[NOTIFIER] Would send SMS: {body}")
        return
    try:
        client = Client(TWILIO_SID, TWILIO_TOKEN)
        client.messages.create(body=body, from_=TWILIO_FROM, to=MICHAEL_PHONE)
        log_info("notifier", f"SMS sent to Michael: {body[:80]}...")
    except Exception as e:
        log_error("notifier", f"SMS failed: {e}")


def notify_hot_lead(river: str, contact_name: str, business: str, phone: str, action: str):
    body = f"HOT LEAD [{river.upper()}]: {contact_name} at {business} just {action}. Call them now. {phone}"
    _send_sms(body)


def notify_error(river: str, error_message: str):
    body = f"ERROR [{river.upper()}]: {error_message}"
    _send_sms(body)


def notify_daily_summary(stats: dict):
    lines = [f"PAPERCLIP DAILY SUMMARY — {len(stats)} rivers"]
    for river, data in stats.items():
        lines.append(f"  {river}: {data.get('enrolled', 0)} enrolled, {data.get('hot_leads', 0)} hot leads")
    _send_sms("\n".join(lines))
