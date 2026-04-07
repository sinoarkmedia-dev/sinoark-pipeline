/**
 * lib/supabase.ts — Supabase client singleton + article batch insert
 */
import { createClient, type SupabaseClient } from '@supabase/supabase-js'
import * as dotenv from 'dotenv'
import type { ArticleRecord, ArticleTable } from './utils.ts'
import { log } from './utils.ts'
dotenv.config()

let _sb: SupabaseClient | null = null

export function sb(): SupabaseClient {
  if (_sb) return _sb
  const url = process.env.SUPABASE_URL
  const key = process.env.SUPABASE_SERVICE_KEY
  if (!url || !key) throw new Error('Missing SUPABASE_URL or SUPABASE_SERVICE_KEY')
  _sb = createClient(url, key, { auth: { persistSession: false } })
  return _sb
}

// ─── Source lookup cache ──────────────────────────────────────
export interface SourceInfo { id: string; name: string }

let _sourceCache: Map<string, SourceInfo> | null = null

export async function getSourceByMpId(mpId: string): Promise<SourceInfo | null> {
  if (!_sourceCache) {
    const { data, error } = await sb().from('sources').select('id, name, mp_id')
    if (error) throw new Error(`Failed to load sources: ${error.message}`)
    _sourceCache = new Map((data ?? []).map(s => [s.mp_id as string, { id: s.id as string, name: s.name as string }]))
  }
  return _sourceCache.get(mpId) ?? null
}

export function clearSourceCache() { _sourceCache = null }

// ─── Batch insert ─────────────────────────────────────────────
const BATCH = 500

/**
 * Inserts article records into the given table in batches of 500.
 * Duplicates (by original_url) are silently ignored.
 * Returns count of rows successfully queued (not guaranteed new — dedup happens in DB).
 */
export async function batchInsert(
  table: ArticleTable,
  articles: ArticleRecord[]
): Promise<number> {
  if (articles.length === 0) return 0

  const rows = articles.map(a => ({
    source_id:        a.source_id,
    source_name:      a.source_name,
    mp_id:            a.mp_id,
    original_title:   a.original_title,
    original_summary: a.original_summary,
    original_content: null,
    original_url:     a.original_url,
    cover_image_url:  a.cover_image_url,
    published_at:     a.published_at.toISOString(),
    word_count:       null,
    author_name:      null,
    status:           'raw',
  }))

  let inserted = 0
  for (let i = 0; i < rows.length; i += BATCH) {
    const slice = rows.slice(i, i + BATCH)
    const { error } = await sb()
      .from(table)
      .upsert(slice, { onConflict: 'original_url', ignoreDuplicates: true })
    if (error) log(`  insert error on ${table}: ${error.message}`)
    else inserted += slice.length
  }
  return inserted
}
