#!/usr/bin/env node
/**
 * sources/add-source.ts
 *
 * Registers a new WeChat account as a source in Supabase, then auto-triggers:
 *   1. AI profile generation (two-step: Qwen Chinese → Gemini English translation)
 *   2. Digest backfill  — fetches articles from April 1, 2026 onwards
 *   3. Archive backfill — fetches articles from Jan 1, 2021 to April 1, 2026
 *
 * Pre-requisite: subscribe the account in the we-mp-rss web UI (port 8001)
 * BEFORE running this script. The account must appear in the container's feeds table.
 *
 * Usage:
 *   npm run add-source -- --mp-id MP_WXS_3236757533
 *   npm run add-source -- --mp-id MP_WXS_3236757533 --name-en "Quantum Bit" --no-backfill
 */

import { Command } from 'commander'
import { spawn } from 'node:child_process'
import { createWriteStream } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { sb, clearSourceCache } from '../lib/supabase.ts'
import { getAllFeeds, getFeedByMpId, CONTAINERS } from '../lib/containers.ts'
import { generateSourceProfile } from '../lib/profile.ts'
import { log, warn, err } from '../lib/utils.ts'

const __dir = dirname(fileURLToPath(import.meta.url))

const program = new Command()
program
  .name('add-source')
  .description('Register a new WeChat official account as a SinoArk source')
  .requiredOption('--mp-id <mpId>',   'WeChat mp_id, e.g. MP_WXS_3236757533')
  .option('--name <name>',            'Override display name (default: taken from container)')
  .option('--name-en <nameEn>',       'English name (optional)')
  .option('--no-profile',             'Skip AI profile generation')
  .option('--no-backfill',            'Skip triggering digest + archive backfills')
  .parse()

const opts = program.opts<{
  mpId: string
  name?: string
  nameEn?: string
  profile: boolean
  backfill: boolean
}>()

async function main() {
  const mpId = opts.mpId

  // ── 1. Check duplicate ────────────────────────────────────
  const { data: existing } = await sb()
    .from('sources')
    .select('id, name')
    .eq('mp_id', mpId)
    .maybeSingle()

  if (existing) {
    warn(`Source already exists: "${existing.name}" (${mpId}) — id: ${existing.id}`)
    process.exit(0)
  }

  // ── 2. Find account in containers ────────────────────────
  let feed: { mp_name: string; mp_intro: string | null; mp_cover: string | null } | null = null
  for (const c of CONTAINERS) {
    const f = getFeedByMpId(c.dbPath, mpId)
    if (f) { feed = f; log(`Found ${mpId} in ${c.name}`); break }
  }

  if (!feed) {
    err(`mp_id "${mpId}" not found in any container.`)
    err('Subscribe the account in the we-mp-rss web UI (http://localhost:8001) first, then retry.')
    process.exit(1)
  }

  const name = opts.name ?? feed.mp_name
  log(`Adding source: "${name}" (${mpId})`)

  // ── 3. Generate AI profile ────────────────────────────────
  let profile: Partial<{
    profile: string; profile_en: string; entity_nature: string
    focus: string; about_tech: boolean; about_ai: boolean
  }> = {}

  if (opts.profile) {
    log('Generating AI profile via Qwen (Chinese) → Gemini (English)...')
    const p = await generateSourceProfile(name, feed.mp_intro)
    if (p) {
      profile = p
      log(`  entity_nature: ${p.entity_nature} | about_ai: ${p.about_ai} | about_tech: ${p.about_tech}`)
    } else {
      warn('Profile generation failed — source will be created without profile (can be filled later)')
    }
  }

  // ── 4. Insert into Supabase ───────────────────────────────
  const { data: source, error } = await sb()
    .from('sources')
    .insert({
      mp_id:                     mpId,
      name,
      name_en:                   opts.nameEn ?? null,
      profile:                   profile.profile ?? null,
      profile_en:                profile.profile_en ?? null,
      entity_nature:             profile.entity_nature ?? null,
      focus:                     profile.focus ?? null,
      about_tech:                profile.about_tech ?? null,
      about_ai:                  profile.about_ai ?? null,
      status:                    'active',
      old_names:                 [],
      archive_completed:         false,
      digest_backfill_completed: false,
    })
    .select('id, name')
    .single()

  if (error) {
    err(`Failed to insert source: ${error.message}`)
    process.exit(1)
  }

  log(`✓ Created source "${source.name}" → id: ${source.id}`)
  clearSourceCache()

  // ── 5. Trigger backfills ──────────────────────────────────
  if (!opts.backfill) {
    log('Skipping backfills (--no-backfill). Run manually:')
    log(`  npm run digest-new-source  -- --source-id ${source.id}`)
    log(`  npm run archive-new-source -- --source-id ${source.id}`)
    return
  }

  log('Spawning digest backfill in background...')
  spawnBackground('digest/new-source-backfill.ts', ['--source-id', source.id])

  log('Spawning archive backfill in background...')
  spawnBackground('archive/new-source-archive.ts', ['--source-id', source.id])

  log('Both backfills are running in the background.')
  log('Check logs in /tmp/sinoark-*.log or use: tail -f /tmp/sinoark-digest-backfill.log')
}

function spawnBackground(script: string, args: string[]) {
  const scriptPath = resolve(__dir, '..', script)
  const logFile = `/tmp/sinoark-${script.replace(/\//g, '-').replace('.ts','')}.log`
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
  log(`  → ${script} [pid ${child.pid}] logging to ${logFile}`)
}

main().catch(e => { err(String(e)); process.exit(1) })
