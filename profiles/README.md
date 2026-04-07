# sinoark-profiles

**Status: ACTIVE — cron daily at 15:30 UTC (11:30 PM BJT)**

Daily pipeline that identifies the most-talked-about company, person, and product from `articles_digest`, then uses Claude Haiku (headless, Max subscription) to write a ~300-word English HTML profile for each.

Avoids repeating subjects for 6 months via deduplication.

## What It Produces

Every day, 3 profiles saved to the `profiles` Supabase table:
- **company** — e.g. OpenAI, ByteDance, Baidu
- **person** — e.g. Sam Altman, Ren Zhengfei
- **product** — e.g. Claude, DeepSeek-V3, Sora

## Setup (one-time)

Run `migrations/001_create_profiles.sql` in the Supabase SQL editor:
https://supabase.com/dashboard/project/fbjpgaqoldptjnejrbeh/sql

## How It Works

1. **Fetch** today's `about_ai=true` articles from `articles_digest` (24h window ending 11pm BJT)
2. **Identify** top company/person/product using Gemini Flash (fast, cheap)
3. **Deduplicate** — skip subjects already profiled in the last 180 days
4. **Profile** each subject with Claude Haiku (headless: `claude -p "..." --model haiku`)
5. **Save** to `profiles` table in Supabase

## Usage

```bash
# Normal run (production)
python3 generate_profiles.py

# Specific date
python3 generate_profiles.py --date 2026-04-03

# Dry run — shows Gemini subject selection + Claude prompts, no DB writes
python3 generate_profiles.py --dry-run

# Single category only
python3 generate_profiles.py --category company
```

## Dependencies

- Gemini API (subject identification) — uses `GEMINI_API_KEY`
- Claude Code CLI (profile writing) — uses Max subscription (already authenticated)
- Supabase — uses `SUPABASE_URL` + `SUPABASE_SERVICE_KEY`

All loaded from `/root/workspace/sinoark_media_wechat_pipeline/.env`

## Cron

```
30 15 * * *   cd /root/workspace/sinoark-profiles && python3 generate_profiles.py
```
Runs at 15:30 UTC = 11:30 PM BJT, 30 minutes after the digest.

## Supabase Table: `profiles`

| Column | Type | Description |
|---|---|---|
| `date` | date | Profile date |
| `category` | text | `company`, `person`, or `product` |
| `subject` | text | English name |
| `subject_zh` | text | Chinese name (if available) |
| `html_content` | text | ~300-word HTML profile |
| `source_articles` | jsonb | Articles used as sources |
| `article_count` | int | Number of source articles |
| `generated_at` | timestamptz | Generation timestamp |
