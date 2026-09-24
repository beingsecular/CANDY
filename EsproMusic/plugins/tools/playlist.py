import math
import uuid
import time
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
    from EsproMusic.core.call import Espro
except ImportError:
    try:
        from EsproMusic.core.call import EsproMusic as Espro
    except ImportError:
        from EsproMusic.core.call import Call as Espro

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
# DATABASE LAYER (Strict User Isolation with MongoDB)
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
# STATE MANAGEMENT (Per-User Text Input Handler)
# ==============================================================================
# Format: { user_id: { "state": "WAITING_PLAYLIST_NAME" | "WAITING_PLAYLIST_SONG", "playlist_id": str, "chat_id": int, "msg_id": int } }
PLAYLIST_STATES = {}


# ==============================================================================
# UI GENERATORS
# ==============================================================================
async def render_my_playlists_screen(user_id: int):
    """Builds the main playlist list UI."""
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
    """Builds the paginated playlist view UI."""
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

    # Pagination calculation (5 songs per page)
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
        
        # Single row button for quick inspection/deletion of each song
        song_buttons.append([
            InlineKeyboardButton(f"{idx}. {s_title[:28]}", callback_data=f"playlist:song:{playlist_id}:{s_id}")
        ])

    # Action controls
    action_buttons = [
        [InlineKeyboardButton("▶️ Play Playlist", callback_data=f"playlist:play:{playlist_id}")],
        [
            InlineKeyboardButton("➕ Add Song", callback_data=f"playlist:add_manual:{playlist_id}"),
            InlineKeyboardButton("🗑️ Delete Playlist", callback_data=f"playlist:delete:{playlist_id}"),
        ]
    ]

    # Pagination control row
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


# ==============================================================================
# ROUTER & CALLBACK HANDLER
# ==============================================================================
@app.on_callback_query(filters.regex(r"^playlist:") & ~BANNED_USERS)
async def playlist_callback_router(client, cb: CallbackQuery):
    data = cb.data.split(":")
    action = data[1]
    user_id = cb.from_user.id

    # Dummy ignore callback
    if action == "ignore":
        return await cb.answer()

    # --------------------------------------------------------------------------
    # 1. LIST MY PLAYLISTS
    # --------------------------------------------------------------------------
    if action == "list":
        if user_id in PLAYLIST_STATES:
            PLAYLIST_STATES.pop(user_id, None)

        text, reply_markup = await render_my_playlists_screen(user_id)
        await cb.message.edit_text(text, reply_markup=reply_markup)
        await cb.answer()

    # --------------------------------------------------------------------------
    # 2. PROMPT: CREATE PLAYLIST
    # --------------------------------------------------------------------------
    elif action == "create":
        PLAYLIST_STATES[user_id] = {
            "state": "WAITING_PLAYLIST_NAME",
            "chat_id": cb.message.chat.id,
            "msg_id": cb.message.id,
        }

        text = (
            "📁 **Create New Playlist**\n\n"
            "Please **type and send the name** for your new playlist in this chat.\n\n"
            "• *Example:* `Chill Vibes`, `Gym Mix`, `Sad Songs`\n"
            "• *Max length:* 30 characters"
        )
        buttons = [[InlineKeyboardButton("❌ Cancel", callback_data="playlist:cancel")]]
        await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        await cb.answer()

    # --------------------------------------------------------------------------
    # 3. CANCEL INPUT STATE
    # --------------------------------------------------------------------------
    elif action == "cancel":
        PLAYLIST_STATES.pop(user_id, None)
        text, reply_markup = await render_my_playlists_screen(user_id)
        await cb.message.edit_text(text, reply_markup=reply_markup)
        await cb.answer("Action cancelled.")

    # --------------------------------------------------------------------------
    # 4. VIEW PLAYLIST DETAILS (PAGINATED)
    # --------------------------------------------------------------------------
    elif action == "view":
        playlist_id = data[2]
        page = int(data[3]) if len(data) > 3 else 1
        text, reply_markup = await render_playlist_details_screen(user_id, playlist_id, page)
        await cb.message.edit_text(text, reply_markup=reply_markup)
        await cb.answer()

    # --------------------------------------------------------------------------
    # 5. ADD CURRENT PLAYING SONG -> CHOOSE PLAYLIST
    # --------------------------------------------------------------------------
    elif action == "add_current":
        chat_id = cb.message.chat.id
        
        # Resolve current stream track from EsproMusic internal player state
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

    # --------------------------------------------------------------------------
    # 6. SAVE CURRENT SONG TO SELECTED PLAYLIST
    # --------------------------------------------------------------------------
    elif action == "save_curr":
        playlist_id = data[2]
        chat_id = cb.message.chat.id

        active_track = None
        if chat_id in db and db[chat_id]:
            active_track = db[chat_id][0]

        if not active_track:
            return await cb.answer("❌ Active song expired or stopped.", show_alert=True)

        song_data = {
            "title": active_track.get("title", "Unknown Track"),
            "artist": active_track.get("user", "Artist"),
            "vidid": active_track.get("vidid", "none"),
            "url": active_track.get("link", ""),
            "duration": active_track.get("duration_min", "03:00"),
            "thumbnail": active_track.get("thumb", ""),
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

    # --------------------------------------------------------------------------
    # 7. PROMPT: MANUAL ADD SONG
    # --------------------------------------------------------------------------
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

    # --------------------------------------------------------------------------
    # 8. VIEW SINGLE SONG DETAILS
    # --------------------------------------------------------------------------
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

    # --------------------------------------------------------------------------
    # 9. CONFIRM REMOVE SONG
    # --------------------------------------------------------------------------
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

    # --------------------------------------------------------------------------
    # 10. REMOVE SONG EXECUTION
    # --------------------------------------------------------------------------
    elif action == "song_remove":
        playlist_id = data[2]
        song_id = data[3]

        await db_remove_song(user_id, playlist_id, song_id)
        await cb.answer("Song removed!", show_alert=True)

        text, reply_markup = await render_playlist_details_screen(user_id, playlist_id, page=1)
        await cb.message.edit_text(text, reply_markup=reply_markup)

    # --------------------------------------------------------------------------
    # 11. CONFIRM DELETE PLAYLIST
    # --------------------------------------------------------------------------
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

    # --------------------------------------------------------------------------
    # 12. DELETE PLAYLIST EXECUTION
    # --------------------------------------------------------------------------
    elif action == "delete_confirm":
        playlist_id = data[2]
        deleted = await db_delete_playlist(user_id, playlist_id)

        if deleted:
            await cb.answer("Playlist deleted!", show_alert=True)
        else:
            await cb.answer("Failed to delete playlist.", show_alert=True)

        text, reply_markup = await render_my_playlists_screen(user_id)
        await cb.message.edit_text(text, reply_markup=reply_markup)

    # --------------------------------------------------------------------------
    # 13. PLAY ENTIRE PLAYLIST INTO STREAM QUEUE
    # --------------------------------------------------------------------------
    elif action == "play":
        playlist_id = data[2]
        chat_id = cb.message.chat.id

        playlist = await db_get_playlist(user_id, playlist_id)
        if not playlist or not playlist.get("songs"):
            return await cb.answer("❌ Playlist is empty or does not exist!", show_alert=True)

        songs = playlist["songs"]
        pl_name = playlist.get("name", "Playlist")

        await cb.answer(f"▶️ Loading '{pl_name}' playlist...", show_alert=False)

        # Clear active database queue for this chat
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

        try:
            language = await get_lang(chat_id)
            from strings import get_string
            _ = get_string(language)
        except Exception:
            class DummyLang(dict):
                def __getitem__(self, item):
                    return self.get(item, "")
            _ = DummyLang()

        user_name = cb.from_user.first_name

        # Prepare first track
        first_song = songs[0]
        v_id_0 = first_song.get("vidid")
        title_0 = first_song.get("title", "Playlist Song")
        url_0 = first_song.get("url") or (f"https://www.youtube.com/watch?v={v_id_0}" if v_id_0 else title_0)

        default_thumb = (
            first_song.get("thumbnail")
            or getattr(config, "YOUTUBE_IMG_URL", "https://telegra.ph/file/c8f2052028238627e1f33.jpg")
        )

        first_details = {
            "title": title_0,
            "link": url_0,
            "vidid": v_id_0 or "none",
            "duration_min": first_song.get("duration", "03:00"),
            "thumb": default_thumb,
            "by": user_name,
        }

        # Queue remaining tracks
        for song in songs[1:]:
            v_id = song.get("vidid")
            s_title = song.get("title", "Playlist Song")
            u_link = song.get("url") or (f"https://www.youtube.com/watch?v={v_id}" if v_id else s_title)
            s_thumb = song.get("thumbnail") or default_thumb

            d_item = {
                "title": s_title,
                "link": u_link,
                "vidid": v_id or "none",
                "duration_min": song.get("duration", "03:00"),
                "thumb": s_thumb,
                "user": user_name,
                "user_id": user_id,
                "streamtype": "youtube",
                "file": None,
            }
            db[chat_id].append(d_item)

        mystic = await cb.message.reply_text("🔄 **Starting Playlist Playback...**")

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
            text = (
                f"▶️ **Playlist Started!**\n\n"
                f"📁 **Playlist:** `{pl_name}`\n"
                f"🎵 **Added:** `{len(songs)} songs to queue`"
            )
            buttons = [
                [InlineKeyboardButton("🎵 View Playlist", callback_data=f"playlist:view:{playlist_id}:1")],
                [
                    InlineKeyboardButton("⏭️ Skip", callback_data="skip_cb"),
                    InlineKeyboardButton("⏹️ Stop", callback_data="stop_cb"),
                ]
            ]
            await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            await mystic.edit_text(f"❌ **Error playing playlist:** `{e}`")


# ==============================================================================
# TEXT MESSAGE LISTENER FOR INPUT STATES (NAME / SEARCH QUERY)
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

    # --------------------------------------------------------------------------
    # STATE A: CREATING PLAYLIST NAME
    # --------------------------------------------------------------------------
    if state == "WAITING_PLAYLIST_NAME":
        if not input_text or len(input_text) > 30:
            PLAYLIST_STATES[user_id] = state_data  # Restore state
            return await message.reply_text("❌ **Invalid name!** Must be 1 to 30 characters long.")

        pl_id, status = await db_create_playlist(user_id, input_text)
        if status == "DUPLICATE":
            PLAYLIST_STATES[user_id] = state_data  # Restore state
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

    # --------------------------------------------------------------------------
    # STATE B: MANUAL ADD SONG SEARCH QUERY
    # --------------------------------------------------------------------------
    elif state == "WAITING_PLAYLIST_SONG":
        playlist_id = state_data.get("playlist_id")
        searching_msg = await message.reply_text("🔎 **Searching song on YouTube...**")

        videoid = None
        title = input_text
        artist = "YouTube"
        duration = "03:00"
        url = input_text
        thumbnail = ""

        if youtube:
            try:
                results = await youtube.track(input_text)
                if results:
                    details = results[0] if isinstance(results, list) else results
                    videoid = details.get("vidid") or details.get("id")
                    title = details.get("title", input_text)
                    duration = details.get("duration_min", "03:00")
                    thumbnail = details.get("thumb", "")
                    url = details.get("link", f"https://www.youtube.com/watch?v={videoid}")
            except Exception:
                pass

        if not videoid and ("youtube.com" in input_text or "youtu.be" in input_text):
            videoid = input_text.split("v=")[-1].split("&")[0] if "v=" in input_text else input_text.split("/")[-1]

        song_data = {
            "title": title,
            "artist": artist,
            "vidid": videoid or "none",
            "url": url,
            "duration": duration,
            "thumbnail": thumbnail,
        }

        success, res = await db_add_song_to_playlist(user_id, playlist_id, song_data)
        playlist = await db_get_playlist(user_id, playlist_id)
        pl_name = playlist.get("name") if playlist else "Playlist"

        await searching_msg.delete()

        if res == "DUPLICATE":
            text = f"⚠️ **This track is already in '{pl_name}'!**"
        else:
            text = (
                "✅ **Song Added!**\n\n"
                f"🎵 **Track:** `{title}`\n"
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
