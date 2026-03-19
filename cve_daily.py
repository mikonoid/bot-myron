#!/usr/bin/env python3
"""
CVE Daily Monitor for @devopsdaily
Раз на день перевіряє CVE, фільтрує тільки k8s/docker/aws/linux/devops.
"""

import os
import json
import hashlib
import logging
import re
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
POSTED_IDS_FILE = Path(os.environ.get("POSTED_IDS_FILE", "posted_ids_cve.json"))
MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "1"))
MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "48"))

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# ---------------------------------------------------------------------------
# CVE Feeds
# ---------------------------------------------------------------------------
CVE_FEEDS = {
    "🚨 NVD CVE": "https://nvd.nist.gov/feeds/xml/cve/misc/nvd-rss.xml",
    "🚨 CISA Advisories": "https://www.cisa.gov/cybersecurity-advisories/all.xml",
}

# ---------------------------------------------------------------------------
# Keyword filter — тільки DevOps‑related CVE
# ---------------------------------------------------------------------------
KEYWORDS = [
    # Container & orchestration
    "kubernetes", "k8s", "kube-proxy", "kubelet",
    "docker", "containerd", "runc", "moby", "podman", "buildkit",
    # Cloud providers
    "aws", "eks", "ecs", "ecr", "lambda",
    "azure", "aks", "gcp", "gke",
    # CI/CD
    "jenkins", "gitlab", "github actions", "argocd", "argo-cd",
    "circleci", "drone", "tekton", "flux",
    # IaC & config
    "terraform", "ansible", "helm", "pulumi", "crossplane",
    # Networking & ingress
    "nginx", "envoy", "istio", "traefik", "haproxy", "caddy",
    "coredns", "calico", "cilium",
    # Monitoring & observability
    "prometheus", "grafana", "elasticsearch", "kibana", "fluentd",
    "fluentbit", "loki", "jaeger", "datadog", "opentelemetry",
    # Databases (used in DevOps)
    "redis", "postgres", "mysql", "mongodb", "etcd",
    # Security tools
    "vault", "trivy", "falco", "grype", "snyk", "cosign",
    "kyverno", "opa", "gatekeeper",
    # Core infra
    "openssh", "openssl", "systemd", "sudo",
    "git ", "curl", "wget",
    # Languages & runtimes
    "node.js", "python", "golang", "ruby", "java ", "php ",
    "npm", "pip", "cargo",
]


def is_relevant(entry) -> bool:
    """Return True if any keyword matches title or summary."""
    text = (
        entry.get("title", "") + " " + entry.get("summary", "")
    ).lower()
    return any(kw in text for kw in KEYWORDS)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("cve-daily")

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def load_posted_ids() -> set:
    if POSTED_IDS_FILE.exists():
        return set(json.loads(POSTED_IDS_FILE.read_text()))
    return set()


def save_posted_ids(ids: set):
    trimmed = sorted(ids)[-5000:]
    POSTED_IDS_FILE.write_text(json.dumps(trimmed, indent=2))


def entry_id(entry) -> str:
    raw = entry.get("id") or entry.get("link") or entry.get("title", "")
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def strip_html(raw: str) -> str:
    """Remove HTML tags and decode common entities."""
    import re
    text = re.sub(r"<[^>]+>", " ", raw)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&quot;", '"')
    return " ".join(text.split())


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
# Severity
# ---------------------------------------------------------------------------

SEVERITY_EMOJI = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
}


def detect_severity(text: str) -> str:
    text_lower = text.lower()
    for level, emoji in SEVERITY_EMOJI.items():
        if level in text_lower:
            return f"{emoji} {level.upper()}"
    return "⚪ UNKNOWN"


def format_cve(feed_name: str, entry) -> str:
    title = escape_html(entry.get("title", "No title"))
    link = entry.get("link", "")
    raw_summary = entry.get("summary", "")
    plain = strip_html(raw_summary)[:400].strip()
    if len(strip_html(raw_summary)) > 400:
        plain += "…"
    summary = escape_html(plain)
    severity = detect_severity(entry.get("summary", "") + entry.get("title", ""))

    cve_match = re.search(r"CVE-\d{4}-\d+", entry.get("title", "") + entry.get("summary", ""))
    cve_id = cve_match.group(0) if cve_match else ""

    lines = [
        f"<b>{feed_name}</b>",
        "",
        f"🚨 <b>{title}</b>",
    ]
    if cve_id:
        lines.append(f"🆔 <code>{cve_id}</code>  |  Severity: {severity}")
    lines.append("")
    if summary:
        lines.append(summary)
        lines.append("")
    if link:
        lines.append(f'🔗 <a href="{link}">Details</a>')
    lines.append("\n🤖 <i>#CVE #Security #DevOpsDaily</i>")
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

    for name, url in CVE_FEEDS.items():
        log.info("Fetching %s", name)
        try:
            feed = feedparser.parse(url)
        except Exception as exc:
            log.error("Parse error %s: %s", url, exc)
            continue

        for entry in feed.entries:
            if total >= MAX_ITEMS:
                break

            eid = entry_id(entry)
            if eid in posted:
                continue

            pub = parse_published(entry)
            if pub and pub < cutoff:
                continue

            # ---- keyword filter ----
            if not is_relevant(entry):
                continue

            if send_message(format_cve(name, entry)):
                new_posted.add(eid)
                total += 1
                log.info("  ✓ %s", entry.get("title", "?")[:80])
                time.sleep(3)

    save_posted_ids(new_posted)
    log.info("Done. Sent %d CVE items.", total)


if __name__ == "__main__":
    run()
