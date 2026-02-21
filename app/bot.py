"""
Telegram Bot — بدون رمز، همه می‌تونن استفاده کنن
ادمین: دسترسی کامل به دیتابیس + حذف
آرشیوها بر اساس username ذخیره می‌شن
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.config import settings
from app.services.archiver import Archiver
from app.storage.supabase import save_archive, get_supabase
from app.utils import is_valid_url

logger = logging.getLogger(__name__)
TGAPI = f"https://api.telegram.org/bot{settings.telegram_bot_token}"

# state هر کاربر
user_state: dict[int, dict] = {}

S_MENU = "main_menu"
S_URL = "await_url"
S_CHAN = "await_channel"
S_ADMIN_DELETE = "admin_delete"

BTN_ARCHIVE = "🗄 آرشیو لینک"
BTN_MY = "📋 آرشیوهای من"
BTN_CHAN = "📢 تنظیم کانال مقصد"

# ── Admin buttons ─────────────────────────────────────────────────────────────
BTN_ADMIN = "⚙️ پنل ادمین"
BTN_ADMIN_LIST = "📊 لیست همه آرشیوها"
BTN_ADMIN_USERS = "👥 لیست کاربران"
BTN_ADMIN_DELETE = "🗑 حذف آرشیو"
BTN_BACK = "🔙 برگشت"


# ── Telegram helpers ──────────────────────────────────────────────────────────

async def _post(method: str, **kw) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{TGAPI}/{method}", json=kw)
        return r.json()


async def msg(chat_id, text: str, kbd=None, parse_mode="HTML"):
    p = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if kbd:
        p["reply_markup"] = kbd
    try:
        await _post("sendMessage", **p)
    except Exception as e:
        logger.warning("sendMessage: %s", e)


async def send_doc(chat_id, path: Path, caption: str = ""):
    dest = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
    async with httpx.AsyncClient(timeout=60) as c:
        with path.open("rb") as f:
            r = await c.post(f"{TGAPI}/sendDocument",
                             data={"chat_id": str(dest), "caption": caption},
                             files={"document": (path.name, f)})
            if not r.json().get("ok"):
                raise RuntimeError(r.json().get("description", "unknown"))


async def send_photo(chat_id, path: Path, caption: str = ""):
    dest = int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id
    async with httpx.AsyncClient(timeout=60) as c:
        with path.open("rb") as f:
            r = await c.post(f"{TGAPI}/sendPhoto",
                             data={"chat_id": str(dest), "caption": caption},
                             files={"photo": (path.name, f)})
            if not r.json().get("ok"):
                # fallback به document
                with path.open("rb") as f2:
                    await c.post(f"{TGAPI}/sendDocument",
                                 data={"chat_id": str(dest), "caption": caption},
                                 files={"document": (path.name, f2)})


def is_admin(user_id: int) -> bool:
    return user_id == settings.admin_user_id


def user_menu_kbd(user_id: int) -> dict:
    rows = [
        [{"text": BTN_ARCHIVE}],
        [{"text": BTN_MY}, {"text": BTN_CHAN}],
    ]
    if is_admin(user_id):
        rows.append([{"text": BTN_ADMIN}])
    return {"keyboard": rows, "resize_keyboard": True}


def admin_kbd() -> dict:
    return {"keyboard": [
        [{"text": BTN_ADMIN_LIST}],
        [{"text": BTN_ADMIN_USERS}, {"text": BTN_ADMIN_DELETE}],
        [{"text": BTN_BACK}],
    ], "resize_keyboard": True}


# ── Supabase helpers ──────────────────────────────────────────────────────────

async def db_save_user(user_id: int, username: str, full_name: str):
    """کاربر رو در جدول users ذخیره/آپدیت کن"""
    sb = get_supabase()
    if not sb:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            headers = {
                "apikey": sb.key,
                "Authorization": f"Bearer {sb.key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates",
            }
            await c.post(
                f"{sb.base}/rest/v1/bot_users",
                headers=headers,
                json={"user_id": user_id, "username": username, "full_name": full_name},
            )
    except Exception as e:
        logger.warning("db_save_user: %s", e)


async def db_save_archive_user(archive_id: str, user_id: int, username: str):
    """ربط archive به user"""
    sb = get_supabase()
    if not sb:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            headers = {
                "apikey": sb.key,
                "Authorization": f"Bearer {sb.key}",
                "Content-Type": "application/json",
            }
            await c.post(
                f"{sb.base}/rest/v1/archives",
                headers=headers,
                json={"id": archive_id, "saved_by_user_id": user_id, "saved_by_username": username},
                params={"on_conflict": "id"},
            )
    except Exception as e:
        logger.warning("db_save_archive_user: %s", e)


async def db_get_user_archives(user_id: int) -> list[dict]:
    sb = get_supabase()
    if not sb:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            headers = {"apikey": sb.key, "Authorization": f"Bearer {sb.key}"}
            r = await c.get(
                f"{sb.base}/rest/v1/archives",
                headers=headers,
                params={"saved_by_user_id": f"eq.{user_id}", "order": "created_at.desc", "limit": "20"},
            )
            return r.json() if r.is_success else []
    except Exception as e:
        logger.warning("db_get_user_archives: %s", e)
        return []


async def db_get_all_archives(limit: int = 20) -> list[dict]:
    sb = get_supabase()
    if not sb:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            headers = {"apikey": sb.key, "Authorization": f"Bearer {sb.key}"}
            r = await c.get(
                f"{sb.base}/rest/v1/archives",
                headers=headers,
                params={"order": "created_at.desc", "limit": str(limit)},
            )
            return r.json() if r.is_success else []
    except Exception as e:
        logger.warning("db_get_all_archives: %s", e)
        return []


async def db_get_all_users() -> list[dict]:
    sb = get_supabase()
    if not sb:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            headers = {"apikey": sb.key, "Authorization": f"Bearer {sb.key}"}
            r = await c.get(
                f"{sb.base}/rest/v1/bot_users",
                headers=headers,
                params={"order": "created_at.desc"},
            )
            return r.json() if r.is_success else []
    except Exception as e:
        logger.warning("db_get_all_users: %s", e)
        return []


async def db_delete_archive(archive_id: str) -> bool:
    sb = get_supabase()
    if not sb:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            headers = {"apikey": sb.key, "Authorization": f"Bearer {sb.key}"}
            # حذف از DB
            r = await c.delete(
                f"{sb.base}/rest/v1/archives",
                headers=headers,
                params={"id": f"eq.{archive_id}"},
            )
            # حذف از Storage
            for fname in ["archive.html", "raw.html", "screenshot.png"]:
                await c.delete(
                    f"{sb.base}/storage/v1/object/{sb.bucket}/{archive_id}/{fname}",
                    headers=headers,
                )
            return r.is_success
    except Exception as e:
        logger.warning("db_delete_archive: %s", e)
        return False


# ── Main handler ──────────────────────────────────────────────────────────────

async def handle_update(update: dict) -> None:
    message = update.get("message") or update.get("edited_message")
    if not message:
        return

    chat_id: int = message["chat"]["id"]
    user_id: int = message["from"]["id"]
    username: str = message["from"].get("username", "") or ""
    full_name: str = (
        (message["from"].get("first_name", "") or "") + " " +
        (message["from"].get("last_name", "") or "")
    ).strip()
    text: str = (message.get("text") or "").strip()

    # ذخیره اطلاعات کاربر
    await db_save_user(user_id, username, full_name)

    st = user_state.setdefault(user_id, {"state": S_MENU, "channel": settings.telegram_chat_id or ""})

    # ── /start ────────────────────────────────────────────────────────────
    if text == "/start":
        st["state"] = S_MENU
        admin_note = " <b>(ادمین)</b>" if is_admin(user_id) else ""
        uname = f"@{username}" if username else full_name
        await msg(chat_id,
                  f"سلام {uname}{admin_note} 👋\n\n"
                  "لینک پست بده تا آرشیوش کنم.",
                  kbd=user_menu_kbd(user_id))
        return

    if text == "/cancel":
        st["state"] = S_MENU
        await msg(chat_id, "لغو شد.", kbd=user_menu_kbd(user_id))
        return

    # ── Menu buttons ──────────────────────────────────────────────────────

    if text == BTN_ARCHIVE:
        st["state"] = S_URL
        ch = st.get("channel") or str(chat_id)
        await msg(chat_id, f"🔗 لینک را بفرستید:\n<i>مقصد: <code>{ch}</code></i>")
        return

    if text == BTN_MY:
        rows = await db_get_user_archives(user_id)
        if not rows:
            await msg(chat_id, "📭 هنوز آرشیوی نداری.", kbd=user_menu_kbd(user_id))
            return
        lines = []
        base = settings.archive_base
        for i, r in enumerate(rows[:15], 1):
            url = r.get("url", "")
            aid = r.get("id", "")
            date = (r.get("created_at") or "")[:10]
            link = f"{base}/view/{aid}" if base else aid
            lines.append(f"{i}. {date}\n🔗 {url}\n📎 {link}")
        await msg(chat_id, "📋 <b>آرشیوهای تو:</b>\n\n" + "\n\n".join(lines), kbd=user_menu_kbd(user_id))
        return

    if text == BTN_CHAN:
        st["state"] = S_CHAN
        ch = st.get("channel") or "(تنظیم نشده)"
        await msg(chat_id,
                  f"📢 کانال مقصد فعلی: <code>{ch}</code>\n\n"
                  "آیدی جدید:\n"
                  "• <code>@channelname</code>\n"
                  "• <code>-1001234567890</code>\n"
                  "• <code>me</code> — ارسال به خودت\n\n/cancel برای لغو")
        return

    # ── Admin panel ───────────────────────────────────────────────────────

    if text == BTN_ADMIN and is_admin(user_id):
        st["state"] = S_MENU
        sb = get_supabase()
        rows = await db_get_all_archives(1) if sb else []
        total = len(await db_get_all_archives(1000)) if sb else 0
        await msg(chat_id, f"⚙️ <b>پنل ادمین</b>\n\nکل آرشیوها: {total}", kbd=admin_kbd())
        return

    if text == BTN_BACK and is_admin(user_id):
        st["state"] = S_MENU
        await msg(chat_id, "منوی اصلی:", kbd=user_menu_kbd(user_id))
        return

    if text == BTN_ADMIN_LIST and is_admin(user_id):
        rows = await db_get_all_archives(20)
        if not rows:
            await msg(chat_id, "دیتابیس خالیه.", kbd=admin_kbd())
            return
        base = settings.archive_base
        lines = []
        for i, r in enumerate(rows, 1):
            url = r.get("url", "")[:50]
            aid = r.get("id", "")[:8]
            uname = r.get("saved_by_username", "") or str(r.get("saved_by_user_id", ""))
            date = (r.get("created_at") or "")[:10]
            view = f"{base}/view/{r.get('id','')}" if base else ""
            lines.append(f"{i}. [{aid}] {date}\n👤 @{uname}\n🔗 {url}\n📎 {view}")
        await msg(chat_id, "📊 <b>آخرین ۲۰ آرشیو:</b>\n\n" + "\n\n".join(lines), kbd=admin_kbd())
        return

    if text == BTN_ADMIN_USERS and is_admin(user_id):
        users = await db_get_all_users()
        if not users:
            await msg(chat_id, "هنوز کاربری نیست.", kbd=admin_kbd())
            return
        lines = []
        for u in users:
            uid = u.get("user_id", "")
            uname = u.get("username", "") or u.get("full_name", "")
            date = (u.get("created_at") or "")[:10]
            # تعداد آرشیوهای این کاربر
            archives = await db_get_user_archives(uid)
            lines.append(f"👤 @{uname} (ID: {uid})\n📅 {date} | 🗄 {len(archives)} آرشیو")
        await msg(chat_id, "👥 <b>کاربران:</b>\n\n" + "\n\n".join(lines), kbd=admin_kbd())
        return

    if text == BTN_ADMIN_DELETE and is_admin(user_id):
        st["state"] = S_ADMIN_DELETE
        await msg(chat_id,
                  "🗑 <b>حذف آرشیو</b>\n\n"
                  "شناسه آرشیو (UUID) را بفرستید.\n"
                  "از لیست آرشیوها می‌تونی کپی کنی.\n\n/cancel برای لغو")
        return

    # ── States ────────────────────────────────────────────────────────────

    if st["state"] == S_CHAN:
        raw = text.strip()
        st["channel"] = str(chat_id) if raw.lower() == "me" else raw
        st["state"] = S_MENU
        await msg(chat_id, f"✅ کانال مقصد: <code>{st['channel']}</code>", kbd=user_menu_kbd(user_id))
        return

    if st["state"] == S_ADMIN_DELETE and is_admin(user_id):
        archive_id = text.strip()
        st["state"] = S_MENU
        ok = await db_delete_archive(archive_id)
        if ok:
            await msg(chat_id, f"✅ آرشیو <code>{archive_id[:8]}...</code> حذف شد.", kbd=admin_kbd())
        else:
            await msg(chat_id, f"❌ حذف ناموفق. شناسه رو چک کن.", kbd=admin_kbd())
        return

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

            # ذخیره ربط کاربر به آرشیو
            await db_save_archive_user(archive_id, user_id, username)

            public_url = ""
            if settings.archive_base:
                public_url = f"{settings.archive_base}/view/{archive_id}"

            dest = int(target) if target.lstrip("-").isdigit() else target
            results = []

            # ارسال archive.html
            try:
                cap = f"📦 archive.html\n🔗 {url}"
                if public_url:
                    cap += f"\n🌐 {public_url}"
                await send_doc(dest, artifact.rendered_html_path, cap)
                results.append("✅ archive.html")
            except Exception as e:
                results.append(f"❌ archive.html: {e}")

            # ارسال screenshot
            if artifact.screenshot_path.exists() and artifact.screenshot_path.stat().st_size > 5000:
                try:
                    await send_photo(dest, artifact.screenshot_path, f"📸 {url}")
                    results.append("✅ screenshot")
                except Exception as e:
                    results.append(f"❌ screenshot: {e}")
            else:
                results.append("⚠️ screenshot نگرفت")

            reply = (f"✅ <b>آرشیو شد</b>\n\n"
                     f"🔗 {url}\n"
                     f"📤 مقصد: <code>{target}</code>\n\n"
                     + "\n".join(results))
            if public_url:
                reply += f"\n\n🌐 {public_url}"

            await msg(chat_id, reply, kbd=user_menu_kbd(user_id))

        except Exception as exc:
            logger.exception("Archive failed: %s", url)
            await msg(chat_id, f"❌ خطا:\n<code>{exc}</code>", kbd=user_menu_kbd(user_id))
        return

    # Default
    await msg(chat_id, "از دکمه‌های منو استفاده کنید:", kbd=user_menu_kbd(user_id))
