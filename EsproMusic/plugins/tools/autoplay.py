import os
import random
import re
from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
from pytgcalls.types.input_stream import AudioPiped
from pytgcalls.types.input_stream.quality import HighQualityAudio
from youtubesearchpython.__future__ import VideosSearch

from EsproMusic import LOGGER, YouTube, app
from EsproMusic.core.mongo import mongodb
from EsproMusic.misc import SUDOERS, db
from EsproMusic.utils.database import get_lang, is_active_chat
from EsproMusic.utils.decorators import AdminRightsCheck
from EsproMusic.utils.inline.play import stream_markup
from EsproMusic.utils.stream.queue import put_queue
from EsproMusic.utils.thumbnails import get_thumb
from config import BANNED_USERS, adminlist
from strings import get_string

# MongoDB Collection & In-Memory Cache
autoplaydb = mongodb.autoplay
autoplay_cache = {}
played_history = {}


# --- Database Functions ---
async def is_autoplay_enabled(chat_id: int) -> bool:
    if chat_id in autoplay_cache:
        return autoplay_cache[chat_id]
    chat = await autoplaydb.find_one({"chat_id": chat_id})
    if not chat:
        autoplay_cache[chat_id] = False
        return False
    status = chat.get("autoplay", False)
    autoplay_cache[chat_id] = status
    return status


async def set_autoplay(chat_id: int, status: bool):
    autoplay_cache[chat_id] = status
    await autoplaydb.update_one(
        {"chat_id": chat_id},
        {"$set": {"autoplay": status}},
        upsert=True,
    )


# --- Strict Safe String Converter ---
def safe_str(val) -> str:
    """Guarantees output is string, never None."""
    if val is None:
        return ""
    return str(val).strip()


# --- Helper: Clean Title ---
def clean_title(title) -> str:
    text = safe_str(title)
    if not text:
        return ""
    try:
        text = re.sub(r"\(.*?\)|\[.*?\]", "", text)
        text = re.sub(
            r"(official|video|audio|lyrical|full song|hd|4k|remix|mix|version|mv)",
            "",
            text,
            flags=re.IGNORECASE,
        )
    except Exception:
        pass
    return text.strip()


# --- Helper: Detect Mood & Search Query ---
def detect_mood_or_query(title) -> str:
    safe_title = safe_str(title)
    if not safe_title:
        return "hindi trending songs"
        
    cleaned = clean_title(safe_title)
    title_lower = safe_title.lower()
    
    # Sad / Emotional Keywords Check
    sad_keywords = ["sad", "dil", "dard", "broken", "tanhai", "juda", "yaad", "kaise hua", "tujhe kitna", "roye", "alvida", "khairiyat", "channa", "humari aduri", "sanam teri kasam", "bewafa", "thukra ke"]
    if any(kw in title_lower for kw in sad_keywords):
        keywords = ["bollywood sad songs playlist", "hindi emotional sad audio tracks", "heartbroken hindi songs", "arijit singh sad songs"]
        return random.choice(keywords)

    # Romantic Keywords Check
    romantic_keywords = ["love", "pyar", "ishq", "mohabat", "tum", "sanam", "dil", "romantic", "humsafar", "teriyaan", "kesariya", "raataan"]
    if any(kw in title_lower for kw in romantic_keywords):
        keywords = ["bollywood romantic audio songs", "hindi love songs mix", "latest romantic hindi songs"]
        return random.choice(keywords)

    # Party / Fast Keywords Check
    party_keywords = ["party", "dance", "beat", "dj", "club", "nachi", "thumka", "mashup", "daru"]
    if any(kw in title_lower for kw in party_keywords):
        keywords = ["hindi party dance mix", "latest bollywood party tracks", "club hindi songs"]
        return random.choice(keywords)

    # Safe Concatenation
    if cleaned:
        return f"{cleaned} similar hindi songs"
    return "hindi trending songs"


# --- Related Song Finder ---
async def get_autoplay_song(chat_id: int, last_title):
    if chat_id not in played_history:
        played_history[chat_id] = set()

    safe_last = safe_str(last_title)
    cleaned_last = clean_title(safe_last)
    search_query = detect_mood_or_query(safe_last)

    # 1. Primary Search using VideosSearch
    candidates = []
    try:
        search = VideosSearch(search_query, limit=20)
        results = await search.next()

        if results and isinstance(results, dict) and "result" in results:
            res_list = results.get("result") or []
            for track in res_list:
                if not isinstance(track, dict):
                    continue

                vidid = safe_str(track.get("id"))
                raw_track_title = safe_str(track.get("title"))
                cleaned_cand_title = clean_title(raw_track_title).lower()
                duration = safe_str(track.get("duration")) or "03:00"

                if not vidid:
                    continue

                # History check
                if vidid in played_history[chat_id]:
                    continue

                # Same song repeat check
                if cleaned_last and (cleaned_last.lower() in cleaned_cand_title or cleaned_cand_title in cleaned_last.lower()):
                    continue

                candidates.append((vidid, raw_track_title if raw_track_title else "AutoPlay Track", duration))
    except Exception as e:
        LOGGER(__name__).warning(f"[AutoPlay VideosSearch Warning]: {e}")

    # 2. Fallback Search using YoutubeSearch if primary yielded no candidates
    if not candidates:
        try:
            from youtube_search import YoutubeSearch
            fallback_results = YoutubeSearch(search_query, max_results=15).to_dict()
            if fallback_results:
                for track in fallback_results:
                    if not isinstance(track, dict):
                        continue
                    vidid = safe_str(track.get("id"))
                    raw_track_title = safe_str(track.get("title"))
                    cleaned_cand_title = clean_title(raw_track_title).lower()
                    duration = safe_str(track.get("duration")) or "03:00"

                    if not vidid or vidid in played_history[chat_id]:
                        continue
                    if cleaned_last and (cleaned_last.lower() in cleaned_cand_title or cleaned_cand_title in cleaned_last.lower()):
                        continue

                    candidates.append((vidid, raw_track_title if raw_track_title else "AutoPlay Track", duration))
        except Exception as e:
            LOGGER(__name__).error(f"[AutoPlay YoutubeSearch Fallback Error]: {e}")

    # 3. Final Candidate Selection
    if candidates:
        selected = random.choice(candidates)
        played_history[chat_id].add(selected[0])
        if len(played_history[chat_id]) > 60:
            played_history[chat_id].clear()
        return selected[0], selected[1], selected[2]

    return None, None, None


# --- Auto-Stream Execution ---
async def trigger_autoplay(client, chat_id: int, last_track: dict = None):
    from EsproMusic.core.call import Ritik, _clear_
    from EsproMusic.utils.database import add_active_chat, Music_on, group_assistant
    from config import autoclean, time_to_seconds

    try:
        # If client not passed directly (e.g. from /skip or callback), resolve it
        if client is None:
            try:
                client = await group_assistant(Ritik, chat_id)
            except Exception as e:
                LOGGER(__name__).error(f"[AutoPlay] Failed to get group assistant: {e}")
                await _clear_(chat_id)
                return

        last_title = ""
        if isinstance(last_track, dict):
            last_title = safe_str(last_track.get("title"))

        # Download Retry Mechanism (3 Tries)
        file_path = None
        chosen_vidid = None
        chosen_title = None
        chosen_duration = None

        for _ in range(3):
            vidid, title, duration_min = await get_autoplay_song(chat_id, last_title)
            if not vidid:
                break

            try:
                downloaded, direct = await YouTube.download(
                    vidid, None, videoid=True, video=False
                )
                if downloaded and os.path.exists(downloaded) and os.path.getsize(downloaded) > 0:
                    file_path = downloaded
                    chosen_vidid = vidid
                    chosen_title = title
                    chosen_duration = duration_min
                    break
                else:
                    if downloaded and os.path.exists(downloaded):
                        try:
                            os.remove(downloaded)
                        except Exception:
                            pass
            except Exception as ex:
                LOGGER(__name__).error(f"[AutoPlay] Download Retry Error: {ex}")
                continue

        if not file_path:
            await _clear_(chat_id)
            try:
                return await client.leave_group_call(chat_id)
            except Exception:
                return

        # Stream via PyTgCalls
        stream = AudioPiped(file_path, audio_parameters=HighQualityAudio())
        await client.change_stream(chat_id, stream)

        # Set up active state & queue entry cleanly
        original_chat_id = chat_id
        if isinstance(last_track, dict):
            original_chat_id = last_track.get("chat_id", chat_id)

        await add_active_chat(chat_id)
        await Music_on(chat_id)

        try:
            dur_sec = time_to_seconds(chosen_duration) - 3
        except Exception:
            dur_sec = 0

        put = {
            "title": (chosen_title or "AutoPlay Track").title(),
            "dur": chosen_duration or "03:00",
            "streamtype": "audio",
            "by": "AutoPlay 🔄",
            "user_id": app.id,
            "chat_id": original_chat_id,
            "file": file_path,
            "vidid": chosen_vidid,
            "seconds": dur_sec,
            "played": 0,
        }
        db[chat_id] = [put]
        autoclean.append(file_path)

        # Send Player Notification in Group with working controls
        img = await get_thumb(chosen_vidid)
        language = await get_lang(chat_id)
        _ = get_string(language)
        button = stream_markup(_, chat_id)

        safe_caption_title = safe_str(chosen_title)[:35] or "AutoPlay Track"
        info_link = f"https://t.me/{app.username}?start=info_{chosen_vidid}"

        caption_text = (
            f"🔄 <b><u>AUTOPLAY TRIGGERED</u></b>\n\n"
            f"📌 <b>Title:</b> <a href='{info_link}'>{safe_caption_title}</a>\n"
            f"⏱ <b>Duration:</b> <code>{chosen_duration}</code>\n"
            f"👤 <b>Requested By:</b> <code>AutoPlay System</code>"
        )

        run = await app.send_photo(
            chat_id=original_chat_id,
            photo=img,
            caption=caption_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(button),
        )
        if chat_id in db and len(db[chat_id]) > 0:
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "stream"
    except Exception as e:
        LOGGER(__name__).error(f"[AutoPlay] Runner Error: {e}")
        await _clear_(chat_id)
        try:
            await client.leave_group_call(chat_id)
        except Exception:
            pass


# --- Command Handler: /autoplay ---
@app.on_message(filters.command(["autoplay", "cautoplay"]) & filters.group & ~BANNED_USERS)
@AdminRightsCheck
async def autoplay_command(cli, message: Message, _, chat_id):
    if len(message.command) < 2:
        current = await is_autoplay_enabled(chat_id)
        new_state = not current
        await set_autoplay(chat_id, new_state)
        status_text = "ENABLED ✅" if new_state else "DISABLED ❌"
        return await message.reply_text(
            f"🔄 <b>AutoPlay {status_text}</b> by {message.from_user.mention}",
            parse_mode=ParseMode.HTML
        )

    state = message.text.split(None, 1)[1].strip().lower()
    if state in ["enable", "on", "yes"]:
        await set_autoplay(chat_id, True)
        await message.reply_text(f"🔄 <b>AutoPlay ENABLED ✅</b> by {message.from_user.mention}", parse_mode=ParseMode.HTML)
    elif state in ["disable", "off", "no"]:
        await set_autoplay(chat_id, False)
        await message.reply_text(f"🔄 <b>AutoPlay DISABLED ❌</b> by {message.from_user.mention}", parse_mode=ParseMode.HTML)
    else:
        await message.reply_text("Usage:\n/autoplay [enable|disable]")


# --- Callback Query Handler ---
@app.on_callback_query(filters.regex(r"^ADMIN AutoPlay\|") & ~BANNED_USERS)
async def autoplay_callback_handler(client, CallbackQuery: CallbackQuery):
    callback_data = CallbackQuery.data.strip()
    chat_id = int(callback_data.split("|")[1])

    if not await is_active_chat(chat_id):
        return await CallbackQuery.answer("❌ No song is currently playing in VC.", show_alert=True)

    if CallbackQuery.from_user.id not in SUDOERS:
        admins = adminlist.get(CallbackQuery.message.chat.id)
        if not admins or CallbackQuery.from_user.id not in admins:
            return await CallbackQuery.answer("❌ This button can only be used by admins.", show_alert=True)

    current = await is_autoplay_enabled(chat_id)
    new_state = not current
    await set_autoplay(chat_id, new_state)

    status_str = "ENABLED ✅" if new_state else "DISABLED ❌"
    await CallbackQuery.answer(f"🔄 AutoPlay {status_str}", show_alert=True)
