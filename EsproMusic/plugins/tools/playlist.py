import math
import uuid
import time
import re
import asyncio
from pyrogram import filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from EsproMusic import app
from EsproMusic.misc import db
from EsproMusic.core.mongo import mongodb
import config

# Safe import for Call / Stream instance
try:
    from EsproMusic.core.call import Ritik as EsproCall
except ImportError:
    try:
        from EsproMusic.core.call import Espro as EsproCall
    except ImportError:
        from EsproMusic.core.call import Call as EsproCall

from EsproMusic.utils.database import (
    get_lang,
    remove_active_chat,
    remove_active_video_chat,
)
from EsproMusic.utils.stream.stream import stream
from config import BANNED_USERS

# Safe import for YouTube search helper
try:
    from EsproMusic.platforms import YouTube
    youtube = YouTube()
except Exception:
    youtube = None


# ==============================================================================
# DATABASE LAYER (MongoDB)
# ==============================================================================
playlist_collection = mongodb.playlists_v2

async def db_get_user_playlists(user_id: int):
    cursor = playlist_collection.find({"user_id": user_id})
    playlists = []
    async for doc in cursor:
        playlists.append(doc)
    return playlists

async def db_get_playlist(user_id: int, playlist_id: str):
    return await playlist_collection.find_one(
        {"user_id": user_id, "playlist_id": playlist_id}
    )

async def db_create_playlist(user_id: int, name: str):
    existing = await playlist_collection.find_one(
        {"user_id": user_id, "name": name}
    )
    if existing:
        return None, "DUPLICATE"

    playlist_id = f"pl_{uuid.uuid4().hex[:8]}"
    doc = {
        "user_id": user_id,
        "playlist_id": playlist_id,
        "name": name,
        "songs": [],
        "created_at": time.time(),
    }
    await playlist_collection.insert_one(doc)
    return playlist_id, "SUCCESS"

async def db_add_song_to_playlist(user_id: int, playlist_id: str, song_data: dict):
    playlist = await db_get_playlist(user_id, playlist_id)
    if not playlist:
        return False, "NOT_FOUND"

    song_id = f"s_{uuid.uuid4().hex[:8]}"
    song_entry = {
        "song_id": song_id,
        "title": song_data.get("title", "Unknown Track"),
        "artist": song_data.get("artist", "Unknown Artist"),
        "vidid": song_data.get("vidid", "none"),
        "url": song_data.get("url", ""),
        "duration": song_data.get("duration", "03:00"),
        "thumbnail": song_data.get("thumbnail", ""),
        "added_at": time.time(),
    }

    for s in playlist.get("songs", []):
        if s.get("vidid") and s.get("vidid") != "none" and s.get("vidid") == song_entry["vidid"]:
            return False, "DUPLICATE"

    await playlist_collection.update_one(
        {"user_id": user_id, "playlist_id": playlist_id},
        {"$push": {"songs": song_entry}},
    )
    return True, song_entry

async def db_delete_playlist(user_id: int, playlist_id: str):
    res = await playlist_collection.delete_one(
        {"user_id": user_id, "playlist_id": playlist_id}
    )
    return res.deleted_count > 0

async def db_remove_song(user_id: int, playlist_id: str, song_id: str):
    res = await playlist_collection.update_one(
        {"user_id": user_id, "playlist_id": playlist_id},
        {"$pull": {"songs": {"song_id": song_id}}},
    )
    return res.modified_count > 0


# ==============================================================================
# ROBUST TRACK RESOLVER
# ==============================================================================
def extract_yt_id(url_or_id: str):
    if not url_or_id or str(url_or_id).lower() in ["none", "null", ""]:
        return None
    url_or_id = str(url_or_id).strip()
    if re.match(r"^[a-zA-Z0-9_-]{11}$", url_or_id):
        return url_or_id
    match = re.search(r"(?:v=|\/|vi\/|youtu\.be\/)([a-zA-Z0-9_-]{11})", url_or_id)
    if match:
        return match.group(1)
    return None

async def resolve_youtube_track(query: str, vidid: str = None, url: str = None):
    existing_id = extract_yt_id(vidid) or extract_yt_id(url) or extract_yt_id(query)
    if existing_id:
        return {
            "vidid": existing_id,
            "title": query if query and not query.startswith("http") else "YouTube Track",
            "link": f"https://www.youtube.com/watch?v={existing_id}",
            "duration_min": "03:00",
            "thumb": f"https://i.ytimg.com/vi/{existing_id}/hqdefault.jpg",
        }

    search_query = query if query else "Hindi Music"

    if youtube:
        try:
            res = await youtube.track(search_query)
            if res:
                if isinstance(res, (list, tuple)):
                    if len(res) >= 5 and isinstance(res[4], str):
                        v = extract_yt_id(res[4])
                        if v:
                            return {
                                "vidid": v,
                                "title": str(res[0]),
                                "link": f"https://www.youtube.com/watch?v={v}",
                                "duration_min": str(res[1]),
                                "thumb": str(res[3]),
                            }
                    elif len(res) >= 1 and isinstance(res[0], dict):
                        d = res[0]
                        v = extract_yt_id(d.get("vidid") or d.get("id"))
                        if v:
                            return {
                                "vidid": v,
                                "title": d.get("title", search_query),
                                "link": f"https://www.youtube.com/watch?v={v}",
                                "duration_min": d.get("duration_min", "03:00"),
                                "thumb": d.get("thumb", f"https://i.ytimg.com/vi/{v}/hqdefault.jpg"),
                            }
                elif isinstance(res, dict):
                    v = extract_yt_id(res.get("vidid") or res.get("id"))
                    if v:
                        return {
                            "vidid": v,
                            "title": res.get("title", search_query),
                            "link": f"https://www.youtube.com/watch?v={v}",
                            "duration_min": res.get("duration_min", "03:00"),
                            "thumb": res.get("thumb", f"https://i.ytimg.com/vi/{v}/hqdefault.jpg"),
                        }
        except Exception:
            pass

    try:
        from youtubesearchpython.__future__ import VideosSearch
        resultsSearch = VideosSearch(search_query, limit=1)
        searchResults = await resultsSearch.next()
        if searchResults and searchResults.get("result"):
            first_res = searchResults["result"][0]
            v_id = first_res.get("id")
            if v_id:
                return {
                    "vidid": v_id,
                    "title": first_res.get("title", search_query),
                    "link": f"https://www.youtube.com/watch?v={v_id}",
                    "duration_min": first_res.get("duration", "03:00"),
                    "thumb": f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg",
                }
    except Exception:
        pass

    return None


# ==============================================================================
# PREMIUM UI / STATE MANAGEMENT 
# ==============================================================================
PLAYLIST_STATES = {}

async def render_my_playlists_screen(user_id: int):
    playlists = await db_get_user_playlists(user_id)
    if not playlists:
        text = (
            "▰▰▰▰▰▰▰▰▰▰▰▰\n"
            "   ★ ᴍʏ ᴘʟᴀʏʟɪsᴛs ★\n"
            "▰▰▰▰▰▰▰▰▰▰▰▰\n\n"
            "➥ ʏᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ᴀɴʏ sᴀᴠᴇᴅ ᴘʟᴀʏʟɪsᴛs ʏᴇᴛ."
        )
        buttons = [
            [InlineKeyboardButton("✚ ᴄʀᴇᴀᴛᴇ ɴᴇᴡ ᴘʟᴀʏʟɪsᴛ", callback_data="playlist:create")],
            [InlineKeyboardButton("✯ ᴄʟᴏsᴇ ✯", callback_data="close_cb")],
        ]
        return text, InlineKeyboardMarkup(buttons)

    text = (
        "───────────────\n"
        "   🎵 **ᴍʏ ᴘʟᴀʏʟɪsᴛs** 🎵\n"
        "───────────────\n\n"
        "Select a playlist to view songs or start playing:"
    )
    buttons = []
    for pl in playlists:
        pl_name = pl.get("name", "Playlist")
        pl_id = pl.get("playlist_id")
        song_count = len(pl.get("songs", []))
        
        buttons.append([
            InlineKeyboardButton(f"📁 {pl_name[:15]} • {song_count} songs", callback_data=f"playlist:view:{pl_id}:1"),
            InlineKeyboardButton("▶️", callback_data=f"playlist:play:{pl_id}"),
        ])

    buttons.append([InlineKeyboardButton("➕ ᴄʀᴇᴀᴛᴇ ɴᴇᴡ ᴘʟᴀʏʟɪsᴛ", callback_data="playlist:create")])
    buttons.append([InlineKeyboardButton("❌ ᴄʟᴏsᴇ", callback_data="close_cb")])

    return text, InlineKeyboardMarkup(buttons)


async def render_playlist_details_screen(user_id: int, playlist_id: str, page: int = 1):
    playlist = await db_get_playlist(user_id, playlist_id)
    if not playlist:
        text = "❌ **Playlist not found or has been deleted.**"
        buttons = [[InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴘʟᴀʏʟɪsᴛs", callback_data="playlist:list")]]
        return text, InlineKeyboardMarkup(buttons)

    pl_name = playlist.get("name", "Playlist")
    songs = playlist.get("songs", [])
    total_songs = len(songs)

    if total_songs == 0:
        text = (
            f"★ **ᴘʟᴀʏʟɪsᴛ:** `{pl_name}`\n\n"
            "➥ ɴᴏ sᴏɴɢs ʜᴀᴠᴇ ʙᴇᴇɴ ᴀᴅᴅᴇᴅ ʏᴇᴛ."
        )
        buttons = [
            [InlineKeyboardButton("✚ ᴀᴅᴅ sᴏɴɢ", callback_data=f"playlist:add_manual:{playlist_id}")],
            [
                InlineKeyboardButton("🗑 ᴅᴇʟᴇᴛᴇ", callback_data=f"playlist:delete:{playlist_id}"),
                InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data="playlist:list"),
            ],
        ]
        return text, InlineKeyboardMarkup(buttons)

    per_page = 5
    total_pages = math.ceil(total_songs / per_page)
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    page_songs = songs[start_idx:end_idx]

    text = (
        f"★ **ᴘʟᴀʏʟɪsᴛ:** `{pl_name}`\n"
        f"★ **ᴛᴏᴛᴀʟ sᴏɴɢs:** `{total_songs}`\n\n"
        f"➥ **sᴏɴɢs ʟɪsᴛ (ᴘᴀɢᴇ {page}/{total_pages}):**\n"
    )

    song_buttons = []
    for idx, song in enumerate(page_songs, start=start_idx + 1):
        s_title = song.get("title", "Track")
        s_id = song.get("song_id")
        text += f"**{idx}.** `{s_title[:30]}`\n"
        song_buttons.append([
            InlineKeyboardButton(f"{idx}. {s_title[:28]}", callback_data=f"playlist:song:{playlist_id}:{s_id}")
        ])

    action_buttons = [
        [InlineKeyboardButton("⊳ ᴘʟᴀʏ ᴀʟʟ", callback_data=f"playlist:play:{playlist_id}")],
        [
            InlineKeyboardButton("✚ ᴀᴅᴅ sᴏɴɢ", callback_data=f"playlist:add_manual:{playlist_id}"),
            InlineKeyboardButton("🗑 ᴅᴇʟᴇᴛᴇ", callback_data=f"playlist:delete:{playlist_id}"),
        ]
    ]

    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("⇦ ᴘʀᴇᴠ", callback_data=f"playlist:view:{playlist_id}:{page-1}"))
    nav_buttons.append(InlineKeyboardButton(f"📖 {page}/{total_pages}", callback_data="playlist:ignore"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("ɴᴇxᴛ ⇨", callback_data=f"playlist:view:{playlist_id}:{page+1}"))

    full_keyboard = song_buttons + action_buttons
    if total_pages > 1:
        full_keyboard.append(nav_buttons)
    full_keyboard.append([InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴘʟᴀʏʟɪsᴛs", callback_data="playlist:list")])

    return text, InlineKeyboardMarkup(full_keyboard)


# ==============================================================================
# MAIN COMMAND HANDLER: /playlist & /myplaylist
# ==============================================================================
@app.on_message(filters.command(["playlist", "myplaylist"]) & ~BANNED_USERS)
async def my_playlist_cmd(client, message: Message):
    user_id = message.from_user.id
    text, reply_markup = await render_my_playlists_screen(user_id)
    await message.reply_text(text, reply_markup=reply_markup)


# ==============================================================================
# COMPATIBILITY ALIAS FOR OLD IMPORTS
# ==============================================================================
async def show_my_playlists_menu(client, message: Message):
    """Alias for legacy modules expecting show_my_playlists_menu"""
    user_id = message.from_user.id
    text, reply_markup = await render_my_playlists_screen(user_id)
    return await message.reply_text(text, reply_markup=reply_markup)


# ==============================================================================
# CLOSE BUTTON CALLBACK
# ==============================================================================
@app.on_callback_query(filters.regex(r"^close_cb$") & ~BANNED_USERS)
async def close_cb_handler(client, cb: CallbackQuery):
    try:
        await cb.message.delete()
    except Exception:
        pass


# ==============================================================================
# ADD TO PLAYLIST FROM GROUP STREAM BUTTON
# ==============================================================================
@app.on_callback_query(filters.regex(r"^add_playlist") & ~BANNED_USERS)
async def add_playlist_from_stream(client, cb: CallbackQuery):
    try:
        chat_id = int(cb.data.split()[1])
    except Exception:
        chat_id = cb.message.chat.id

    playing = db.get(chat_id)
    if not playing or len(playing) == 0:
        return await cb.answer("❌ Abhi koi song play nahi ho raha hai!", show_alert=True)

    track = playing[0]
    vidid = track.get("vidid")
    title = str(track.get("title", "Unknown Track")).title()

    if not vidid or vidid in ["telegram", "soundcloud"]:
        return await cb.answer("❌ Yeh track playlist mein add nahi ho sakta.", show_alert=True)

    user_id = cb.from_user.id
    user_playlists = await db_get_user_playlists(user_id)

    if not user_playlists:
        pl_id, status = await db_create_playlist(user_id, "My Favorite Songs")
        target_pl_id = pl_id
    else:
        target_pl_id = user_playlists[0]["playlist_id"]

    song_data = {
        "title": title,
        "artist": "YouTube",
        "vidid": vidid,
        "url": f"https://www.youtube.com/watch?v={vidid}",
        "duration": str(track.get("dur", "03:00")),
        "thumbnail": str(track.get("thumb", "")),
    }

    success, res = await db_add_song_to_playlist(user_id, target_pl_id, song_data)
    if res == "DUPLICATE":
        return await cb.answer("⚠️ Yeh song pehle se aapki playlist mein added hai!", show_alert=True)

    await cb.answer(f"✅ '{title[:25]}' aapki playlist mein save ho gaya!", show_alert=True)


# ==============================================================================
# ROUTER & CALLBACK HANDLER
# ==============================================================================
@app.on_callback_query(filters.regex(r"^playlist:") & ~BANNED_USERS)
async def playlist_callback_router(client, cb: CallbackQuery):
    data = cb.data.split(":")
    action = data[1]
    user_id = cb.from_user.id

    if action == "ignore":
        return await cb.answer()

    if action == "list":
        if user_id in PLAYLIST_STATES:
            PLAYLIST_STATES.pop(user_id, None)

        text, reply_markup = await render_my_playlists_screen(user_id)
        await cb.message.edit_text(text, reply_markup=reply_markup)
        await cb.answer()

    elif action == "create":
        PLAYLIST_STATES[user_id] = {
            "state": "WAITING_PLAYLIST_NAME",
            "chat_id": cb.message.chat.id,
            "msg_id": cb.message.id,
        }

        text = (
            "📂 **ᴄʀᴇᴀᴛᴇ ɴᴇᴡ ᴘʟᴀʏʟɪsᴛ**\n\n"
            "➥ ᴘʟᴇᴀsᴇ **ᴛʏᴘᴇ ᴀɴᴅ sᴇɴᴅ ᴛʜᴇ ɴᴀᴍᴇ** ꜰᴏʀ ʏᴏᴜʀ ɴᴇᴡ ᴘʟᴀʏʟɪsᴛ ɪɴ ᴛʜɪs ᴄʜᴀᴛ."
        )
        buttons = [[InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="playlist:cancel")]]
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        await cb.answer()

    elif action == "cancel":
        PLAYLIST_STATES.pop(user_id, None)
        text, reply_markup = await render_my_playlists_screen(user_id)
        await cb.message.edit_text(text, reply_markup=reply_markup)
        await cb.answer("Action cancelled.")

    elif action == "view":
        playlist_id = data[2]
        page = int(data[3]) if len(data) > 3 else 1
        text, reply_markup = await render_playlist_details_screen(user_id, playlist_id, page)
        await cb.message.edit_text(text, reply_markup=reply_markup)
        await cb.answer()

    elif action == "add_manual":
        playlist_id = data[2]
        PLAYLIST_STATES[user_id] = {
            "state": "WAITING_PLAYLIST_SONG",
            "playlist_id": playlist_id,
            "chat_id": cb.message.chat.id,
            "msg_id": cb.message.id,
        }

        text = (
            "🎵 **ᴀᴅᴅ sᴏɴɢ ᴛᴏ ᴘʟᴀʏʟɪsᴛ**\n\n"
            "➥ ᴘʟᴇᴀsᴇ **ᴛʏᴘᴇ ᴀɴᴅ sᴇɴᴅ ᴛʜᴇ sᴏɴɢ ɴᴀᴍᴇ ᴏʀ ʏᴏᴜᴛᴜʙᴇ ʟɪɴᴋ** ɪɴ ᴛʜɪs ᴄʜᴀᴛ."
        )
        buttons = [[InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"playlist:view:{playlist_id}:1")]]
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        await cb.answer()

    elif action == "song":
        playlist_id = data[2]
        song_id = data[3]

        playlist = await db_get_playlist(user_id, playlist_id)
        if not playlist:
            return await cb.answer("Playlist not found.", show_alert=True)

        matched_song = None
        for s in playlist.get("songs", []):
            if s.get("song_id") == song_id:
                matched_song = s
                break

        if not matched_song:
            return await cb.answer("Song not found.", show_alert=True)

        text = (
            f"🎵 **{matched_song.get('title')}**\n"
            f"👤 **Artist:** `{matched_song.get('artist')}`\n"
            f"⏱️ **Duration:** `{matched_song.get('duration')}`\n"
            f"📂 **Playlist:** `{playlist.get('name')}`"
        )
        buttons = [
            [InlineKeyboardButton("🗑 ʀᴇᴍᴏᴠᴇ sᴏɴɢ", callback_data=f"playlist:song_remove_confirm:{playlist_id}:{song_id}")],
            [InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"playlist:view:{playlist_id}:1")],
        ]
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        await cb.answer()

    elif action == "song_remove_confirm":
        playlist_id = data[2]
        song_id = data[3]

        text = (
            "⚠️ **ʀᴇᴍᴏᴠᴇ sᴏɴɢ?**\n\n"
            "Are you sure you want to remove this song from your playlist?"
        )
        buttons = [
            [
                InlineKeyboardButton("✅ ʏᴇs", callback_data=f"playlist:song_remove:{playlist_id}:{song_id}"),
                InlineKeyboardButton("❌ ɴᴏ", callback_data=f"playlist:song:{playlist_id}:{song_id}"),
            ]
        ]
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        await cb.answer()

    elif action == "song_remove":
        playlist_id = data[2]
        song_id = data[3]

        await db_remove_song(user_id, playlist_id, song_id)
        await cb.answer("Song removed!", show_alert=True)

        text, reply_markup = await render_playlist_details_screen(user_id, playlist_id, page=1)
        await cb.message.edit_text(text, reply_markup=reply_markup)

    elif action == "delete":
        playlist_id = data[2]
        playlist = await db_get_playlist(user_id, playlist_id)
        if not playlist:
            return await cb.answer("Playlist not found.", show_alert=True)

        text = (
            f"⚠️ **ᴅᴇʟᴇᴛᴇ ᴘʟᴀʏʟɪsᴛ?**\n\n"
            f"📂 **Name:** `{playlist.get('name')}`\n"
            f"🎵 **Songs:** `{len(playlist.get('songs', []))}`\n\n"
            "This will