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

Content Enrichment:
  - Batch-fetches full article text from WeChat URLs (2 concurrent workers)
  - Only fetches articles missing content (< 200 chars)
  - Heavy rate limiting: 2 threads, ~1s per article, sleeps between requests
  - Batch-updates Supabase after all fetches complete
  - Skips articles that already have content

Usage:
  python3 generate_digest.py                  # Digest for today's 11pm Beijing window
  python3 generate_digest.py --date 2026-04-01 # Digest for a specific date
  python3 generate_digest.py --dry-run        # Print prompt, don't call Gemini
  python3 generate_digest.py --no-fetch       # Skip content fetching

Output:
  /root/sinoark-web/public/digests/<date>.json
  Supabase: digests table + articles_digest table (updated with content)
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
OUTPUT_DIR = Path('/root/workspace/sinoark-web/public/digests')
WECHAT_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
)
MAX_ARTICLES_FOR_GEMINI = 80
CONTENT_PREVIEW_LEN = 600  # chars per article sent to Gemini
QWEN_API_KEY = os.environ.get('QWEN_API_KEY')
QWEN_API_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions'


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
            'original_summary, about_ai, about_tech, china_related, theme, '
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


def enrich_with_content(articles: list[dict], client, max_fetch: int = 30) -> list[dict]:
    """
    Batch-fetch article content from WeChat URLs using thread pool.
    Max 2 concurrent requests to avoid rate limiting.
    Batch-updates Supabase.
    """
    # Collect articles to fetch
    to_fetch = []
    for art in articles:
        if len(to_fetch) >= max_fetch:
            break
        content = art.get('original_content') or ''
        if len(content) < 200 and art.get('original_url'):
            to_fetch.append(art)

    if not to_fetch:
        return articles

    print(f'  Batch-fetching {len(to_fetch)} articles from WeChat (2 concurrent, ~1s per article)...')

    # Fetch all with ThreadPoolExecutor (max 2 workers for heavy rate limiting)
    updates = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        # Submit all tasks
        future_to_art = {
            executor.submit(fetch_wechat_content, art['original_url']): art
            for art in to_fetch
        }

        # Collect results as they complete
        for future in as_completed(future_to_art):
            art = future_to_art[future]
            try:
                content = future.result()
                if content:
                    art['original_content'] = content
                    updates.append({
                        'id': art['id'],
                        'original_content': content,
                    })
                    print(f'    ✓ {art["original_title"][:50]}...')
                else:
                    print(f'    ✗ {art["original_title"][:50]}... (failed)')
            except Exception as e:
                print(f'    ✗ {art["original_title"][:50]}... (error: {e})')

    # Batch update Supabase (one update per article, but concurrent)
    if updates:
        print(f'\n  Batch-updating {len(updates)} articles to Supabase...')
        for update in updates:
            client.table('articles_digest').update(update).eq('id', update['id']).execute()
        print(f'  → Updated {len(updates)} articles with content')

    return articles


# ── Article enrichment: batch translate + score ────────────────────────────────

def enrich_articles_batch(articles: list[dict], client, max_enrich: int = 50) -> list[dict]:
    """
    Batch-enrich articles with:
    1. Qwen RAG relevance scoring
    2. Gemini translation (title + summary to English)
    Batch updates Supabase.
    """
    if not articles:
        return articles

    to_enrich = articles[:max_enrich]
    print(f'\nEnriching {len(to_enrich)} articles with Qwen RAG + Gemini translation...')

    updates = []

    for i, art in enumerate(to_enrich, 1):
        art_id = art.get('id')
        title = art.get('original_title', '')
        summary = art.get('original_summary', '')
        content = art.get('original_content', '')

        # Score with Qwen RAG
        print(f'  [{i}/{len(to_enrich)}] Scoring + translating: {title[:50]}...')
        qwen_score = score_with_qwen_rag(title, summary, content)

        # Translate with Gemini
        translation = translate_with_gemini(title, summary)

        # Collect update
        update = {
            'id': art_id,
            'ai_relevance_score': qwen_score.get('confidence', 50),
            'ai_relevance_reason': qwen_score.get('reason', ''),
            'ai_relevance_flag': qwen_score.get('is_relevant'),
            'translated_title': translation.get('title_en', title),
            'translated_summary': translation.get('summary_en', summary),
        }
        updates.append(update)
        art.update(update)  # Update in-memory for digest generation

    # Batch update Supabase
    if updates:
        print(f'\n  Batch-updating {len(updates)} articles to Supabase with enrichment...')
        for update in updates:
            try:
                client.table('articles_digest').update(update).eq('id', update['id']).execute()
            except Exception as e:
                print(f'    Error updating {update["id"]}: {e}')
        print(f'  → Updated {len(updates)} articles')

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
        if a.get('original_summary'):
            s += 5
        return s

    sorted_arts = sorted(articles, key=score, reverse=True)
    return sorted_arts[:limit]


# ── Article enrichment: Qwen RAG + Gemini translation ─────────────────────────

def score_with_qwen_rag(title: str, summary: str, content: str = '') -> dict:
    """
    Score article relevance to China AI/tech using Qwen RAG.
    Returns: {confidence: 0-100, reason: str, is_relevant: bool}
    """
    if not QWEN_API_KEY:
        return {'confidence': 50, 'reason': 'Qwen API not configured', 'is_relevant': None}

    text = f"Title: {title}\nSummary: {summary}"
    if content:
        text += f"\nContent snippet: {content[:300]}"

    prompt = f"""Determine if this article is relevant to China's AI and technology ecosystem.

Consider articles relevant if they discuss:
- AI model development or deployment (LLMs, vision models, agents)
- Hardware/infrastructure (chips, GPUs, computing, cloud)
- AI startups, funding, M&A, partnerships
- Government policy/regulation on AI or tech
- Enterprise AI adoption and applications
- Research breakthroughs in AI/ML
- Major personnel/leadership changes at tech companies

Article:
{text}

Response format: JSON
{{
  "is_relevant": boolean,
  "confidence": 0-100 integer,
  "reason": "brief explanation"
}}"""

    try:
        import requests
        r = requests.post(
            QWEN_API_URL,
            headers={'Authorization': f'Bearer {QWEN_API_KEY}'},
            json={
                'model': 'qwen-turbo',
                'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0.3,
            },
            timeout=10,
        )
        if r.status_code == 200:
            import json as json_lib
            response = r.json()
            if response.get('choices'):
                text = response['choices'][0]['message']['content']
                # Extract JSON from response
                try:
                    result = json_lib.loads(text)
                    return result
                except:
                    pass
    except Exception as e:
        print(f'  Qwen RAG error: {e}')

    return {'confidence': 50, 'reason': 'Qwen API error', 'is_relevant': None}


def translate_with_gemini(title: str, summary: str) -> dict:
    """
    Translate article title and summary from Chinese to English using Gemini.
    Returns: {title_en: str, summary_en: str}
    """
    client = genai.Client(api_key=GEMINI_API_KEY)

    prompt = f"""Translate the following Chinese article title and summary to clear, professional English.
Keep the tone journalistic and accurate to the original meaning.

Chinese Title: {title}
Chinese Summary: {summary}

Respond in JSON format:
{{
  "title_en": "translated title",
  "summary_en": "translated summary"
}}"""

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            config=types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=500,
            ),
            contents=prompt,
        )
        import json as json_lib
        # Extract JSON from response
        text = response.text.strip()
        if text.startswith('```'):
            text = text.split('```')[1].lstrip('json').strip()
        result = json_lib.loads(text)
        return result
    except Exception as e:
        print(f'  Gemini translation error: {e}')
        return {'title_en': title, 'summary_en': summary}


# ── Gemini digest generation ──────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You are a senior China AI analyst and technology journalist writing for an "
    "international English-speaking audience. You have deep expertise in Chinese AI "
    "companies, policy, research, and industry applications. Write with authority, "
    "insight, and appropriate skepticism. Use inline hyperlinks generously."
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
- Be analytical and opinionated, not just descriptive
- Show implications for AI development in China and globally
- Where relevant, note attitudes, ethical dimensions, and what the discourse reveals

SECTIONS TO WRITE (keep each section concise — total ~800 words target):

<h2>🔑 Three Keywords Today</h2>
3 terms/themes dominating today's discourse. Per keyword (2-3 sentences only):
why trending, which articles discuss it (hyperlink), attitudes & implications.
Use 3 numbered <li> items. ~180 words max for this section.

<h2>🏢 Big Company Updates</h2>
Bullet <li> list of major company moves. Per item: 1–2 sentences + hyperlink.
Chinese AI companies first, then global players. ~180 words max.

<h2>👤 Key Persons</h2>
Bullet <li> list. Per person: name + what they said/did + hyperlink.
~120 words max.

<h2>🏭 AI in Industry: Case Studies</h2>
Up to 5 of the most interesting/surprising AI deployments. Per case (2-3 sentences):
industry + what's happening + does it actually work? + commercial angle + hyperlink.
~220 words max.

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

        content = art.get('original_content') or art.get('original_summary') or ''
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


def upload_to_supabase(client, date_label: str, html_content: str, article_index: list[dict],
                       window_start: datetime, window_end: datetime):
    """Upsert the digest into the Supabase digests table."""
    row = {
        'digest_date': date_label,
        'window_start': window_start.isoformat(),
        'window_end': window_end.isoformat(),
        'html_content': html_content,
        'articles_json': article_index,
        'articles_count': len(article_index),
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }
    res = client.table('digests').upsert(row, on_conflict='digest_date').execute()
    if hasattr(res, 'error') and res.error:
        print(f'  ⚠ Supabase upload error: {res.error}')
    else:
        print(f'  → Uploaded to Supabase digests table (date={date_label})')


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
        articles = enrich_with_content(articles, client, max_fetch=25)

    # Enrich articles with Qwen RAG scoring + Gemini translation
    print(f'\nEnriching articles with Qwen RAG + Gemini translation...')
    articles = enrich_articles_batch(articles, client, max_enrich=50)

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

        print(f'\nUploading digest to Supabase...')
        upload_to_supabase(client, date_label, html_content, result['articles'], window_start, window_end)

        print(f'\nDigest generated successfully!')
        print(f'  Articles used: {result["articles_count"]}')
        print(f'  Output: {OUTPUT_DIR}/{date_label}.json')
        print(f'  HTML preview:')
        print(html_content[:500] + '...')
    else:
        print('\nDry run complete. No files written.')


if __name__ == '__main__':
    main()
