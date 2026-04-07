#!/usr/bin/env node
/**
 * digest/monthly-backup.ts
 *
 * Runs on the 1st of each month (Beijing Time).
 * Copies last month's articles from articles_digest → articles_archive.
 *
 * Cron example (1st of every month at 02:00 BJT = 18:00 UTC previous day):
 *   0 18 28-31 * * [ $(TZ=Asia/Shanghai date +\%d) -eq 1 ] && npm run monthly-backup
 *
 * Simpler cron (1st of month at 02:00 UTC — acceptable ~8h window):
 *   0 2 1 * * cd /root/sinoark_media_wechat_pipeline && npm run monthly-backup >> /var/log/sinoark-backup.log 2>&1
 */

import { sb } from '../lib/supabase.ts'
import { log, warn, err, lastMonthRangeUTC } from '../lib/utils.ts'

const PAGE = 1000

async function main() {
  const { start, end, label } = lastMonthRangeUTC()
  log(`=== Monthly backup: ${label} ===`)
  log(`Copying articles_digest [${start.toISOString()} → ${end.toISOString()}) to articles_archive`)

  let page = 0
  let totalCopied = 0
  let totalFetched = 0

  while (true) {
    // ── Fetch a page from digest ──────────────────────────
    const { data: rows, error: fetchErr } = await sb()
      .from('articles_digest')
      .select('*')
      .gte('published_at', start.toISOString())
      .lt('published_at', end.toISOString())
      .range(page * PAGE, (page + 1) * PAGE - 1)

    if (fetchErr) { err(`Fetch error: ${fetchErr.message}`); break }
    if (!rows || rows.length === 0) break

    totalFetched += rows.length

    // ── Upsert into archive (strip id so archive gets new UUIDs) ──
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

    log(`  Page ${page + 1}: fetched ${rows.length}, cumulative ${totalCopied}`)
    page++
    if (rows.length < PAGE) break
  }

  log(`=== Backup complete: ${totalCopied}/${totalFetched} rows copied to articles_archive ===`)
  log(`Run monthly-cleanup.ts on the 5th to delete these rows from articles_digest.`)
}

main().catch(e => { err(String(e)); process.exit(1) })
