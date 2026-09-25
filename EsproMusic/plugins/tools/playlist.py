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
# UI MANAGEMENT & IMPORT HELPER FUNCTIONS
# ==============================================================================
PLAYLIST_STATES = {}

async def render_my_playlists_screen(user_id: int):
    playlists = await db_get_user_playlists(user_id)
    if not playlists:
        text = (
            "★ **ᴍʏ ᴘʟᴀʏʟɪsᴛs** ★\n\n"
            "➥ ʏᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ᴀɴʏ sᴀᴠᴇᴅ ᴘʟᴀʏʟɪsᴛs ʏᴇᴛ."
        )
        buttons = [
            [InlineKeyboardButton("✚ ᴄʀᴇᴀᴛᴇ ɴᴇᴡ ᴘʟᴀʏʟɪsᴛ", callback_data="playlist:create")],
            [InlineKeyboardButton("✯ ᴄʟᴏsᴇ ✯", callback_data="close_cb")],
        ]
        return text, InlineKeyboardMarkup(buttons)

    text = (
        "🎵 **ᴍʏ ᴘʟᴀʏʟɪsᴛs** 🎵\n\n"
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


async def show_my_playlists_menu(client, message_or_cb, user_id: int = None):
    if user_id is None:
        user_id = message_or_cb.from_user.id
    text, reply_markup = await render_my_playlists_screen(user_id)

    if isinstance(message_or_cb, CallbackQuery):
        await message_or_cb.message.edit_text(text, reply_markup=reply_markup)
    else:
        await message_or_cb.reply_text(text, reply_markup=reply_markup)

display_my_playlists = show_my_playlists_menu


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
# COMMAND HANDLER: /playlist & /myplaylist
# ==============================================================================
@app.on_message(filters.command(["playlist", "myplaylist"]) & ~BANNED_USERS)
async def my_playlist_cmd(client, message: Message):
    user_id = message.from_user.id
    await show_my_playlists_menu(client, message, user_id)


@app.on_callback_query(filters.regex(r"^close_cb$") & ~BANNED_USERS)
async def close_cb_handler(client, cb: CallbackQuery):
    try:
        await cb.message.delete()
    except Exception:
        pass


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

        await show_my_playlists_menu(client, cb, user_id)
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
        await show_my_playlists_menu(client, cb, user_id)
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
            "This will permanently delete this playlist."
        )
        buttons = [
            [
                InlineKeyboardButton("✅ ʏᴇs, ᴅᴇʟᴇᴛᴇ", callback_data=f"playlist:delete_confirm:{playlist_id}"),
                InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"playlist:view:{playlist_id}:1"),
            ]
        ]
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        await cb.answer()

    elif action == "delete_confirm":
        playlist_id = data[2]
        deleted = await db_delete_playlist(user_id, playlist_id)

        if deleted:
            await cb.answer("Playlist deleted!", show_alert=True)
        else:
            await cb.answer("Failed to delete playlist.", show_alert=True)

        await show_my_playlists_menu(client, cb, user_id)

    elif action == "play":
        playlist_id = data[2]
        playlist = await db_get_playlist(user_id, playlist_id)
        if not playlist or not playlist.get("songs"):
            return await cb.answer("❌ Playlist is empty or does not exist!", show_alert=True)

        pl_name = playlist.get("name", "Playlist")
        cmd = f"/playplaylist {playlist_id}"

        text = (
            f"⊳ **ᴘʟᴀʏ ᴘʟᴀʏʟɪsᴛ ɪɴ ɢʀᴏᴜᴘ**\n\n"
            f"📂 **Playlist:** `{pl_name}` ({len(playlist['songs'])} songs)\n\n"
            f"👇 **Group mein bhejain:**\n\n"
            f"`{cmd}`"
        )
        buttons = [
            [InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴘʟᴀʏʟɪsᴛ", callback_data=f"playlist:view:{playlist_id}:1")],
            [InlineKeyboardButton("✯ ᴄʟᴏsᴇ ✯", callback_data="close_cb")]
        ]
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        await cb.answer()


# ==============================================================================
# GROUP COMMAND: /playplaylist & /playpl (FIXED QUEUE SKIP NEXT SONG)
# ==============================================================================
@app.on_message(filters.command(["playplaylist", "playpl"]) & ~BANNED_USERS)
async def play_playlist_cmd(client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if message.chat.type.name == "PRIVATE":
        return await message.reply_text("⚠️ **This command works in Group Chats!**")

    args = message.text.split()
    if len(args) < 2:
        playlists = await db_get_user_playlists(user_id)
        if not playlists:
            return await message.reply_text("❌ You don't have any saved playlists!")
        
        text = "🎶 **Your Saved Playlists:**\n\n"
        for pl in playlists:
            text += f"• `{pl.get('name')}` ➡️ `/playplaylist {pl.get('playlist_id')}`\n"
        return await message.reply_text(text)

    playlist_id = args[1]
    playlist = await db_get_playlist(user_id, playlist_id)
    if not playlist or not playlist.get("songs"):
        return await message.reply_text("❌ **Playlist not found or empty!**")

    songs = playlist["songs"]
    pl_name = playlist.get("name", "Playlist")

    mystic = await message.reply_text("🔄 **Resolving tracks for playlist stream...**")

    valid_queue = []
    for song in songs:
        resolved = await resolve_youtube_track(
            query=song.get("title", ""),
            vidid=song.get("vidid"),
            url=song.get("url"),
        )
        if resolved:
            valid_queue.append(resolved)

    if not valid_queue:
        return await mystic.edit_text("❌ **Playlist ke gane resolve nahi ho paaye.**")

    # Clear active chat voice call and old db queue
    db[chat_id] = []
    try:
        if hasattr(EsproCall, "stop_stream"):
            await EsproCall.stop_stream(chat_id)
    except Exception:
        pass

    try:
        await remove_active_chat(chat_id)
        await remove_active_video_chat(chat_id)
    except Exception:
        pass

    await asyncio.sleep(1)

    try:
        language = await get_lang(chat_id)
        from strings import get_string
        _ = get_string(language)
    except Exception:
        class DummyLang(dict):
            def __getitem__(self, item):
                return self.get(item, "")
        _ = DummyLang()

    user_name = message.from_user.first_name if message.from_user else "User"

    first_track = valid_queue[0]
    first_details = {
        "title": first_track["title"],
        "link": first_track["link"],
        "vidid": first_track["vidid"],
        "dur": first_track["duration_min"],
        "duration_min": first_track["duration_min"],
        "thumb": first_track["thumb"],
        "by": user_name,
        "user": user_name,
        "user_id": user_id,
        "streamtype": "youtube",
        "file": f"vid_{first_track['vidid']}",
    }

    # PROPER METADATA FOR QUEUE NEXT TRACK SKIP
    for song in valid_queue[1:]:
        d_item = {
            "title": song["title"],
            "link": song["link"],
            "vidid": song["vidid"],
            "dur": song["duration_min"],
            "duration_min": song["duration_min"],
            "thumb": song["thumb"],
            "by": user_name,
            "user": user_name,
            "user_id": user_id,
            "streamtype": "youtube",
            "file": f"vid_{song['vidid']}",
            "old_dur": song["duration_min"],
            "old_second": 180,
            "played": 0,
        }
        db[chat_id].append(d_item)

    try:
        await stream(
            _,
            mystic,
            user_id,
            first_details,
            chat_id,
            user_name,
            chat_id,
            video=None,
            streamtype="youtube",
            forceplay=True,
        )
        await mystic.edit_text(
            f"⊳ **ᴘʟᴀʏʟɪsᴛ ᴘʟᴀʏɪɴɢ ɪɴ ɢʀᴏᴜᴘ!**\n\n"
            f"📂 **Name:** `{pl_name}`\n"
            f"🎵 **Total Queued:** `{len(valid_queue)} songs`\n\n"
            f"💡 *Ab aap `/skip` ya Inline Skip button dabaney par agla song play ho jayega!*"
        )
    except Exception as e:
        await mystic.edit_text(f"❌ **Error playing playlist:** `{e}`")


# ==============================================================================
# TEXT INPUT LISTENER FOR CREATION
# ==============================================================================
@app.on_message(filters.text & filters.private & ~BANNED_USERS, group=10)
async def playlist_text_input_handler(client, message: Message):
    user_id = message.from_user.id
    if user_id not in PLAYLIST_STATES:
        return

    input_text = message.text.strip()
    if input_text.startswith("/"):
        return

    state_data = PLAYLIST_STATES.pop(user_id, None)
    if not state_data:
        return

    state = state_data.get("state")
    chat_id = state_data.get("chat_id")
    msg_id = state_data.get("msg_id")

    if state == "WAITING_PLAYLIST_NAME":
        if not input_text or len(input_text) > 30:
            PLAYLIST_STATES[user_id] = state_data
            return await message.reply_text("❌ **Invalid name!** Must be 1 to 30 characters long.")

        pl_id, status = await db_create_playlist(user_id, input_text)
        if status == "DUPLICATE":
            PLAYLIST_STATES[user_id] = state_data
            return await message.reply_text(f"❌ You already have a playlist named `{input_text}`!")

        text = (
            "✅ **ᴘʟᴀʏʟɪsᴛ ᴄʀᴇᴀᴛᴇᴅ sᴜᴄᴄᴇssꜰᴜʟʟʏ!**\n\n"
            f"📂 **Name:** `{input_text}`\n"
            "Your playlist is ready. Start adding songs now!"
        )
        buttons = [
            [InlineKeyboardButton("✚ ᴀᴅᴅ sᴏɴɢs", callback_data=f"playlist:add_manual:{pl_id}")],
            [InlineKeyboardButton("🎵 ᴠɪᴇᴡ ᴘʟᴀʏʟɪsᴛ", callback_data=f"playlist:view:{pl_id}:1")],
            [InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴘʟᴀʏʟɪsᴛs", callback_data="playlist:list")],
        ]

        try:
            await client.edit_message_text(chat_id, msg_id, text, reply_markup=InlineKeyboardMarkup(buttons))
        except Exception:
            await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))

    elif state == "WAITING_PLAYLIST_SONG":
        playlist_id = state_data.get("playlist_id")
        searching_msg = await message.reply_text("🔎 **Searching song on YouTube...**")

        resolved = await resolve_youtube_track(input_text)
        await searching_msg.delete()

        if not resolved:
            return await message.reply_text("❌ **YouTube par song nahi mila!** Phir se try karein.")

        song_data = {
            "title": resolved["title"],
            "artist": "YouTube",
            "vidid": resolved["vidid"],
            "url": resolved["link"],
            "duration": resolved["duration_min"],
            "thumbnail": resolved["thumb"],
        }

        success, res = await db_add_song_to_playlist(user_id, playlist_id, song_data)
        playlist = await db_get_playlist(user_id, playlist_id)
        pl_name = playlist.get("name") if playlist else "Playlist"

        if res == "DUPLICATE":
            text = f"⚠️ **This track is already in '{pl_name}'!**"
        else:
            text = (
                "✅ **sᴏɴɢ ᴀᴅᴅᴇᴅ!**\n\n"
                f"🎵 **Track:** `{resolved['title']}`\n"
                f"📂 **Playlist:** `{pl_name}`"
            )

        buttons = [
            [InlineKeyboardButton("✚ ᴀᴅᴅ ᴀɴᴏᴛʜᴇʀ sᴏɴɢ", callback_data=f"playlist:add_manual:{playlist_id}")],
            [InlineKeyboardButton("🎵 ᴠɪᴇᴡ ᴘʟᴀʏʟɪsᴛ", callback_data=f"playlist:view:{playlist_id}:1")],
            [InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴘʟᴀʏʟɪsᴛs", callback_data="playlist:list")],
        ]

        try:
            await client.edit_message_text(chat_id, msg_id, text, reply_markup=InlineKeyboardMarkup(buttons))
        except Exception:
            await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
