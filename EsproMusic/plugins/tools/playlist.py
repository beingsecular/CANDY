```python
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


# ============================================================
# YOUTUBE
# ============================================================

try:
    from EsproMusic.platforms import YouTube

    youtube = YouTube()
except Exception:
    youtube = None


# ============================================================
# MONGODB
# ============================================================

playlist_collection = mongodb.playlists_v2


async def db_get_user_playlists(user_id: int):
    cursor = playlist_collection.find(
        {"user_id": user_id}
    )

    playlists = []

    async for doc in cursor:
        playlists.append(doc)

    return playlists


async def db_get_playlist(user_id: int, playlist_id: str):
    return await playlist_collection.find_one(
        {
            "user_id": user_id,
            "playlist_id": playlist_id,
        }
    )


async def db_create_playlist(user_id: int, name: str):
    existing = await playlist_collection.find_one(
        {
            "user_id": user_id,
            "name": name,
        }
    )

    if existing:
        return None, "DUPLICATE"

    playlist_id = f"pl_{uuid.uuid4().hex[:8]}"

    document = {
        "user_id": user_id,
        "playlist_id": playlist_id,
        "name": name,
        "songs": [],
        "created_at": time.time(),
    }

    await playlist_collection.insert_one(document)

    return playlist_id, "SUCCESS"


async def db_add_song_to_playlist(
    user_id: int,
    playlist_id: str,
    song_data: dict,
):
    playlist = await db_get_playlist(
        user_id,
        playlist_id,
    )

    if not playlist:
        return False, "NOT_FOUND"

    vidid = song_data.get("vidid")

    # Never save invalid YouTube IDs
    if not vidid or vidid == "none":
        return False, "INVALID_ID"

    song_id = f"s_{uuid.uuid4().hex[:8]}"

    song_entry = {
        "song_id": song_id,
        "title": song_data.get(
            "title",
            "Unknown Track",
        ),
        "artist": song_data.get(
            "artist",
            "Unknown Artist",
        ),
        "vidid": vidid,
        "url": song_data.get(
            "url",
            f"https://www.youtube.com/watch?v={vidid}",
        ),
        "duration": song_data.get(
            "duration",
            "03:00",
        ),
        "thumbnail": song_data.get(
            "thumbnail",
            "",
        ),
        "added_at": time.time(),
    }

    # Duplicate protection
    for old_song in playlist.get("songs", []):
        old_id = old_song.get("vidid")

        if (
            old_id
            and old_id != "none"
            and old_id == vidid
        ):
            return False, "DUPLICATE"

    await playlist_collection.update_one(
        {
            "user_id": user_id,
            "playlist_id": playlist_id,
        },
        {
            "$push": {
                "songs": song_entry
            }
        },
    )

    return True, song_entry


async def db_delete_playlist(
    user_id: int,
    playlist_id: str,
):
    result = await playlist_collection.delete_one(
        {
            "user_id": user_id,
            "playlist_id": playlist_id,
        }
    )

    return result.deleted_count > 0


async def db_remove_song(
    user_id: int,
    playlist_id: str,
    song_id: str,
):
    result = await playlist_collection.update_one(
        {
            "user_id": user_id,
            "playlist_id": playlist_id,
        },
        {
            "$pull": {
                "songs": {
                    "song_id": song_id
                }
            },
        },
    )

    return result.modified_count > 0


# ============================================================
# YOUTUBE ID
# ============================================================

def extract_yt_id(value: str):
    if not value:
        return None

    value = str(value).strip()

    if value.lower() in {
        "none",
        "null",
        "",
    }:
        return None

    # Direct YouTube ID
    if re.fullmatch(
        r"[A-Za-z0-9_-]{11}",
        value,
    ):
        return value

    patterns = [
        r"(?:v=)([A-Za-z0-9_-]{11})",
        r"(?:youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:youtube\.com/embed/)([A-Za-z0-9_-]{11})",
        r"(?:youtube\.com/shorts/)([A-Za-z0-9_-]{11})",
        r"(?:youtube\.com/live/)([A-Za-z0-9_-]{11})",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            value,
        )

        if match:
            return match.group(1)

    return None


# ============================================================
# RESOLVE TRACK
# ============================================================

async def resolve_youtube_track(
    query: str,
    vidid: str = None,
    url: str = None,
):
    existing_id = (
        extract_yt_id(vidid)
        or extract_yt_id(url)
        or extract_yt_id(query)
    )

    # Existing valid ID
    if existing_id:
        return {
            "vidid": existing_id,
            "title": (
                query
                if query and not query.startswith("http")
                else "YouTube Track"
            ),
            "link": (
                f"https://www.youtube.com/watch?v={existing_id}"
            ),
            "duration_min": "03:00",
            "thumb": (
                f"https://i.ytimg.com/vi/"
                f"{existing_id}/hqdefault.jpg"
            ),
        }

    search_query = query or "Hindi Music"

    # --------------------------------------------------------
    # EsproMusic YouTube helper
    # --------------------------------------------------------

    if youtube:
        try:
            result = await youtube.track(
                search_query
            )

            if result:

                if isinstance(
                    result,
                    (list, tuple),
                ):
                    # Common format:
                    # title, duration, ..., thumbnail, url
                    if (
                        len(result) >= 5
                        and isinstance(result[4], str)
                    ):
                        found_id = extract_yt_id(
                            result[4]
                        )

                        if found_id:
                            return {
                                "vidid": found_id,
                                "title": str(result[0]),
                                "link": (
                                    "https://www.youtube.com/watch?v="
                                    f"{found_id}"
                                ),
                                "duration_min": str(
                                    result[1]
                                ),
                                "thumb": str(
                                    result[3]
                                ),
                            }

                    elif (
                        len(result) >= 1
                        and isinstance(result[0], dict)
                    ):
                        data = result[0]

                        found_id = extract_yt_id(
                            data.get("vidid")
                            or data.get("id")
                        )

                        if found_id:
                            return {
                                "vidid": found_id,
                                "title": data.get(
                                    "title",
                                    search_query,
                                ),
                                "link": (
                                    "https://www.youtube.com/watch?v="
                                    f"{found_id}"
                                ),
                                "duration_min": data.get(
                                    "duration_min",
                                    "03:00",
                                ),
                                "thumb": data.get(
                                    "thumb",
                                    (
                                        "https://i.ytimg.com/vi/"
                                        f"{found_id}/hqdefault.jpg"
                                    ),
                                ),
                            }

                elif isinstance(
                    result,
                    dict,
                ):
                    found_id = extract_yt_id(
                        result.get("vidid")
                        or result.get("id")
                    )

                    if found_id:
                        return {
                            "vidid": found_id,
                            "title": result.get(
                                "title",
                                search_query,
                            ),
                            "link": (
                                "https://www.youtube.com/watch?v="
                                f"{found_id}"
                            ),
                            "duration_min": result.get(
                                "duration_min",
                                "03:00",
                            ),
                            "thumb": result.get(
                                "thumb",
                                (
                                    "https://i.ytimg.com/vi/"
                                    f"{found_id}/hqdefault.jpg"
                                ),
                            ),
                        }

        except Exception:
            pass

    # --------------------------------------------------------
    # YoutubeSearchPython fallback
    # --------------------------------------------------------

    try:
        from youtubesearchpython.__future__ import VideosSearch

        search = VideosSearch(
            search_query,
            limit=1,
        )

        results = await search.next()

        if (
            results
            and results.get("result")
        ):
            first = results["result"][0]

            found_id = extract_yt_id(
                first.get("id")
            )

            if found_id:
                return {
                    "vidid": found_id,
                    "title": first.get(
                        "title",
                        search_query,
                    ),
                    "link": (
                        "https://www.youtube.com/watch?v="
                        f"{found_id}"
                    ),
                    "duration_min": first.get(
                        "duration",
                        "03:00",
                    ),
                    "thumb": (
                        "https://i.ytimg.com/vi/"
                        f"{found_id}/hqdefault.jpg"
                    ),
                }

    except Exception:
        pass

    return None


# ============================================================
# QUEUE ITEM
# ============================================================

def make_queue_item(
    track: dict,
    user_id: int,
    user_name: str,
):
    """
    Creates the exact queue format used by EsproMusic.
    """

    vidid = track["vidid"]

    return {
        "title": track["title"],
        "link": track["link"],
        "vidid": vidid,
        "dur": track.get(
            "duration_min",
            "03:00",
        ),
        "duration_min": track.get(
            "duration_min",
            "03:00",
        ),
        "thumb": track.get(
            "thumb",
            "",
        ),
        "by": user_name,
        "user": user_name,
        "user_id": user_id,
        "streamtype": "youtube",
        "file": f"vid_{vidid}",

        # Queue state
        "old_dur": track.get(
            "duration_min",
            "03:00",
        ),
        "old_second": 0,
        "played": 0,
    }


# ============================================================
# PLAYLIST UI
# ============================================================

PLAYLIST_STATES = {}


async def render_my_playlists_screen(
    user_id: int,
):
    playlists = await db_get_user_playlists(
        user_id
    )

    if not playlists:
        text = (
            "🎵 **MY PLAYLISTS**\n\n"
            "You don't have any saved playlists yet."
        )

        buttons = [
            [
                InlineKeyboardButton(
                    "➕ Create Playlist",
                    callback_data="playlist:create",
                )
            ],
            [
                InlineKeyboardButton(
                    "✕ Close",
                    callback_data="close_cb",
                )
            ],
        ]

        return (
            text,
            InlineKeyboardMarkup(buttons),
        )

    text = (
        "🎵 **MY PLAYLISTS**\n\n"
        "Select a playlist to manage or play it."
    )

    buttons = []

    for playlist in playlists:
        name = playlist.get(
            "name",
            "Playlist",
        )

        playlist_id = playlist.get(
            "playlist_id"
        )

        count = len(
            playlist.get(
                "songs",
                [],
            )
        )

        # Separate rows = much better on phones
        buttons.append(
            [
                InlineKeyboardButton(
                    f"📁 {name[:24]}",
                    callback_data=(
                        f"playlist:view:"
                        f"{playlist_id}:1"
                    ),
                )
            ]
        )

        buttons.append(
            [
                InlineKeyboardButton(
                    f"▶️ Play • {count} Songs",
                    callback_data=(
                        f"playlist:play:"
                        f"{playlist_id}"
                    ),
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "➕ Create New Playlist",
                callback_data="playlist:create",
            )
        ]
    )

    buttons.append(
        [
            InlineKeyboardButton(
                "✕ Close",
                callback_data="close_cb",
            )
        ]
    )

    return (
        text,
        InlineKeyboardMarkup(buttons),
    )


async def show_my_playlists_menu(
    client,
    message_or_cb,
    user_id: int = None,
):
    if user_id is None:
        user_id = message_or_cb.from_user.id

    text, markup = await render_my_playlists_screen(
        user_id
    )

    if isinstance(
        message_or_cb,
        CallbackQuery,
    ):
        await message_or_cb.message.edit_text(
            text,
            reply_markup=markup,
        )
    else:
        await message_or_cb.reply_text(
            text,
            reply_markup=markup,
        )


display_my_playlists = show_my_playlists_menu


# ============================================================
# PLAYLIST DETAILS
# ============================================================

async def render_playlist_details_screen(
    user_id: int,
    playlist_id: str,
    page: int = 1,
):
    playlist = await db_get_playlist(
        user_id,
        playlist_id,
    )

    if not playlist:
        return (
            "❌ **Playlist not found.**",
            InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🔙 Back",
                            callback_data="playlist:list",
                        )
                    ]
                ]
            ),
        )

    name = playlist.get(
        "name",
        "Playlist",
    )

    songs = playlist.get(
        "songs",
        [],
    )

    total = len(songs)

    if total == 0:
        buttons = [
            [
                InlineKeyboardButton(
                    "➕ Add Song",
                    callback_data=(
                        f"playlist:add_manual:"
                        f"{playlist_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    "🗑 Delete",
                    callback_data=(
                        f"playlist:delete:"
                        f"{playlist_id}"
                    ),
                ),
                InlineKeyboardButton(
                    "🔙 Back",
                    callback_data="playlist:list",
                ),
            ],
        ]

        return (
            f"📁 **{name}**\n\n"
            "No songs have been added yet.",
            InlineKeyboardMarkup(buttons),
        )

    per_page = 5

    total_pages = math.ceil(
        total / per_page
    )

    page = max(
        1,
        min(
            page,
            total_pages,
        ),
    )

    start = (
        page - 1
    ) * per_page

    page_songs = songs[
        start:start + per_page
    ]

    text = (
        f"📁 **{name}**\n"
        f"🎵 **Songs:** `{total}`\n"
        f"📄 **Page:** `{page}/{total_pages}`\n\n"
    )

    song_buttons = []

    for index, song in enumerate(
        page_songs,
        start=start + 1,
    ):
        title = song.get(
            "title",
            "Track",
        )

        song_id = song.get(
            "song_id"
        )

        text += (
            f"**{index}.** "
            f"`{title[:35]}`\n"
        )

        song_buttons.append(
            [
                InlineKeyboardButton(
                    f"{index}. {title[:30]}",
                    callback_data=(
                        f"playlist:song:"
                        f"{playlist_id}:"
                        f"{song_id}"
                    ),
                )
            ]
        )

    buttons = song_buttons

    buttons.append(
        [
            InlineKeyboardButton(
                "▶️ Play All",
                callback_data=(
                    f"playlist:play:"
                    f"{playlist_id}"
                ),
            )
        ]
    )

    buttons.append(
        [
            InlineKeyboardButton(
                "➕ Add Song",
                callback_data=(
                    f"playlist:add_manual:"
                    f"{playlist_id}"
                ),
            ),
            InlineKeyboardButton(
                "🗑 Delete",
                callback_data=(
                    f"playlist:delete:"
                    f"{playlist_id}"
                ),
            ),
        ]
    )

    if total_pages > 1:
        navigation = []

        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    "⬅️ Prev",
                    callback_data=(
                        f"playlist:view:"
                        f"{playlist_id}:"
                        f"{page - 1}"
                    ),
                )
            )

        navigation.append(
            InlineKeyboardButton(
                f"📄 {page}/{total_pages}",
                callback_data="playlist:ignore",
            )
        )

        if page < total_pages:
            navigation.append(
                InlineKeyboardButton(
                    "Next ➡️",
                    callback_data=(
                        f"playlist:view:"
                        f"{playlist_id}:"
                        f"{page + 1}"
                    ),
                )
            )

        buttons.append(navigation)

    buttons.append(
        [
            InlineKeyboardButton(
                "🔙 Back to Playlists",
                callback_data="playlist:list",
            )
        ]
    )

    return (
        text,
        InlineKeyboardMarkup(buttons),
    )


# ============================================================
# /PLAYLIST
# ============================================================

@app.on_message(
    filters.command(
        [
            "playlist",
            "myplaylist",
        ]
    )
    & ~BANNED_USERS
)
async def my_playlist_cmd(
    client,
    message: Message,
):
    await show_my_playlists_menu(
        client,
        message,
        message.from_user.id,
    )


# ============================================================
# CLOSE
# ============================================================

@app.on_callback_query(
    filters.regex(r"^close_cb$")
    & ~BANNED_USERS
)
async def close_cb_handler(
    client,
    cb: CallbackQuery,
):
    try:
        await cb.message.delete()
    except Exception:
        pass

    try:
        await cb.answer()
    except Exception:
        pass


# ============================================================
# ADD CURRENT SONG TO PLAYLIST
# ============================================================

@app.on_callback_query(
    filters.regex(r"^add_playlist")
    & ~BANNED_USERS
)
async def add_playlist_from_stream(
    client,
    cb: CallbackQuery,
):
    try:
        parts = cb.data.split()

        if len(parts) > 1:
            chat_id = int(parts[1])
        else:
            chat_id = cb.message.chat.id

    except Exception:
        chat_id = cb.message.chat.id

    playing = db.get(chat_id)

    if not playing:
        return await cb.answer(
            "❌ No song is currently playing.",
            show_alert=True,
        )

    track = playing[0]

    vidid = track.get(
        "vidid"
    )

    if not vidid:
        return await cb.answer(
            "❌ Invalid track.",
            show_alert=True,
        )

    if vidid in {
        "telegram",
        "soundcloud",
        "none",
        "null",
    }:
        return await cb.answer(
            "❌ This track cannot be saved.",
            show_alert=True,
        )

    user_id = cb.from_user.id

    playlists = await db_get_user_playlists(
        user_id
    )

    if not playlists:
        playlist_id, status = (
            await db_create_playlist(
                user_id,
                "My Favorite Songs",
            )
        )
    else:
        playlist_id = playlists[0][
            "playlist_id"
        ]

    title = str(
        track.get(
            "title",
            "Unknown Track",
        )
    )

    song_data = {
        "title": title,
        "artist": "YouTube",
        "vidid": vidid,
        "url": (
            f"https://www.youtube.com/watch?v={vidid}"
        ),
        "duration": str(
            track.get(
                "dur",
                "03:00",
            )
        ),
        "thumbnail": str(
            track.get(
                "thumb",
                "",
            )
        ),
    }

    success, result = (
        await db_add_song_to_playlist(
            user_id,
            playlist_id,
            song_data,
        )
    )

    if result == "DUPLICATE":
        return await cb.answer(
            "⚠️ This song is already saved.",
            show_alert=True,
        )

    if result == "INVALID_ID":
        return await cb.answer(
            "❌ Invalid YouTube ID.",
            show_alert=True,
        )

    await cb.answer(
        f"✅ Saved: {title[:30]}",
        show_alert=True,
    )


# ============================================================
# PLAYLIST CALLBACK ROUTER
# ============================================================

@app.on_callback_query(
    filters.regex(r"^playlist:")
    & ~BANNED_USERS
)
async def playlist_callback_router(
    client,
    cb: CallbackQuery,
):
    data = cb.data.split(":")
    action = data[1]

    user_id = cb.from_user.id

    # --------------------------------------------------------
    # IGNORE
    # --------------------------------------------------------

    if action == "ignore":
        return await cb.answer()

    # --------------------------------------------------------
    # LIST
    # --------------------------------------------------------

    if action == "list":
        PLAYLIST_STATES.pop(
            user_id,
            None,
        )

        await show_my_playlists_menu(
            client,
            cb,
            user_id,
        )

        return await cb.answer()

    # --------------------------------------------------------
    # CREATE
    # --------------------------------------------------------

    if action == "create":
        PLAYLIST_STATES[user_id] = {
            "state": "WAITING_PLAYLIST_NAME",
            "chat_id": cb.message.chat.id,
            "msg_id": cb.message.id,
        }

        text = (
            "📂 **CREATE PLAYLIST**\n\n"
            "Send the name of your new playlist.\n\n"
            "Maximum: `30 characters`"
        )

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="playlist:cancel",
                    )
                ]
            ]
        )

        await cb.message.edit_text(
            text,
            reply_markup=markup,
        )

        return await cb.answer()

    # --------------------------------------------------------
    # CANCEL
    # --------------------------------------------------------

    if action == "cancel":
        PLAYLIST_STATES.pop(
            user_id,
            None,
        )

        await show_my_playlists_menu(
            client,
            cb,
            user_id,
        )

        return await cb.answer(
            "Cancelled."
        )

    # --------------------------------------------------------
    # VIEW
    # --------------------------------------------------------

    if action == "view":
        playlist_id = data[2]

        page = (
            int(data[3])
            if len(data) > 3
            else 1
        )

        text, markup = (
            await render_playlist_details_screen(
                user_id,
                playlist_id,
                page,
            )
        )

        await cb.message.edit_text(
            text,
            reply_markup=markup,
        )

        return await cb.answer()

    # --------------------------------------------------------
    # ADD SONG
    # --------------------------------------------------------

    if action == "add_manual":
        playlist_id = data[2]

        PLAYLIST_STATES[user_id] = {
            "state": "WAITING_PLAYLIST_SONG",
            "playlist_id": playlist_id,
            "chat_id": cb.message.chat.id,
            "msg_id": cb.message.id,
        }

        text = (
            "🎵 **ADD SONG**\n\n"
            "Send a song name or YouTube link."
        )

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data=(
                            f"playlist:view:"
                            f"{playlist_id}:1"
                        ),
                    )
                ]
            ]
        )

        await cb.message.edit_text(
            text,
            reply_markup=markup,
        )

        return await cb.answer()

    # --------------------------------------------------------
    # SONG DETAILS
    # --------------------------------------------------------

    if action == "song":
        playlist_id = data[2]
        song_id = data[3]

        playlist = await db_get_playlist(
            user_id,
            playlist_id,
        )

        if not playlist:
            return await cb.answer(
                "Playlist not found.",
                show_alert=True,
            )

        song = next(
            (
                item
                for item in playlist.get(
                    "songs",
                    [],
                )
                if item.get(
                    "song_id"
                ) == song_id
            ),
            None,
        )

        if not song:
            return await cb.answer(
                "Song not found.",
                show_alert=True,
            )

        text = (
            f"🎵 **{song.get('title', 'Track')}**\n\n"
            f"👤 Artist: `{song.get('artist', 'YouTube')}`\n"
            f"⏱ Duration: `{song.get('duration', '03:00')}`\n"
            f"📂 Playlist: `{playlist.get('name', 'Playlist')}`"
        )

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🗑 Remove Song",
                        callback_data=(
                            f"playlist:song_remove_confirm:"
                            f"{playlist_id}:"
                            f"{song_id}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data=(
                            f"playlist:view:"
                            f"{playlist_id}:1"
                        ),
                    )
                ],
            ]
        )

        await cb.message.edit_text(
            text,
            reply_markup=markup,
        )

        return await cb.answer()

    # --------------------------------------------------------
    # REMOVE CONFIRM
    # --------------------------------------------------------

    if action == "song_remove_confirm":
        playlist_id = data[2]
        song_id = data[3]

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Yes, Remove",
                        callback_data=(
                            f"playlist:song_remove:"
                            f"{playlist_id}:"
                            f"{song_id}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data=(
                            f"playlist:song:"
                            f"{playlist_id}:"
                            f"{song_id}"
                        ),
                    )
                ],
            ]
        )

        await cb.message.edit_text(
            "⚠️ **REMOVE SONG?**\n\n"
            "This song will be removed from your playlist.",
            reply_markup=markup,
        )

        return await cb.answer()

    # --------------------------------------------------------
    # REMOVE SONG
    # --------------------------------------------------------

    if action == "song_remove":
        playlist_id = data[2]
        song_id = data[3]

        await db_remove_song(
            user_id,
            playlist_id,
            song_id,
        )

        await cb.answer(
            "Song removed.",
            show_alert=True,
        )

        text, markup = (
            await render_playlist_details_screen(
                user_id,
                playlist_id,
                1,
            )
        )

        await cb.message.edit_text(
            text,
            reply_markup=markup,
        )

        return

    # --------------------------------------------------------
    # DELETE
    # --------------------------------------------------------

    if action == "delete":
        playlist_id = data[2]

        playlist = await db_get_playlist(
            user_id,
            playlist_id,
        )

        if not playlist:
            return await cb.answer(
                "Playlist not found.",
                show_alert=True,
            )

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Delete",
                        callback_data=(
                            f"playlist:delete_confirm:"
                            f"{playlist_id}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data=(
                            f"playlist:view:"
                            f"{playlist_id}:1"
                        ),
                    )
                ],
            ]
        )

        await cb.message.edit_text(
            f"⚠️ **DELETE PLAYLIST?**\n\n"
            f"📂 `{playlist.get('name')}`\n"
            f"🎵 Songs: `{len(playlist.get('songs', []))}`",
            reply_markup=markup,
        )

        return await cb.answer()

    # --------------------------------------------------------
    # DELETE CONFIRM
    # --------------------------------------------------------

    if action == "delete_confirm":
        playlist_id = data[2]

        deleted = await db_delete_playlist(
            user_id,
            playlist_id,
        )

        await cb.answer(
            "Playlist deleted."
            if deleted
            else "Playlist not found.",
            show_alert=True,
        )

        return await show_my_playlists_menu(
            client,
            cb,
            user_id,
        )

    # --------------------------------------------------------
    # PLAY
    # --------------------------------------------------------

    if action == "play":
        playlist_id = data[2]

        playlist = await db_get_playlist(
            user_id,
            playlist_id,
        )

        if (
            not playlist
            or not playlist.get("songs")
        ):
            return await cb.answer(
                "❌ Playlist is empty.",
                show_alert=True,
            )

        name = playlist.get(
            "name",
            "Playlist",
        )

        command = (
            f"/playplaylist {playlist_id}"
        )

        text = (
            "▶️ **PLAY PLAYLIST**\n\n"
            f"📂 `{name}`\n"
            f"🎵 `{len(playlist['songs'])} songs`\n\n"
            "Send this command in your group:\n\n"
            f"`{command}`"
        )

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔙 Back",
                        callback_data=(
                            f"playlist:view:"
                            f"{playlist_id}:1"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "✕ Close",
                        callback_data="close_cb",
                    )
                ],
            ]
        )

        await cb.message.edit_text(
            text,
            reply_markup=markup,
        )

        return await cb.answer()


# ============================================================
# /PLAYPLAYLIST
# ============================================================

@app.on_message(
    filters.command(
        [
            "playplaylist",
            "playpl",
        ]
    )
    & ~BANNED_USERS
)
async def play_playlist_cmd(
    client,
    message: Message,
):
    chat_id = message.chat.id
    user_id = message.from_user.id

    # Only groups
    if message.chat.type.name == "PRIVATE":
        return await message.reply_text(
            "⚠️ **This command works in group chats only.**"
        )

    args = message.text.split()

    # --------------------------------------------------------
    # SHOW USER PLAYLISTS
    # --------------------------------------------------------

    if len(args) < 2:
        playlists = await db_get_user_playlists(
            user_id
        )

        if not playlists:
            return await message.reply_text(
                "❌ You don't have any saved playlists."
            )

        text = (
            "🎵 **YOUR PLAYLISTS**\n\n"
        )

        for playlist in playlists:
            text += (
                f"📁 **{playlist.get('name', 'Playlist')}**\n"
                f"`/playplaylist "
                f"{playlist.get('playlist_id')}`\n\n"
            )

        return await message.reply_text(
            text
        )

    playlist_id = args[1]

    playlist = await db_get_playlist(
        user_id,
        playlist_id,
    )

    if (
        not playlist
        or not playlist.get("songs")
    ):
        return await message.reply_text(
            "❌ **Playlist not found or empty.**"
        )

    songs = playlist["songs"]

    playlist_name = playlist.get(
        "name",
        "Playlist",
    )

    mystic = await message.reply_text(
        "🔄 **Preparing playlist...**\n\n"
        "Resolving songs..."
    )

    # ========================================================
    # RESOLVE ALL SONGS
    # ========================================================

    resolved_tracks = []

    for song in songs:
        try:
            resolved = await resolve_youtube_track(
                query=song.get(
                    "title",
                    "",
                ),
                vidid=song.get(
                    "vidid"
                ),
                url=song.get(
                    "url"
                ),
            )

            if not resolved:
                continue

            valid_id = extract_yt_id(
                resolved.get(
                    "vidid"
                )
            )

            if not valid_id:
                continue

            resolved_tracks.append(
                resolved
            )

        except Exception:
            continue

    if not resolved_tracks:
        return await mystic.edit_text(
            "❌ **No valid songs were found in this playlist.**"
        )

    # ========================================================
    # IMPORTANT QUEUE FIX
    # ========================================================
    #
    # Before:
    #
    # db[chat_id] = []
    # append(song2...)
    # stream(song1)
    #
    # Depending on the stream/skip implementation,
    # song1 could be inserted into db again.
    #
    # We now explicitly create the queue as:
    #
    # CURRENT = song1
    # QUEUE   = song2, song3, song4...
    #
    # The current song is NOT manually inserted into queue.
    # ========================================================

    db[chat_id] = []

    # Stop old stream
    try:
        if hasattr(
            EsproCall,
            "stop_stream",
        ):
            await EsproCall.stop_stream(
                chat_id
            )
    except Exception:
        pass

    try:
        await remove_active_chat(
            chat_id
        )

        await remove_active_video_chat(
            chat_id
        )

    except Exception:
        pass

    await asyncio.sleep(1)

    # ========================================================
    # LANGUAGE
    # ========================================================

    try:
        language = await get_lang(
            chat_id
        )

        from strings import get_string

        _ = get_string(
            language
        )

    except Exception:

        class DummyLang(dict):
            def __getitem__(
                self,
                key,
            ):
                return self.get(
                    key,
                    "",
                )

        _ = DummyLang()

    # ========================================================
    # USER
    # ========================================================

    user_name = (
        message.from_user.first_name
        if message.from_user
        else "User"
    )

    # ========================================================
    # CURRENT TRACK = SONG #1
    # ========================================================

    first = resolved_tracks[0]

    first_details = {
        "title": first["title"],
        "link": first["link"],
        "vidid": first["vidid"],
        "dur": first.get(
            "duration_min",
            "03:00",
        ),
        "duration_min": first.get(
            "duration_min",
            "03:00",
        ),
        "thumb": first.get(
            "thumb",
            "",
        ),
        "by": user_name,
        "user": user_name,
        "user_id": user_id,
        "streamtype": "youtube",
        "file": f"vid_{first['vidid']}",

        # Queue state
        "old_dur": first.get(
            "duration_min",
            "03:00",
        ),
        "old_second": 0,
        "played": 0,
    }

    # ========================================================
    # QUEUE = SONG #2 ONWARDS
    # ========================================================

    for next_track in resolved_tracks[1:]:

        queue_item = make_queue_item(
            next_track,
            user_id,
            user_name,
        )

        db[chat_id].append(
            queue_item
        )

    # ========================================================
    # START SONG #1
    # ========================================================

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
            "▶️ **PLAYLIST STARTED**\n\n"
            f"📂 **Playlist:** `{playlist_name}`\n"
            f"🎵 **Songs:** `{len(resolved_tracks)}`\n"
            f"🎧 **Now Playing:** `{first['title'][:45]}`\n\n"
            "⏭ Use `/skip` to play the next song."
        )

    except Exception as error:
        await mystic.edit_text(
            "❌ **Playlist playback failed.**\n\n"
            f"`{str(error)[:500]}`"
        )


# ============================================================
# PRIVATE TEXT INPUT
# ============================================================

@app.on_message(
    filters.text
    & filters.private
    & ~BANNED_USERS,
    group=10,
)
async def playlist_text_input_handler(
    client,
    message: Message,
):
    user_id = message.from_user.id

    if user_id not in PLAYLIST_STATES:
        return

    input_text = (
        message.text.strip()
    )

    if input_text.startswith("/"):
        return

    state_data = PLAYLIST_STATES.pop(
        user_id,
        None,
    )

    if not state_data:
        return

    state = state_data.get(
        "state"
    )

    chat_id = state_data.get(
        "chat_id"
    )

    msg_id = state_data.get(
        "msg_id"
    )

    # ========================================================
    # CREATE PLAYLIST
    # ========================================================

    if state == "WAITING_PLAYLIST_NAME":

        if (
            not input_text
            or len(input_text) > 30
        ):
            PLAYLIST_STATES[user_id] = state_data

            return await message.reply_text(
                "❌ Playlist name must be between 1 and 30 characters."
            )

        playlist_id, status = (
            await db_create_playlist(
                user_id,
                input_text,
            )
        )

        if status == "DUPLICATE":
            PLAYLIST_STATES[user_id] = state_data

            return await message.reply_text(
                f"❌ You already have a playlist named `{input_text}`."
            )

        text = (
            "✅ **PLAYLIST CREATED**\n\n"
            f"📂 **Name:** `{input_text}`\n\n"
            "Your playlist is ready."
        )

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "➕ Add Songs",
                        callback_data=(
                            f"playlist:add_manual:"
                            f"{playlist_id}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🎵 View Playlist",
                        callback_data=(
                            f"playlist:view:"
                            f"{playlist_id}:1"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 My Playlists",
                        callback_data="playlist:list",
                    )
                ],
            ]
        )

        try:
            await client.edit_message_text(
                chat_id,
                msg_id,
                text,
                reply_markup=markup,
            )
        except Exception:
            await message.reply_text(
                text,
                reply_markup=markup,
            )

        return

    # ========================================================
    # ADD SONG
    # ========================================================

    if state == "WAITING_PLAYLIST_SONG":

        playlist_id = state_data.get(
            "playlist_id"
        )

        searching = await message.reply_text(
            "🔎 **Searching YouTube...**"
        )

        try:
            resolved = (
                await resolve_youtube_track(
                    input_text
                )
            )
        except Exception:
            resolved = None

        try:
            await searching.delete()
        except Exception:
            pass

        if not resolved:
            PLAYLIST_STATES[user_id] = {
                **state_data,
                "state": "WAITING_PLAYLIST_SONG",
            }

            return await message.reply_text(
                "❌ **Song not found.**\n"
                "Please send another song name or YouTube link."
            )

        valid_id = extract_yt_id(
            resolved.get(
                "vidid"
            )
        )

        if not valid_id:
            PLAYLIST_STATES[user_id] = {
                **state_data,
                "state": "WAITING_PLAYLIST_SONG",
            }

            return await message.reply_text(
                "❌ **Invalid YouTube result.**\n"
                "Please try another song."
            )

        song_data = {
            "title": resolved.get(
                "title",
                "Unknown Track",
            ),
            "artist": "YouTube",
            "vidid": valid_id,
            "url": resolved.get(
                "link",
                f"https://www.youtube.com/watch?v={valid_id}",
            ),
            "duration": resolved.get(
                "duration_min",
                "03:00",
            ),
            "thumbnail": resolved.get(
                "thumb",
                "",
            ),
        }

        success, result = (
            await db_add_song_to_playlist(
                user_id,
                playlist_id,
                song_data,
            )
        )

        playlist = await db_get_playlist(
            user_id,
            playlist_id,
        )

        playlist_name = (
            playlist.get(
                "name",
                "Playlist",
            )
            if playlist
            else "Playlist"
        )

        if result == "DUPLICATE":

            text = (
                "⚠️ **ALREADY IN PLAYLIST**\n\n"
                f"🎵 `{resolved['title'][:50]}`\n"
                f"📂 `{playlist_name}`"
            )

        elif result == "INVALID_ID":

            text = (
                "❌ **Invalid YouTube track.**"
            )

        else:

            text = (
                "✅ **SONG ADDED**\n\n"
                f"🎵 `{resolved['title'][:50]}`\n"
                f"📂 `{playlist_name}`"
            )

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "➕ Add Another Song",
                        callback_data=(
                            f"playlist:add_manual:"
                            f"{playlist_id}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🎵 View Playlist",
                        callback_data=(
                            f"playlist:view:"
                            f"{playlist_id}:1"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🔙 My Playlists",
                        callback_data="playlist:list",
                    )
                ],
            ]
        )

        try:
            await client.edit_message_text(
                chat_id,
                msg_id,
                text,
                reply_markup=markup,
            )
        except Exception:
            await message.reply_text(
                text,
                reply_markup=markup,
            )
```
