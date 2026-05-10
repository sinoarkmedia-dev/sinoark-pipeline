#!/usr/bin/env python3
"""
SinoArk Daily AI Digest Generator
===================================
Fetches articles from the 24-hour window ending at 11pm Beijing time,
uses Gemini to generate a structured English digest, and saves it as JSON.

The digest covers:
  1. Three Keywords Today — what's dominating AI discourse
  2. Big Companies Update — products, tech, investments
  3. Key Persons — who's saying / doing what
  4. AI in Industry — up to 5 case studies (real results)
  5. Noteworthy (optional) — breakthroughs, regulation, events

Each section embeds hyperlinks to the source articles.

Usage:
  python3 generate_digest.py                  # Digest for today's 11pm Beijing window
  python3 generate_digest.py --date 2026-04-01 # Digest for a specific date
  python3 generate_digest.py --dry-run        # Print prompt, don't call Gemini

Output:
  /root/sinoark-web/public/digests/<date>.json
  (Symlinked to latest.json)
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google import genai
from google.genai import types
from supabase import create_client

load_dotenv('/root/workspace/sinoark_media_wechat_pipeline/.env')

# ── Config ──────────────────────────────────────────────────────────────────
SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
GEMINI_API_KEY = os.environ['GEMINI_API_KEY']

BEIJING_TZ = timezone(timedelta(hours=8))
OUTPUT_DIR = Path('/root/sinoark-web/public/digests')
WECHAT_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
)
MAX_ARTICLES_FOR_GEMINI = 80
CONTENT_PREVIEW_LEN = 600  # chars per article sent to Gemini


# ── Time window ──────────────────────────────────────────────────────────────

def get_digest_window(target_date: Optional[str] = None) -> tuple[datetime, datetime, str]:
    """
    Returns (window_start_utc, window_end_utc, date_label).
    Window: 11pm Beijing (target_date - 1) → 11pm Beijing (target_date).
    """
    if target_date:
        target = datetime.strptime(target_date, '%Y-%m-%d').replace(tzinfo=BEIJING_TZ)
    else:
        now_bj = datetime.now(BEIJING_TZ)
        # Use today if it's past 11pm, else use yesterday
        if now_bj.hour >= 23:
            target = now_bj.replace(hour=23, minute=0, second=0, microsecond=0)
        else:
            target = (now_bj - timedelta(days=1)).replace(hour=23, minute=0, second=0, microsecond=0)

    end_bj = target.replace(hour=23, minute=0, second=0, microsecond=0)
    start_bj = end_bj - timedelta(hours=24)

    return start_bj.astimezone(timezone.utc), end_bj.astimezone(timezone.utc), target.strftime('%Y-%m-%d')


# ── Article fetching ─────────────────────────────────────────────────────────

def fetch_articles_from_supabase(client, start_utc: datetime, end_utc: datetime) -> list[dict]:
    """Fetch articles in the time window from Supabase.

    Filter: about_ai=True AND china_related=True. The digest is *only* about
    Chinese AI subjects — not general AI concepts and not pure US/EU coverage.
    """
    print(f'Fetching articles: {start_utc.strftime("%Y-%m-%d %H:%M")} → {end_utc.strftime("%Y-%m-%d %H:%M")} UTC')

    res = (
        client.table('articles_digest')
        .select(
            'id, original_title, original_content, original_url, published_at, '
            'translated_summary, about_ai, about_tech, china_related, theme, '
            'keywords, key_entities, source_id, source_name'
        )
        .gte('published_at', start_utc.isoformat())
        .lt('published_at', end_utc.isoformat())
        .eq('about_ai', True)
        .eq('china_related', True)
        .order('published_at', desc=True)
        .execute()
    )

    articles = res.data or []
    print(f'  → Found {len(articles)} China-AI articles in window')
    return articles


def fetch_wechat_content(url: str) -> Optional[str]:
    """Fetch text content from a WeChat article URL."""
    try:
        r = requests.get(
            url, timeout=12,
            headers={
                'User-Agent': WECHAT_UA,
                'Referer': 'https://mp.weixin.qq.com/',
                'Accept': 'text/html,application/xhtml+xml',
                'Accept-Language': 'zh-CN,zh;q=0.9',
            }
        )
        if not r.ok:
            return None
        soup = BeautifulSoup(r.text, 'html.parser')
        content_div = soup.find(id='js_content')
        if content_div:
            return content_div.get_text(separator=' ', strip=True)[:4000]
    except Exception:
        pass
    return None


def enrich_with_content(articles: list[dict], max_fetch: int = 30) -> list[dict]:
    """
    For articles missing original_content, try to fetch from WeChat URL.
    Limits to max_fetch to avoid rate limiting.
    """
    fetched = 0
    for art in articles:
        if fetched >= max_fetch:
            break
        content = art.get('original_content') or ''
        if len(content) < 200 and art.get('original_url'):
            print(f'  Fetching content for: {art["original_title"][:50]}...')
            content = fetch_wechat_content(art['original_url'])
            if content:
                art['original_content'] = content
                fetched += 1
                time.sleep(0.8)

    return articles


# ── Article prioritization ────────────────────────────────────────────────────

def prioritize_articles(articles: list[dict], limit: int = MAX_ARTICLES_FOR_GEMINI) -> list[dict]:
    """
    Select the most relevant articles for the digest.
    Priority: has content > about_ai > about_tech > recent
    """
    def score(a):
        s = 0
        if len(a.get('original_content') or '') > 200:
            s += 30
        if a.get('about_ai'):
            s += 20
        if a.get('about_tech'):
            s += 10
        if a.get('translated_summary'):
            s += 5
        return s

    sorted_arts = sorted(articles, key=score, reverse=True)
    return sorted_arts[:limit]


# ── Gemini digest generation ──────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a senior China AI analyst and technology journalist writing for an "
    "international English-speaking audience. You have deep expertise in Chinese AI "
    "companies, policy, research, and industry applications.\n\n"
    "SCOPE — IMPORTANT:\n"
    "- This digest is exclusively about CHINA — Chinese AI companies, Chinese AI products, "
    "Chinese AI people, Chinese policy and markets. Even when a foreign player (OpenAI, "
    "Anthropic, Nvidia, etc.) appears in the source articles, you only cover them through "
    "the lens of their direct interaction with China (regulatory action, JV, market entry, "
    "competitive response from a Chinese player).\n"
    "- If an article is purely about a non-Chinese company with no China angle, ignore it.\n\n"
    "WRITING PHILOSOPHY:\n"
    "- AI is a powerful general-purpose technology that is genuinely changing many aspects "
    "of business, daily life, and social structure — but it is evolving steadily, not "
    "explosively. Do not create FOMO or treat AI as an almighty force. Avoid hype and "
    "breathless superlatives.\n"
    "- Anchor every claim in actual user cases, commercial outcomes, measurable data, or "
    "concrete social change. If evidence is thin, say so.\n"
    "- Demystify jargon. When you use technical or trending terms (e.g. 'inference scaling', "
    "'RAG', 'multimodal'), briefly put them in plain business or daily-life terms so a "
    "curious non-technical reader — or even an AI skeptic — can immediately grasp why it "
    "matters and how it might affect them.\n"
    "- Write with authority, nuance, and appropriate skepticism. Use inline hyperlinks generously."
)

DIGEST_PROMPT_TEMPLATE = """\
Today is {today_bj} (Beijing time). You are writing the SinoArk Daily China-AI Digest.

The digest covers articles published between {start_label} and {end_label} (Beijing time).
These articles have already been pre-filtered to be specifically about CHINA-related
AI subjects (Chinese companies, Chinese people, Chinese policy, Chinese markets).

ARTICLES ({num_articles} articles):
{articles_text}

---

Write a Daily China-AI Digest in English as structured HTML, using EXACTLY the four
sections below in order.

GLOBAL REQUIREMENTS:
- HTML only, no markdown, no <html>/<head>/<body> wrapper tags
- Use <h2> for section headings (with emoji), <h3> for sub-headings inside Deep Dive and Profiles
- Use <p>, <ul>, <ol>, <li>, <strong>, <em> for content
- For EVERY article reference, use a hyperlink: <a href="URL" target="_blank" rel="noopener">anchor text</a>
- Be analytical and grounded; no FOMO language; ground every point in real cases or data
- Plain-language gloss when introducing technical terms
- Total target: ~900 words across all four sections

SECTION 1 — TRENDING
<h2>📈 Top 10 Trending Topics</h2>
Identify the TOP 10 distinct topics that dominate today's articles. A "topic" is a
tight theme (e.g., "DeepSeek-V4 release", "Beijing AI safety draft regulation",
"Bytedance Doubao enterprise rollout"), not a broad category like "AI chips".
Render as an <ol> with one <li> per topic, ordered by significance (#1 is the most
prominent). Per <li>: 2–3 sentences total —
"<strong>Topic name</strong> — what happened today (with at least one inline
hyperlink to a source article), who is involved (named Chinese players), and the
single most important implication." Aim for ~50–70 words per item.
If fewer than 10 distinct topics genuinely exist in the article set, list as many
real topics as you can (minimum 5) and end the <ol> early — do NOT pad with weak
or duplicate items.

SECTION 2 — DEEP DIVE
Pick the TOP 1–2 topics from Section 1 that warrant deeper analysis (a major launch,
a meaningful regulatory move, a notable consolidation, etc.). For each, produce:
<h2>🔍 Deep Dive: [Topic name]</h2>
2–3 short paragraphs (~120–160 words per topic). Synthesize across the relevant
articles: what happened, who is involved, commercial or policy implication, and the
broader China-AI context (how this fits with prior moves by the same player or with
regulator behavior). Hyperlink at least 2 source articles inline.
Avoid restating the Trending entry — go deeper.

SECTION 3 — TODAY'S NEWS
<h2>📰 Today's News</h2>
A bulleted <ul> of OTHER notable China-AI news from today that did not become a Deep Dive
topic. Each <li>: 1 sentence + hyperlink. Aim for 6–10 bullets covering distinct stories
across companies, products, policy, funding, infrastructure, and applications. Order by
significance, not chronology. Skip near-duplicates of Deep Dive items.

SECTION 4 — PROFILES
<h2>👥 Profiles</h2>
Pick exactly ONE Chinese company and ONE Chinese person who feature prominently in
today's articles. STRICT RULES:
  - "Chinese company" = headquartered in mainland China, Hong Kong, Macau, or
    a Chinese state-affiliated entity. Foreign multinationals (OpenAI, Anthropic,
    Google, DeepMind, Meta, Microsoft, Apple, Tesla, Nvidia, etc.) DO NOT QUALIFY,
    even when prominent in today's coverage.
  - "Chinese person" = Chinese national or person primarily affiliated with a
    Chinese institution / company. Foreign executives (Sam Altman, Dario Amodei,
    Demis Hassabis, Sundar Pichai, Satya Nadella, Tim Cook, Mark Zuckerberg,
    Elon Musk, Jensen Huang, etc.) DO NOT QUALIFY, even if mentioned today.
  - If no clear Chinese candidate exists in today's articles for a given category,
    write "<h3>Company to Know: Not applicable today</h3>" (or the person variant)
    followed by a single-sentence <p> explaining briefly. Do NOT substitute a
    foreign entity to fill the slot.

<h3>Company to Know: [Chinese name + English transliteration if useful]</h3>
2–3 sentences. What they do, why they matter today, one concrete recent move
(hyperlink the source article that shows it). Plain-language gloss on any jargon.

<h3>Person to Know: [Name + Chinese characters if useful]</h3>
2–3 sentences. Their role, their Chinese company / institution, what they said or
did today (hyperlinked source). Prefer someone whose action is concrete (launch,
statement, deal) over a passive mention.

OUTPUT: HTML only. No wrapper tags. Start directly with the first <h2>. No preamble or
trailing remarks.
"""


def build_articles_text(articles: list[dict]) -> str:
    """Format articles as numbered list for the Gemini prompt."""
    lines = []
    for i, art in enumerate(articles, 1):
        source_info = art.get('source_name') or ''

        pub_bj = ''
        if art.get('published_at'):
            try:
                dt = datetime.fromisoformat(art['published_at'].replace('Z', '+00:00'))
                pub_bj = dt.astimezone(BEIJING_TZ).strftime('%m-%d %H:%M BJ')
            except Exception:
                pub_bj = art['published_at'][:16]

        content = art.get('original_content') or art.get('translated_summary') or ''
        content_preview = content[:CONTENT_PREVIEW_LEN].strip()
        if len(content) > CONTENT_PREVIEW_LEN:
            content_preview += '...'

        lines.append(
            f'[{i}] {art["original_title"]}\n'
            f'    Source: {source_info} | Published: {pub_bj}\n'
            f'    URL: {art["original_url"]}\n'
            f'    Content: {content_preview or "(no content)"}\n'
        )
    return '\n'.join(lines)


def generate_digest_html(articles: list[dict], window_start: datetime, window_end: datetime, dry_run: bool = False) -> tuple[str, str]:
    """Call Gemini to generate the digest HTML. Returns (html, prompt)."""

    today_bj = datetime.now(BEIJING_TZ).strftime('%A, %B %-d, %Y')
    start_label = window_start.astimezone(BEIJING_TZ).strftime('%I:%M %p %B %-d')
    end_label = window_end.astimezone(BEIJING_TZ).strftime('%I:%M %p %B %-d')

    articles_text = build_articles_text(articles)

    prompt = DIGEST_PROMPT_TEMPLATE.format(
        today_bj=today_bj,
        start_label=start_label,
        end_label=end_label,
        num_articles=len(articles),
        articles_text=articles_text,
    )

    if dry_run:
        print('\n' + '=' * 60)
        print('DRY RUN — Gemini prompt:')
        print('=' * 60)
        print(prompt[:3000])
        print('...(truncated)')
        print('=' * 60)
        return '<p><em>Dry run — no Gemini call made.</em></p>', prompt

    print(f'\nCalling Gemini with {len(articles)} articles...')
    client = genai.Client(api_key=GEMINI_API_KEY)

    response = client.models.generate_content(
        model='gemini-2.5-flash',
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.4,
            max_output_tokens=8192,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
        contents=prompt,
    )

    raw = response.text.strip()

    # Strip markdown fences and any HTML wrapper Gemini may add
    raw = re.sub(r'^```(?:html)?\s*', '', raw)
    raw = re.sub(r'\s*```\s*$', '', raw)
    # Remove <html>/<head>/<body> wrapper if present
    raw = re.sub(r'<!DOCTYPE[^>]*>', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'<html[^>]*>|</html>', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'<head[\s\S]*?</head>', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'<body[^>]*>|</body>', '', raw, flags=re.IGNORECASE)
    # Trim to first <h2>
    m = re.search(r'<h2', raw)
    if m:
        raw = raw[m.start():]

    return raw.strip(), prompt


# ── Output ────────────────────────────────────────────────────────────────────

def save_digest(date_label: str, html_content: str, articles_used: list[dict],
                 window_start: datetime, window_end: datetime, llm_prompt: str):
    """Save digest to Supabase digests table and local JSON files."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Build article index for the digest metadata
    article_index = [
        {
            'id': a.get('id', ''),
            'title': a.get('original_title', ''),
            'url': a.get('original_url', ''),
            'source_name': a.get('source_name') or '',
            'published_at': a.get('published_at', ''),
        }
        for a in articles_used
    ]

    generated_at = datetime.now(timezone.utc).isoformat()

    # Save to Supabase digests table
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    row = {
        'digest_date': date_label,
        'window_start': window_start.isoformat(),
        'window_end': window_end.isoformat(),
        'html_content': html_content,
        'articles_count': len(articles_used),
        'generated_at': generated_at,
        'source_articles': article_index,
        'llm_prompt': llm_prompt,
    }
    try:
        client.table('digests').upsert(row, on_conflict='digest_date').execute()
        print(f'\nSaved to Supabase digests table (date={date_label})')
    except Exception as e:
        print(f'\nWARN: Failed to save to Supabase digests table: {e}')

    # Save local JSON files (fallback for Vercel static serving)
    payload = {
        'date': date_label,
        'window_start': window_start.isoformat(),
        'window_end': window_end.isoformat(),
        'generated_at': generated_at,
        'articles_count': len(articles_used),
        'html_content': html_content,
        'articles': article_index,
    }

    dated_path = OUTPUT_DIR / f'{date_label}.json'
    dated_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f'Saved: {dated_path}')

    latest_path = OUTPUT_DIR / 'latest.json'
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f'Updated: {latest_path}')

    return payload


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Generate SinoArk Daily AI Digest')
    parser.add_argument('--date', help='Target date YYYY-MM-DD (default: today/yesterday based on current time)')
    parser.add_argument('--dry-run', action='store_true', help='Print prompt without calling Gemini')
    parser.add_argument('--no-fetch', action='store_true', help='Skip fetching missing content from WeChat URLs')
    parser.add_argument('--max-articles', type=int, default=MAX_ARTICLES_FOR_GEMINI, help=f'Max articles to send Gemini (default: {MAX_ARTICLES_FOR_GEMINI})')
    args = parser.parse_args()

    # Determine time window
    window_start, window_end, date_label = get_digest_window(args.date)

    print(f'SinoArk Digest Generator')
    print(f'Date: {date_label}')
    print(f'Window: {window_start.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M BJ")} → {window_end.astimezone(BEIJING_TZ).strftime("%Y-%m-%d %H:%M BJ")}')
    print()

    # Fetch articles
    client = create_client(SUPABASE_URL, SUPABASE_KEY)
    articles = fetch_articles_from_supabase(client, window_start, window_end)

    if not articles:
        print('No articles found in window. Try a different date or run sync_rss_april2.py first.')
        sys.exit(1)

    # Enrich with content from WeChat URLs
    if not args.no_fetch:
        print(f'\nEnriching articles with content from WeChat URLs...')
        articles = enrich_with_content(articles, max_fetch=25)

    # Prioritize and limit articles for Gemini
    selected = prioritize_articles(articles, limit=args.max_articles)
    has_content = sum(1 for a in selected if len(a.get('original_content') or '') > 200)
    print(f'\nSelected {len(selected)} articles for digest ({has_content} with content)')

    if len(selected) < 5:
        print('Warning: Very few articles found. Digest quality may be limited.')

    # Generate digest
    html_content, llm_prompt = generate_digest_html(selected, window_start, window_end, dry_run=args.dry_run)

    if not args.dry_run:
        result = save_digest(date_label, html_content, selected, window_start, window_end, llm_prompt)
        print(f'\nDigest generated successfully!')
        print(f'  Articles used: {result["articles_count"]}')
        print(f'  Output: {OUTPUT_DIR}/{date_label}.json')
        print(f'  HTML preview:')
        print(html_content[:500] + '...')
    else:
        print('\nDry run complete. No files written.')


if __name__ == '__main__':
    main()
