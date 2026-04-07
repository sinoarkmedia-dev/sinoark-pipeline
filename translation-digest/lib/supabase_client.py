"""
Supabase client for reading and updating WeChat article records.
"""

import os
from functools import lru_cache

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()


@lru_cache(maxsize=1)
def _get_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in the environment")
    return create_client(url, key)


def get_pending_articles(limit: int = 50, days: int = 7) -> list[dict]:
    """
    Fetch recent articles with status='raw' that have a non-empty original_title,
    ordered by published_at DESC.

    Args:
        limit: Maximum number of articles to return.
        days:  Only consider articles published within this many days (default 7).
               Prevents translating the entire historical backfill.

    Returns:
        A list of article dicts, each containing at minimum:
        id, original_title, original_content, source_id, and the joined
        source name fields.
    """
    from datetime import datetime, timezone, timedelta

    client = _get_client()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    response = (
        client.table("articles_digest")
        .select(
            "id, original_title, original_content, original_url, published_at, source_name"
        )
        .eq("status", "raw")
        .neq("original_title", "")
        .not_.is_("original_title", "null")
        .gte("published_at", cutoff)
        .order("published_at", desc=True)
        .limit(limit)
        .execute()
    )

    return response.data or []


def mark_translated(
    article_id: str,
    translated_title: str,
    translated_summary: str,
) -> None:
    """
    Persist the translated title and summary, and advance the article status
    to 'translated'.

    Args:
        article_id: UUID of the article to update.
        translated_title: English translation of the article title.
        translated_summary: 150-200 word English summary.
    """
    client = _get_client()

    client.table("articles_digest").update(
        {
            "translated_title": translated_title,
            "translated_summary": translated_summary,
            "status": "translated",
        }
    ).eq("id", article_id).execute()


def mark_failed(article_id: str) -> None:
    """
    Leave the article in 'raw' status so it will be retried on the next run.

    This is a no-op in terms of DB writes (status stays 'raw'), but is provided
    as an explicit call site so failure handling is clear in calling code and
    future retry logic can be added here (e.g. incrementing an error counter).

    Args:
        article_id: UUID of the article that failed translation.
    """
    # Status already 'raw'; nothing to write.
    # Placeholder for future retry-count logic.
    _ = article_id
