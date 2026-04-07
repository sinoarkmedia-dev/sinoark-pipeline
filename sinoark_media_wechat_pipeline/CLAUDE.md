# SinoArk WeChat Pipeline — Developer Notes

## Project Structure

```
sinoark_media_wechat_pipeline/
├── schema/
│   └── schema.sql              # ALTER sources + CREATE articles_digest/archive
├── lib/
│   ├── supabase.ts             # Supabase client singleton + batchInsert()
│   ├── utils.ts                # DIGEST_START, ARCHIVE_START, routeToTable(), types
│   ├── containers.ts           # SQLite access for all 3 containers
│   └── profile.ts              # Claude Haiku AI profile generation
├── sources/
│   ├── add-source.ts           # Register new source (AI profile + auto backfills)
│   ├── rename-source.ts        # Rename with old_names[] history
│   └── list-sources.ts         # Show all sources + counts
├── digest/
│   ├── fetch-new.ts            # Every 2h: new articles → articles_digest
│   ├── new-source-backfill.ts  # New source: fill digest window
│   ├── monthly-backup.ts       # 1st of month: digest → archive
│   └── monthly-cleanup.ts      # 5th of month: delete last month from digest
├── archive/
│   ├── historical-backfill.ts  # Daily: all sources → articles_archive
│   └── new-source-archive.ts   # New source: fill archive window
└── sync/
    └── container-sync.ts       # Full sync all 3 SQLite DBs → Supabase

## Key Design Decisions

### Why SQLite not HTTP RSS for bulk sync?
The containers' HTTP RSS endpoints (/feed/{mp_id}.xml) only return what's already
in their SQLite. Reading SQLite directly is faster, more reliable for bulk operations,
and avoids HTTP overhead. HTTP RSS is fine for the 2h fetch cycle (small volume, always fresh).

### Field mapping: description → original_summary
The `description` field in container SQLite is what WeChat returns via its API — a short
summary/digest of the article, NOT the full text. This was incorrectly mapped to
`original_content` in the old pipeline. Always map to `original_summary`.
`original_content` = null until a separate full-text fetcher populates it.

### source_id + source_name must ALWAYS be populated
Every article record must have both source_id (FK to sources.id) and source_name
(denormalized for query convenience). The lookup is: mp_id → sources table → id + name.
Articles with unknown mp_id are skipped with a warning.

### Three containers, same accounts
C1, C2, C3 all independently scrape WeChat. The same article may appear in multiple
containers. Deduplication is done by original_url — all insert operations use
ON CONFLICT (original_url) DO NOTHING.

### Monthly backup/cleanup timing
- Backup (1st of month): copies last month's data from digest to archive
- Cleanup (5th of month): deletes last month from digest ONLY if archive count >= digest count
- The 4-day gap ensures the backup had time to complete and be verified

### archive_completed flag
Set to true per source when historical-backfill.ts finishes that source.
Re-run with --all flag to re-process everything (e.g. after containers fetch more history).

### AI profile generation
Uses claude-haiku-4-5-20251001 (cheapest Claude model). Called once per new source.
Fields generated: profile (Chinese), profile_en (English), entity_nature, focus,
about_tech (bool), about_ai (bool).
Falls back gracefully if ANTHROPIC_API_KEY is not set.

## Timezone Boundaries

| Event | BJT | UTC |
|---|---|---|
| DIGEST_START | 2026-04-01 00:00 | 2026-03-31 16:00 |
| ARCHIVE_START | 2021-01-01 00:00 | 2020-12-31 16:00 |

All timestamps stored in Supabase as timestamptz (UTC). The BJT convention is only
relevant for the monthly backup/cleanup boundary calculations.

## Environment Variables

```
SUPABASE_URL=               Supabase project URL
SUPABASE_SERVICE_KEY=       Service role key (bypasses RLS)
ANTHROPIC_API_KEY=          For profile generation (optional — skips if missing)
CONTAINER_C1_URL=           http://localhost:8001
CONTAINER_C2_URL=           http://localhost:8004
CONTAINER_C3_URL=           http://localhost:8003
CONTAINER_C1_DB=            /root/workspace/data/original/db.db
CONTAINER_C2_DB=            /root/workspace/data/data2/db.db
CONTAINER_C3_DB=            /root/workspace/data/data3/db.db
```

## Supabase Tables

### sources (extended from existing)
New columns added:
- old_names text[]                 — previous display names, appended on rename
- archive_completed boolean        — true when historical-backfill finished this source
- archive_started_at timestamptz   — when archive backfill started
- digest_backfill_completed bool   — true when new-source-backfill finished
- digest_backfill_started_at       — when digest backfill started
- mp_id text UNIQUE                — WeChat mp_id (was wemprss_account_id in old schema)

### articles_digest / articles_archive (identical structure)
Both have UNIQUE constraint on original_url.
Both have source_id FK + source_name denormalized column.
Both have mp_id column for direct filtering without JOIN.

## Common Operations

### Initialize after setup
```bash
npm run container-sync    # populates both tables from all 3 containers
npm run list-sources      # verify source→article counts
```

### Check why articles are missing for a source
```bash
# Check if mp_id is in sources table
npm run list-sources | grep "AccountName"

# Check container directly
python3 -c "
import sqlite3
conn = sqlite3.connect('/root/workspace/data/original/db.db')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM articles WHERE mp_id = \"MP_WXS_xxx\"')
print(cur.fetchone())
"
```

### Re-sync a single account
```bash
npm run container-sync -- --mp-id MP_WXS_xxx
```

### Re-run archive for all sources (after containers fetched more history)
```bash
npm run historical-backfill -- --all
```

## Future Improvements

1. **Full-text fetcher**: separate script/service to fetch original_content from
   WeChat URLs and populate word_count + author_name.

2. **Container health monitoring**: alert if a container hasn't synced in > 6 hours
   (check feeds.sync_time in SQLite).

3. **AI enrichment pipeline**: after full-text is available, run translation,
   theme classification, entity extraction on articles_digest.

4. **RSS feed for outbound**: serve articles_digest as an Atom feed for internal tools.

5. **Source deduplication**: C2 and C3 track the same 178 accounts as C1. Consider
   having them track different accounts to maximize coverage and reduce WeChat throttling.
