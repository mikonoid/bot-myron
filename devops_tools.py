#!/usr/bin/env python3
"""
Weekly DevOps Tools Discovery for @devopsdaily
Раз на тиждень шукає нові/трендові DevOps інструменти на GitHub
і постить топ-список одним повідомленням.
"""

import os
import json
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHANNEL = os.environ.get("TELEGRAM_CHANNEL", "@devopsdaily")
POSTED_IDS_FILE = Path(os.environ.get("POSTED_IDS_FILE", "posted_ids_tools.json"))
MAX_TOOLS = int(os.environ.get("MAX_TOOLS", "7"))

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# GitHub token is optional but avoids rate limits
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("devops-tools")

# ---------------------------------------------------------------------------
# GitHub search queries — DevOps-related topics
# ---------------------------------------------------------------------------
SEARCH_QUERIES = [
    "topic:devops",
    "topic:kubernetes",
    "topic:docker",
    "topic:terraform",
    "topic:cicd",
    "topic:infrastructure-as-code",
    "topic:cloud-native",
    "topic:sre",
    "topic:gitops",
    "topic:devsecops",
    "topic:monitoring",
    "topic:observability",
]

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def load_posted_ids() -> set:
    if POSTED_IDS_FILE.exists():
        return set(json.loads(POSTED_IDS_FILE.read_text()))
    return set()


def save_posted_ids(ids: set):
    trimmed = sorted(ids)[-2000:]
    POSTED_IDS_FILE.write_text(json.dumps(trimmed, indent=2))

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

# ---------------------------------------------------------------------------
# GitHub API — search trending repos
# ---------------------------------------------------------------------------


def github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def search_trending_repos() -> list[dict]:
    """Search GitHub for DevOps repos created or updated in the last 7 days,
    sorted by stars."""
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

    all_repos = {}

    for query in SEARCH_QUERIES:
        q = f"{query} pushed:>={week_ago}"
        url = "https://api.github.com/search/repositories"
        params = {
            "q": q,
            "sort": "stars",
            "order": "desc",
            "per_page": 10,
        }
        try:
            resp = requests.get(url, headers=github_headers(), params=params, timeout=30)
            if resp.status_code == 403:
                log.warning("GitHub rate limit hit, skipping query: %s", query)
                continue
            resp.raise_for_status()
            data = resp.json()
            for repo in data.get("items", []):
                rid = repo["full_name"]
                if rid not in all_repos:
                    all_repos[rid] = repo
        except Exception as exc:
            log.error("GitHub search error for %s: %s", query, exc)
            continue

        time.sleep(2)  # gentle rate limiting

    # Sort by stars gained (approximation: total stars)
    repos = sorted(all_repos.values(), key=lambda r: r.get("stargazers_count", 0), reverse=True)
    return repos


def lang_emoji(lang: str | None) -> str:
    mapping = {
        "Go": "🔹",
        "Python": "🐍",
        "Rust": "🦀",
        "TypeScript": "📘",
        "JavaScript": "📒",
        "Shell": "🐚",
        "Java": "☕",
        "C": "⚙️",
        "C++": "⚙️",
        "Ruby": "💎",
        "HCL": "🏗️",
    }
    return mapping.get(lang or "", "🔧")

# ---------------------------------------------------------------------------
# Build digest
# ---------------------------------------------------------------------------


def build_tools_post(repos: list[dict], posted: set) -> tuple[str, list[str]]:
    """Build a single Telegram message with top new tools.
    Returns (message, list of repo IDs to mark as posted)."""

    lines = [
        "🛠️ <b>DevOps Tools Weekly Update</b>",
        "",
        "Нові та трендові DevOps інструменти з GitHub:",
        "",
    ]

    added = 0
    new_ids = []

    for repo in repos:
        if added >= MAX_TOOLS:
            break

        rid = hashlib.sha256(repo["full_name"].encode()).hexdigest()[:16]
        if rid in posted:
            continue

        name = escape_html(repo.get("name", "?"))
        full_name = escape_html(repo.get("full_name", "?"))
        desc = escape_html((repo.get("description") or "No description")[:150])
        stars = repo.get("stargazers_count", 0)
        lang = repo.get("language")
        url = repo.get("html_url", "")
        emoji = lang_emoji(lang)

        if stars >= 1000:
            stars_str = f"{stars / 1000:.1f}k"
        else:
            stars_str = str(stars)

        lines.append(
            f'{emoji} <b><a href="{url}">{full_name}</a></b>  ⭐ {stars_str}'
        )
        lines.append(f"   {desc}")
        if lang:
            lines.append(f"   📝 {lang}")
        lines.append("")

        new_ids.append(rid)
        added += 1

    if added == 0:
        return "", []

    lines.append("——————————————")
    lines.append("📢 Підписуйся → @devopsdaily")
    lines.append("\n🤖 <i>#DevOpsTools #OpenSource #DevOpsDaily</i>")

    return "\n".join(lines), new_ids

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run():
    posted = load_posted_ids()

    # Check if we already posted this week
    now = datetime.now(timezone.utc)
    week_id = now.strftime("tools-%Y-W%W")
    week_hash = hashlib.sha256(week_id.encode()).hexdigest()[:16]

    if week_hash in posted:
        log.info("Tools digest for %s already posted, skipping.", week_id)
        return

    log.info("Searching GitHub for trending DevOps repos…")
    repos = search_trending_repos()
    log.info("Found %d unique repos", len(repos))

    message, new_ids = build_tools_post(repos, posted)

    if not message:
        log.info("No new tools to post.")
        return

    if send_message(message, disable_preview=False):
        posted.add(week_hash)
        posted.update(new_ids)
        log.info("Posted %d tools!", len(new_ids))
    else:
        log.error("Failed to send tools digest.")

    save_posted_ids(posted)


if __name__ == "__main__":
    run()
