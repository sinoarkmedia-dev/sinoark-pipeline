#!/usr/bin/env node
/**
 * sources/sync-sources.ts
 *
 * Reads feeds from C1 container (the only place new accounts are subscribed)
 * and ensures every feed has a matching row in Supabase sources table.
 *
 * For any feed found in C1 but not in Supabase:
 *   1. Generates AI profile via two-step approach (Qwen Chinese → Gemini English translation)
 *      - Batched: 1 Qwen call per 10 new accounts, 1 Gemini call per 10
 *   2. Inserts into sources table
 *   3. Spawns digest-backfill + archive-backfill in background
 *
 * Runs every 2 hours via cron (minute 0, every 2 hours):
 *   crontab: 0 *\/2 * * *
 */

import { spawn } from 'node:child_process'
import { createWriteStream } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { sb, clearSourceCache } from '../lib/supabase.ts'
import { getFeeds, CONTAINERS } from '../lib/containers.ts'
import { batchGenerateProfiles } from '../lib/profile.ts'
import { log, warn } from '../lib/utils.ts'

const __dir = dirname(fileURLToPath(import.meta.url))

async function main() {
  log('=== sync-sources starting (C1 → Supabase) ===')

  // ── 1. Read all feeds from C1 (primary container only) ───
  const c1 = CONTAINERS[0]  // C1 is always index 0
  const c1Feeds = getFeeds(c1.dbPath)
  log(`C1 has ${c1Feeds.length} feeds`)

  // ── 2. Load existing mp_ids from Supabase ────────────────
  const { data: existing } = await sb().from('sources').select('mp_id, name')
  const existingMpIds = new Set((existing ?? []).map(s => s.mp_id as string))
  log(`Supabase has ${existingMpIds.size} sources`)

  // ── 3. Find new feeds in C1 not yet in Supabase ──────────
  const newFeeds = c1Feeds.filter(f => f.id && !existingMpIds.has(f.id))
  if (newFeeds.length === 0) {
    log('No new accounts found — nothing to do')
    return
  }
  log(`Found ${newFeeds.length} new account(s) to register:`)
  newFeeds.forEach(f => log(`  + ${f.mp_name} (${f.id})`))

  // ── 4. Batch generate profiles (Qwen → Gemini) ──────────
  log('Generating profiles via Qwen (Chinese) → Gemini (English)...')
  const allProfiles = await batchGenerateProfiles(
    newFeeds.map(f => ({ mpName: f.mp_name, mpIntro: f.mp_intro }))
  )

  // ── 5. Insert new sources into Supabase ──────────────────
  const insertedSourceIds: string[] = []

  for (let i = 0; i < newFeeds.length; i++) {
    const feed    = newFeeds[i]
    const profile = allProfiles[i]

    const { data: source, error } = await sb()
      .from('sources')
      .insert({
        mp_id:                     feed.id,
        name:                      feed.mp_name,
        name_en:                   profile?.name_en ?? null,
        profile:                   profile?.profile ?? null,
        profile_en:                profile?.profile_en ?? null,
        entity_nature:             profile?.entity_nature ?? null,
        focus:                     profile?.focus ?? null,
        focus_en:                  profile?.focus_en ?? null,
        about_tech:                profile?.about_tech ?? null,
        about_ai:                  profile?.about_ai ?? null,
        status:                    'active',
        old_names:                 [],
        archive_completed:         false,
        digest_backfill_completed: false,
      })
      .select('id, name')
      .single()

    if (error) {
      // mp_id unique violation = already exists (race condition) — skip
      if (error.code === '23505') {
        warn(`  "${feed.mp_name}" already exists (race condition) — skipping`)
        continue
      }
      warn(`  Failed to insert "${feed.mp_name}": ${error.message}`)
      continue
    }

    log(`  ✓ Created "${source.name}" (${source.id}) | AI: ${profile ? 'ok' : 'skipped'}`)
    insertedSourceIds.push(source.id)
  }

  clearSourceCache()

  // ── 6. Trigger backfills for newly created sources ───────
  if (insertedSourceIds.length === 0) {
    log('No sources successfully inserted — skipping backfills')
    return
  }

  log(`\nSpawning backfills for ${insertedSourceIds.length} new source(s)...`)
  for (const id of insertedSourceIds) {
    spawnBackground('digest/new-source-backfill.ts', ['--source-id', id], `digest-bf-${id.substring(0,8)}`)
    spawnBackground('archive/new-source-archive.ts', ['--source-id', id], `archive-bf-${id.substring(0,8)}`)
  }

  log(`=== sync-sources done — ${insertedSourceIds.length} new source(s) registered ===`)
}

function spawnBackground(script: string, args: string[], tag: string) {
  const scriptPath = resolve(__dir, '..', script)
  const logFile    = `/tmp/sinoark-${tag}.log`
  const child = spawn(
    process.execPath,
    ['--no-warnings=ExperimentalWarning', '--import', 'tsx/esm', scriptPath, ...args],
    {
      detached: true,
      stdio:    ['ignore', 'pipe', 'pipe'],
      env:      { ...process.env },
      cwd:      resolve(__dir, '..'),
    }
  )
  const ws = createWriteStream(logFile, { flags: 'a' })
  child.stdout?.pipe(ws)
  child.stderr?.pipe(ws)
  child.unref()
  log(`  → ${script} [pid ${child.pid}] → ${logFile}`)
}

main().catch(e => { console.error(e); process.exit(1) })
