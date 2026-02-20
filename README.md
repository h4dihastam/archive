# Archive Hub

یک سرویس ساده برای آرشیو کردن لینک (مثل توییت/پست) به‌صورت:
- HTML خام (Fetch)
- HTML رندرشده (Headless browser)
- Screenshot

و ذخیره خروجی‌ها در:
- Local storage
- Telegram (از طریق Bot API)
- Dropbox
- Google Drive

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open: `http://localhost:8000`

## Env

برای فعال‌سازی هر مقصد، متغیرهای مربوطه را در `.env` پر کنید.

## Notes

- اگر تنظیمات مقصدی وارد نشده باشد، آپلود آن مقصد رد می‌شود ولی خروجی لوکال ذخیره می‌گردد.
- برای رندر و اسکرین‌شات از Playwright استفاده می‌شود.
