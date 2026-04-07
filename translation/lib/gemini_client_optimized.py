"""
Token-optimized Gemini API client for translating Chinese WeChat articles.

Optimizations:
1. Truncate content to 2000 chars (sufficient for 150-200 word summary)
2. Streamlined prompt (removed redundant instructions)
3. Skip very short articles (<100 chars)
4. Use gemini-1.5-flash (cheaper, faster)

Token savings: ~70% reduction vs original
"""

import json
import os
import re

from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

_client: genai.Client | None = None


def _init_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable is not set")
        _client = genai.Client(api_key=api_key)
    return _client


# Shorter system prompt
SYSTEM_PROMPT = (
    "Professional translator for Chinese AI/tech news. "
    "Translate to clear English for tech-savvy audience. "
    "Preserve technical terms and names accurately."
)

# Streamlined prompt - removed redundancy
TRANSLATION_PROMPT_TEMPLATE = """\
Source: {source_name}
Title: {title}
Content: {content_section}

Return valid JSON only:
{{
  "translated_title": "...",
  "translated_summary": "150-200 word summary, engaging style, key points only"
}}
"""


def translate_article(title: str, content: str, source_name: str) -> dict | None:
    """
    Translate article with optimized token usage.

    Optimizations:
    - Truncates content to first 2000 chars (enough for summary)
    - Skips articles < 100 chars (too short to be meaningful)
    - Streamlined prompts

    Token usage: ~500-800 tokens input (vs ~1000-8000 before)
    """
    _init_client()

    # Skip very short articles
    if not content or len(content.strip()) < 100:
        return None

    # Truncate to first 2000 chars - sufficient for 150-200 word summary
    # Saves ~70% tokens on long articles
    content_truncated = content.strip()[:2000]

    # Add indicator if truncated
    if len(content.strip()) > 2000:
        content_truncated += "..."

    prompt = TRANSLATION_PROMPT_TEMPLATE.format(
        source_name=source_name or "WeChat",
        title=title.strip()[:300],  # Also cap title
        content_section=content_truncated,
    )

    try:
        client = _init_client()
        response = client.models.generate_content(
            model="gemini-2.0-flash-exp",  # Fast and cheap
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.3,  # Lower temp = more consistent, fewer tokens
            ),
            contents=prompt,
        )
        raw_text = response.text.strip()

        # Strip markdown fences
        raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
        raw_text = re.sub(r"\s*```$", "", raw_text)

        result = json.loads(raw_text)

        translated_title = result.get("translated_title", "").strip()
        translated_summary = result.get("translated_summary", "").strip()

        if not translated_title or not translated_summary:
            return None

        return {
            "translated_title": translated_title,
            "translated_summary": translated_summary,
        }

    except json.JSONDecodeError:
        return None
    except Exception as exc:
        print(f"[gemini-opt] Error: {exc}")
        return None
