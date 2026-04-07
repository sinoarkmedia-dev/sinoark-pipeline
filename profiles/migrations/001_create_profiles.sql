-- Profiles table: one company, one person, one product per day
-- Run this once in Supabase SQL editor before using generate_profiles.py

CREATE TABLE IF NOT EXISTS public.profiles (
    id          bigserial PRIMARY KEY,
    date        date        NOT NULL,
    category    text        NOT NULL CHECK (category IN ('company', 'person', 'product')),
    subject     text        NOT NULL,   -- English name
    subject_zh  text,                   -- Chinese name (if available)
    html_content text       NOT NULL,
    source_articles jsonb   DEFAULT '[]'::jsonb,  -- [{id, title, url, source_name}]
    article_count integer   DEFAULT 0,
    generated_at timestamptz DEFAULT now(),
    UNIQUE (date, category)
);

CREATE INDEX IF NOT EXISTS idx_profiles_date     ON public.profiles (date DESC);
CREATE INDEX IF NOT EXISTS idx_profiles_category ON public.profiles (category);
CREATE INDEX IF NOT EXISTS idx_profiles_subject  ON public.profiles (lower(subject));
