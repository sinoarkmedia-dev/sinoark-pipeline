#!/usr/bin/env node
/**
 * digest/daily-backup.ts
 *
 * Runs every night at 11:59pm BJT (= 15:59 UTC).
 * Copies ALL articles from articles_digest → articles_archive (upsert, ignoreDuplicates).
 *
 * This is idempotent — safe to re-run. Articles already in archive are skipped.
 * The monthly-cleanup.ts on the 5th will delete last-month rows from digest
 * only after confirming they're present in archive (safety check).
 *
 * Cron (daily at 15:59 UTC = 23:59 BJT):
 *   59 15 * * * (cd /root/workspace/sinoark_media_wechat_pipeline && npm run daily-backup)
 */

import { sb } from '../lib/supabase.ts'
import { log, warn, err } from '../lib/utils.ts'

const PAGE = 1000

async function main() {
  log('=== daily-backup starting (digest → archive) ===')

  let page = 0
  let totalFetched = 0
  let totalCopied  = 0

  while (true) {
    // ── Fetch a page from digest ──────────────────────────────
    const { data: rows, error: fetchErr } = await sb()
      .from('articles_digest')
      .select('*')
      .range(page * PAGE, (page + 1) * PAGE - 1)
      .order('published_at', { ascending: true })

    if (fetchErr) { err(`Fetch error: ${fetchErr.message}`); break }
    if (!rows || rows.length === 0) break

    totalFetched += rows.length

    // ── Upsert into archive (strip id/timestamps so archive gets its own UUIDs) ──
    const archiveRows = rows.map(({ id: _id, created_at: _c, updated_at: _u, ...rest }: any) => ({
      ...rest,
      scraped_at: new Date().toISOString(),
    }))

    const { error: insertErr } = await sb()
      .from('articles_archive')
      .upsert(archiveRows, { onConflict: 'original_url', ignoreDuplicates: true })

    if (insertErr) {
      warn(`Insert error on page ${page}: ${insertErr.message}`)
    } else {
      totalCopied += rows.length
    }

    if (page % 5 === 0) {
      log(`  Page ${page + 1}: processed ${totalFetched} rows so far`)
    }

    page++
    if (rows.length < PAGE) break
  }

  log(`=== daily-backup complete: ${totalFetched} fetched, ${totalCopied} upserted to articles_archive ===`)
  log('Skipped rows are already present in archive (normal on re-runs).')
  log('Monthly cleanup runs on the 5th — check archive counts before deleting from digest.')
}

main().catch(e => { err(String(e)); process.exit(1) })
