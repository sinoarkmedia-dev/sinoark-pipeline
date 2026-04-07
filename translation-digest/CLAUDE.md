# translation-digest — SinoArk Translation Layer

## Purpose

This project translates Chinese WeChat articles stored in a Supabase database into English using the Google Gemini API (`gemini-1.5-flash`). For each article it produces:

- An **English translated title**
- A **150-200 word English summary** written in a journalistic style for a technically literate audience

Translated articles feed downstream into a newsletter or digest pipeline. Articles move through a status lifecycle in the `articles` table: `raw` → `translated` → `reviewed` → `published`.

## Project Structure

```
translation-digest/
├── .env                     # Local secrets (not committed)
├── .env.example             # Template showing required env vars
├── requirements.txt         # Python dependencies
├── CLAUDE.md                # This file
├── lib/
│   ├── __init__.py
│   ├── gemini_client.py     # Gemini API wrapper (translate_article)
│   └── supabase_client.py   # Supabase read/write helpers
└── scripts/
    ├── __init__.py
    └── translate_batch.py   # Main runnable batch script
```

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

Required variables:

| Variable | Description |
|---|---|
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase service role key (bypasses RLS) |
| `GEMINI_API_KEY` | Google Gemini API key |

## Running the Translator

### Basic run (translate up to 100 pending articles)

```bash
python scripts/translate_batch.py
```

### Translate a specific number of articles

```bash
python scripts/translate_batch.py --limit 50
```

### Dry run (preview without writing to Supabase)

```bash
python scripts/translate_batch.py --dry-run
```

### Combine flags

```bash
python scripts/translate_batch.py --limit 20 --dry-run
```

## How It Works

1. **Fetch**: The script queries Supabase for articles with `status = 'raw'` and a non-empty `original_title`, ordered by `published_at DESC`, in batches of 20.
2. **Translate**: Each article's title and content are sent to Gemini with a system prompt instructing it to act as a Chinese AI/tech news translator. The model returns a JSON object with `translated_title` and `translated_summary`.
3. **Write**: On success, the article row is updated with the translated fields and its status is advanced to `'translated'`. On failure, the article stays `'raw'` so it will be retried on the next run.
4. **Rate limiting**: A 0.5-second delay is inserted between Gemini API calls to avoid hitting rate limits.

## Database Schema (relevant columns)

### `articles`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | Primary key |
| `source_id` | uuid | FK to `sources` |
| `original_title` | text | Chinese title |
| `original_content` | text | Chinese body text |
| `status` | text | `raw` / `translated` / `reviewed` / `published` / `rejected` |
| `translated_title` | text | Written by this pipeline |
| `translated_summary` | text | Written by this pipeline |
| `published_at` | timestamptz | Original article publication time |

### `sources`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | Primary key |
| `name` | text | Chinese source name |
| `name_en` | text | English source name |
| `platform` | text | e.g. `wechat` |

## Exit Codes

- `0` — All articles translated successfully (or nothing to do)
- `1` — One or more articles failed translation
