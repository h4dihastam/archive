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
./scripts/install_deps.sh
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open: `http://localhost:8000`

## Env

برای فعال‌سازی هر مقصد، متغیرهای مربوطه را در `.env` پر کنید.

## Notes

- اگر تنظیمات مقصدی وارد نشده باشد، آپلود آن مقصد رد می‌شود ولی خروجی لوکال ذخیره می‌گردد.
- برای رندر و اسکرین‌شات از Playwright استفاده می‌شود.
- اگر Playwright رندر نکند یا timeout شود، فایل `rendered.html` همچنان ساخته می‌شود و پیام خطا به‌صورت کامنت داخل آن ذخیره می‌شود.

## Troubleshooting نصب dependency

اگر در محیط شما خطایی شبیه `Tunnel connection failed: 403 Forbidden` دارید:

1. از اسکریپت پروژه استفاده کنید که proxy/index سراسری pip را نادیده می‌گیرد:
   ```bash
   ./scripts/install_deps.sh
   ```
2. اگر شبکه شما فقط mirror داخلی دارد:
   ```bash
   pip install -r requirements.txt --index-url <YOUR_INTERNAL_PYPI_MIRROR>
   ```
3. نصب مرورگر Playwright (بعد از نصب dependencyها):
   ```bash
   python -m playwright install chromium
   ```
