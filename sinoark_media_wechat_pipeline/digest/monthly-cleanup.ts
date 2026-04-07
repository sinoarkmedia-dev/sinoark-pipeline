#!/usr/bin/env node
/**
 * digest/monthly-cleanup.ts
 *
 * Runs on the 5th of each month (Beijing Time).
 * Deletes last month's articles from articles_digest ONLY IF they are
 * confirmed present in articles_archive (safety check).
 *
 * Cron example (5th of month at 02:00 UTC):
 *   0 2 5 * * cd /root/sinoark_media_wechat_pipeline && npm run monthly-cleanup >> /var/log/sinoark-cleanup.log 2>&1
 */

import { sb } from '../lib/supabase.ts'
import { log, warn, err, lastMonthRangeUTC } from '../lib/utils.ts'

async function main() {
  const { start, end, label } = lastMonthRangeUTC()
  log(`=== Monthly cleanup: ${label} ===`)
  log(`Checking articles_digest [${start.toISOString()} → ${end.toISOString()})`)

  // ── Count in digest ───────────────────────────────────────
  const { count: digestCount, error: dc } = await sb()
    .from('articles_digest')
    .select('*', { count: 'exact', head: true })
    .gte('published_at', start.toISOString())
    .lt('published_at', end.toISOString())

  if (dc) { err(`Count error (digest): ${dc.message}`); process.exit(1) }
  log(`Digest count for ${label}: ${digestCount}`)

  if (!digestCount || digestCount === 0) {
    log('Nothing to clean up in digest for this period.')
    return
  }

  // ── Count in archive ──────────────────────────────────────
  const { count: archiveCount, error: ac } = await sb()
    .from('articles_archive')
    .select('*', { count: 'exact', head: true })
    .gte('published_at', start.toISOString())
    .lt('published_at', end.toISOString())

  if (ac) { err(`Count error (archive): ${ac.message}`); process.exit(1) }
  log(`Archive count for ${label}: ${archiveCount}`)

  // ── Safety check ──────────────────────────────────────────
  if ((archiveCount ?? 0) < digestCount) {
    err(`SAFETY ABORT: Archive has ${archiveCount} but digest has ${digestCount} for ${label}.`)
    err('Run monthly-backup.ts first, then retry cleanup.')
    process.exit(1)
  }

  // ── Delete from digest ────────────────────────────────────
  log(`Safety check passed. Deleting ${digestCount} rows from articles_digest...`)

  const { error: delErr } = await sb()
    .from('articles_digest')
    .delete()
    .gte('published_at', start.toISOString())
    .lt('published_at', end.toISOString())

  if (delErr) {
    err(`Delete failed: ${delErr.message}`)
    process.exit(1)
  }

  log(`=== Cleanup complete: ${digestCount} rows deleted from articles_digest for ${label} ===`)
}

main().catch(e => { err(String(e)); process.exit(1) })
