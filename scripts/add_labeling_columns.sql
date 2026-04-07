-- Add new columns for enhanced labeling
-- Run this in Supabase SQL Editor

-- Add content_type column to articles
ALTER TABLE public.articles
ADD COLUMN IF NOT EXISTS content_type text
CHECK (content_type IN (
  'research',      -- academic oriented
  'report',        -- application/policy/business oriented
  'commentary',    -- opinion/analysis
  'newsletter',    -- regular updates/digests
  'education',     -- tutorials/explanations
  'discussion',    -- conversations/debates
  'notification',  -- announcements/updates
  'press_release', -- official statements
  'ads',           -- advertisements/promotions
  'news',          -- news reporting
  'interview',     -- Q&A format
  'other'          -- miscellaneous
));

-- Add other labeling columns if not exist
ALTER TABLE public.articles
ADD COLUMN IF NOT EXISTS china_related boolean;

ALTER TABLE public.articles
ADD COLUMN IF NOT EXISTS theme text
CHECK (theme IN ('academic', 'tech', 'business', 'finance', 'people', 'education', 'soft_ads', 'policy', 'other'));

ALTER TABLE public.articles
ADD COLUMN IF NOT EXISTS key_entities text[];

ALTER TABLE public.articles
ADD COLUMN IF NOT EXISTS is_original boolean;

ALTER TABLE public.articles
ADD COLUMN IF NOT EXISTS source_reference text;

ALTER TABLE public.articles
ADD COLUMN IF NOT EXISTS keywords text[];

-- Add indexes for filtering
CREATE INDEX IF NOT EXISTS idx_articles_content_type ON public.articles(content_type);
CREATE INDEX IF NOT EXISTS idx_articles_china_related ON public.articles(china_related);
CREATE INDEX IF NOT EXISTS idx_articles_theme ON public.articles(theme);
CREATE INDEX IF NOT EXISTS idx_articles_tech_china ON public.articles(tech, china_related) WHERE tech = true;

-- Update sources table to ensure name_en exists
ALTER TABLE public.sources
ALTER COLUMN name_en DROP NOT NULL;

COMMENT ON COLUMN public.articles.content_type IS 'Type of content: research, report, commentary, newsletter, education, discussion, notification, press_release, ads, news, interview, other';
COMMENT ON COLUMN public.articles.china_related IS 'Whether the article is primarily about China or Chinese entities';
COMMENT ON COLUMN public.articles.theme IS 'Main theme of the article';
COMMENT ON COLUMN public.articles.content_type IS 'Content format/type';
