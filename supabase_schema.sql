-- ================================================================
-- Archive Hub — Supabase Schema
-- اجرا کنید در: Supabase Dashboard → SQL Editor
-- ================================================================

-- جدول آرشیوها
CREATE TABLE IF NOT EXISTS archives (
    id           UUID PRIMARY KEY,
    url          TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    screenshot_url TEXT DEFAULT '',
    html_url     TEXT DEFAULT '',
    raw_url      TEXT DEFAULT '',
    folder       TEXT DEFAULT ''
);

-- ایندکس برای جستجوی سریع
CREATE INDEX IF NOT EXISTS archives_created_at_idx ON archives (created_at DESC);
CREATE INDEX IF NOT EXISTS archives_url_idx ON archives (url);

-- ================================================================
-- Supabase Storage Bucket
-- ================================================================
-- در داشبورد Supabase → Storage → New Bucket:
--   Name: archives
--   Public: YES (برای سرو مستقیم فایل‌ها)
--
-- یا با SQL:
INSERT INTO storage.buckets (id, name, public)
VALUES ('archives', 'archives', true)
ON CONFLICT (id) DO NOTHING;

-- ================================================================
-- RLS Policies (Row Level Security)
-- ================================================================

-- غیرفعال کردن RLS برای جدول archives (سرویس ما با service key کار می‌کنه)
ALTER TABLE archives DISABLE ROW LEVEL SECURITY;

-- اگر می‌خواهید RLS فعال بماند:
-- ALTER TABLE archives ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY "service_role_all" ON archives
--     FOR ALL USING (true);


-- ================================================================
-- جدول کاربران ربات
-- ================================================================
CREATE TABLE IF NOT EXISTS bot_users (
    user_id      BIGINT PRIMARY KEY,
    username     TEXT DEFAULT '',
    full_name    TEXT DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ستون‌های جدید برای جدول archives
ALTER TABLE archives ADD COLUMN IF NOT EXISTS saved_by_user_id BIGINT;
ALTER TABLE archives ADD COLUMN IF NOT EXISTS saved_by_username TEXT DEFAULT '';

CREATE INDEX IF NOT EXISTS archives_user_idx ON archives (saved_by_user_id);

-- ستون‌های اطلاعات پست توییتر
ALTER TABLE archives ADD COLUMN IF NOT EXISTS post_author TEXT DEFAULT '';
ALTER TABLE archives ADD COLUMN IF NOT EXISTS post_username TEXT DEFAULT '';
ALTER TABLE archives ADD COLUMN IF NOT EXISTS post_date TEXT DEFAULT '';
ALTER TABLE archives ADD COLUMN IF NOT EXISTS post_title TEXT DEFAULT '';
