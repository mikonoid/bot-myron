#!/usr/bin/env python3
"""
DOU.UA News for @devopsdaily
Раз на день постить новини з DOU.UA.
"""

import os
import json
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "@devopsdaily")
POSTED_IDS_FILE = Path(os.environ.get("POSTED_IDS_FILE", "posted_ids_dou.json"))
MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "1"))
MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "48"))

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# ---------------------------------------------------------------------------
# DOU Feeds
# ---------------------------------------------------------------------------
DOU_FEEDS = {
    "🇺🇦 DOU — Найцікавіше": "https://dou.ua/feed/",
    "🇺🇦 DOU — Стрічка": "https://dou.ua/lenta/feed/",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("dou-news")

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def load_posted_ids() -> set:
    if POSTED_IDS_FILE.exists():
        return set(json.loads(POSTED_IDS_FILE.read_text()))
    return set()


def save_posted_ids(ids: set):
    trimmed = sorted(ids)[-3000:]
    POSTED_IDS_FILE.write_text(json.dumps(trimmed, indent=2))


def entry_id(entry) -> str:
    raw = entry.get("id") or entry.get("link") or entry.get("title", "")
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_message(text: str, disable_preview: bool = True) -> bool:
    payload = {
        "chat_id": TELEGRAM_CHANNEL,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": disable_preview,
    }
    try:
        resp = requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=30)
        if resp.status_code == 429:
            retry = int(resp.json().get("parameters", {}).get("retry_after", 5))
            log.warning("Rate‑limited, sleeping %ds", retry)
            time.sleep(retry + 1)
            resp = requests.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as exc:
        log.error("Send failed: %s", exc)
        return False


def parse_published(entry) -> datetime | None:
    for field in ("published_parsed", "updated_parsed"):
        tp = entry.get(field)
        if tp:
            try:
                return datetime(*tp[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def strip_html(raw: str) -> str:
    """Rough HTML→plaintext (good enough for summaries)."""
    import re
    text = re.sub(r"<[^>]+>", " ", raw)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&quot;", '"')
    return " ".join(text.split())


def format_dou(entry) -> str:
    title = escape_html(entry.get("title", "No title"))
    link = entry.get("link", "")

    raw_summary = entry.get("summary", "")
    plain = strip_html(raw_summary)[:350].strip()
    if len(raw_summary) > 350:
        plain += "…"
    summary = escape_html(plain)

    lines = [
        "<b>🇺🇦 DOU.UA</b>",
        "",
        f"📰 <b>{title}</b>",
        "",
    ]
    if summary:
        lines.append(summary)
        lines.append("")
    if link:
        lines.append(f'🔗 <a href="{link}">Читати далі</a>')
    lines.append("\n🤖 <i>#DOU #Ukraine #DevOpsDaily</i>")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run():
    posted = load_posted_ids()
    new_posted = set(posted)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=MAX_AGE_HOURS)
    total = 0

    seen_titles = set()  # deduplicate between feeds

    for feed_name, feed_url in DOU_FEEDS.items():
        log.info("Fetching %s …", feed_name)
        try:
            feed = feedparser.parse(feed_url)
        except Exception as exc:
            log.error("Parse error %s: %s", feed_url, exc)
            continue

        for entry in feed.entries:
            if total >= MAX_ITEMS:
                break

            eid = entry_id(entry)
            if eid in posted:
                continue

            title = entry.get("title", "")
            if title in seen_titles:
                continue
            seen_titles.add(title)

            pub = parse_published(entry)
            if pub and pub < cutoff:
                continue

            if send_message(format_dou(entry)):
                new_posted.add(eid)
                total += 1
                log.info("  ✓ %s", title[:80])
                time.sleep(3)

    save_posted_ids(new_posted)
    log.info("Done. Sent %d DOU items.", total)


if __name__ == "__main__":
    run()
