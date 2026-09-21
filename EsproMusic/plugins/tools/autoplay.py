import asyncio
import os
import re

import yt_dlp
from pyrogram import filters
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from pytgcalls.types.input_stream import AudioPiped
from pytgcalls.types.input_stream.quality import HighQualityAudio

import config
from EsproMusic import LOGGER, YouTube, app
from EsproMusic.core.mongo import mongodb
from EsproMusic.misc import SUDOERS, db
from EsproMusic.utils.database import get_lang, group_assistant, is_active_chat
from EsproMusic.utils.decorators import AdminRightsCheck
from EsproMusic.utils.inline.play import stream_markup
from EsproMusic.utils.stream.queue import put_queue
from EsproMusic.utils.thumbnails import get_thumb
from config import BANNED_USERS, adminlist
from strings import get_string

try:
    from youtubesearchpython.__future__ import VideosSearch
except Exception:  # pragma: no cover
    VideosSearch = None

# ─────────────────────────── Settings ───────────────────────────
AUTOPLAY_MAX_SECS = 15 * 60  # autoplay me 15 min se lambe track skip honge
MAX_CANDIDATES = 6  # download fail hone par itne alag tracks try honge
HISTORY_LIMIT = 60  # ek chat me itne last played ids yaad rakhe jayenge

autoplaydb = mongodb.autoplay
autoplay_cache = {}  # chat_id -> bool
played_history = {}  # chat_id -> [video ids]
_busy = set()  # chats jaha autoplay abhi chal raha hai (double trigger rokne ke liye)
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


# ─────────────────────────── Database ───────────────────────────
async def is_autoplay_enabled(chat_id: int) -> bool:
    if chat_id in autoplay_cache:
        return autoplay_cache[chat_id]
    chat = await autoplaydb.find_one({"chat_id": chat_id})
    status = bool(chat.get("autoplay", False)) if chat else False
    autoplay_cache[chat_id] = status
    return status


async def set_autoplay(chat_id: int, status: bool):
    autoplay_cache[chat_id] = status
    if not status:
        played_history.pop(chat_id, None)
    await autoplaydb.update_one(
        {"chat_id": chat_id},
        {"$set": {"autoplay": status}},
        upsert=True,
    )


# ─────────────────────────── Helpers ───────────────────────────
def _clean_title(title) -> str:
    text = str(title or "")
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text)
    text = re.sub(
        r"\b(official|video|audio|lyrical|lyrics|full song|full video|hd|4k|mv|song)\b",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", text).strip()


def _remember(chat_id: int, vidid: str):
    hist = played_history.setdefault(chat_id, [])
    if vidid and vidid not in hist:
        hist.append(vidid)
    if len(hist) > HISTORY_LIMIT:
        del hist[: len(hist) - HISTORY_LIMIT]


def _too_long(seconds) -> bool:
    try:
        seconds = int(seconds)
    except Exception:
        return False
    if seconds <= 0:
        return False
    return seconds > AUTOPLAY_MAX_SECS or seconds > config.DURATION_LIMIT


# ─────────────────── Related track finding ───────────────────
def _mix_ids_sync(seed_id: str) -> list:
    """YouTube ka 'Mix / Radio' playlist (RD<id>) -> seed se related video ids."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "noplaylist": False,
        "playlistend": 25,
        "socket_timeout": 15,
    }
    ids = []
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            url = f"https://www.youtube.com/watch?v={seed_id}&list=RD{seed_id}"
            data = ydl.extract_info(url, download=False)
            for entry in (data or {}).get("entries") or []:
                if not entry:
                    continue
                vid = entry.get("id")
                if not vid or not _YT_ID.match(str(vid)):
                    continue
                if entry.get("live_status") in ("is_live", "is_upcoming"):
                    continue
                if _too_long(entry.get("duration")):
                    continue
                ids.append(vid)
    except Exception as e:
        LOGGER(__name__).warning(f"[AutoPlay] mix fetch failed: {e}")
    return ids


async def _related_ids(seed_id, title: str, exclude: set) -> list:
    """Candidate video ids (best match pehle) return karta hai."""
    found = []

    # 1) YouTube mix (sabse accurate "related")
    if seed_id:
        try:
            loop = asyncio.get_running_loop()
            mix = await asyncio.wait_for(
                loop.run_in_executor(None, _mix_ids_sync, seed_id), timeout=45
            )
            found.extend(v for v in mix if v not in exclude and v not in found)
        except Exception as e:
            LOGGER(__name__).warning(f"[AutoPlay] mix timeout/error: {e}")

    # 2) Fallback: title se search
    if len(found) < MAX_CANDIDATES and VideosSearch:
        cleaned = _clean_title(title)
        queries = [q for q in (f"{cleaned} songs" if cleaned else "", cleaned) if q]
        if not queries:
            queries = ["trending songs"]
        for q in queries:
            try:
                res = await VideosSearch(q, limit=15).next()
                for track in (res or {}).get("result") or []:
                    vid = str(track.get("id") or "")
                    if not _YT_ID.match(vid) or vid in exclude or vid in found:
                        continue
                    dur = track.get("duration")
                    if dur:
                        try:
                            if _too_long(config.time_to_seconds(dur)):
                                continue
                        except Exception:
                            pass
                    found.append(vid)
            except Exception as e:
                LOGGER(__name__).warning(f"[AutoPlay] search failed ({q}): {e}")
            if len(found) >= MAX_CANDIDATES:
                break

    return found


async def _download(vidid: str):
    try:
        result = await YouTube.download(vidid, None, videoid=True, video=False)
    except Exception as e:
        LOGGER(__name__).warning(f"[AutoPlay] download error {vidid}: {e}")
        return None
    path = result[0] if isinstance(result, (tuple, list)) else result
    if path and isinstance(path, str) and os.path.exists(path):
        if os.path.getsize(path) > 0:
            return path
        try:
            os.remove(path)
        except Exception:
            pass
    return None


# ─────────────────────────── Core ───────────────────────────
async def trigger_autoplay(client, chat_id: int, last_track: dict) -> bool:
    """
    Queue khatam hone par related song dhoondh kar VC me chala deta hai.
    True = naya track chalu ho gaya, False = kuch nahi mila (caller VC leave karega).
    """
    if chat_id in _busy:
        return False
    _busy.add(chat_id)
    try:
        last_track = last_track or {}
        last_id = str(last_track.get("vidid") or "")
        last_title = str(last_track.get("title") or "")
        origin_chat = last_track.get("chat_id") or chat_id

        seed_id = last_id if _YT_ID.match(last_id) else None
        if seed_id:
            _remember(chat_id, seed_id)
        exclude = set(played_history.get(chat_id, []))

        candidates = await _related_ids(seed_id, last_title, exclude)
        if not candidates:
            return False

        for vidid in candidates[:MAX_CANDIDATES]:
            title, duration_min, duration_sec, _thumb, real_id = await YouTube.details(
                vidid, True
            )
            if not title or _too_long(duration_sec):
                continue
            # same song (alag upload) dobara na aaye
            if last_title and _clean_title(title).lower() == _clean_title(last_title).lower():
                continue

            file_path = await _download(vidid)
            if not file_path:
                continue

            try:
                await client.change_stream(
                    chat_id,
                    AudioPiped(file_path, audio_parameters=HighQualityAudio()),
                )
            except Exception as e:
                LOGGER(__name__).error(f"[AutoPlay] change_stream failed: {e}")
                try:
                    os.remove(file_path)
                except Exception:
                    pass
                return False

            _remember(chat_id, vidid)
            if db.get(chat_id) is None:
                db[chat_id] = []
            await put_queue(
                chat_id,
                origin_chat,
                file_path,
                title,
                duration_min or "03:00",
                "AutoPlay 🔄",
                vidid,
                app.id,
                "audio",
            )

            try:
                language = await get_lang(chat_id)
                _ = get_string(language)
                img = await get_thumb(vidid)
                run = await app.send_photo(
                    chat_id=origin_chat,
                    photo=img,
                    caption=_["stream_1"].format(
                        f"https://t.me/{app.username}?start=info_{vidid}",
                        title[:23],
                        duration_min,
                        "AutoPlay 🔄",
                    ),
                    reply_markup=InlineKeyboardMarkup(stream_markup(_, chat_id)),
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "stream"
            except Exception as e:
                # stream chalu hai, bas notification fail hui - koi dikkat nahi
                LOGGER(__name__).warning(f"[AutoPlay] notify failed: {e}")
            return True

        return False
    except Exception as e:
        LOGGER(__name__).error(f"[AutoPlay] runner error: {e}")
        return False
    finally:
        _busy.discard(chat_id)


async def try_autoplay(chat_id: int, last_track: dict, client=None) -> bool:
    """
    Hook helper (call.py / skip.py se use hota hai).
    Autoplay ON ho aur related track chal jaye to True, warna False.
    """
    try:
        if not last_track or not await is_autoplay_enabled(chat_id):
            return False
        if client is None:
            from EsproMusic.core.call import Ritik

            client = await group_assistant(Ritik, chat_id)
        return await trigger_autoplay(client, chat_id, last_track)
    except Exception as e:
        LOGGER(__name__).error(f"[AutoPlay] hook error: {e}")
        return False


# ─────────────────────────── Commands ───────────────────────────
@app.on_message(
    filters.command(["autoplay", "cautoplay"]) & filters.group & ~BANNED_USERS
)
@AdminRightsCheck
async def autoplay_command(cli, message: Message, _, chat_id):
    mention = message.from_user.mention
    if len(message.command) < 2:
        new_state = not await is_autoplay_enabled(chat_id)
    else:
        arg = message.command[1].strip().lower()
        if arg in ("enable", "on", "yes"):
            new_state = True
        elif arg in ("disable", "off", "no"):
            new_state = False
        else:
            return await message.reply_text("Usage:\n/autoplay [on|off]")

    await set_autoplay(chat_id, new_state)
    status = "ENABLED ✅" if new_state else "DISABLED ❌"
    await message.reply_text(
        f"🔄 <b>AutoPlay {status}</b> by {mention}", parse_mode=ParseMode.HTML
    )


async def _handle_autoplay_button(CallbackQuery: CallbackQuery):
    chat_id = int(CallbackQuery.data.strip().split("|")[1])

    if not await is_active_chat(chat_id):
        return await CallbackQuery.answer(
            "❌ No song is currently playing in VC.", show_alert=True
        )

    user_id = CallbackQuery.from_user.id
    allowed = user_id in SUDOERS
    if not allowed:
        admins = adminlist.get(CallbackQuery.message.chat.id)
        if admins:
            allowed = user_id in admins
        else:
            # adminlist cache khali ho (restart ke baad) to live check
            try:
                member = await app.get_chat_member(
                    CallbackQuery.message.chat.id, user_id
                )
                allowed = member.status in (
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.OWNER,
                )
            except Exception:
                allowed = False
    if not allowed:
        return await CallbackQuery.answer(
            "❌ This button can only be used by admins.", show_alert=True
        )

    new_state = not await is_autoplay_enabled(chat_id)
    await set_autoplay(chat_id, new_state)
    status = "ENABLED ✅" if new_state else "DISABLED ❌"
    await CallbackQuery.answer(f"🔄 AutoPlay {status}", show_alert=True)


# group=-1: admins/callback.py ka regex("ADMIN") bhi is data ko match karta hai,
# isliye hume pehle chalna hai aur uske baad propagation rokna hai.
@app.on_callback_query(filters.regex(r"^ADMIN AutoPlay\|") & ~BANNED_USERS, group=-1)
async def autoplay_callback_handler(client, CallbackQuery: CallbackQuery):
    try:
        await _handle_autoplay_button(CallbackQuery)
    except Exception as e:
        LOGGER(__name__).error(f"[AutoPlay] callback error: {e}")
    CallbackQuery.stop_propagation()
