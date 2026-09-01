import os
import random
import re
from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery
from pytgcalls.types import MediaStream
from pytgcalls.types.stream import StreamAudioEnded
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
    
    try:
        search = VideosSearch(search_query, limit=20)
        results = await search.next()
        
        if results and isinstance(results, dict) and "result" in results:
            res_list = results.get("result") or []
            candidates = []
            
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

            # Pick from valid candidates
            if candidates:
                selected = random.choice(candidates)
                played_history[chat_id].add(selected[0])
                
                if len(played_history[chat_id]) > 60:
                    played_history[chat_id].clear()
                    
                return selected[0], selected[1], selected[2]

            # Fallback for unplayed track
            for track in res_list:
                if not isinstance(track, dict):
                    continue
                vidid = safe_str(track.get("id"))
                raw_track_title = safe_str(track.get("title")) or "AutoPlay Track"
                duration = safe_str(track.get("duration")) or "03:00"
                
                if vidid and vidid not in played_history[chat_id]:
                    played_history[chat_id].add(vidid)
                    return vidid, raw_track_title, duration

    except Exception as e:
        LOGGER(__name__).error(f"[AutoPlay Search Error]: {e}")
        
    return None, None, None


# --- Auto-Stream Execution ---
async def trigger_autoplay(client, chat_id: int, last_track: dict = None):
    from EsproMusic.core.call import _clear_

    try:
        last_title = ""
        if isinstance(last_track, dict):
            last_title = safe_str(last_track.get("title"))
        
        # Download Retry Mechanism (3 Tries)
        file_path = None
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
                    break
                else:
                    if downloaded and os.path.exists(downloaded):
                        os.remove(downloaded)
            except Exception as ex:
                LOGGER(__name__).error(f"Download Retry Error: {ex}")
                continue

        if not file_path:
            await _clear_(chat_id)
            return await client.leave_group_call(chat_id)

        # Stream via PyTgCalls (MediaStream Universal Support)
        try:
            stream = MediaStream(file_path)
            await client.change_stream(chat_id, stream)
        except Exception:
            # Fallback for older pytgcalls syntax
            from pytgcalls.types.input_stream import AudioPiped
            stream = AudioPiped(file_path)
            await client.change_stream(chat_id, stream)

        # Queue Entry
        original_chat_id = chat_id
        if isinstance(last_track, dict):
            original_chat_id = last_track.get("chat_id", chat_id)

        await put_queue(
            chat_id,
            original_chat_id,
            file_path,
            title,
            duration_min,
            "AutoPlay 🔄",
            vidid,
            app.id,
            "audio",
        )

        # Send Player Notification in Group
        img = await get_thumb(vidid)
        language = await get_lang(chat_id)
        _ = get_string(language)
        button = stream_markup(_, chat_id)

        safe_caption_title = safe_str(title)[:35] or "AutoPlay Track"
        info_link = f"https://t.me/{app.username}?start=info_{vidid}"

        # --- Premium HTML Rendered UI ---
        caption_text = (
            f"🔄 <b><u>AUTOPLAY TRIGGERED</u></b>\n\n"
            f"📌 <b>Title:</b> <a href='{info_link}'>{safe_caption_title}</a>\n"
            f"⏱ <b>Duration:</b> <code>{duration_min}</code>\n"
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
        LOGGER(__name__).error(f"AutoPlay Runner Error: {e}")
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
