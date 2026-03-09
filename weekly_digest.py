#!/usr/bin/env python3
"""
Weekly Digest for @devopsdaily
Щонеділі збирає найважливіше за тиждень і постить одним повідомленням.
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
POSTED_IDS_FILE = Path(os.environ.get("POSTED_IDS_FILE", "posted_ids_digest.json"))
MAX_PER_SECTION = int(os.environ.get("MAX_PER_SECTION", "5"))

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# ---------------------------------------------------------------------------
# Feed sources grouped by section
# ---------------------------------------------------------------------------
SECTIONS = {
    "☁️ Cloud & DevOps": [
        ("Kubernetes Blog", "https://kubernetes.io/feed.xml"),
        ("AWS Blog", "https://aws.amazon.com/blogs/aws/feed/"),
        ("Docker Blog", "https://www.docker.com/blog/feed/"),
        ("HashiCorp Blog", "https://www.hashicorp.com/blog/feed.xml"),
        ("DevOps.com", "https://devops.com/feed/"),
    ],
    "🔒 Security": [
        ("The Hacker News", "https://feeds.feedburner.com/TheHackersNews"),
        ("Krebs on Security", "https://krebsonsecurity.com/feed/"),
        ("CISA Advisories", "https://www.cisa.gov/cybersecurity-advisories/all.xml"),
    ],
    "📦 Releases": [
        ("Kubernetes", "https://github.com/kubernetes/kubernetes/releases.atom"),
        ("Terraform", "https://github.com/hashicorp/terraform/releases.atom"),
        ("ArgoCD", "https://github.com/argoproj/argo-cd/releases.atom"),
        ("Helm", "https://github.com/helm/helm/releases.atom"),
        ("Docker CLI", "https://github.com/docker/cli/releases.atom"),
        ("Prometheus", "https://github.com/prometheus/prometheus/releases.atom"),
        ("Trivy", "https://github.com/aquasecurity/trivy/releases.atom"),
    ],
    "🇺🇦 DOU.UA": [
        ("DOU", "https://dou.ua/feed/"),
    ],
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("weekly-digest")

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def load_posted_ids() -> set:
    if POSTED_IDS_FILE.exists():
        return set(json.loads(POSTED_IDS_FILE.read_text()))
    return set()


def save_posted_ids(ids: set):
    trimmed = sorted(ids)[-1000:]
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

# ---------------------------------------------------------------------------
# Collect items from feeds
# ---------------------------------------------------------------------------


def collect_section(feeds: list, cutoff: datetime, max_items: int) -> list[dict]:
    """Return list of {title, link} from feeds, newest first, max max_items."""
    items = []
    for feed_name, feed_url in feeds:
        log.info("  Fetching %s …", feed_name)
        try:
            feed = feedparser.parse(feed_url)
        except Exception as exc:
            log.error("  Parse error %s: %s", feed_url, exc)
            continue

        for entry in feed.entries:
            pub = parse_published(entry)
            if pub and pub < cutoff:
                continue
            items.append({
                "title": entry.get("title", "No title"),
                "link": entry.get("link", ""),
                "source": feed_name,
                "published": pub or cutoff,
            })

    # Sort by date descending, deduplicate by title, take top N
    items.sort(key=lambda x: x["published"], reverse=True)
    seen = set()
    unique = []
    for item in items:
        key = item["title"].lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(item)
        if len(unique) >= max_items:
            break
    return unique

# ---------------------------------------------------------------------------
# Build digest message
# ---------------------------------------------------------------------------


def build_digest(sections_data: dict[str, list]) -> str:
    now = datetime.now(timezone.utc)
    week_start = (now - timedelta(days=7)).strftime("%d.%m")
    week_end = now.strftime("%d.%m.%Y")

    lines = [
        f"📋 <b>Weekly Digest</b>",
        f"📅 {week_start} — {week_end}",
        "",
        "Найважливіше за тиждень:",
        "",
    ]

    for section_name, items in sections_data.items():
        if not items:
            continue
        lines.append(f"<b>{section_name}</b>")
        for i, item in enumerate(items, 1):
            title = escape_html(item["title"][:100])
            link = item["link"]
            if link:
                lines.append(f'  {i}. <a href="{link}">{title}</a>')
            else:
                lines.append(f"  {i}. {title}")
        lines.append("")

    lines.append("——————————————")
    lines.append("📢 Підписуйся → @devopsdaily")
    lines.append("\n🤖 <i>#WeeklyDigest #DevOpsDaily</i>")
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run():
    posted = load_posted_ids()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)

    # Build a unique digest ID based on the week
    week_id = now.strftime("digest-%Y-W%W")
    digest_hash = hashlib.sha256(week_id.encode()).hexdigest()[:16]

    if digest_hash in posted:
        log.info("Digest for %s already posted, skipping.", week_id)
        return

    sections_data = {}
    for section_name, feeds in SECTIONS.items():
        log.info("Collecting %s …", section_name)
        items = collect_section(feeds, cutoff, MAX_PER_SECTION)
        sections_data[section_name] = items
        log.info("  Found %d items", len(items))

    total_items = sum(len(v) for v in sections_data.values())
    if total_items == 0:
        log.info("No items found for digest, skipping.")
        return

    message = build_digest(sections_data)

    # Telegram max message = 4096 chars. Split if needed.
    if len(message) <= 4096:
        if send_message(message):
            posted.add(digest_hash)
            log.info("Digest sent! (%d chars, %d items)", len(message), total_items)
    else:
        # Split into header + sections
        log.info("Message too long (%d chars), splitting…", len(message))
        header = "📋 <b>Weekly Digest</b>\n📅 Найважливіше за тиждень:\n"
        send_message(header)
        time.sleep(2)

        for section_name, items in sections_data.items():
            if not items:
                continue
            section_lines = [f"<b>{section_name}</b>"]
            for i, item in enumerate(items, 1):
                title = escape_html(item["title"][:100])
                link = item["link"]
                if link:
                    section_lines.append(f'  {i}. <a href="{link}">{title}</a>')
                else:
                    section_lines.append(f"  {i}. {title}")
            send_message("\n".join(section_lines))
            time.sleep(2)

        send_message("——————————————\n📢 Підписуйся → @devopsdaily\n\n🤖 <i>#WeeklyDigest #DevOpsDaily</i>")
        posted.add(digest_hash)
        log.info("Digest sent in parts! (%d items)", total_items)

    save_posted_ids(posted)


if __name__ == "__main__":
    run()
