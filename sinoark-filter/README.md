# sinoark-filter

**Status: ACTIVE — running as systemd service `sinoark-filter.service`**

Continuous Python process that classifies articles in Supabase as `about_tech` and/or `about_ai`.

## What It Does

Runs in a continuous loop, fetching unclassified articles from Supabase and applying two classification passes:

1. **Source-level flag** — if the source has `about_ai=true`, all its articles are auto-tagged
2. **Keyword scan** — scans `title` and `summary` for Chinese/English AI and tech keywords

Uses Moonshot (Kimi) API for LLM-based classification where keyword matching is ambiguous.

## Tech Stack

- Python 3.11
- Moonshot (Kimi) API
- Supabase (PostgreSQL)

## Key Files

```
filter.py        # Main process (runs continuously)
requirements.txt # Python dependencies
.env             # API keys and Supabase credentials
```

## Running

```bash
# As systemd service (production)
sudo systemctl status sinoark-filter
sudo systemctl restart sinoark-filter

# Manually
python3 filter.py
```

## Systemd Service

Defined at `/etc/systemd/system/sinoark-filter.service`. The service auto-restarts on failure and starts on boot.

## Output

Updates `about_tech` and `about_ai` boolean columns on articles in Supabase. Articles classified here are then picked up by `translation-digest` for translation.
