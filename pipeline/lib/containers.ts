/**
 * lib/containers.ts — Container config and direct SQLite access
 *
 * CONTAINER ROLES:
 *   C1 (port 8001) — PRIMARY. All new accounts are subscribed HERE ONLY via the web UI.
 *                    Has 212 feeds. Source of truth for feed metadata.
 *   C2 (port 8004) — REDUNDANCY. Mirrors 178 of C1's accounts independently.
 *   C3 (port 8003) — REDUNDANCY. Same 178 accounts as C2.
 *
 * Why 3 containers? WeChat rate-limits/blocks by session (faker_id), not by IP.
 * Each container has its own WeChat login session. If C1's session expires or gets
 * throttled, C2/C3 keep fetching the shared 178 accounts. This is NOT for speed —
 * it's pure session redundancy. All 3 do the same work on overlapping accounts.
 *
 * Subscribe NEW accounts via C1 web UI only (http://localhost:8001).
 * When reading for sync, we read ALL 3 containers and deduplicate by original_url.
 * C1 data takes priority in case of any conflict.
 *
 * SQLite field mapping → Supabase:
 *   articles.title        → original_title
 *   articles.description  → original_summary  ← RSS returns a SUMMARY, not full content
 *   articles.url          → original_url
 *   articles.pic_url      → cover_image_url
 *   articles.publish_time → published_at  (unix seconds → ISO timestamptz)
 *   articles.mp_id        → mp_id
 *   (NOT synced initially) original_content, word_count, author_name
 *
 *   feeds.id              → sources.mp_id
 *   feeds.mp_name         → sources.name
 *   feeds.mp_intro        → sources.profile (raw, pre-AI)
 *   feeds.mp_cover        → sources.cover_image_url (not in sources table, informational)
 */

import { DatabaseSync } from 'node:sqlite'
import * as dotenv from 'dotenv'
dotenv.config()

export interface ContainerConfig {
  name: string
  port: number
  dbPath: string
  baseUrl: string
}

export const CONTAINERS: ContainerConfig[] = [
  {
    name:    'C1',
    port:    8001,
    dbPath:  process.env.CONTAINER_C1_DB ?? '/root/workspace/data/original/db.db',
    baseUrl: process.env.CONTAINER_C1_URL ?? 'http://localhost:8001',
  },
  {
    name:    'C2',
    port:    8004,
    dbPath:  process.env.CONTAINER_C2_DB ?? '/root/workspace/data/data2/db.db',
    baseUrl: process.env.CONTAINER_C2_URL ?? 'http://localhost:8004',
  },
  {
    name:    'C3',
    port:    8003,
    dbPath:  process.env.CONTAINER_C3_DB ?? '/root/workspace/data/data3/db.db',
    baseUrl: process.env.CONTAINER_C3_URL ?? 'http://localhost:8003',
  },
]

// ─── Raw row types from SQLite ────────────────────────────────
export interface RawFeed {
  id:         string   // = mp_id e.g. MP_WXS_xxx
  mp_name:    string
  mp_intro:   string | null
  mp_cover:   string | null
  faker_id:   string | null
  status:     number
}

export interface RawArticle {
  id:           string   // composite ID in container, e.g. "mp_id-article_id"
  mp_id:        string
  title:        string
  url:          string
  description:  string | null   // this is the RSS summary
  pic_url:      string | null
  publish_time: number           // unix seconds
}

// ─── SQLite queries ───────────────────────────────────────────

/** Get all feeds from a container's SQLite DB. Returns [] if DB unavailable. */
export function getFeeds(dbPath: string): RawFeed[] {
  try {
    const db = new DatabaseSync(dbPath)
    const rows = db
      .prepare("SELECT id, mp_name, mp_intro, mp_cover, faker_id, status FROM feeds")
      .all() as RawFeed[]
    db.close()
    return rows
  } catch { return [] }
}

/** Get a single feed by mp_id. Returns null if not found. */
export function getFeedByMpId(dbPath: string, mpId: string): RawFeed | null {
  try {
    const db = new DatabaseSync(dbPath)
    const row = db
      .prepare("SELECT id, mp_name, mp_intro, mp_cover, faker_id, status FROM feeds WHERE id = ?")
      .get(mpId) as RawFeed | undefined
    db.close()
    return row ?? null
  } catch { return null }
}

export interface ArticleFilter {
  mpId?:   string    // filter by a specific account
  since?:  Date      // published_at >= since (unix seconds)
  until?:  Date      // published_at <  until (unix seconds)
}

/**
 * Read raw articles from a container's SQLite.
 * Returns [] if DB is unavailable.
 * Only returns rows with a valid url.
 */
export function getArticles(dbPath: string, filter: ArticleFilter = {}): RawArticle[] {
  try {
    const db = new DatabaseSync(dbPath)
    const parts: string[] = ["url IS NOT NULL AND url != ''"]
    const params: (string | number)[] = []

    if (filter.mpId) {
      parts.push('mp_id = ?')
      params.push(filter.mpId)
    }
    if (filter.since) {
      parts.push('publish_time >= ?')
      params.push(Math.floor(filter.since.getTime() / 1000))
    }
    if (filter.until) {
      parts.push('publish_time < ?')
      params.push(Math.floor(filter.until.getTime() / 1000))
    }

    const sql = `SELECT id, mp_id, title, url, description, pic_url, publish_time
                 FROM articles WHERE ${parts.join(' AND ')}`
    const rows = db.prepare(sql).all(...params) as RawArticle[]
    db.close()
    return rows
  } catch { return [] }
}

/**
 * Collect all unique feeds across all containers.
 * Returns a Map<mp_id, RawFeed>, preferring C1 data when multiple containers have the same account.
 */
export function getAllFeeds(): Map<string, RawFeed> {
  const map = new Map<string, RawFeed>()
  // Process in reverse so C1 (index 0) wins (overwrites C2/C3)
  for (const c of [...CONTAINERS].reverse()) {
    for (const f of getFeeds(c.dbPath)) map.set(f.id, f)
  }
  return map
}

/**
 * Collect all unique articles across all containers, deduped by url.
 * C1 data takes priority (overwrites C2/C3 for same URL).
 */
export function getAllArticles(filter: ArticleFilter = {}): Map<string, RawArticle> {
  const map = new Map<string, RawArticle>()
  for (const c of [...CONTAINERS].reverse()) {
    for (const a of getArticles(c.dbPath, filter)) {
      if (a.url) map.set(a.url, a)
    }
  }
  return map
}
