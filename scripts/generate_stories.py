#!/usr/bin/env python3
"""
Generate narrative stories for Sinoark based on daily digest.

Uses Gemini 2.5 Flash to create two ~2500-word English narrative stories
based on themes extracted from the daily AI digest.

Stories focus on real people/companies, decision-maker perspectives, and
human-centric angles aligned with "AI Demystifier" mission.
"""

import os
import json
import re
import sys
from datetime import datetime
from pathlib import Path
import asyncio
from typing import Optional
import html

# Load environment variables
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / "sinoark_media_wechat_pipeline" / ".env")

# Gemini API
from google import genai
from google.genai import types

# Supabase
from supabase import create_client, Client

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.5-flash"

if not all([SUPABASE_URL, SUPABASE_SERVICE_KEY, GEMINI_API_KEY]):
    raise ValueError("Missing required environment variables")

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────


def strip_html_tags(html_text: str) -> str:
    """Remove HTML tags and decode entities."""
    return html.unescape(re.sub(r"<[^>]+>", "", html_text))


def fetch_latest_digest() -> Optional[dict]:
    """Fetch the most recent digest from Supabase."""
    try:
        response = supabase.table("digests").select("*").order(
            "digest_date", desc=True
        ).limit(1).execute()
        if response.data:
            return response.data[0]
        return None
    except Exception as e:
        print(f"Error fetching digest: {e}")
        return None


def fetch_context_articles(limit: int = 20) -> list:
    """Fetch recent articles for additional context."""
    try:
        now = datetime.utcnow().isoformat()
        window_start = datetime.fromtimestamp(
            (datetime.utcnow().timestamp() - 86400)
        ).isoformat()

        response = supabase.table("articles_digest").select(
            "original_title, translated_title, translated_summary"
        ).gte("published_at", window_start).lte("published_at", now).eq(
            "about_ai", True
        ).order("published_at", desc=True).limit(limit).execute()

        return response.data or []
    except Exception as e:
        print(f"Error fetching context articles: {e}")
        return []


def generate_story_with_gemini(
    theme: str,
    perspective: str,
    digest_excerpt: str,
    context_articles: str,
    story_number: int
) -> Optional[dict]:
    """Generate a single story using Gemini."""

    prompt = f"""You are a world-class narrative journalist writing for "The AI Demystifier" —
a publication dedicated to demystifying AI for educated professionals through human-centric storytelling.

DIGEST CONTEXT:
{digest_excerpt}

ADDITIONAL ARTICLES TODAY:
{context_articles}

ASSIGNMENT (Story {story_number}):
Theme: {theme}
Narrative Perspective: {perspective}

STORY REQUIREMENTS:
- Length: 2500-2800 words
- Style: Narrative journalism, "fly-on-the-wall" or "decision-maker perspective"
- Tone: Anti-anxious, grounded, empowering. NO hype words ("mind-blowing", "insane", "revolutionary")
- Focus: HOW this technology is actually being used by real people and companies
- Structure: Hook → Context → Main narrative arc → Resolution/implications
- Language: Professional, clear, narrative-driven English

TONE GUIDELINES:
- Treat AI as a TOOL used by humans, not a force acting upon them
- Humanize the AI industry by embedding technical developments into social life
- Avoid corporate sycophancy and "End of the World" narratives
- Ground abstract concepts in concrete examples and real decision-makers

Write the complete story below, starting with a compelling headline and subtitle/deck.

FORMAT:
HEADLINE: [Your headline here]
DECK: [One-line subtitle/deck describing the story]

[Story body: 2500-2800 words]"""

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,
                max_output_tokens=4000,
                top_p=0.95,
            ),
        )
        return response.text if response.text else None
    except Exception as e:
        print(f"Error generating story {story_number}: {e}")
        return None


def parse_story_response(response_text: str) -> Optional[dict]:
    """Parse Gemini response into structured story data."""
    try:
        # Extract headline
        headline_match = re.search(r"HEADLINE:\s*(.+?)(?:\n|$)", response_text)
        headline = headline_match.group(1).strip() if headline_match else "Untitled"

        # Extract deck
        deck_match = re.search(r"DECK:\s*(.+?)(?:\n|$)", response_text)
        subtitle = deck_match.group(1).strip() if deck_match else ""

        # Extract body (everything after DECK)
        body_start = response_text.find("\n", response_text.find("DECK:") + 5) + 1
        content = response_text[body_start:].strip() if body_start > 0 else ""

        # Generate slug
        slug = re.sub(r"[^\w\s-]", "", headline.lower())
        slug = re.sub(r"[-\s]+", "-", slug)
        slug = slug.strip("-")[:80]

        word_count = len(content.split())

        return {
            "title": headline,
            "subtitle": subtitle,
            "content": content,
            "slug": slug,
            "word_count": word_count,
            "published_at": datetime.utcnow().isoformat(),
            "cover_image_url": None,
            "tags": None,
            "source_digest_ids": None,
        }
    except Exception as e:
        print(f"Error parsing story response: {e}")
        return None


def insert_story_to_supabase(story: dict) -> bool:
    """Insert a story into the Supabase stories table."""
    try:
        response = supabase.table("stories").insert(story).execute()
        print(f"✓ Inserted story: {story['title']}")
        return True
    except Exception as e:
        print(f"✗ Error inserting story: {e}")
        return False


def main():
    """Main execution flow."""
    print("=" * 80)
    print("Sinoark Story Generator")
    print("=" * 80)
    print()

    # Fetch latest digest
    print("📥 Fetching latest digest...")
    digest = fetch_latest_digest()
    if not digest:
        print("✗ No digest found. Exiting.")
        sys.exit(1)

    digest_date = digest.get("digest_date", "Unknown")
    print(f"✓ Found digest for {digest_date}")

    # Extract digest content
    digest_html = digest.get("html_content", "")
    digest_excerpt = strip_html_tags(digest_html)[:2000]  # First 2000 chars

    # Fetch context articles
    print("📥 Fetching context articles...")
    articles = fetch_context_articles(limit=15)
    context_text = "\n".join([
        f"- {a.get('translated_title') or a.get('original_title', 'Unknown')}"
        for a in articles[:10]
    ])
    print(f"✓ Found {len(articles)} context articles")

    # Story themes and perspectives
    stories_config = [
        {
            "number": 1,
            "theme": "Claude Code Leak & Open Source AI — The Impact on Developers Worldwide",
            "perspective": "Follow a Chinese AI engineer discovering and experimenting with leaked Claude Code, navigating the opportunity and ethical implications."
        },
        {
            "number": 2,
            "theme": "Digital Pets & Enterprise AI Adoption — The 'Lobster' Strategy for Making AI Accessible",
            "perspective": "A manufacturing executive discovers the 'lobster' gamified AI approach; shows how whimsy is becoming a serious enterprise strategy in China."
        },
    ]

    generated_count = 0

    for config in stories_config:
        print()
        print(f"🎬 Generating Story {config['number']}: {config['theme']}")
        print("-" * 80)

        # Generate story
        story_response = generate_story_with_gemini(
            theme=config["theme"],
            perspective=config["perspective"],
            digest_excerpt=digest_excerpt,
            context_articles=context_text,
            story_number=config["number"]
        )

        if not story_response:
            print(f"✗ Failed to generate story {config['number']}")
            continue

        # Parse response
        story = parse_story_response(story_response)
        if not story:
            print(f"✗ Failed to parse story {config['number']}")
            continue

        # Insert to Supabase
        if insert_story_to_supabase(story):
            generated_count += 1
            print(f"  Title: {story['title']}")
            print(f"  Slug: {story['slug']}")
            print(f"  Word count: {story['word_count']}")
        else:
            print(f"✗ Failed to insert story {config['number']}")

    print()
    print("=" * 80)
    print(f"✓ Story generation complete. Generated: {generated_count}/2")
    print("=" * 80)

    return generated_count == 2


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
