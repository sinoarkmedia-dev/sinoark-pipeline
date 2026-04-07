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
    """Fetch articles in the time window from Supabase."""
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
        .order('published_at', desc=True)
        .execute()
    )

    articles = res.data or []
    print(f'  → Found {len(articles)} articles in window')
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
    "WRITING PHILOSOPHY:\n"
    "- AI is a powerful general-purpose technology that is genuinely changing many aspects "
    "of business, daily life, and social structure — but it is evolving steadily, not "
    "explosively. Do not create FOMO or treat AI as an almighty force. Avoid hype and "
    "breathless superlatives.\n"
    "- Your goal is to record and recognize real AI progress from a pragmatic standpoint: "
    "anchor every claim in actual user cases, commercial outcomes, measurable data, or "
    "concrete social change. If evidence is thin, say so.\n"
    "- Demystify jargon. When you use technical or trending terms (e.g. 'inference scaling', "
    "'RAG', 'multimodal'), briefly put them in plain business or daily-life terms so a "
    "curious non-technical reader — or even an AI skeptic — can immediately grasp why it "
    "matters and how it might affect them.\n"
    "- Write with authority, nuance, and appropriate skepticism. Use inline hyperlinks generously."
)

DIGEST_PROMPT_TEMPLATE = """\
Today is {today_bj} (Beijing time). You are writing the SinoArk Daily AI Digest.

The digest covers articles published between {start_label} and {end_label} (Beijing time).
These articles are from Chinese WeChat official accounts covering AI, technology, and industry.

ARTICLES ({num_articles} articles):
{articles_text}

---

Write a comprehensive Daily AI Digest in English as structured HTML.

REQUIREMENTS:
- Write in HTML only (no markdown, no <html>/<head>/<body> wrapper tags)
- Use <h2> for section headings (include the emoji prefix)
- Use <p>, <ul>, <li>, <strong>, <em> for content
- For EVERY article reference, use a hyperlink: <a href="URL" target="_blank" rel="noopener">anchor text</a>
- Each section must reference 2–5 of the most relevant articles with hyperlinks
- Be analytical and grounded, not hype-driven. No FOMO language ("revolutionary", "game-changer", "AGI is here").
- Ground every point in real cases, commercial outcomes, or data — not just announcements or promises
- When you use a technical term or buzzword, immediately follow it with a plain-language parenthetical so non-technical readers understand the actual impact
- An AI skeptic reading this should finish each section feeling informed and connected — not alarmed or left behind
- Show implications for AI development in China and globally, with measured perspective on pace and scale

SECTIONS TO WRITE (keep each section concise — total ~800 words target):

<h2>🔑 Three Keywords Today</h2>
3 terms or themes dominating today's AI discourse. Per keyword (2-3 sentences):
define it in plain language first (what it actually means for a business or person),
then why it is trending today (with hyperlink), and what practical implication it carries.
Use 3 numbered <li> items. ~180 words max for this section.

<h2>🏢 Big Company Updates</h2>
Bullet <li> list of major company moves. Per item: 1–2 sentences + hyperlink.
Chinese AI companies first, then global players. ~180 words max.

<h2>👤 Key Persons</h2>
Bullet <li> list. Per person: name + what they said/did + hyperlink.
~120 words max.

<h2>🏭 AI in Industry: Case Studies</h2>
Up to 5 of the most concrete AI deployments or applications. Per case (2-3 sentences):
what industry, what specific problem is being solved, what measurable result or commercial signal exists,
and — in plain words — what this means for workers, customers, or the business.
Skip cases that are pure announcements with no real-world evidence. ~220 words max.

<h2>⚡ Noteworthy</h2>
ONLY if significant: breakthroughs, cybersecurity, ethics, regulation, major events.
Max 3 bullet points. If nothing: <p><em>Nothing major today.</em></p>

OUTPUT: HTML only. No wrapper tags. Start directly with first <h2>. No preamble.
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


def generate_digest_html(articles: list[dict], window_start: datetime, window_end: datetime, dry_run: bool = False) -> str:
    """Call Gemini to generate the digest HTML."""

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
        return '<p><em>Dry run — no Gemini call made.</em></p>'

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

    return raw.strip()


# ── Output ────────────────────────────────────────────────────────────────────

def save_digest(date_label: str, html_content: str, articles_used: list[dict], window_start: datetime, window_end: datetime):
    """Save digest to public/digests/<date>.json and update latest.json."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Build article index for the digest metadata
    article_index = [
        {
            'title': a.get('original_title', ''),
            'url': a.get('original_url', ''),
            'source': a.get('source_name') or '',
            'published_at': a.get('published_at', ''),
        }
        for a in articles_used
    ]

    payload = {
        'date': date_label,
        'window_start': window_start.isoformat(),
        'window_end': window_end.isoformat(),
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'articles_count': len(articles_used),
        'html_content': html_content,
        'articles': article_index,
    }

    # Save dated file
    dated_path = OUTPUT_DIR / f'{date_label}.json'
    dated_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f'\nSaved: {dated_path}')

    # Update latest.json
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
    html_content = generate_digest_html(selected, window_start, window_end, dry_run=args.dry_run)

    if not args.dry_run:
        result = save_digest(date_label, html_content, selected, window_start, window_end)
        print(f'\nDigest generated successfully!')
        print(f'  Articles used: {result["articles_count"]}')
        print(f'  Output: {OUTPUT_DIR}/{date_label}.json')
        print(f'  HTML preview:')
        print(html_content[:500] + '...')
    else:
        print('\nDry run complete. No files written.')


if __name__ == '__main__':
    main()
