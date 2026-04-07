#!/usr/bin/env node
/**
 * sync/container-sync.ts
 *
 * Full sync from all 3 container SQLite DBs → Supabase (articles_digest + articles_archive).
 *
 * Run once to initialize the pipeline, and periodically to catch up when containers
 * accumulate new historical articles that the 2h fetch-new cycle wouldn't cover.
 *
 * Field mapping (SQLite → Supabase):
 *   articles.title        → original_title
 *   articles.description  → original_summary  ← RSS summary, NOT full text
 *   articles.url          → original_url
 *   articles.pic_url      → cover_image_url
 *   articles.publish_time → published_at  (unix seconds)
 *   articles.mp_id        → mp_id + joined to sources for source_id/source_name
 *   (left null)           → original_content, word_count, author_name
 *
 * Routing by published_at:
 *   >= DIGEST_START  (Apr 1, 2026 BJT) → articles_digest
 *   >= ARCHIVE_START (Jan 1, 2021 BJT) → articles_archive
 *   before ARCHIVE_START               → skipped (too old)
 *
 * Usage:
 *   npm run container-sync
 *   npm run container-sync -- --mp-id MP_WXS_3236757533   # single account
 */

import { Command } from 'commander'
import { sb, batchInsert, getSourceByMpId } from '../lib/supabase.ts'
import { getAllArticles, getArticles, CONTAINERS } from '../lib/containers.ts'
import {
  DIGEST_START, ARCHIVE_START, routeToTable,
  log, warn, err, sleep,
  type ArticleRecord, type ArticleTable,
} from '../lib/utils.ts'

const program = new Command()
program.option('--mp-id <mpId>', 'Sync only one account').parse()
const opts = program.opts<{ mpId?: string }>()

async function main() {
  log('=== container-sync starting ===')
  log(`Digest  window: ${DIGEST_START.toISOString()} → now`)
  log(`Archive window: ${ARCHIVE_START.toISOString()} → ${DIGEST_START.toISOString()}`)

  // ── 1. Load all articles from containers ──────────────────
  const filter = {
    since: ARCHIVE_START,
    ...(opts.mpId ? { mpId: opts.mpId } : {}),
  }

  log(opts.mpId
    ? `Syncing single account: ${opts.mpId}`
    : 'Syncing all accounts across all 3 containers...'
  )

  const rawMap = getAllArticles(filter)
  log(`Unique articles found (across all containers): ${rawMap.size}`)

  // ── 2. Route articles to digest / archive buckets ─────────
  const buckets = new Map<ArticleTable, Map<string, ArticleRecord[]>>()
  buckets.set('articles_digest',  new Map())
  buckets.set('articles_archive', new Map())

  let skippedTooOld   = 0
  let skippedNoSource = 0

  for (const raw of rawMap.values()) {
    const pub = new Date(raw.publish_time * 1000)
    const table = routeToTable(pub)

    if (!table) { skippedTooOld++; continue }

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
      published_at:     pub,
      original_content: null,
      word_count:       null,
      author_name:      null,
    }

    const bucket = buckets.get(table)!
    if (!bucket.has(source.id)) bucket.set(source.id, [])
    bucket.get(source.id)!.push(record)
  }

  if (skippedTooOld > 0)   log(`Skipped ${skippedTooOld} articles before Jan 1, 2021 BJT`)
  if (skippedNoSource > 0) warn(`Skipped ${skippedNoSource} articles with unknown mp_id (not in sources table)`)

  // ── 3. Insert into Supabase ───────────────────────────────
  let totalDigest  = 0
  let totalArchive = 0

  for (const [table, sourceMap] of buckets) {
    log(`\nInserting into ${table}...`)
    for (const [sourceId, articles] of sourceMap) {
      const n = await batchInsert(table, articles)
      if (table === 'articles_digest')  totalDigest  += n
      if (table === 'articles_archive') totalArchive += n
    }
    log(`  ${table}: ${table === 'articles_digest' ? totalDigest : totalArchive} rows queued`)
  }

  log('\n=== container-sync complete ===')
  log(`articles_digest  inserted: ${totalDigest}`)
  log(`articles_archive inserted: ${totalArchive}`)
  log(`Total: ${totalDigest + totalArchive}`)
}

main().catch(e => { err(String(e)); process.exit(1) })
