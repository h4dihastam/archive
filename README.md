# Archive Hub

آرشیو لینک‌ها (توییت/پست) با:
- **SingleFile-style HTML** — یک فایل کامل آفلاین (CSS/تصاویر inline)
- **Screenshot** — تصویر کامل صفحه
- **لینک آرشیو** — `/view/{id}` مثل Wayback Machine
- **ربات تلگرام** — آرشیو از داخل تلگرام، ارسال به هر کانال

## راه‌اندازی

```bash
python -m venv .venv
source .venv/bin/activate
./scripts/install_deps.sh
python -m playwright install chromium
cp .env.example .env
# .env را ویرایش کنید
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## تنظیم Supabase (رایگان)

1. بروید به [supabase.com](https://supabase.com) و پروژه رایگان بسازید
2. در داشبورد → **SQL Editor** فایل `supabase_schema.sql` را اجرا کنید
3. در **Settings → API**:
   - `SUPABASE_URL` = Project URL
   - `SUPABASE_KEY` = `service_role` key (نه anon)
4. در **Storage → Buckets**: باکت `archives` با تنظیم **Public** بسازید

## تنظیم روی Render

در **Environment Variables** اضافه کنید:

| متغیر | مقدار |
|---|---|
| `TELEGRAM_BOT_TOKEN` | توکن از @BotFather |
| `WEBHOOK_URL` | `https://your-app.onrender.com` |
| `BOT_PASSWORD` | رمز دلخواه |
| `SUPABASE_URL` | آدرس Supabase |
| `SUPABASE_KEY` | service_role key |

بعد از deploy، یک بار باز کنید:
```
https://your-app.onrender.com/bot/set_webhook
```

## استفاده از ربات

1. `/start` → رمز عبور وارد کنید
2. **📢 تنظیم کانال مقصد** → آیدی کانال را بدهید:
   - `@mychannel` — کانال عمومی
   - `-1001234567890` — کانال/گروه خصوصی (ربات را ادمین کنید)
   - `me` — ارسال به خودتان
3. **🗄 آرشیو لینک** → لینک بدهید

## لینک آرشیو

هر آرشیو یک لینک عمومی دارد:
```
https://your-app.onrender.com/view/{archive_id}
```
