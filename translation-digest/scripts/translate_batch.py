"""
Batch translation script: fetches raw WeChat articles from Supabase,
translates them with Gemini, and writes results back.

Usage:
    python scripts/translate_batch.py [--limit N] [--dry-run]
"""

import argparse
import sys
import time

# Ensure the project root is on sys.path when the script is run directly
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.gemini_client import translate_article
from lib.supabase_client import get_pending_articles, mark_failed, mark_translated

BATCH_SIZE = 10        # process 10 articles at a time
API_CALL_DELAY = 0.5  # seconds between Gemini calls within a batch
BATCH_PAUSE = 10.0    # seconds to wait between batches


def _source_name(article: dict) -> str:
    """Extract the best available source name from an articles_digest row."""
    return article.get("source_name") or "Unknown"


def _char_count(article: dict) -> int:
    content = article.get("original_content") or ""
    title = article.get("original_title") or ""
    return len(title) + len(content)


def process_batch(articles: list[dict], dry_run: bool) -> tuple[int, int]:
    """
    Translate a single batch of articles.

    Returns:
        (success_count, fail_count)
    """
    success = 0
    fail = 0

    for i, article in enumerate(articles):
        article_id = article["id"]
        title = article.get("original_title") or ""
        content = article.get("original_content") or ""
        source = _source_name(article)
        chars = _char_count(article)

        print(
            f"  [{i + 1}/{len(articles)}] {title[:70]!r}  "
            f"({chars} chars, source: {source})"
        )

        if dry_run:
            print("    [dry-run] Would translate and mark as translated.")
            success += 1
            continue

        result = translate_article(title=title, content=content, source_name=source)

        if result is None:
            print(f"    FAILED — leaving as 'raw' for retry.")
            mark_failed(article_id)
            fail += 1
        else:
            mark_translated(
                article_id=article_id,
                translated_title=result["translated_title"],
                translated_summary=result["translated_summary"],
            )
            print(
                f"    OK — \"{result['translated_title'][:70]}\""
            )
            success += 1

        if i < len(articles) - 1:
            time.sleep(API_CALL_DELAY)

    return success, fail


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Translate raw WeChat articles from Supabase using Gemini."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum total number of articles to process (default: 100).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without making any writes.",
    )
    parser.add_argument(
        "--continuous",
        action="store_true",
        help="Keep running: poll for new articles every 30s after exhausting pending ones.",
    )
    args = parser.parse_args()

    if args.dry_run:
        print("[dry-run mode] No changes will be written to Supabase.\n")

    total_success = 0
    total_fail = 0
    total_processed = 0
    remaining = args.limit

    print(f"Starting translation run (limit={args.limit}, batch_size={BATCH_SIZE}).\n")

    while True:
        fetch_size = min(BATCH_SIZE, remaining) if not args.continuous else BATCH_SIZE
        print(f"Fetching up to {fetch_size} pending articles from Supabase...")

        articles = get_pending_articles(limit=fetch_size)

        if not articles:
            if args.continuous:
                print(f"No pending articles. Waiting 30s for new ones...\n")
                time.sleep(30)
                continue
            else:
                print("No more pending articles found.")
                break

        print(f"Fetched {len(articles)} articles. Translating...\n")

        success, fail = process_batch(articles, dry_run=args.dry_run)

        total_success += success
        total_fail += fail
        total_processed += len(articles)
        if not args.continuous:
            remaining -= len(articles)

        print(
            f"\nBatch of {len(articles)} done: {success} ok, {fail} failed. "
            f"Total: {total_processed} processed. Pausing {BATCH_PAUSE}s...\n"
        )
        time.sleep(BATCH_PAUSE)

        if not args.continuous and (remaining <= 0 or len(articles) < fetch_size):
            break

    print(
        f"Run finished. Total: {total_processed} processed, "
        f"{total_success} translated, {total_fail} failed."
    )
    if total_fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
