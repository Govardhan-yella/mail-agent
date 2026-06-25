"""
Daily email-to-WhatsApp summary agent.

Setup:
1. Enable IMAP for your mailbox.
2. For Gmail, create an App Password and use it as MAIL_IMAP_PASSWORD.
3. Choose one or both delivery channels:
   - Twilio WhatsApp sandbox/sender
   - Telegram bot + chat id
4. Create a .env file beside this script using the example at the bottom.

Run once:
    python3 mail_agent.py --once

Run every day:
    python3 mail_agent.py
"""

from __future__ import annotations

import argparse
import base64
import email
import imaplib
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable


STATE_FILE = Path(__file__).with_name(".mail_agent_state.json")
ENV_FILE = Path(__file__).with_name(".env")
SETUP_HELP = f"""
Create this file:
{ENV_FILE}

With values like:

MAIL_IMAP_HOST=imap.gmail.com
MAIL_IMAP_PORT=993
MAIL_IMAP_USER=your-email@gmail.com
MAIL_IMAP_PASSWORD=your-gmail-app-password
MAILBOX=INBOX
MAIL_LOOKBACK_HOURS=24
MAIL_MAX_EMAILS=25
MAIL_ONLY_UNSEEN=false
DAILY_TIME=09:00

DELIVERY_CHANNEL=whatsapp
# DELIVERY_CHANNEL can be: whatsapp, telegram, both

TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
WHATSAPP_TO=whatsapp:+91your_number

TELEGRAM_BOT_TOKEN=123456789:your_bot_token
TELEGRAM_CHAT_ID=123456789

USE_LLM_SUMMARY=true
LLM_MODEL=llama3:latest
LLM_TIMEOUT_SECONDS=90
""".strip()


@dataclass(frozen=True)
class Config:
    imap_host: str
    imap_port: int
    imap_user: str
    imap_password: str
    mailbox: str
    lookback_hours: int
    max_emails: int
    only_unseen: bool
    daily_time: str
    delivery_channel: str
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_whatsapp_from: str
    whatsapp_to: str
    telegram_bot_token: str
    telegram_chat_id: str
    use_llm_summary: bool
    llm_model: str
    llm_timeout_seconds: int


@dataclass
class MailItem:
    uid: str
    sender: str
    subject: str
    date: datetime | None
    snippet: str
    important: bool
    links: tuple[str, ...] | None = None


def get_env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


def load_dotenv_file() -> None:
    if not ENV_FILE.exists():
        return

    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def load_config() -> Config:
    load_dotenv_file()
    base_required = ("MAIL_IMAP_USER", "MAIL_IMAP_PASSWORD")
    missing_base = [name for name in base_required if not os.getenv(name)]
    if missing_base:
        missing_list = ", ".join(missing_base)
        raise RuntimeError(f"Missing required setting(s): {missing_list}\n\n{SETUP_HELP}")

    delivery_channel = get_env("DELIVERY_CHANNEL", "whatsapp").strip().lower()
    if delivery_channel not in {"whatsapp", "telegram", "both"}:
        raise RuntimeError(
            "DELIVERY_CHANNEL must be one of: whatsapp, telegram, both"
        )

    if delivery_channel in {"whatsapp", "both"}:
        whatsapp_required = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "WHATSAPP_TO")
        missing_whatsapp = [name for name in whatsapp_required if not os.getenv(name)]
        if missing_whatsapp:
            missing_list = ", ".join(missing_whatsapp)
            raise RuntimeError(
                f"Missing WhatsApp setting(s): {missing_list}\n\n{SETUP_HELP}"
            )

    if delivery_channel in {"telegram", "both"}:
        telegram_required = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
        missing_telegram = [name for name in telegram_required if not os.getenv(name)]
        if missing_telegram:
            missing_list = ", ".join(missing_telegram)
            raise RuntimeError(
                f"Missing Telegram setting(s): {missing_list}\n\n{SETUP_HELP}"
            )

    return Config(
        imap_host=get_env("MAIL_IMAP_HOST", "imap.gmail.com"),
        imap_port=int(get_env("MAIL_IMAP_PORT", "993")),
        imap_user=get_env("MAIL_IMAP_USER", required=True),
        imap_password=get_env("MAIL_IMAP_PASSWORD", required=True),
        mailbox=get_env("MAILBOX", "INBOX"),
        lookback_hours=int(get_env("MAIL_LOOKBACK_HOURS", "24")),
        max_emails=int(get_env("MAIL_MAX_EMAILS", "25")),
        only_unseen=get_env("MAIL_ONLY_UNSEEN", "false").lower() == "true",
        daily_time=get_env("DAILY_TIME", "09:00"),
        delivery_channel=delivery_channel,
        twilio_account_sid=get_env("TWILIO_ACCOUNT_SID"),
        twilio_auth_token=get_env("TWILIO_AUTH_TOKEN"),
        twilio_whatsapp_from=get_env("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886"),
        whatsapp_to=get_env("WHATSAPP_TO"),
        telegram_bot_token=get_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=get_env("TELEGRAM_CHAT_ID"),
        use_llm_summary=get_env("USE_LLM_SUMMARY", "true").lower() == "true",
        llm_model=get_env("LLM_MODEL", "llama3:latest"),
        llm_timeout_seconds=int(get_env("LLM_TIMEOUT_SECONDS", "90")),
    )


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}

    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def decode_mime_words(value: str | None) -> str:
    if not value:
        return "(no subject)"

    parts: list[str] = []
    for text, charset in decode_header(value):
        if isinstance(text, bytes):
            parts.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(text)
    return " ".join("".join(parts).split())


def clean_text(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_links(text: str) -> tuple[str, ...]:
    matches = __import__("re").findall(r"https?://\S+", text)
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in matches:
        cleaned_url = raw.strip()
        while cleaned_url and cleaned_url[-1] in ("\\)", ">", "]", "}", ",", "\\", "\"", "'", "`", ":", ";", ",", ".", "!", "?", "(", "[", "{", "=", "&", "%", "*", "^", "~", "|", "/", "-", "_", "+"):
            cleaned_url = cleaned_url[:-1]
        if cleaned_url and cleaned_url not in seen:
            seen.add(cleaned_url)
            cleaned.append(cleaned_url)
    return tuple(cleaned[:10])


def clean_terminal_output(text: str) -> str:
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = text.replace("\r", "")
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)
    return text.strip()


def get_text_body(message: Message) -> str:
    if message.is_multipart():
        plain_parts: list[str] = []
        html_parts: list[str] = []

        for part in message.walk():
            content_type = part.get_content_type()
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue

            payload = part.get_payload(decode=True)
            if not payload:
                continue

            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if content_type == "text/plain":
                plain_parts.append(decoded)
            elif content_type == "text/html":
                html_parts.append(strip_html(decoded))

        return "\n".join(plain_parts or html_parts)

    payload = message.get_payload(decode=True)
    if not payload:
        return ""

    charset = message.get_content_charset() or "utf-8"
    decoded = payload.decode(charset, errors="replace")
    if message.get_content_type() == "text/html":
        return strip_html(decoded)
    return decoded


def strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<br\s*/?>", "\n", html)
    html = re.sub(r"(?s)</p>", "\n", html)
    html = re.sub(r"(?s)<.*?>", " ", html)
    return clean_text(html)


def parse_message(uid: str, raw_email: bytes) -> MailItem:
    message = email.message_from_bytes(raw_email)
    sender = decode_mime_words(message.get("From"))
    subject = decode_mime_words(message.get("Subject"))
    body = clean_text(get_text_body(message))
    snippet = body[:220] + ("..." if len(body) > 220 else "")

    parsed_date = None
    if message.get("Date"):
        try:
            parsed_date = parsedate_to_datetime(message.get("Date"))
        except (TypeError, ValueError):
            parsed_date = None

    important = is_important(subject, body, sender)
    links = extract_links(body)
    return MailItem(uid, sender, subject, parsed_date, snippet, important, links)


def is_important(subject: str, body: str, sender: str) -> bool:
    text = f"{subject} {body} {sender}".lower()
    keywords = (
        "urgent",
        "important",
        "action required",
        "deadline",
        "interview",
        "offer",
        "invoice",
        "payment",
        "security",
        "password",
        "otp",
        "verification",
        "failed",
        "overdue",
    )
    return any(keyword in text for keyword in keywords)


def imap_date(date: datetime) -> str:
    return date.strftime("%d-%b-%Y")


def get_cutoff(state: dict, config: Config) -> datetime:
    fallback = datetime.now().astimezone() - timedelta(hours=config.lookback_hours)
    last_run = state.get("last_run")
    if not last_run:
        return fallback

    try:
        parsed = datetime.fromisoformat(last_run)
    except ValueError:
        return fallback

    return max(parsed, fallback - timedelta(hours=1))


def fetch_mail(config: Config, state: dict) -> list[MailItem]:
    cutoff = get_cutoff(state, config)
    seen_uids = set(state.get("seen_uids", []))
    search_terms = ["SINCE", imap_date(cutoff)]
    if config.only_unseen:
        search_terms.insert(0, "UNSEEN")

    context = ssl.create_default_context()
    with imaplib.IMAP4_SSL(config.imap_host, config.imap_port, ssl_context=context) as mail:
        mail.login(config.imap_user, config.imap_password)
        mail.select(config.mailbox)

        status, data = mail.uid("search", None, *search_terms)
        if status != "OK":
            raise RuntimeError("Could not search mailbox")

        uids = data[0].split()
        newest_first = list(reversed(uids))[: config.max_emails]
        items: list[MailItem] = []

        for raw_uid in newest_first:
            uid = raw_uid.decode("ascii")
            if uid in seen_uids:
                continue

            status, message_data = mail.uid("fetch", raw_uid, "(RFC822)")
            if status != "OK" or not message_data:
                continue

            for part in message_data:
                if not isinstance(part, tuple):
                    continue
                items.append(parse_message(uid, part[1]))

        mail.logout()
        return items


def fetch_mail_with_retries(config: Config, state: dict) -> list[MailItem]:
    attempts = 5
    wait_seconds = 20
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return fetch_mail(config, state)
        except (OSError, socket.gaierror, imaplib.IMAP4.abort) as exc:
            last_error = exc
            if attempt == attempts:
                break
            print(
                f"Mail fetch failed on attempt {attempt}/{attempts}: {exc}. "
                f"Retrying in {wait_seconds} seconds..."
            )
            time.sleep(wait_seconds)

    raise RuntimeError(f"Could not fetch mail after {attempts} attempts: {last_error}")


def sender_name(sender: str) -> str:
    sender = re.sub(r"<.*?>", "", sender).strip()
    return sender or "Unknown sender"


def summarize(items: Iterable[MailItem], config: Config) -> str:
    mails = list(items)
    if config.use_llm_summary and mails:
        llm_summary = summarize_with_ollama(mails, config)
        if llm_summary:
            return llm_summary

    today = datetime.now().strftime("%d %b %Y")

    if not mails:
        return (
            f"Daily Mail Summary - {today}\n\n"
            f"No new emails found in {config.mailbox} for the checked period."
        )

    important = [mail for mail in mails if mail.important]
    job_alerts = [mail for mail in mails if is_job_alert(mail)]
    regular = [mail for mail in mails if not mail.important]

    lines = [
        f"Daily Mail Summary - {today}",
        "",
        f"Checked {len(mails)} new email(s) in {config.mailbox}.",
        f"Important: {len(important)} | Job Alerts: {len(job_alerts)} | Other: {len(regular)}",
        "",
    ]

    if important:
        lines.append("Important")
        lines.extend(format_mail_lines(important[:8]))
        lines.append("")

    if job_alerts:
        lines.append("Job Alerts")
        for mail in job_alerts[:8]:
            lines.append(f"- {sender_name(mail.sender)}: {mail.subject}")
            if mail.links:
                lines.append("  Apply: " + " | ".join(mail.links[:3]))
        lines.append("")

    if regular:
        lines.append("Other")
        lines.extend(format_mail_lines(regular[:12]))

    hidden_count = len(mails) - min(len(important), 8) - min(len(regular), 12)
    if hidden_count > 0:
        lines.append(f"\n+ {hidden_count} more email(s) not shown.")

    return "\n".join(lines).strip()


def summarize_with_ollama(mails: list[MailItem], config: Config) -> str:
    context = build_mail_context(mails)
    prompt = (
        "You are a concise personal email assistant.\n"
        "Create a daily summary in plain text with these sections exactly:\n"
        "1) Daily Mail Summary - <today date>\n"
        "2) Stats line: Checked X emails | Important: Y | Job Alerts: Z | Other: W\n"
        "3) Important\n"
        "4) Job Alerts\n"
        "5) Other\n"
        "For each section, provide numbered bullets with sender, subject, and one short reason.\n"
        "Do not include markdown code fences.\n\n"
        f"Email data:\n{context}\n"
    )

    try:
        result = subprocess.run(
            ["ollama", "run", config.llm_model, prompt],
            check=False,
            capture_output=True,
            text=True,
            timeout=config.llm_timeout_seconds,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""

    if result.returncode != 0:
        return ""

    return clean_terminal_output(result.stdout)


def build_mail_context(mails: list[MailItem], limit: int = 25) -> str:
    lines: list[str] = []
    for index, mail in enumerate(mails[:limit], start=1):
        lines.append(
            f"{index}. sender={sender_name(mail.sender)} | subject={mail.subject} | "
            f"important={mail.important} | job_alert={is_job_alert(mail)} | snippet={mail.snippet}"
        )
    return "\n".join(lines)


def format_mail_lines(items: list[MailItem]) -> list[str]:
    lines: list[str] = []
    for index, mail in enumerate(items, start=1):
        snippet = f" - {mail.snippet}" if mail.snippet else ""
        lines.append(f"{index}. {sender_name(mail.sender)}: {mail.subject}{snippet}")
    return lines


def split_message(message: str, limit: int = 1500) -> list[str]:
    if len(message) <= limit:
        return [message]

    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    for line in message.splitlines():
        line_length = len(line) + 1
        if current and current_length + line_length > limit:
            chunks.append("\n".join(current))
            current = []
            current_length = 0
        current.append(line)
        current_length += line_length

    if current:
        chunks.append("\n".join(current))

    return chunks


def send_whatsapp(config: Config, message: str) -> None:
    url = (
        "https://api.twilio.com/2010-04-01/Accounts/"
        f"{config.twilio_account_sid}/Messages.json"
    )
    credentials = f"{config.twilio_account_sid}:{config.twilio_auth_token}"
    auth_header = base64.b64encode(credentials.encode("utf-8")).decode("ascii")

    for chunk in split_message(message):
        data = urllib.parse.urlencode(
            {
                "From": config.twilio_whatsapp_from,
                "To": config.whatsapp_to,
                "Body": chunk,
            }
        ).encode("utf-8")

        request = urllib.request.Request(url, data=data, method="POST")
        request.add_header("Authorization", f"Basic {auth_header}")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")

        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status >= 300:
                raise RuntimeError(f"Twilio send failed with HTTP {response.status}")


def send_telegram(config: Config, message: str) -> None:
    url = f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage"
    for chunk in split_message(message, limit=3500):
        data = urllib.parse.urlencode(
            {
                "chat_id": config.telegram_chat_id,
                "text": chunk,
            }
        ).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status >= 300:
                raise RuntimeError(f"Telegram send failed with HTTP {response.status}")


def send_summary(config: Config, message: str) -> None:
    if config.delivery_channel == "whatsapp":
        send_whatsapp(config, message)
        return
    if config.delivery_channel == "telegram":
        send_telegram(config, message)
        return
    send_whatsapp(config, message)
    send_telegram(config, message)


def is_job_alert(mail: MailItem) -> bool:
    text = f"{mail.subject} {mail.snippet} {mail.sender}".lower()
    keywords = (
        "job",
        "jobs",
        "career",
        "careers",
        "hiring",
        "opportunity",
        "vacancy",
        "position",
        "application",
        "recruiter",
        "interview",
        "offer",
    )
    return any(keyword in text for keyword in keywords)


def run_once(config: Config) -> None:
    state = load_state()
    mails = fetch_mail_with_retries(config, state)
    summary = summarize(mails, config)
    send_summary(config, summary)

    previous_uids = state.get("seen_uids", [])
    new_uids = [mail.uid for mail in mails]
    state["seen_uids"] = (previous_uids + new_uids)[-500:]
    state["last_run"] = datetime.now().astimezone().isoformat()
    save_state(state)

    print(summary)


def seconds_until_daily_time(daily_time: str) -> int:
    try:
        hour_text, minute_text = daily_time.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except ValueError as exc:
        raise RuntimeError("DAILY_TIME must be in HH:MM format, like 09:00") from exc

    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(1, int((target - now).total_seconds()))


def run_forever(config: Config) -> None:
    print(f"Mail agent started. Daily summary time: {config.daily_time}")
    while True:
        wait_seconds = seconds_until_daily_time(config.daily_time)
        print(f"Next check in {wait_seconds // 60} minute(s).")
        time.sleep(wait_seconds)

        try:
            run_once(config)
        except Exception as exc:
            print(f"Agent failed: {exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Send daily email summaries to WhatsApp/Telegram.")
    parser.add_argument("--once", action="store_true", help="Run one check immediately.")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Check whether required settings are available.",
    )
    args = parser.parse_args()

    try:
        config = load_config()
        if args.check_config:
            print("Configuration looks ready.")
            return
        if args.once:
            run_once(config)
        else:
            run_forever(config)
    except RuntimeError as exc:
        print(f"Setup error:\n{exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()


"""
.env example:

MAIL_IMAP_HOST=imap.gmail.com
MAIL_IMAP_PORT=993
MAIL_IMAP_USER=your-email@gmail.com
MAIL_IMAP_PASSWORD=your-gmail-app-password
MAILBOX=INBOX
MAIL_LOOKBACK_HOURS=24
MAIL_MAX_EMAILS=25
MAIL_ONLY_UNSEEN=false
DAILY_TIME=09:00

TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your_twilio_auth_token
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
WHATSAPP_TO=whatsapp:+91your_number

DELIVERY_CHANNEL=whatsapp
# whatsapp | telegram | both
TELEGRAM_BOT_TOKEN=123456789:your_bot_token
TELEGRAM_CHAT_ID=123456789

USE_LLM_SUMMARY=true
LLM_MODEL=llama3:latest
LLM_TIMEOUT_SECONDS=90
"""
