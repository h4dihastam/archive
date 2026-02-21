"""Telegram Bot — webhook mode, handles archive requests."""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.config import settings
from app.services.archiver import Archiver
from app.storage.supabase import save_archive
from app.utils import is_valid_url

logger = logging.getLogger(__name__)
TGAPI = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

# Per-user state { user_id: {"authed","state","channel"} }
user_state: dict[int, dict] = {}

S_PASS = "await_password"
S_MENU = "main_menu"
S_URL = "await_url"
S_CHAN = "await_channel"

BTN_ARCHIVE = "🗄 آرشیو لینک"
BTN_CHAN = "📢 تنظیم کانال مقصد"
BTN_STATUS = "ℹ️ وضعیت"


async def _post(method: str, **kw) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{TGAPI}/{method}", json=kw)
        r.raise_for_status()
        return r.json()


async def msg(chat_id, text: str, kbd=None):
    p = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if kbd:
        p["reply_markup"] = kbd
    try:
        await _post("sendMessage", **p)
    except Exception as e:
        logger.warning("sendMessage: %s", e)


def menu_kbd():
    return {"keyboard": [[{"text": BTN_ARCHIVE}], [{"text": BTN_CHAN}, {"text": BTN_STATUS}]],
            "resize_keyboard": True}


async def forward_or_send_doc(chat_id, file_path: Path, caption: str):
    """Send document to chat_id (supports @username and numeric IDs)."""
    dest = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
    url = f"{TGAPI}/sendDocument"
    async with httpx.AsyncClient(timeout=60) as c:
        with file_path.open("rb") as f:
            r = await c.post(url, data={"chat_id": str(dest), "caption": caption},
                             files={"document": (file_path.name, f)})
            if not r.json().get("ok"):
                raise RuntimeError(r.json().get("description", "unknown error"))


async def forward_or_send_photo(chat_id, file_path: Path, caption: str):
    dest = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
    url = f"{TGAPI}/sendPhoto"
    async with httpx.AsyncClient(timeout=60) as c:
        with file_path.open("rb") as f:
            r = await c.post(url, data={"chat_id": str(dest), "caption": caption},
                             files={"photo": (file_path.name, f)})
            if not r.json().get("ok"):
                # Photo might be too large → send as document
                with file_path.open("rb") as f2:
                    r2 = await c.post(f"{TGAPI}/sendDocument",
                                      data={"chat_id": str(dest), "caption": caption},
                                      files={"document": (file_path.name, f2)})


async def handle_update(update: dict) -> None:
    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat_id: int = message["chat"]["id"]
    user_id: int = message["from"]["id"]
    text: str = (message.get("text") or "").strip()
    is_admin = user_id == settings.admin_user_id

    st = user_state.setdefault(user_id, {
        "authed": False, "state": S_PASS,
        "channel": settings.telegram_chat_id or "",
    })

    # ── Not authenticated ─────────────────────────────────────────────────────
    if not st["authed"]:
        if text == "/start":
            await msg(chat_id, "🔐 برای ورود رمز عبور را وارد کنید:")
            st["state"] = S_PASS
        elif st["state"] == S_PASS:
            if text == settings.bot_password:
                st["authed"] = True
                st["state"] = S_MENU
                lbl = " <b>(ادمین)</b>" if is_admin else ""
                await msg(chat_id, f"✅ خوش آمدید{lbl}!", kbd=menu_kbd())
            else:
                await msg(chat_id, "❌ رمز اشتباه. دوباره تلاش کنید:")
        else:
            await msg(chat_id, "🔐 ابتدا /start بزنید.")
        return

    # ── Authenticated ─────────────────────────────────────────────────────────
    if text == "/start":
        st["state"] = S_MENU
        await msg(chat_id, "منوی اصلی:", kbd=menu_kbd())
        return

    if text == "/cancel":
        st["state"] = S_MENU
        await msg(chat_id, "لغو شد.", kbd=menu_kbd())
        return

    if text == BTN_ARCHIVE:
        st["state"] = S_URL
        ch = st.get("channel") or str(chat_id)
        await msg(chat_id, f"🔗 لینک را بفرستید:\n<i>مقصد: <code>{ch}</code></i>")
        return

    if text == BTN_CHAN:
        st["state"] = S_CHAN
        ch = st.get("channel") or "(تنظیم نشده)"
        await msg(chat_id,
                  f"📢 کانال مقصد فعلی: <code>{ch}</code>\n\n"
                  "آیدی جدید:\n"
                  "• <code>@channelname</code> — کانال عمومی\n"
                  "• <code>-1001234567890</code> — کانال/گروه خصوصی\n"
                  "• <code>me</code> — ارسال به خودت\n\n"
                  "💡 برای کانال خصوصی: ربات را ادمین کانال کنید، سپس آیدی عددی را بفرستید.\n\n"
                  "/cancel برای لغو")
        return

    if text == BTN_STATUS:
        ch = st.get("channel") or "(تنظیم نشده)"
        sb_ok = "✅" if settings.supabase_url else "❌"
        await msg(chat_id,
                  f"⚙️ <b>وضعیت</b>\n\n"
                  f"کانال مقصد: <code>{ch}</code>\n"
                  f"Supabase: {sb_ok}\n"
                  f"نقش: {'✅ ادمین' if is_admin else '👤 کاربر'}\n"
                  f"یوزر آیدی: <code>{user_id}</code>")
        return

    # ── Set channel ───────────────────────────────────────────────────────────
    if st["state"] == S_CHAN:
        raw = text.strip()
        if raw.lower() == "me":
            st["channel"] = str(chat_id)
        else:
            st["channel"] = raw
        st["state"] = S_MENU
        await msg(chat_id, f"✅ کانال مقصد: <code>{st['channel']}</code>", kbd=menu_kbd())
        return

    # ── Archive URL ───────────────────────────────────────────────────────────
    if st["state"] == S_URL:
        if not is_valid_url(text):
            await msg(chat_id, "❌ لینک نامعتبر. دوباره بفرستید یا /cancel بزنید.")
            return

        url = text
        st["state"] = S_MENU
        target = st.get("channel") or str(chat_id)
        await msg(chat_id, "⏳ در حال آرشیو... صبر کنید.")

        try:
            artifact = await Archiver().archive(url)
            archive_id = await save_archive(artifact)
            artifact.archive_id = archive_id

            public_url = ""
            if settings.archive_base:
                public_url = f"{settings.archive_base}/view/{archive_id}"

            results = []

            # Send archive.html
            try:
                caption = f"📦 archive.html\n🔗 {url}"
                if public_url:
                    caption += f"\n🌐 {public_url}"
                await forward_or_send_doc(target, artifact.rendered_html_path, caption)
                results.append("✅ archive.html")
            except Exception as e:
                results.append(f"❌ archive.html: {e}")

            # Send screenshot
            if artifact.screenshot_path.exists() and artifact.screenshot_path.stat().st_size > 0:
                try:
                    await forward_or_send_photo(target, artifact.screenshot_path, f"📸 screenshot\n🔗 {url}")
                    results.append("✅ screenshot.png")
                except Exception as e:
                    results.append(f"❌ screenshot.png: {e}")
            else:
                results.append("⚠️ screenshot نگرفت")

            summary = "\n".join(results)
            reply = (f"✅ <b>آرشیو شد</b>\n\n"
                     f"🔗 {url}\n"
                     f"📤 مقصد: <code>{target}</code>\n\n"
                     f"{summary}")
            if public_url:
                reply += f"\n\n🌐 لینک آرشیو:\n{public_url}"

            await msg(chat_id, reply, kbd=menu_kbd())

        except Exception as exc:
            logger.exception("Archive failed: %s", url)
            await msg(chat_id, f"❌ خطا:\n<code>{exc}</code>", kbd=menu_kbd())
        return

    await msg(chat_id, "از دکمه‌های منو استفاده کنید:", kbd=menu_kbd())
