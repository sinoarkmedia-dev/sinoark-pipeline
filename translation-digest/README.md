# translation-digest

**Status: ACTIVE — runs on cron every 4–6 hours**

Python service that translates Chinese WeChat articles to English using Gemini Flash API.

## What It Does

1. Fetches articles with `status='raw'` and a non-empty `original_title` from Supabase
2. Calls Gemini Flash to translate the title and generate a 150–200 word English summary
3. Updates the article with `translated_title`, `translated_summary`, and sets `status='translated'`
4. Applies 0.5s rate limiting between API calls

## Tech Stack

- Python 3.11
- Google Gemini Flash API
- Supabase (PostgreSQL)

## Key Files

```
translate.py     # Main script
requirements.txt # Python dependencies
.env             # API keys and Supabase credentials
```

## Running

```bash
python3 translate.py
```

## Pipeline Position

```
[articles_digest / articles_archive]
    status='raw'
         ↓
  translation-digest
         ↓
    status='translated'
         ↓
  stakeholder-labelling
```

## Output Columns

- `translated_title` — English title
- `translated_summary` — 150–200 word English summary
- `status` — updated to `'translated'`
