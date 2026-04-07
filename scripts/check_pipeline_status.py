#!/usr/bin/env python3
"""Check how many articles need translation and labeling"""

import os
from supabase import create_client

# Load env
url = os.getenv("SUPABASE_URL", "https://fbjpgaqoldptjnejrbeh.supabase.co")
key = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZianBnYXFvbGRwdGpuZWpyYmVoIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3NDY3NDY4MSwiZXhwIjoyMDkwMjUwNjgxfQ.LyxUyaJVfaKsLZipJfmXemFOS7nTVowejFZxQ6jdDz4")

supabase = create_client(url, key)

print("📊 SinoArk Processing Pipeline Status\n")
print("=" * 60)

# Total articles
total = supabase.table("articles").select("id", count="exact").execute()
print(f"\n📰 Total articles: {total.count:,}")

# Tech articles
tech_total = supabase.table("articles").select("id", count="exact").eq("tech", True).execute()
print(f"🔬 Tech articles (tech=true): {tech_total.count:,}")

# Tech articles with content
tech_with_content = supabase.table("articles").select("id", count="exact")\
    .eq("tech", True)\
    .not_.is_("original_content", "null")\
    .execute()
print(f"📝 Tech articles with content: {tech_with_content.count:,}")

# Need translation (tech=true, has content, status='raw')
need_translation = supabase.table("articles").select("id", count="exact")\
    .eq("tech", True)\
    .eq("status", "raw")\
    .not_.is_("original_content", "null")\
    .execute()
print(f"\n🌐 Need translation (tech + raw + has content): {need_translation.count:,}")

# Already translated
translated = supabase.table("articles").select("id", count="exact")\
    .eq("tech", True)\
    .eq("status", "translated")\
    .execute()
print(f"✅ Already translated (tech + translated): {translated.count:,}")

# Need labeling (translated but not labeled)
# Check if article_stakeholders table exists
try:
    labeled = supabase.table("article_stakeholders").select("article_id", count="exact").execute()
    labeled_count = labeled.count

    need_labeling = max(0, translated.count - labeled_count) if translated.count else 0
    print(f"🏷️  Already labeled: {labeled_count:,}")
    print(f"🔖 Need labeling (translated but not labeled): ~{need_labeling:,}")
except Exception as e:
    print(f"🏷️  Labeling table not ready: {e}")

print("\n" + "=" * 60)
print("\n📈 Estimation (if we process tech articles only):")
if need_translation.count:
    # Gemini can do ~120 articles/hour with delays
    hours = need_translation.count / 120
    print(f"   Translation: {need_translation.count:,} articles × ~30s = ~{hours:.1f} hours")

if translated.count:
    # Labeling is similar speed
    hours = translated.count / 120
    print(f"   Labeling: {translated.count:,} articles × ~30s = ~{hours:.1f} hours")

print("\n💡 Recommendation:")
print("   Use parallel processing (2-3 workers) to speed up 2-3x")
print("   Enable continuous mode to process as articles arrive")
