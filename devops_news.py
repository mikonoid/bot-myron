#!/usr/bin/env python3
"""
DevOps News for @devopsdaily
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
POSTED_IDS_FILE = Path(os.environ.get("POSTED_IDS_FILE", "posted_ids_news.json"))
MAX_ITEMS_PER_FEED = int(os.environ.get("MAX_ITEMS_PER_FEED", "1"))
MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "48"))

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

# ---------------------------------------------------------------------------
# News Feeds
# ---------------------------------------------------------------------------
FEEDS = {
    # --- DevOps / Cloud ---
    "☸️ Kubernetes Blog": "https://kubernetes.io/feed.xml",
    "☁️ AWS News": "https://aws.amazon.com/blogs/aws/feed/",
    "☁️ AWS DevOps Blog": "https://aws.amazon.com/blogs/devops/feed/",
    "☁️ AWS Security Blog": "https://aws.amazon.com/blogs/security/feed/",
    "☁️ AWS Containers": "https://aws.amazon.com/blogs/containers/feed/",
    "☁️ AWS What's New": "https://aws.amazon.com/about-aws/whats-new/recent/feed/",
    "🐳 Docker Blog": "https://www.docker.com/blog/feed/",
    "🔀 HashiCorp Blog": "https://www.hashicorp.com/blog/feed.xml",
    "🔧 DevOps.com": "https://devops.com/feed/",
    # --- Security (DevOps-filtered) ---
    "🔑 Krebs on Security": "https://krebsonsecurity.com/feed/",
    "🛡️ BleepingComputer": "https://www.bleepingcomputer.com/feed/",
    # --- AI for DevOps (filtered) ---
    "🤖 Google Cloud AI": "https://cloud.google.com/blog/topics/ai-machine-learning/rss/",
    "🤖 AWS ML Blog": "https://aws.amazon.com/blogs/machine-learning/feed/",
    "🤖 GitHub Blog": "https://github.blog/feed/",
    # --- Releases ---
    "📦 K8s Releases": "https://github.com/kubernetes/kubernetes/releases.atom",
    "📦 Terraform Releases": "https://github.com/hashicorp/terraform/releases.atom",
    "📦 ArgoCD Releases": "https://github.com/argoproj/argo-cd/releases.atom",
    "📦 Helm Releases": "https://github.com/helm/helm/releases.atom",
}

# ---------------------------------------------------------------------------
# Keyword filter — applied to security and AI feeds
# to keep content DevOps/infra-relevant
# ---------------------------------------------------------------------------
KEYWORD_FILTER_FEEDS = {
    "🔑 Krebs on Security",
    "🛡️ BleepingComputer",
    "🤖 Google Cloud AI",
    "🤖 AWS ML Blog",
    "🤖 GitHub Blog",
}

DEVOPS_KEYWORDS = [
    # OS / kernel
    "linux", "kernel", "ubuntu", "debian", "rhel", "centos", "alpine",
    # containers / orchestration
    "kubernetes", "k8s", "kubectl", "helm", "docker", "containerd", "podman",
    "container", "oci", "cri-o",
    # CI/CD
    "ci/cd", "cicd", "pipeline", "jenkins", "gitlab", "github actions",
    "argocd", "flux", "tekton", "spinnaker",
    # IaC
    "terraform", "ansible", "pulumi", "cloudformation", "crossplane",
    "puppet", "chef",
    # cloud
    "aws", "amazon web services", "ec2", "s3", "iam", "lambda",
    "eks", "ecs", "fargate", "rds", "cloudwatch",
    "gcp", "google cloud", "gke", "bigquery",
    "azure", "aks",
    # networking / proxies
    "nginx", "envoy", "istio", "traefik", "cilium", "calico",
    # secrets / PKI
    "vault", "openssh", "openssl", "tls", "ssl", "certificate", "ssh key",
    # supply chain
    "supply chain", "npm", "pypi", "pip", "registry", "artifact",
    # observability
    "prometheus", "grafana", "datadog", "loki", "jaeger", "opentelemetry",
    # DBs / queues  (infra angle)
    "redis", "postgres", "postgresql", "etcd", "kafka",
    # source control / scm
    "git", "github", "gitlab",
    # generic
    "devops", "sre", "platform engineering", "devsecops", "infra",
    "cloud-native", "microservice",
    # AI / ML for DevOps
    "copilot", "llm", "ai agent", "generative ai", "chatgpt", "gemini",
    "ai ops", "aiops", "mlops", "model deployment", "inference",
    "code generation", "github copilot", "amazon q", "vertex ai",
    "bedrock", "sagemaker",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("devops-news")


def is_devops_relevant(entry) -> bool:
    """Return True if title+summary contain at least one DevOps keyword."""
    text = (
        (entry.get("title") or "") + " " + (entry.get("summary") or "")
    ).lower()
    return any(kw in text for kw in DEVOPS_KEYWORDS)

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


def format_entry(feed_name: str, entry) -> str:
    title = escape_html(entry.get("title", "No title"))
    link = entry.get("link", "")
    raw_summary = entry.get("summary", "")
    plain = strip_html(raw_summary)[:300].strip()
    if len(strip_html(raw_summary)) > 300:
        plain += "…"
    summary = escape_html(plain)

    is_release = feed_name.startswith("📦")

    lines = [
        f"<b>{feed_name}</b>",
        "",
        f"{'🆕' if is_release else '📰'} <b>{title}</b>",
        "",
    ]
    if summary and not is_release:
        lines.append(summary)
        lines.append("")
    if link:
        label = "Release notes" if is_release else "Read more"
        lines.append(f'🔗 <a href="{link}">{label}</a>')

    tag = "#Release" if is_release else "#DevOps #News"
    lines.append(f"\n🤖 <i>{tag} #DevOpsDaily</i>")
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

    for feed_name, feed_url in FEEDS.items():
        log.info("Fetching %s …", feed_name)
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

            if feed_name in KEYWORD_FILTER_FEEDS and not is_devops_relevant(entry):
                log.debug("  skip (not devops-relevant): %s", entry.get("title", "?")[:80])
                continue

            if send_message(format_entry(feed_name, entry)):
                new_posted.add(eid)
                sent_for_feed += 1
                total += 1
                log.info("  ✓ %s", entry.get("title", "?")[:80])
                time.sleep(3)

    save_posted_ids(new_posted)
    log.info("Done. Sent %d news items.", total)


if __name__ == "__main__":
    run()
