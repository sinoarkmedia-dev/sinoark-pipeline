#!/usr/bin/env node
/**
 * digest/fetch-new.ts
 *
 * Checks all 3 containers for newly published articles and inserts them
 * into articles_digest. Designed to run every 2 hours via cron.
 *
 * Cron: every 30 minutes
 *   (see crontab — expression: 30 * * * *)
 *
 * Logic:
 *   - Reads directly from all 3 container SQLite DBs
 *   - Filters: published_at >= DIGEST_START (April 1, 2026 00:00 BJT)
 *   - Deduplicates across containers by original_url
 *   - Matches mp_id → source_id/source_name from Supabase sources table
 *   - Inserts into articles_digest (Supabase UNIQUE on original_url handles dedup)
 */

import { sb, batchInsert, getSourceByMpId } from '../lib/supabase.ts'
import { getAllArticles } from '../lib/containers.ts'
import { DIGEST_START, log, warn, type ArticleRecord } from '../lib/utils.ts'

async function main() {
  log('=== fetch-new starting ===')
  log(`Digest window: published_at >= ${DIGEST_START.toISOString()} (April 1, 2026 00:00 BJT)`)

  // Read from all 3 containers, dedup by URL, filter by date
  const rawMap = getAllArticles({ since: DIGEST_START })
  log(`Found ${rawMap.size} unique articles in containers since DIGEST_START`)

  // Build article records
  const bySource = new Map<string, ArticleRecord[]>()
  let skippedNoSource = 0

  for (const raw of rawMap.values()) {
    const source = await getSourceByMpId(raw.mp_id)
    if (!source) { skippedNoSource++; continue }

    const record: ArticleRecord = {
      source_id:        source.id,
      source_name:      source.name,
      mp_id:            raw.mp_id,
      original_title:   raw.title?.trim() || '(no title)',
      original_summary: raw.description?.trim() || null,
      original_url:     raw.url,
      cover_image_url:  raw.pic_url || null,
      published_at:     new Date(raw.publish_time * 1000),
      original_content: null,
      word_count:       null,
      author_name:      null,
    }

    if (!bySource.has(source.id)) bySource.set(source.id, [])
    bySource.get(source.id)!.push(record)
  }

  if (skippedNoSource > 0) {
    warn(`Skipped ${skippedNoSource} articles with unrecognized mp_id (account not in sources table)`)
  }

  // Insert per source (easier to track progress)
  let totalInserted = 0
  for (const [, articles] of bySource) {
    const n = await batchInsert('articles_digest', articles)
    totalInserted += n
  }

  log(`=== Done — inserted/queued ${totalInserted} articles into articles_digest ===`)
}

main().catch(e => { console.error(e); process.exit(1) })
