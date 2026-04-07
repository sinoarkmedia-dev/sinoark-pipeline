#!/usr/bin/env node
/**
 * digest/new-source-backfill.ts
 *
 * When a new source is added, fetch ALL its articles from April 1, 2026 onwards
 * and insert them into articles_digest.
 *
 * Called automatically by add-source.ts (as background process).
 * Can also be run manually:
 *   npm run digest-new-source -- --source-id <uuid>
 *   npm run digest-new-source -- --mp-id MP_WXS_3236757533
 */

import { Command } from 'commander'
import { sb, batchInsert, getSourceByMpId } from '../lib/supabase.ts'
import { getAllArticles } from '../lib/containers.ts'
import { DIGEST_START, log, warn, err, type ArticleRecord } from '../lib/utils.ts'

const program = new Command()
program
  .option('--source-id <id>',  'Supabase source uuid')
  .option('--mp-id <mpId>',    'WeChat mp_id (alternative to --source-id)')
  .parse()

const opts = program.opts<{ sourceId?: string; mpId?: string }>()

async function main() {
  // ── Resolve source ────────────────────────────────────────
  let sourceId: string
  let sourceName: string
  let mpId: string

  if (opts.sourceId) {
    const { data, error } = await sb()
      .from('sources')
      .select('id, name, mp_id')
      .eq('id', opts.sourceId)
      .single()
    if (error || !data) { err(`Source not found: ${opts.sourceId}`); process.exit(1) }
    sourceId = data.id; sourceName = data.name; mpId = data.mp_id
  } else if (opts.mpId) {
    const s = await getSourceByMpId(opts.mpId)
    if (!s) { err(`Source not found for mp_id: ${opts.mpId}`); process.exit(1) }
    sourceId = s.id; sourceName = s.name; mpId = opts.mpId
  } else {
    err('Provide --source-id or --mp-id'); process.exit(1)
  }

  log(`=== Digest backfill for "${sourceName}" (${mpId}) ===`)
  log(`Window: ${DIGEST_START.toISOString()} → now`)

  // ── Mark started ─────────────────────────────────────────
  await sb().from('sources').update({ digest_backfill_started_at: new Date().toISOString() }).eq('id', sourceId)

  // ── Fetch from all containers (dedup by URL) ──────────────
  const rawMap = getAllArticles({ mpId, since: DIGEST_START })
  log(`Found ${rawMap.size} articles in containers since DIGEST_START`)

  const records: ArticleRecord[] = []
  for (const raw of rawMap.values()) {
    records.push({
      source_id:        sourceId,
      source_name:      sourceName,
      mp_id:            mpId,
      original_title:   raw.title?.trim() || '(no title)',
      original_summary: raw.description?.trim() || null,
      original_url:     raw.url,
      cover_image_url:  raw.pic_url || null,
      published_at:     new Date(raw.publish_time * 1000),
      original_content: null,
      word_count:       null,
      author_name:      null,
    })
  }

  const inserted = await batchInsert('articles_digest', records)
  log(`Inserted ${inserted} articles into articles_digest`)

  // ── Mark completed ────────────────────────────────────────
  await sb().from('sources').update({ digest_backfill_completed: true }).eq('id', sourceId)
  log(`=== Digest backfill complete for "${sourceName}" ===`)
}

main().catch(e => { err(String(e)); process.exit(1) })
