-- ============================================================
-- SinoArk WeChat Pipeline — Full Schema
-- Run in Supabase SQL Editor.
-- Safe to re-run (uses IF NOT EXISTS / IF EXISTS guards).
-- ============================================================

-- ─── 1. Extend existing sources table ───────────────────────
-- Add new columns if they don't exist yet.
-- (Existing rows, data, and RLS policies are preserved.)

ALTER TABLE public.sources
  ADD COLUMN IF NOT EXISTS mp_id                     text,
  ADD COLUMN IF NOT EXISTS old_names                 text[]      DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS archive_completed         boolean     DEFAULT false,
  ADD COLUMN IF NOT EXISTS archive_started_at        timestamptz,
  ADD COLUMN IF NOT EXISTS digest_backfill_completed boolean     DEFAULT false,
  ADD COLUMN IF NOT EXISTS digest_backfill_started_at timestamptz,
  ADD COLUMN IF NOT EXISTS focus_en                  text;

-- Unique index on mp_id (WeChat account ID, e.g. MP_WXS_3236757533)
CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_mp_id ON public.sources(mp_id)
  WHERE mp_id IS NOT NULL;

-- Back-fill mp_id from wemprss_account_id if that column exists
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
    AND table_name = 'sources'
    AND column_name = 'wemprss_account_id'
  ) THEN
    UPDATE public.sources
    SET mp_id = wemprss_account_id
    WHERE mp_id IS NULL AND wemprss_account_id IS NOT NULL;
  END IF;
END $$;


-- ─── 2. articles_digest ──────────────────────────────────────
-- Holds articles published on or after April 1, 2026 00:00 BJT
-- (= 2026-03-31 16:00:00 UTC).
-- Monthly backup copies last month's rows to articles_archive,
-- then those rows are deleted here on the 5th of the month.

CREATE TABLE IF NOT EXISTS public.articles_digest (
  id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id        uuid        NOT NULL REFERENCES public.sources(id),
  source_name      text        NOT NULL,
  mp_id            text        NOT NULL,
  original_title   text        NOT NULL,
  original_summary text,                       -- RSS "content" (summary only, NOT full text)
  original_content text,                       -- full article body (fetched later)
  original_url     text        NOT NULL,
  cover_image_url  text,
  published_at     timestamptz NOT NULL,
  scraped_at       timestamptz NOT NULL DEFAULT now(),
  word_count       int,                        -- calculated from original_content when available
  author_name      text,                       -- fetched with full text later
  status           text        NOT NULL DEFAULT 'raw'
                   CHECK (status IN ('raw','translated','reviewed','published','rejected')),
  -- Translation & AI enrichment (populated later)
  translated_title   text,
  translated_summary text,
  about_tech         boolean,
  about_ai           boolean,
  china_related      boolean,
  theme              text,
  key_entities       jsonb NOT NULL DEFAULT '[]',
  keywords           jsonb NOT NULL DEFAULT '[]',
  content_type       text,
  is_original        boolean,
  source_reference   text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT articles_digest_url_unique UNIQUE (original_url)
);

CREATE INDEX IF NOT EXISTS idx_digest_source_date  ON public.articles_digest(source_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_digest_published     ON public.articles_digest(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_digest_mp_id         ON public.articles_digest(mp_id);
CREATE INDEX IF NOT EXISTS idx_digest_status        ON public.articles_digest(status);

ALTER TABLE public.articles_digest ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='articles_digest' AND policyname='Service role full access') THEN
    CREATE POLICY "Service role full access" ON public.articles_digest FOR ALL USING (true);
  END IF;
END $$;


-- ─── 3. articles_archive ─────────────────────────────────────
-- Holds articles published between Jan 1, 2021 00:00 BJT
-- (= 2020-12-31 16:00:00 UTC) and April 1, 2026 00:00 BJT.
-- Also receives monthly backups from articles_digest.

CREATE TABLE IF NOT EXISTS public.articles_archive (
  id               uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id        uuid        NOT NULL REFERENCES public.sources(id),
  source_name      text        NOT NULL,
  mp_id            text        NOT NULL,
  original_title   text        NOT NULL,
  original_summary text,
  original_content text,
  original_url     text        NOT NULL,
  cover_image_url  text,
  published_at     timestamptz NOT NULL,
  scraped_at       timestamptz NOT NULL DEFAULT now(),
  word_count       int,
  author_name      text,
  status           text        NOT NULL DEFAULT 'raw'
                   CHECK (status IN ('raw','translated','reviewed','published','rejected')),
  translated_title   text,
  translated_summary text,
  about_tech         boolean,
  about_ai           boolean,
  china_related      boolean,
  theme              text,
  key_entities       jsonb NOT NULL DEFAULT '[]',
  keywords           jsonb NOT NULL DEFAULT '[]',
  content_type       text,
  is_original        boolean,
  source_reference   text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT articles_archive_url_unique UNIQUE (original_url)
);

CREATE INDEX IF NOT EXISTS idx_archive_source_date ON public.articles_archive(source_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_archive_published    ON public.articles_archive(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_archive_mp_id        ON public.articles_archive(mp_id);
CREATE INDEX IF NOT EXISTS idx_archive_status       ON public.articles_archive(status);

ALTER TABLE public.articles_archive ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='articles_archive' AND policyname='Service role full access') THEN
    CREATE POLICY "Service role full access" ON public.articles_archive FOR ALL USING (true);
  END IF;
END $$;


-- ─── 4. Helper: updated_at auto-refresh ──────────────────────
CREATE OR REPLACE FUNCTION public.touch_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END $$;

DROP TRIGGER IF EXISTS trg_digest_updated_at  ON public.articles_digest;
DROP TRIGGER IF EXISTS trg_archive_updated_at ON public.articles_archive;

CREATE TRIGGER trg_digest_updated_at
  BEFORE UPDATE ON public.articles_digest
  FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

CREATE TRIGGER trg_archive_updated_at
  BEFORE UPDATE ON public.articles_archive
  FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
