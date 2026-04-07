#!/usr/bin/env node
/**
 * archive/historical-backfill.ts
 *
 * Reads all articles from all 3 container SQLite DBs that fall in the archive
 * window [ARCHIVE_START, DIGEST_START) and inserts them into articles_archive.
 *
 * Run once initially, then periodically as containers accumulate more historical data.
 * Sources are processed one-by-one; archive_completed is set to true when done.
 *
 * Cron: every 3 hours
 *   (see crontab — expression: 0 0,3,6,9,12,15,18,21 * * *)
 *
 * Usage:
 *   npm run historical-backfill               # all unarchived sources
 *   npm run historical-backfill -- --all      # re-process all sources (ignore archive_completed)
 */

import { Command } from 'commander'
import { sb, batchInsert, getSourceByMpId } from '../lib/supabase.ts'
import { getAllArticles } from '../lib/containers.ts'
import { DIGEST_START, ARCHIVE_START, log, warn, err, sleep, type ArticleRecord } from '../lib/utils.ts'

const program = new Command()
program.option('--all', 'Re-process all sources, ignoring archive_completed flag').parse()
const opts = program.opts<{ all?: boolean }>()

async function main() {
  log('=== Historical archive backfill ===')
  log(`Archive window: ${ARCHIVE_START.toISOString()} → ${DIGEST_START.toISOString()}`)
  log(`(Jan 1, 2021 00:00 BJT → April 1, 2026 00:00 BJT)`)

  // ── Load sources to process ───────────────────────────────
  let query = sb().from('sources').select('id, name, mp_id, archive_completed').eq('status', 'active')
  if (!opts.all) query = query.eq('archive_completed', false) as typeof query

  const { data: sources, error } = await query.order('name')
  if (error) { err(error.message); process.exit(1) }

  log(`Sources to process: ${sources!.length} (${opts.all ? 'all' : 'unarchived only'})`)

  let totalArticles = 0
  let totalSources  = 0

  for (const source of sources!) {
    if (!source.mp_id) { warn(`Skipping "${source.name}" — no mp_id`); continue }

    log(`\nProcessing "${source.name}" (${source.mp_id})...`)

    // Mark started
    await sb().from('sources')
      .update({ archive_started_at: new Date().toISOString() })
      .eq('id', source.id)

    // Read from all 3 containers for this mp_id, in archive window
    const rawMap = getAllArticles({
      mpId:  source.mp_id,
      since: ARCHIVE_START,
      until: DIGEST_START,
    })

    log(`  Found ${rawMap.size} articles in containers for archive window`)

    if (rawMap.size === 0) {
      log(`  No articles to archive — marking complete`)
      await sb().from('sources').update({ archive_completed: true }).eq('id', source.id)
      totalSources++
      continue
    }

    // Build records
    const records: ArticleRecord[] = []
    for (const raw of rawMap.values()) {
      const pub = new Date(raw.publish_time * 1000)
      // Double-check boundary (safety)
      if (pub < ARCHIVE_START || pub >= DIGEST_START) continue

      records.push({
        source_id:        source.id,
        source_name:      source.name,
        mp_id:            source.mp_id,
        original_title:   raw.title?.trim() || '(no title)',
        original_summary: raw.description?.trim() || null,
        original_url:     raw.url,
        cover_image_url:  raw.pic_url || null,
        published_at:     pub,
        original_content: null,
        word_count:       null,
        author_name:      null,
      })
    }

    const inserted = await batchInsert('articles_archive', records)
    log(`  Inserted ${inserted} articles`)
    totalArticles += inserted

    // Mark completed
    await sb().from('sources').update({ archive_completed: true }).eq('id', source.id)
    totalSources++

    await sleep(500) // brief pause between sources
  }

  log(`\n=== Archive backfill done ===`)
  log(`Sources processed: ${totalSources} | Articles inserted: ${totalArticles}`)
}

main().catch(e => { err(String(e)); process.exit(1) })
