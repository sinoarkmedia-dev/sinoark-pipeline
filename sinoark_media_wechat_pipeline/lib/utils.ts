/**
 * lib/utils.ts — shared constants, helpers, types
 */

// ─── Timezone boundary constants ─────────────────────────────
// All thresholds are expressed in UTC.
// Beijing Time = UTC+8.

/** April 1, 2026 00:00:00 BJT → 2026-03-31 16:00:00 UTC */
export const DIGEST_START = new Date('2026-03-31T16:00:00Z')

/** January 1, 2021 00:00:00 BJT → 2020-12-31 16:00:00 UTC */
export const ARCHIVE_START = new Date('2020-12-31T16:00:00Z')

// ─── Article table routing ────────────────────────────────────
export type ArticleTable = 'articles_digest' | 'articles_archive'

/**
 * Returns which Supabase table an article belongs to based on published_at.
 * Returns null if the article is outside all tracked ranges (before 2021).
 */
export function routeToTable(publishedAt: Date): ArticleTable | null {
  if (publishedAt >= DIGEST_START)  return 'articles_digest'
  if (publishedAt >= ARCHIVE_START) return 'articles_archive'
  return null // older than 2021 — skip
}

// ─── Logging ─────────────────────────────────────────────────
function ts() { return new Date().toISOString().substring(11, 19) }
export const log  = (msg: string) => console.log(`[${ts()}] ${msg}`)
export const warn = (msg: string) => console.warn(`[${ts()}] WARN  ${msg}`)
export const err  = (msg: string) => console.error(`[${ts()}] ERROR ${msg}`)

// ─── Timing ──────────────────────────────────────────────────
export const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

// ─── Monthly range helper (Beijing Time) ─────────────────────
/**
 * Returns the UTC start and end of "last month" in Beijing Time.
 * e.g. called on May 3 BJT → { start: 2026-03-31T16:00Z, end: 2026-04-30T16:00Z }
 */
export function lastMonthRangeUTC(): { start: Date; end: Date; label: string } {
  const nowUTC = new Date()
  const bjtMs  = nowUTC.getTime() + 8 * 3600_000
  const bjt    = new Date(bjtMs)
  const year   = bjt.getUTCFullYear()
  const month  = bjt.getUTCMonth() // 0-based, current month in BJT

  // First moment of last month in BJT, expressed as UTC
  const start = new Date(Date.UTC(year, month - 1, 1) - 8 * 3600_000)
  // First moment of current month in BJT (= exclusive end of last month)
  const end   = new Date(Date.UTC(year, month, 1) - 8 * 3600_000)

  // label = "YYYY-MM" of last month in BJT
  const labelYear  = month === 0 ? year - 1 : year
  const labelMonth = month === 0 ? 12 : month
  const label = `${labelYear}-${String(labelMonth).padStart(2, '0')}`

  return { start, end, label }
}

// ─── Article insert record type ───────────────────────────────
export interface ArticleRecord {
  source_id:        string
  source_name:      string
  mp_id:            string
  original_title:   string
  original_summary: string | null   // from RSS (it's a summary, NOT full text)
  original_url:     string
  cover_image_url:  string | null
  published_at:     Date
  // These are always null on initial fetch — populated later via separate mechanism
  original_content: null
  word_count:       null
  author_name:      null
}
