#!/usr/bin/env python3
"""
SinoArk Daily Profile Generator
=================================
Every day, finds the most-talked-about company, person, and product from
today's articles_digest, then calls Claude Haiku (headless) to write a
~300-word English HTML profile for each.

Deduplication: skips subjects already profiled in the last 6 months.

Usage:
  python3 generate_profiles.py                   # today's window
  python3 generate_profiles.py --date 2026-04-04 # specific date
  python3 generate_profiles.py --dry-run         # identify subjects, print prompts, skip Claude + DB
  python3 generate_profiles.py --category company # only one category
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from dotenv import load_dotenv
from google import genai
from google.genai import types
from supabase import create_client

load_dotenv('/root/workspace/sinoark_media_wechat_pipeline/.env')

# ── Config ───────────────────────────────────────────────────────────────────
SUPABASE_URL     = os.environ['SUPABASE_URL']
SUPABASE_KEY     = os.environ['SUPABASE_SERVICE_KEY']
GEMINI_API_KEY   = os.environ['GEMINI_API_KEY']

BEIJING_TZ       = timezone(timedelta(hours=8))
CATEGORIES       = ['company', 'person', 'product']
DEDUP_DAYS       = 180   # avoid repeating a subject for 6 months
MAX_ARTICLES_IN  = 120   # max articles fed to Gemini for subject selection
ARTICLE_PREVIEW  = 300   # chars of content per article shown to Gemini
MAX_CONTEXT_ARTS = 8     # max articles shown to Claude per profile


# ── Supabase helpers ─────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def db():
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_todays_articles(date_str: str) -> list[dict]:
    """Fetch China-AI articles in the 24h window ending 11pm BJT on date_str.

    Matches the digest pipeline's scope: about_ai=True AND china_related=True.
    Without the china_related filter, the daily company/person/product picks
    drift to foreign entities (OpenAI, Sam Altman, etc.) reported on by
    Chinese sources, which conflicts with the SinoArk China-only positioning.
    """
    target = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=BEIJING_TZ)
    end_bj = target.replace(hour=23, minute=0, second=0, microsecond=0)
    start_bj = end_bj - timedelta(hours=24)
    start_utc = start_bj.astimezone(timezone.utc).isoformat()
    end_utc   = end_bj.astimezone(timezone.utc).isoformat()

    print(f'Fetching articles: {start_bj.strftime("%Y-%m-%d %H:%M BJ")} → {end_bj.strftime("%Y-%m-%d %H:%M BJ")}')
    res = (
        db().table('articles_digest')
        .select('id, original_title, original_content, translated_summary, original_url, published_at, source_name')
        .gte('published_at', start_utc)
        .lt('published_at', end_utc)
        .eq('about_ai', True)
        .eq('china_related', True)
        .order('published_at', desc=True)
        .limit(MAX_ARTICLES_IN)
        .execute()
    )
    articles = res.data or []
    print(f'  → {len(articles)} China-AI articles found')
    return articles


MIGRATION_SQL = """
-- Run this once in the Supabase SQL editor (https://supabase.com/dashboard/project/fbjpgaqoldptjnejrbeh/sql)
CREATE TABLE IF NOT EXISTS public.profiles (
    id           bigserial PRIMARY KEY,
    date         date         NOT NULL,
    category     text         NOT NULL CHECK (category IN ('company', 'person', 'product')),
    subject      text         NOT NULL,
    subject_zh   text,
    html_content text         NOT NULL,
    source_articles jsonb     DEFAULT '[]'::jsonb,
    article_count integer     DEFAULT 0,
    generated_at timestamptz  DEFAULT now(),
    UNIQUE (date, category)
);
CREATE INDEX IF NOT EXISTS idx_profiles_date     ON public.profiles (date DESC);
CREATE INDEX IF NOT EXISTS idx_profiles_category ON public.profiles (category);
CREATE INDEX IF NOT EXISTS idx_profiles_subject  ON public.profiles (lower(subject));
""".strip()


def check_table_exists() -> bool:
    """Return True if profiles table exists, False otherwise."""
    try:
        db().table('profiles').select('id').limit(1).execute()
        return True
    except Exception as e:
        if 'Could not find the table' in str(e) or 'PGRST205' in str(e):
            return False
        raise


def fetch_recent_subjects() -> list[str]:
    """Return list of subject names (lower-cased) profiled in the last DEDUP_DAYS days."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DEDUP_DAYS)).date().isoformat()
    res = (
        db().table('profiles')
        .select('subject, subject_zh')
        .gte('date', cutoff)
        .execute()
    )
    names = []
    for row in (res.data or []):
        if row.get('subject'):
            names.append(row['subject'].lower())
        if row.get('subject_zh'):
            names.append(row['subject_zh'].lower())
    return names


def save_profile(date_str: str, category: str, subject: str, subject_zh: str | None,
                 html_content: str, source_articles: list[dict]) -> None:
    """Upsert a single profile into the profiles table."""
    db().table('profiles').upsert({
        'date': date_str,
        'category': category,
        'subject': subject,
        'subject_zh': subject_zh,
        'html_content': html_content,
        'source_articles': source_articles,
        'article_count': len(source_articles),
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }, on_conflict='date,category').execute()


# ── Step 1: Identify top company / person / product via Gemini ───────────────

IDENTIFY_SYSTEM = (
    "You are a China AI/tech news analyst. You extract factual entity names from article data. "
    "Return only valid JSON, no explanation, no markdown fences."
)

IDENTIFY_PROMPT = """\
Today is {date_bj}. Below are {num_articles} Chinese AI/technology news articles
published today, pre-filtered to be specifically about CHINA.

Identify exactly ONE subject per category that is most prominently discussed
across multiple articles. STRICT RULES:
- company: a CHINESE business organization headquartered in mainland China,
   Hong Kong, Macau, or a Chinese state-affiliated entity (e.g. ByteDance,
   Huawei, Baidu, Alibaba, DeepSeek, Tencent, Xiaomi, BYD, SMIC).
   Foreign multinationals (OpenAI, Anthropic, Google, DeepMind, Meta, Microsoft,
   Apple, Tesla, Nvidia, etc.) DO NOT QUALIFY.
- person: a CHINESE individual or someone primarily affiliated with a Chinese
   institution (e.g. Ren Zhengfei, Lei Jun, Robin Li, Liang Wenfeng, Yang Zhilin).
   Foreign executives (Sam Altman, Dario Amodei, Demis Hassabis, Sundar Pichai,
   Tim Cook, Elon Musk, Jensen Huang, etc.) DO NOT QUALIFY.
- product: a CHINESE-developed software, AI model, hardware device, or named
   service (e.g. DeepSeek-V3, Doubao, Qwen, Wenxin, HarmonyOS, Kunlun chip).
   Non-Chinese products (ChatGPT, Claude, Sora, iPhone) DO NOT QUALIFY.

If no clear Chinese candidate exists for a category, set "name" to null and
"reason" to a short explanation. Do NOT substitute a foreign entity to fill
the slot.

EXCLUDE these subjects (already profiled recently):
{excluded}

ARTICLES (numbered):
{articles_text}

Return ONLY this JSON structure (no other text):
{{
  "company": {{
    "name": "English name or null",
    "name_zh": "Chinese name or null",
    "article_indices": [1, 3, 5],
    "reason": "one sentence"
  }},
  "person": {{
    "name": "English name or null",
    "name_zh": "Chinese name or null",
    "article_indices": [2, 4],
    "reason": "one sentence"
  }},
  "product": {{
    "name": "English name or null",
    "name_zh": "Chinese name or null",
    "article_indices": [1, 6],
    "reason": "one sentence"
  }}
}}"""


def identify_subjects(articles: list[dict], excluded: list[str], date_str: str) -> dict:
    """Use Gemini Flash to identify today's top company, person, and product."""
    date_bj = datetime.strptime(date_str, '%Y-%m-%d').strftime('%A, %B %-d, %Y')
    excluded_str = ', '.join(excluded[:40]) if excluded else '(none)'

    lines = []
    for i, a in enumerate(articles[:MAX_ARTICLES_IN], 1):
        content = a.get('translated_summary') or a.get('original_content') or ''
        content = content[:ARTICLE_PREVIEW].strip()
        lines.append(
            f'[{i}] {a["original_title"]}\n'
            f'    Source: {a.get("source_name","")}\n'
            f'    Content: {content or "(none)"}'
        )

    prompt = IDENTIFY_PROMPT.format(
        date_bj=date_bj,
        num_articles=len(articles),
        excluded=excluded_str,
        articles_text='\n\n'.join(lines),
    )

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        config=types.GenerateContentConfig(
            system_instruction=IDENTIFY_SYSTEM,
            temperature=0.1,
            max_output_tokens=1024,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
        contents=prompt,
    )

    raw = response.text.strip()
    # Strip markdown fences if Gemini adds them
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```\s*$', '', raw)

    result = json.loads(raw)
    return result, articles  # return articles so we can index them


# ── Step 2: Generate profile HTML via Claude Haiku (headless) ────────────────

PROFILE_SYSTEM = """\
You are a senior China technology analyst writing for an international English-speaking audience.
Your writing is analytical, grounded, and skepticism-friendly — no hype, no "revolutionary", no "game-changing".
Every claim is anchored in what is actually happening: real deployments, measurable outcomes, or concrete statements.
When using technical terms, briefly explain them in plain language so a non-technical reader understands the relevance.
"""

PROFILE_PROMPT = """\
Write a ~300-word English profile of {subject} ({category_label}) based on these articles published today ({date_bj}).

{category_guidance}

FORMAT RULES:
- HTML only. Use <p> tags for paragraphs (2-4 paragraphs). Use <strong> for key terms/names.
- Use <a href="URL" target="_blank" rel="noopener">anchor text</a> to link to specific articles.
- No <html>/<head>/<body> wrapper. No markdown. Start directly with <p>.
- Target exactly ~300 words.

ARTICLES ({num_articles} articles about {subject}):
{articles_text}

Write the profile now:"""

CATEGORY_GUIDANCE = {
    'company': (
        "Structure: (1) what the company does and its position in the AI ecosystem, "
        "(2) what happened today / why it is in the news, "
        "(3) what this signals for the industry or for competitors."
    ),
    'person': (
        "Structure: (1) who this person is and their role/influence, "
        "(2) what they said or did today and the specific context, "
        "(3) why this matters — what it reveals about direction, priorities, or tensions."
    ),
    'product': (
        "Structure: (1) what this product is and what real problem it solves (plain language), "
        "(2) what is new or notable about it today — benchmark, deployment, update, or adoption, "
        "(3) what real-world use looks like and what it means for users or the market."
    ),
}

CATEGORY_LABELS = {
    'company': 'Company',
    'person': 'Person',
    'product': 'Product / Service',
}


def build_profile_prompt(subject: str, category: str, articles: list[dict], date_str: str) -> str:
    date_bj = datetime.strptime(date_str, '%Y-%m-%d').strftime('%A, %B %-d, %Y')
    lines = []
    for i, a in enumerate(articles, 1):
        content = a.get('original_content') or a.get('translated_summary') or ''
        content = content[:600].strip()
        lines.append(
            f'[{i}] {a["original_title"]}\n'
            f'    Source: {a.get("source_name","")}\n'
            f'    URL: {a.get("original_url","")}\n'
            f'    Content: {content or "(none)"}'
        )
    return PROFILE_PROMPT.format(
        subject=subject,
        category_label=CATEGORY_LABELS[category],
        date_bj=date_bj,
        category_guidance=CATEGORY_GUIDANCE[category],
        num_articles=len(articles),
        articles_text='\n\n'.join(lines),
    )


def call_claude_headless(prompt: str, model: str = 'haiku', dry_run: bool = False) -> str:
    """Call Claude Code headlessly and return the text response."""
    if dry_run:
        print('  [DRY RUN] Claude prompt (first 500 chars):')
        print('  ' + prompt[:500].replace('\n', '\n  '))
        print('  ...')
        return '<p><em>Dry run — no Claude call made.</em></p>'

    print('  Calling Claude Haiku...')
    result = subprocess.run(
        ['claude', '-p', prompt, '--model', model, '--output-format', 'text'],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f'Claude exited {result.returncode}: {result.stderr[:300]}')

    raw = result.stdout.strip()

    # Strip any markdown fences Claude might add
    raw = re.sub(r'^```(?:html)?\s*', '', raw)
    raw = re.sub(r'\s*```\s*$', '', raw)
    # Remove wrapper tags if present
    for tag in ['<!DOCTYPE[^>]*>', '<html[^>]*>', '</html>', '<head[\\s\\S]*?</head>',
                '<body[^>]*>', '</body>']:
        raw = re.sub(tag, '', raw, flags=re.IGNORECASE)
    # Trim to first <p>
    m = re.search(r'<p', raw, re.IGNORECASE)
    if m:
        raw = raw[m.start():]

    return raw.strip()


# ── Helpers ──────────────────────────────────────────────────────────────────

def articles_for_subject(all_articles: list[dict], indices: list[int]) -> list[dict]:
    """Return articles by 1-based indices from the identify step, capped at MAX_CONTEXT_ARTS."""
    selected = []
    for idx in indices:
        if 1 <= idx <= len(all_articles):
            selected.append(all_articles[idx - 1])
    # Deduplicate by id
    seen = set()
    out = []
    for a in selected:
        if a['id'] not in seen:
            seen.add(a['id'])
            out.append(a)
    return out[:MAX_CONTEXT_ARTS]


def make_source_articles(articles: list[dict]) -> list[dict]:
    return [
        {
            'id': a['id'],
            'title': a.get('original_title', ''),
            'url': a.get('original_url', ''),
            'source_name': a.get('source_name', ''),
        }
        for a in articles
    ]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Generate SinoArk Daily Profiles')
    parser.add_argument('--date', help='Target date YYYY-MM-DD (default: today)')
    parser.add_argument('--dry-run', action='store_true', help='Identify subjects and print prompts without calling Claude or writing to DB')
    parser.add_argument('--category', choices=CATEGORIES, help='Only generate one category')
    args = parser.parse_args()

    date_str = args.date or datetime.now(BEIJING_TZ).strftime('%Y-%m-%d')
    categories = [args.category] if args.category else CATEGORIES

    print(f'SinoArk Profile Generator — {date_str}')
    print(f'Categories: {", ".join(categories)}')
    print()

    # 0. Check table exists (skip for dry-run)
    if not args.dry_run and not check_table_exists():
        print('ERROR: profiles table does not exist in Supabase.')
        print()
        print('Run this SQL once in the Supabase SQL editor:')
        print('  https://supabase.com/dashboard/project/fbjpgaqoldptjnejrbeh/sql')
        print()
        print(MIGRATION_SQL)
        sys.exit(1)

    # 1. Fetch articles
    articles = fetch_todays_articles(date_str)
    if not articles:
        print('ERROR: No articles found for this date. Exiting.')
        sys.exit(1)

    # 2. Fetch recent subjects for dedup (skip if dry-run and table not yet created)
    print(f'Checking dedup window ({DEDUP_DAYS} days)...')
    if args.dry_run and not check_table_exists():
        excluded = []
        print('  → (table not yet created, no dedup)')
    else:
        excluded = fetch_recent_subjects()
        print(f'  → {len(excluded)} excluded subjects')

    # 3. Identify subjects via Gemini
    print('\nIdentifying top subjects via Gemini...')
    subjects, _ = identify_subjects(articles, excluded, date_str)
    for cat in CATEGORIES:
        name = subjects[cat]['name'] if subjects.get(cat) else None
        name_zh = subjects[cat].get('name_zh') or '' if subjects.get(cat) else ''
        reason = subjects[cat].get('reason', '—') if subjects.get(cat) else '—'
        status = name or '(none found)'
        print(f'  {cat:<8}: {status} ({name_zh}) — {reason}')

    if args.dry_run:
        print('\n[DRY RUN] Showing prompts only — no Claude calls, no DB writes.\n')

    # 4. Generate each profile
    results = []
    skipped = []
    for category in categories:
        info = subjects.get(category, {})
        subject    = info.get('name')
        subject_zh = info.get('name_zh') or None

        if not subject or subject.lower() in ('none', 'null', 'unknown', 'n/a'):
            print(f'\n── {category.upper()}: skipped (no subject identified) ──')
            skipped.append(category)
            continue
        indices    = info.get('article_indices', [])

        print(f'\n── {category.upper()}: {subject} ──────────────────')
        context_articles = articles_for_subject(articles, indices)
        if not context_articles:
            # Fall back to first few articles if indices missing
            context_articles = articles[:MAX_CONTEXT_ARTS]
        print(f'  Context articles: {len(context_articles)}')

        prompt = build_profile_prompt(subject, category, context_articles, date_str)
        html = call_claude_headless(prompt, model='haiku', dry_run=args.dry_run)

        if not args.dry_run:
            source_arts = make_source_articles(context_articles)
            save_profile(date_str, category, subject, subject_zh, html, source_arts)
            print(f'  Saved: {category}/{subject} ({len(html)} chars HTML)')
            results.append({'category': category, 'subject': subject})

        # Small delay between Claude calls to be respectful
        if not args.dry_run and category != categories[-1]:
            time.sleep(2)

    # 5. Summary
    print('\n' + '=' * 50)
    if args.dry_run:
        print('Dry run complete. No profiles written.')
    else:
        print(f'Done. {len(results)} profiles saved to Supabase for {date_str}:')
        for r in results:
            print(f'  [{r["category"]}] {r["subject"]}')
        if skipped:
            print(f'  Skipped (no subject identified): {", ".join(skipped)}')


if __name__ == '__main__':
    main()
