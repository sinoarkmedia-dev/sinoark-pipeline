#!/usr/bin/env python3
"""
sinoark-pipeline/pipeline.py

Hourly pipeline: classify → translate → label.

Only processes articles that:
  - have word_count > 50  (meaningful content already fetched)
  - are marked tech = true  (relevant Chinese tech / AI content)

Classification sets two independent booleans:
  - tech      : true if the article is about Chinese tech broadly
                (AI, chips, policy, BATMH, 5G, robotics, VC, US-China, …)
  - ai_label  : true only if the article is *primarily* about AI / ML

The web displays only ai_label = true articles.
Translate + stakeholder-labelling run on tech = true articles.

Runs in a continuous loop with a 1-hour sleep between passes.

Usage:
    python3 /root/sinoark-pipeline/pipeline.py >> /tmp/sinoark-logs/pipeline.log 2>&1 &
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

# Load env from translation-digest (has GEMINI_API_KEY + SUPABASE_*)
load_dotenv("/root/translation-digest/.env")

from google import genai
from google.genai import types
from supabase import create_client, Client

# ── Config ────────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

MIN_WORD_COUNT = 50          # skip articles with fewer words
CLASSIFY_BATCH = 30          # articles to classify per run
TRANSLATE_BATCH = 20         # articles to translate per run
LABEL_BATCH = 20             # articles to label per run
GEMINI_DELAY = 0.5           # seconds between Gemini calls
LOOP_INTERVAL = 3600         # 1 hour between pipeline runs

_sb: Client | None = None
_gemini: genai.Client | None = None


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print(f"[{ts()}] {msg}", flush=True)


def sb() -> Client:
    global _sb
    if _sb is None:
        _sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _sb


def gemini() -> genai.Client:
    global _gemini
    if _gemini is None:
        _gemini = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini


# ── Step 1: Classification ────────────────────────────────────────────────────

CLASSIFY_SYSTEM = (
    "You are an analyst filtering news articles for a China technology and AI intelligence platform. "
    "The platform tracks: AI/ML research and products, semiconductors and chips, Chinese tech companies "
    "(Baidu, Alibaba, Tencent, Huawei, ByteDance, etc.), Chinese tech policy and regulation, "
    "telecom and 5G/6G, robotics, biotech with AI applications, venture capital in Chinese tech, "
    "and geopolitical tech competition (US-China). "
    "Respond ONLY with a JSON object, no markdown."
)

CLASSIFY_PROMPT = """\
Assess this Chinese article against two criteria:

1. tech: Is this article relevant to Chinese technology broadly?
   (AI/ML, chips, Chinese tech companies, tech policy, 5G/6G, robotics, VC in tech, US-China tech competition)

2. ai_label: Is this article *primarily* about AI or machine learning?
   (LLMs, AI products/research, AI companies, AI policy, AI applications — must be the main focus, not just mentioned)

Title: {title}
Content preview: {preview}

Respond with ONLY this JSON:
{{"tech": true/false, "ai_label": true/false, "reason": "<one sentence>"}}
"""


def classify_batch() -> int:
    """Classify tech and ai_label for articles with word_count > MIN_WORD_COUNT and tech IS NULL."""
    response = (
        sb().table("articles")
        .select("id, original_title, original_content")
        .is_("tech", "null")
        .gt("word_count", MIN_WORD_COUNT)
        .order("published_at", desc=True)
        .limit(CLASSIFY_BATCH)
        .execute()
    )
    articles = response.data or []
    if not articles:
        log("[classify] Nothing to classify.")
        return 0

    log(f"[classify] {len(articles)} articles to classify.")
    classified = 0

    for art in articles:
        title = art.get("original_title") or ""
        content = art.get("original_content") or ""
        preview = content[:500].strip()

        prompt = CLASSIFY_PROMPT.format(title=title, preview=preview)
        try:
            resp = gemini().models.generate_content(
                model="gemini-2.0-flash",
                config=types.GenerateContentConfig(system_instruction=CLASSIFY_SYSTEM),
                contents=prompt,
            )
            raw = resp.text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            result = json.loads(raw)
            is_tech = bool(result.get("tech", False))
            is_ai = bool(result.get("ai_label", False))
            reason = result.get("reason", "")
        except Exception as e:
            log(f"  [classify] WARN: {title[:50]!r}: {e}")
            time.sleep(GEMINI_DELAY)
            continue

        sb().table("articles").update({"tech": is_tech, "ai_label": is_ai}).eq("id", art["id"]).execute()
        mark = "AI" if is_ai else ("T" if is_tech else "✗")
        log(f"  [{mark}] {title[:60]!r} — {reason[:80]}")
        classified += 1
        time.sleep(GEMINI_DELAY)

    log(f"[classify] Done — {classified} classified.")
    return classified


# ── Step 2: Translation ───────────────────────────────────────────────────────

TRANSLATION_SYSTEM = (
    "You are a professional translator specializing in Chinese AI and technology news. "
    "Translate into clear, accurate English for a technically literate audience. "
    "Preserve technical terms, product names, and company names. "
    "Journalistic style suitable for an AI/tech newsletter."
)

TRANSLATION_PROMPT = """\
Source: {source_name}

Title: {title}

Content:
{content}

Respond with ONLY valid JSON (no markdown):
{{"translated_title": "<English title>", "translated_summary": "<150-200 word English summary>"}}
"""


def translate_batch() -> int:
    """Translate articles that are raw, tech=true, and have enough content."""
    response = (
        sb().table("articles")
        .select("id, original_title, original_content, source_name")
        .eq("status", "raw")
        .eq("tech", True)
        .gt("word_count", MIN_WORD_COUNT)
        .order("published_at", desc=True)
        .limit(TRANSLATE_BATCH)
        .execute()
    )
    articles = response.data or []
    if not articles:
        log("[translate] Nothing to translate.")
        return 0

    log(f"[translate] {len(articles)} articles to translate.")
    translated = 0

    for art in articles:
        title = art.get("original_title") or ""
        content = (art.get("original_content") or "")[:3000]
        source = art.get("source_name") or "Unknown"

        prompt = TRANSLATION_PROMPT.format(
            source_name=source, title=title, content=content
        )
        try:
            resp = gemini().models.generate_content(
                model="gemini-2.5-flash",
                config=types.GenerateContentConfig(system_instruction=TRANSLATION_SYSTEM),
                contents=prompt,
            )
            raw = resp.text.strip()
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            result = json.loads(raw)
            t_title = result.get("translated_title", "").strip()
            t_summary = result.get("translated_summary", "").strip()
            if not t_title or not t_summary:
                raise ValueError("Incomplete translation response")
        except Exception as e:
            log(f"  [translate] WARN: {title[:50]!r}: {e}")
            sb().table("articles").update({"status": "failed"}).eq("id", art["id"]).execute()
            time.sleep(GEMINI_DELAY)
            continue

        sb().table("articles").update({
            "translated_title": t_title,
            "translated_summary": t_summary,
            "status": "translated",
        }).eq("id", art["id"]).execute()
        log(f"  [OK] {t_title[:70]!r}")
        translated += 1
        time.sleep(GEMINI_DELAY)

    log(f"[translate] Done — {translated} translated.")
    return translated


# ── Step 3: Stakeholder labelling ─────────────────────────────────────────────

# Reuse extractor from stakeholder-labelling
sys.path.insert(0, "/root/stakeholder-labelling")
from lib.extractor import extract_stakeholders
from lib.supabase_client import (
    link_article_stakeholder,
    upsert_relation,
    upsert_stakeholder,
)


def label_batch() -> int:
    """Label translated, tech=true articles that have no stakeholder links yet."""
    # Get translated + tech articles
    response = (
        sb().table("articles")
        .select("id, original_title, original_content, translated_title, translated_summary")
        .eq("status", "translated")
        .eq("tech", True)
        .order("published_at", desc=True)
        .limit(LABEL_BATCH * 3)
        .execute()
    )
    articles = response.data or []
    if not articles:
        log("[label] Nothing to label.")
        return 0

    # Filter out already-labelled
    ids = [a["id"] for a in articles]
    linked = (
        sb().table("article_stakeholders")
        .select("article_id")
        .in_("article_id", ids)
        .execute()
    )
    linked_ids = {r["article_id"] for r in (linked.data or [])}
    to_label = [a for a in articles if a["id"] not in linked_ids][:LABEL_BATCH]

    if not to_label:
        log("[label] All translated articles already labelled.")
        return 0

    log(f"[label] {len(to_label)} articles to label.")
    labelled = 0

    for art in to_label:
        title = art.get("translated_title") or art.get("original_title") or ""
        content = art.get("original_content") or ""
        summary = art.get("translated_summary") or ""

        try:
            result = extract_stakeholders(title, content, summary)
        except Exception as e:
            log(f"  [label] WARN: {title[:50]!r}: {e}")
            time.sleep(GEMINI_DELAY)
            continue

        if not result:
            time.sleep(GEMINI_DELAY)
            continue

        name_to_id: dict[str, str] = {}
        for s in (result.get("stakeholders") or []):
            name = (s.get("name") or "").strip()
            if not name:
                continue
            try:
                sid = upsert_stakeholder(
                    name=name,
                    name_zh=s.get("name_zh"),
                    type_=s.get("type", "institution"),
                    description=s.get("description"),
                )
                name_to_id[name] = sid
                link_article_stakeholder(art["id"], sid, s.get("role_in_article"), None)
            except Exception as e:
                log(f"  [label] WARN stakeholder '{name}': {e}")

        for r in (result.get("relations") or []):
            from_id = name_to_id.get((r.get("from_name") or "").strip())
            to_id = name_to_id.get((r.get("to_name") or "").strip())
            rtype = (r.get("relation_type") or "").strip()
            if from_id and to_id and rtype:
                try:
                    upsert_relation(from_id, to_id, rtype, art["id"], r.get("description"))
                except Exception as e:
                    log(f"  [label] WARN relation: {e}")

        log(f"  [OK] {title[:60]!r} — {len(name_to_id)} stakeholders")
        labelled += 1
        time.sleep(GEMINI_DELAY)

    log(f"[label] Done — {labelled} labelled.")
    return labelled


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_once() -> None:
    log("── pipeline run starting ──")
    c = classify_batch()
    t = translate_batch()
    l = label_batch()
    log(f"── run complete: {c} classified, {t} translated, {l} labelled ──")


def main() -> None:
    log("=== SinoArk Pipeline started (hourly) ===")
    while True:
        try:
            run_once()
        except Exception as e:
            log(f"ERROR in pipeline run: {e}")
        log(f"Sleeping {LOOP_INTERVAL // 60} min until next run...")
        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    main()
