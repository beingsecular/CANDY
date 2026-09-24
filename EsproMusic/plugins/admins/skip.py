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

# Call instance: in this fork it is `Ritik` (same as skip.py)
from EsproMusic.core.call import Ritik as Espro

from EsproMusic.utils.database import (
    get_lang,
    remove_active_chat,
    remove_active_video_chat,
)
from EsproMusic.utils.stream.stream import stream
from EsproMusic.utils.stream.queue import put_queue  # NEW: proper queue format
from config import BANNED_USERS

# Safe import for YouTube search helper
from EsproMusic import YouTube as youtube  # same import as skip.py


# ==============================================================================
# DATABASE LAYER (MongoDB)
# ==============================================================================
playlist_collection = mongodb.playlists_v2


async def db_get_user_playlists(user_id: int):
    """Fetch all playlists belonging to a specific user."""
    cursor = playlist_collection.find({"user_id": user_id})
    playlists = []
    async for doc in cursor:
        playlists.append(doc)
    return playlists


async def db_get_playlist(user_id: int, playlist_id: str):
    """Fetch a specific playlist belonging to a user."""
    return await playlist_collection.find_one(
        {"user_id": user_id, "playlist_id": playlist_id}
    )


async def db_create_playlist(user_id: int, name: str):
    """Create a new empty playlist for a user."""
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
    """Add a song object into a specific user playlist."""
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

    # Check duplicate video ID in playlist
    for s in playlist.get("songs", []):
        if s.get("vidid") and s.get("vidid") != "none" and s.get("vidid") == song_entry["vidid"]:
            return False, "DUPLICATE"

    await playlist_collection.update_one(
        {"user_id": user_id, "playlist_id": playlist_id},
        {"$push": {"songs": song_entry}},
    )
    return True, song_entry


async def db_delete_playlist(user_id: int, playlist_id: str):
    """Permanently delete a playlist belonging to user."""
    res = await playlist_collection.delete_one(
        {"user_id": user_id, "playlist_id": playlist_id}
    )
    return res.deleted_count > 0


async def db_remove_song(user_id: int, playlist_id: str, song_id: str):
    """Remove a single song from a playlist."""
    res = await playlist_collection.update_one(
        {"user_id": user_id, "playlist_id": playlist_id},
        {"$pull": {"songs": {"song_id": song_id}}},
    )
    return res.modified_count > 0


# ==============================================================================
# ROBUST TRACK RESOLVER
# ==============================================================================
def extract_yt_id(url_or_id: str):
    """Extract valid 11-character YouTube video ID."""
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
    """Guarantees a valid 11-char YouTube Video ID and streaming link."""
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

    try:
        import yt_dlp
        def yt_search_sync(q):
            ydl_opts = {'quiet': True, 'extract_flat': True, 'skip_download': True}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch1:{q}", download=False)
                if info and 'entries' in info and len(info['entries']) > 0:
                    entry = info['entries'][0]
                    return entry.get('id'), entry.get('title')
            return None, None

        v_id, v_title = await asyncio.to_thread(yt_search_sync, search_query)
        if v_id:
            return {
                "vidid": v_id,
                "title": v_title or search_query,
                "link": f"https://www.youtube.com/watch?v={v_id}",
                "duration_min": "03:00",
                "thumb": f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg",
            }
    except Exception:
        pass

    return None


# ==============================================================================
# STATE MANAGEMENT & UI
# ==============================================================================
PLAYLIST_STATES = {}


async def render_my_playlists_screen(user_id: int):
    playlists = await db_get_user_playlists(user_id)
    if not playlists:
        text = (
            "🎶 **─── ｢ MY PLAYLISTS ｣ ───**\n\n"
            "❌ *You don't have any saved playlists yet.*"
        )
        buttons = [
            [InlineKeyboardButton("➕ Create Your First Playlist", callback_data="playlist:create")],
            [InlineKeyboardButton("❌ Close", callback_data="close_cb")],
        ]
        return text, InlineKeyboardMarkup(buttons)

    text = (
        "🎶 **─── ｢ MY PLAYLISTS ｣ ───**\n\n"
        "Select a playlist to view songs or start playing:"
    )
    buttons = []
    for pl in playlists:
        pl_name = pl.get("name", "Playlist")
        pl_id = pl.get("playlist_id")
        song_count = len(pl.get("songs", []))
        buttons.append([
            InlineKeyboardButton(f"📁 {pl_name} • {song_count} songs", callback_data=f"playlist:view:{pl_id}:1"),
            InlineKeyboardButton("▶️", callback_data=f"playlist:play:{pl_id}"),
        ])

    buttons.append([InlineKeyboardButton("➕ Create New Playlist", callback_data="playlist:create")])
    buttons.append([InlineKeyboardButton("❌ Close", callback_data="close_cb")])

    return text, InlineKeyboardMarkup(buttons)


async def render_playlist_details_screen(user_id: int, playlist_id: str, page: int = 1):
    playlist = await db_get_playlist(user_id, playlist_id)
    if not playlist:
        text = "❌ **Playlist not found or has been deleted.**"
        buttons = [[InlineKeyboardButton("⬅️ Back to Playlists", callback_data="playlist:list")]]
        return text, InlineKeyboardMarkup(buttons)

    pl_name = playlist.get("name", "Playlist")
    songs = playlist.get("songs", [])
    total_songs = len(songs)

    if total_songs == 0:
        text = (
            f"📁 **Playlist:** `{pl_name}`\n\n"
            "❌ *No songs have been added yet.*"
        )
        buttons = [
            [InlineKeyboardButton("➕ Add Song", callback_data=f"playlist:add_manual:{playlist_id}")],
            [
                InlineKeyboardButton("🗑️ Delete Playlist", callback_data=f"playlist:delete:{playlist_id}"),
                InlineKeyboardButton("⬅️ Back", callback_data="playlist:list"),
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
        f"📁 **Playlist:** `{pl_name}`\n"
        f"🎵 **Total Songs:** {total_songs}\n"
        f"👤 **Owner:** You\n\n"
        f"**Songs List (Page {page}/{total_pages}):**\n"
    )

    song_buttons = []
    for idx, song in enumerate(page_songs, start=start_idx + 1):
        s_title = song.get("title", "Track")
        s_artist = song.get("artist", "Artist")
        s_id = song.get("song_id")
        text += f"{idx}. 🎵 **{s_title}** — _{s_artist}_\n"
        song_buttons.append([
            InlineKeyboardButton(f"{idx}. {s_title[:28]}", callback_data=f"playlist:song:{playlist_id}:{s_id}")
        ])

    action_buttons = [
        [InlineKeyboardButton("▶️ Play Playlist", callback_data=f"playlist:play:{playlist_id}")],
        [
            InlineKeyboardButton("➕ Add Song", callback_data=f"playlist:add_manual:{playlist_id}"),
            InlineKeyboardButton("🗑️ Delete Playlist", callback_data=f"playlist:delete:{playlist_id}"),
        ]
    ]

    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"playlist:view:{playlist_id}:{page-1}"))
    nav_buttons.append(InlineKeyboardButton(f"📖 {page}/{total_pages}", callback_data="playlist:ignore"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"playlist:view:{playlist_id}:{page+1}"))

    full_keyboard = song_buttons + action_buttons
    if total_pages > 1:
        full_keyboard.append(nav_buttons)
    full_keyboard.append([InlineKeyboardButton("⬅️ Back to Playlists", callback_data="playlist:list")])

    return text, InlineKeyboardMarkup(full_keyboard)


async def show_my_playlists_menu(client, message_or_cb):
    if isinstance(message_or_cb, Message):
        user_id = message_or_cb.from_user.id
        text, reply_markup = await render_my_playlists_screen(user_id)
        await message_or_cb.reply_text(text, reply_markup=reply_markup)
    elif isinstance(message_or_cb, CallbackQuery):
        user_id = message_or_cb.from_user.id
        text, reply_markup = await render_my_playlists_screen(user_id)
        await message_or_cb.message.edit_text(text, reply_markup=reply_markup)


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
            "📁 **Create New Playlist**\n\n"
            "Please **type and send the name** for your new playlist in this chat."
        )
        buttons = [[InlineKeyboardButton("❌ Cancel", callback_data="playlist:cancel")]]
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

    elif action == "add_current":
        chat_id = cb.message.chat.id
        active_track = None
        if chat_id in db and db[chat_id]:
            active_track = db[chat_id][0]

        if not active_track:
            return await cb.answer("❌ No active track found playing right now!", show_alert=True)

        playlists = await db_get_user_playlists(user_id)
        if not playlists:
            text = (
                "🎵 **Add Song to Playlist**\n\n"
                "You don't have any playlist yet! Create one first to save this song."
            )
            buttons = [
                [InlineKeyboardButton("➕ Create Playlist", callback_data="playlist:create")],
                [InlineKeyboardButton("⬅️ Back", callback_data="close_cb")],
            ]
            return await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))

        text = (
            "🎵 **Add Song to Playlist**\n\n"
            f"**Current Track:** `{active_track.get('title', 'Unknown Track')}`\n\n"
            "Choose a playlist where you want to save this song:"
        )

        buttons = []
        for pl in playlists:
            pl_id = pl.get("playlist_id")
            pl_name = pl.get("name")
            buttons.append([
                InlineKeyboardButton(f"📁 {pl_name}", callback_data=f"playlist:save_curr:{pl_id}")
            ])

        buttons.append([InlineKeyboardButton("➕ Create New Playlist", callback_data="playlist:create")])
        buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="close_cb")])

        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        await cb.answer()

    elif action == "save_curr":
        playlist_id = data[2]
        chat_id = cb.message.chat.id

        active_track = None
        if chat_id in db and db[chat_id]:
            active_track = db[chat_id][0]

        if not active_track:
            return await cb.answer("❌ Active song expired or stopped.", show_alert=True)

        resolved = await resolve_youtube_track(
            query=active_track.get("title", ""),
            vidid=active_track.get("vidid"),
            url=active_track.get("link"),
        )

        song_data = {
            "title": resolved["title"] if resolved else active_track.get("title", "Unknown Track"),
            "artist": active_track.get("user", "Artist"),
            "vidid": resolved["vidid"] if resolved else "none",
            "url": resolved["link"] if resolved else "",
            "duration": resolved["duration_min"] if resolved else "03:00",
            "thumbnail": resolved["thumb"] if resolved else "",
        }

        success, res = await db_add_song_to_playlist(user_id, playlist_id, song_data)
        playlist = await db_get_playlist(user_id, playlist_id)
        pl_name = playlist.get("name") if playlist else "Playlist"

        if res == "DUPLICATE":
            return await cb.answer(f"⚠️ Track already exists in '{pl_name}'!", show_alert=True)

        text = (
            "✅ **Song Added Successfully!**\n\n"
            f"🎵 **Track:** `{song_data['title']}`\n"
            f"📁 **Playlist:** `{pl_name}`"
        )
        buttons = [
            [InlineKeyboardButton("🎵 View Playlist", callback_data=f"playlist:view:{playlist_id}:1")],
            [InlineKeyboardButton("🎶 My Playlists", callback_data="playlist:list")],
            [InlineKeyboardButton("❌ Close", callback_data="close_cb")],
        ]
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        await cb.answer("Added to playlist!")

    elif action == "add_manual":
        playlist_id = data[2]
        PLAYLIST_STATES[user_id] = {
            "state": "WAITING_PLAYLIST_SONG",
            "playlist_id": playlist_id,
            "chat_id": cb.message.chat.id,
            "msg_id": cb.message.id,
        }

        text = (
            "🎵 **Add Song to Playlist**\n\n"
            "Please **type and send the song name or YouTube link** in this chat."
        )
        buttons = [[InlineKeyboardButton("❌ Cancel", callback_data=f"playlist:view:{playlist_id}:1")]]
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
            f"📁 **Playlist:** `{playlist.get('name')}`"
        )
        buttons = [
            [InlineKeyboardButton("🗑️ Remove Song", callback_data=f"playlist:song_remove_confirm:{playlist_id}:{song_id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"playlist:view:{playlist_id}:1")],
        ]
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        await cb.answer()

    elif action == "song_remove_confirm":
        playlist_id = data[2]
        song_id = data[3]

        text = (
            "⚠️ **Remove Song?**\n\n"
            "Are you sure you want to remove this song from your playlist?"
        )
        buttons = [
            [
                InlineKeyboardButton("✅ Yes, Remove", callback_data=f"playlist:song_remove:{playlist_id}:{song_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"playlist:song:{playlist_id}:{song_id}"),
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
            f"⚠️ **Delete Playlist?**\n\n"
            f"📁 **Name:** `{playlist.get('name')}`\n"
            f"🎵 **Songs:** `{len(playlist.get('songs', []))}`\n\n"
            "This will permanently delete this playlist and its saved tracks."
        )
        buttons = [
            [
                InlineKeyboardButton("✅ Yes, Delete", callback_data=f"playlist:delete_confirm:{playlist_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"playlist:view:{playlist_id}:1"),
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

        text, reply_markup = await render_my_playlists_screen(user_id)
        await cb.message.edit_text(text, reply_markup=reply_markup)

    elif action == "play":
        playlist_id = data[2]
        playlist = await db_get_playlist(user_id, playlist_id)
        if not playlist or not playlist.get("songs"):
            return await cb.answer("❌ Playlist is empty or does not exist!", show_alert=True)

        pl_name = playlist.get("name", "Playlist")
        cmd = f"/playplaylist {playlist_id}"

        text = (
            f"▶️ **Play Playlist in Group Chat**\n\n"
            f"📁 **Playlist:** `{pl_name}` ({len(playlist['songs'])} songs)\n\n"
            f"👇 **Neeche diya gaya command copy karke apne Group Chat (GC) mein bhejain:**\n\n"
            f"`{cmd}`\n\n"
            f"✨ *Yeh command group mein daalte hi playlist start ho jayegi!*"
        )
        buttons = [
            [InlineKeyboardButton("⬅️ Back to Playlist", callback_data=f"playlist:view:{playlist_id}:1")],
            [InlineKeyboardButton("❌ Close", callback_data="close_cb")]
        ]
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        await cb.answer()


# ==============================================================================
# GROUP COMMAND: /playplaylist & /playpl
# ==============================================================================
@app.on_message(filters.command(["playplaylist", "playpl"]) & ~BANNED_USERS)
async def play_playlist_cmd(client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id

    if message.chat.type.name == "PRIVATE":
        return await message.reply_text(
            "⚠️ **This command works in Group Chats!**\n\n"
            "Group mein jaakar ye command bhejain taaki voice chat mein playlist play ho sake."
        )

    args = message.text.split()
    if len(args) < 2:
        playlists = await db_get_user_playlists(user_id)
        if not playlists:
            return await message.reply_text("❌ You don't have any saved playlists!")

        text = "🎶 **Your Saved Playlists:**\n\n"
        for pl in playlists:
            text += f"• `{pl.get('name')}` ➡️ `/playplaylist {pl.get('playlist_id')}`\n"
        text += "\nCopy the command and send it to play in GC!"
        return await message.reply_text(text)

    playlist_id = args[1]
    playlist = await db_get_playlist(user_id, playlist_id)
    if not playlist or not playlist.get("songs"):
        return await message.reply_text("❌ **Playlist not found or empty!**")

    songs = playlist["songs"]
    pl_name = playlist.get("name", "Playlist")

    mystic = await message.reply_text("🔄 **Resolving YouTube tracks... Please wait...**")

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
        return await mystic.edit_text("❌ **Playlist ke gane YouTube par nahi mil paye!**")

    # Clear queue and safely stop any current stream
    db[chat_id] = []
    try:
        if hasattr(Espro, "stop_stream"):
            await Espro.stop_stream(chat_id)
        elif hasattr(Espro, "stop_stream_force"):
            await Espro.stop_stream_force(chat_id)
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

    user_name = message.from_user.first_name

    first = valid_queue[0]
    first_details = {
        "title": first["title"],
        "link": first["link"],
        "vidid": first["vidid"],
        "duration_min": first["duration_min"],
        "thumb": first["thumb"],
    }

    try:
        # 1) Pehla song stream() se (db khaali hai, to ye index 0 banega)
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

        # 2) Baaki songs bot ki proper queue format mein
        for song in valid_queue[1:]:
            await put_queue(
                chat_id,
                chat_id,
                f"vid_{song['vidid']}",
                song["title"],
                song["duration_min"],
                user_name,
                song["vidid"],
                user_id,
                "audio",
            )
    except Exception as e:
        return await message.reply_text(f"❌ **Error playing playlist:** `{e}`")

    await message.reply_text(
        f"▶️ **Playlist Playing in Group!**\n\n"
        f"📁 **Name:** `{pl_name}`\n"
        f"🎵 **Total Queued:** `{len(valid_queue)} songs`\n\n"
        f"💡 *Agla song skip karne ke liye `/plskip` bhejain!*"
    )


# ==============================================================================
# GROUP COMMAND: /plskip & /playlistskip
# ==============================================================================
@app.on_message(filters.command(["plskip", "playlistskip"]) & ~BANNED_USERS)
async def pl_skip_cmd(client, message: Message):
    chat_id = message.chat.id
    if message.chat.type.name == "PRIVATE":
        return await message.reply_text("⚠️ **This command works in Group Chats!**")

    check = db.get(chat_id)
    if not check:
        return await message.reply_text("❌ **Koi song play nahi ho raha!**")

    # Current song hatao
    try:
        check.pop(0)
    except Exception:
        pass

    # Queue khatam
    if not check:
        try:
            if hasattr(Espro, "stop_stream"):
                await Espro.stop_stream(chat_id)
            elif hasattr(Espro, "stop_stream_force"):
                await Espro.stop_stream_force(chat_id)
        except Exception:
            pass
        try:
            await remove_active_chat(chat_id)
            await remove_active_video_chat(chat_id)
        except Exception:
            pass
        return await message.reply_text("✅ **Playlist khatam ho gayi!**")

    mystic = await message.reply_text("⏭️ **Skipping to next playlist song...**")

    nxt = check[0]
    queued = nxt.get("file", "")
    videoid = nxt.get("vidid")
    title = nxt.get("title", "Track")

    try:
        if isinstance(queued, str) and "vid_" in queued:
            if youtube is None:
                return await mystic.edit_text(
                    "❌ YouTube helper load nahi hua. `from EsproMusic import YouTube` check karo."
                )
            file_path, direct = await youtube.download(
                videoid, mystic, videoid=True, video=False
            )
        else:
            file_path = queued

        try:
            image = await youtube.thumbnail(videoid, True)
        except Exception:
            image = None

        await Espro.skip_stream(chat_id, file_path, video=False, image=image)
        nxt["played"] = 0
        await mystic.edit_text(f"⏭️ **Skipped! Now playing:** `{title}`")
    except Exception as e:
        await mystic.edit_text(f"❌ **Error skipping track:** `{e}`")


# ==============================================================================
# TEXT INPUT LISTENER
# ==============================================================================
@app.on_message(filters.text & filters.private & ~BANNED_USERS, group=10)
async def playlist_text_input_handler(client, message: Message):
    user_id = message.from_user.id
    if user_id not in PLAYLIST_STATES:
        return

    state_data = PLAYLIST_STATES.pop(user_id, None)
    if not state_data:
        return

    state = state_data.get("state")
    chat_id = state_data.get("chat_id")
    msg_id = state_data.get("msg_id")
    input_text = message.text.strip()

    if state == "WAITING_PLAYLIST_NAME":
        if not input_text or len(input_text) > 30:
            PLAYLIST_STATES[user_id] = state_data
            return await message.reply_text("❌ **Invalid name!** Must be 1 to 30 characters long.")

        pl_id, status = await db_create_playlist(user_id, input_text)
        if status == "DUPLICATE":
            PLAYLIST_STATES[user_id] = state_data
            return await message.reply_text(f"❌ You already have a playlist named `{input_text}`!")

        text = (
            "✅ **Playlist Created Successfully!**\n\n"
            f"📁 **Name:** `{input_text}`\n"
            "Your playlist is ready. Start adding songs now!"
        )
        buttons = [
            [InlineKeyboardButton("➕ Add Songs", callback_data=f"playlist:add_manual:{pl_id}")],
            [InlineKeyboardButton("🎵 View Playlist", callback_data=f"playlist:view:{pl_id}:1")],
            [InlineKeyboardButton("⬅️ Back to Playlists", callback_data="playlist:list")],
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
                "✅ **Song Added!**\n\n"
                f"🎵 **Track:** `{resolved['title']}`\n"
                f"📁 **Playlist:** `{pl_name}`"
            )

        buttons = [
            [InlineKeyboardButton("➕ Add Another Song", callback_data=f"playlist:add_manual:{playlist_id}")],
            [InlineKeyboardButton("🎵 View Playlist", callback_data=f"playlist:view:{playlist_id}:1")],
            [InlineKeyboardButton("⬅️ Back to Playlists", callback_data="playlist:list")],
        ]

        try:
            await client.edit_message_text(chat_id, msg_id, text, reply_markup=InlineKeyboardMarkup(buttons))
        except Exception:
            await message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
