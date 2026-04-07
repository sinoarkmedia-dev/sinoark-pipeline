#!/usr/bin/env node
/**
 * sources/rename-source.ts
 *
 * Updates a source's display name and preserves the old name in old_names[].
 * Also updates source_name on all existing articles in both tables.
 *
 * Usage:
 *   npm run rename-source -- --mp-id MP_WXS_3236757533 --new-name "量子位AI"
 *   npm run rename-source -- --id <uuid> --new-name "量子位AI"
 */

import { Command } from 'commander'
import { sb, clearSourceCache } from '../lib/supabase.ts'
import { log, warn, err } from '../lib/utils.ts'

const program = new Command()
program
  .name('rename-source')
  .option('--mp-id <mpId>',     'Identify source by mp_id')
  .option('--id <id>',          'Identify source by Supabase uuid')
  .requiredOption('--new-name <name>', 'New display name')
  .parse()

const opts = program.opts<{ mpId?: string; id?: string; newName: string }>()

async function main() {
  if (!opts.mpId && !opts.id) {
    err('Provide either --mp-id or --id')
    process.exit(1)
  }

  // ── 1. Fetch current source ───────────────────────────────
  let query = sb().from('sources').select('id, name, old_names, mp_id')
  if (opts.id)   query = query.eq('id', opts.id) as typeof query
  if (opts.mpId) query = query.eq('mp_id', opts.mpId) as typeof query

  const { data: source, error } = await (query as any).maybeSingle()
  if (error) { err(error.message); process.exit(1) }
  if (!source) { err('Source not found'); process.exit(1) }

  const oldName  = source.name as string
  const newName  = opts.newName.trim()
  const oldNames = (source.old_names as string[]) ?? []

  if (oldName === newName) {
    warn(`Name is already "${newName}" — nothing to do`)
    process.exit(0)
  }

  if (oldNames.includes(oldName)) {
    warn(`"${oldName}" is already in old_names[]`)
  }

  log(`Renaming "${oldName}" → "${newName}" (source: ${source.id})`)

  // ── 2. Update sources table ───────────────────────────────
  const updatedOldNames = [...new Set([...oldNames, oldName])]
  const { error: srcErr } = await sb()
    .from('sources')
    .update({ name: newName, old_names: updatedOldNames, updated_at: new Date().toISOString() })
    .eq('id', source.id)

  if (srcErr) { err(`sources update failed: ${srcErr.message}`); process.exit(1) }
  log(`✓ sources table updated`)

  // ── 3. Propagate source_name to articles_digest ───────────
  const { error: dErr } = await sb()
    .from('articles_digest')
    .update({ source_name: newName })
    .eq('source_id', source.id)
  if (dErr) warn(`articles_digest update failed: ${dErr.message}`)
  else log(`✓ articles_digest.source_name updated`)

  // ── 4. Propagate source_name to articles_archive ──────────
  const { error: aErr } = await sb()
    .from('articles_archive')
    .update({ source_name: newName })
    .eq('source_id', source.id)
  if (aErr) warn(`articles_archive update failed: ${aErr.message}`)
  else log(`✓ articles_archive.source_name updated`)

  clearSourceCache()
  log(`Done. old_names: [${updatedOldNames.join(', ')}]`)
}

main().catch(e => { err(String(e)); process.exit(1) })
