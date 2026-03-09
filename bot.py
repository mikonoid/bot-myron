#!/usr/bin/env python3
"""
DevOps Daily Telegram Bot
Posts DevOps & Security news, CVEs, and release updates to @devopsdaily
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
POSTED_IDS_FILE = Path(os.environ.get("POSTED_IDS_FILE", "posted_ids.json"))
MAX_ITEMS_PER_FEED = int(os.environ.get("MAX_ITEMS_PER_FEED", "3"))
MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "24"))

FEEDS = {
    # ---- DevOps ----
    "☸️ Kubernetes Blog": "https://kubernetes.io/feed.xml",
    "☁️ AWS News": "https://aws.amazon.com/blogs/aws/feed/",
    "🔧 DevOps.com": "https://devops.com/feed/",
    "🐳 Docker Blog": "https://www.docker.com/blog/feed/",
    "🔀 HashiCorp Blog": "https://www.hashicorp.com/blog/feed.xml",

    # ---- Security ----
    "🛡️ OWASP": "https://owasp.org/feed.xml",
    "🔒 SANS NewsBites": "https://www.sans.org/newsletters/newsbites/rss/",
    "🕵️ The Hacker News": "https://feeds.feedburner.com/TheHackersNews",
    "🔑 Krebs on Security": "https://krebsonsecurity.com/feed/",

    # ---- CVE ----
    "🚨 NIST NVD CVE": "https://nvd.nist.gov/feeds/xml/cve/misc/nvd-rss.xml",
    "🚨 CISA Alerts": "https://www.cisa.gov/cybersecurity-advisories/all.xml",

    # ---- Releases ----
    "📦 Kubernetes Releases": "https://github.com/kubernetes/kubernetes/releases.atom",
    "📦 Terraform Releases": "https://github.com/hashicorp/terraform/releases.atom",
}

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("bot-myron")

# ---------------------------------------------------------------------------
# Persistence  – simple JSON file with posted entry hashes
# ---------------------------------------------------------------------------


def load_posted_ids() -> set:
    if POSTED_IDS_FILE.exists():
        data = json.loads(POSTED_IDS_FILE.read_text())
        return set(data)
    return set()


def save_posted_ids(ids: set):
    # Keep only last 5000 entries to avoid infinite growth
    trimmed = sorted(ids)[-5000:]
    POSTED_IDS_FILE.write_text(json.dumps(trimmed, indent=2))


def entry_id(entry) -> str:
    """Deterministic hash for an RSS entry."""
    raw = entry.get("id") or entry.get("link") or entry.get("title", "")
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------


def send_message(text: str, disable_preview: bool = True) -> bool:
    """Send a message to the Telegram channel (MarkdownV2)."""
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
        log.error("Failed to send message: %s", exc)
        return False

# ---------------------------------------------------------------------------
# Feed processing
# ---------------------------------------------------------------------------


def escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram HTML mode."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def format_entry(feed_name: str, entry) -> str:
    title = escape_html(entry.get("title", "No title"))
    link = entry.get("link", "")
    summary = escape_html(entry.get("summary", "")[:300]).strip()
    if len(entry.get("summary", "")) > 300:
        summary += "…"

    lines = [
        f"<b>{feed_name}</b>",
        "",
        f"📰 <b>{title}</b>",
        "",
    ]
    if summary:
        lines.append(summary)
        lines.append("")
    if link:
        lines.append(f'🔗 <a href="{link}">Read more</a>')

    lines.append(f"\n🤖 <i>#DevOpsDaily</i>")
    return "\n".join(lines)


def parse_published(entry) -> datetime | None:
    """Try to extract a timezone‑aware published datetime."""
    for field in ("published_parsed", "updated_parsed"):
        tp = entry.get(field)
        if tp:
            try:
                return datetime(*tp[:6], tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def fetch_and_post():
    posted = load_posted_ids()
    new_posted = set(posted)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=MAX_AGE_HOURS)
    total_sent = 0

    for feed_name, feed_url in FEEDS.items():
        log.info("Fetching %s ...", feed_name)
        try:
            feed = feedparser.parse(feed_url)
        except Exception as exc:
            log.error("Failed to parse %s: %s", feed_url, exc)
            continue

        sent_for_feed = 0
        for entry in feed.entries:
            if sent_for_feed >= MAX_ITEMS_PER_FEED:
                break

            eid = entry_id(entry)
            if eid in posted:
                continue

            pub = parse_published(entry)
            if pub and pub < cutoff:
                continue

            text = format_entry(feed_name, entry)
            if send_message(text):
                new_posted.add(eid)
                sent_for_feed += 1
                total_sent += 1
                log.info("  ✓ %s", entry.get("title", "?")[:80])
                time.sleep(2)  # gentle rate‑limiting

    save_posted_ids(new_posted)
    log.info("Done. Sent %d new items.", total_sent)

# ---------------------------------------------------------------------------
# Entry‑point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    fetch_and_post()
