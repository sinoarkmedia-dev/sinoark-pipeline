#!/usr/bin/env node
/**
 * sources/list-sources.ts — Print all sources with article counts from both tables.
 * Usage: npm run list-sources
 */

import { sb } from '../lib/supabase.ts'
import { log } from '../lib/utils.ts'

async function main() {
  const { data: sources, error } = await sb()
    .from('sources')
    .select('id, name, mp_id, status, about_ai, about_tech, archive_completed, digest_backfill_completed, created_at')
    .order('name')

  if (error) { console.error(error.message); process.exit(1) }

  log(`Total sources: ${sources!.length}\n`)

  const rows: Array<{
    name: string; mpId: string; status: string
    digest: number; archive: number
    aiDone: boolean; archDone: boolean
    aboutAi: boolean | null; createdAt: string
  }> = []

  for (const s of sources!) {
    const [{ count: dc }, { count: ac }] = await Promise.all([
      sb().from('articles_digest').select('*', { count: 'exact', head: true }).eq('source_id', s.id),
      sb().from('articles_archive').select('*', { count: 'exact', head: true }).eq('source_id', s.id),
    ])
    rows.push({
      name:      s.name,
      mpId:      s.mp_id ?? '—',
      status:    s.status,
      digest:    dc ?? 0,
      archive:   ac ?? 0,
      aiDone:    !!s.digest_backfill_completed,
      archDone:  !!s.archive_completed,
      aboutAi:   s.about_ai,
      createdAt: (s.created_at as string)?.substring(0, 10) ?? '—',
    })
  }

  rows.sort((a, b) => b.digest + b.archive - a.digest - a.archive)

  const col = (s: string | number | boolean | null, w: number) =>
    String(s ?? '?').substring(0, w).padEnd(w)

  console.log(
    col('#', 4) +
    col('Name', 28) +
    col('Digest', 8) +
    col('Archive', 9) +
    col('D-done', 7) +
    col('A-done', 7) +
    col('AI', 5) +
    col('Added', 12)
  )
  console.log('─'.repeat(80))
  rows.forEach((r, i) => {
    console.log(
      col(i + 1, 4) +
      col(r.name, 28) +
      col(r.digest, 8) +
      col(r.archive, 9) +
      col(r.aiDone ? '✓' : '…', 7) +
      col(r.archDone ? '✓' : '…', 7) +
      col(r.aboutAi ? 'AI' : '', 5) +
      col(r.createdAt, 12)
    )
  })

  const totalD = rows.reduce((s, r) => s + r.digest, 0)
  const totalA = rows.reduce((s, r) => s + r.archive, 0)
  console.log('─'.repeat(80))
  console.log(`Total: ${totalD.toLocaleString()} digest | ${totalA.toLocaleString()} archive | ${(totalD + totalA).toLocaleString()} combined`)
}

main().catch(e => { console.error(e); process.exit(1) })
