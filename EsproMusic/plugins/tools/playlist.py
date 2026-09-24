from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

from EsproMusic import app
from EsproMusic.misc import db
import config

# Safe import fɔ Call instance
try:
    from EsproMusic.core.call import Espro
except ImportError:
    try:
        from EsproMusic.core.call import EsproMusic as Espro
    except ImportError:
        from EsproMusic.core.call import Call as Espro

from EsproMusic.utils.database import (
    add_song_to_playlist,
    create_playlist,
    delete_playlist,
    get_lang,
    get_user_playlists,
    remove_active_chat,
    remove_active_video_chat,
    remove_song_from_playlist,
)
from EsproMusic.utils.stream.stream import stream
from config import BANNED_USERS

# Safe import fɔ language strings
try:
    from strings import get_string
except ImportError:
    try:
        from EsproMusic.utils.language import get_string
    except ImportError:
        class DummyLang(dict):
            def __getitem__(self, item):
                return self.get(item, "")
        def get_string(lang):
            return DummyLang()

# Safe import fɔ YouTube helper
try:
    from EsproMusic.platforms import YouTube
    youtube = YouTube()
except Exception:
    youtube = None

PENDING_ADD_SONG = {}


# --- PLAYLIST MENU FUNCTION WIT STYLISH BUTTONS ---
async def show_my_playlists_menu(client, message_or_cb):
    user_id = message_or_cb.from_user.id
    playlists = await get_user_playlists(user_id)

    buttons = []
    if playlists:
        for p_name, songs in playlists.items():
            buttons.append([
                InlineKeyboardButton(f"📁 {p_name} ({len(songs)} songs)", callback_data=f"view_pl:{p_name}"),
                InlineKeyboardButton("▶️ Play", callback_data=f"play_pl_cmd:{p_name}")
            ])

    buttons.append([
        InlineKeyboardButton("✨ Create Playlist", callback_data="ui_create_pl"),
        InlineKeyboardButton("🎵 Add Song", switch_inline_query_current_chat="/addplaylist ")
    ])
    buttons.append([InlineKeyboardButton("◀️ Back", callback_data="open_start_menu")])

    text = (
        "🎧 **─── ｢ STYLISH PLAYLIST MANAGER ｣ ───**\n\n"
        "🎵 **Yu kin mɛnej yu yon songs ɛn playlists kebal!**\n"
        "Select an option below or create a new playlist:"
    )

    if isinstance(message_or_cb, Message):
        await message_or_cb.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message_or_cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))


# --- COMMAND: /playlist OR /myplaylist ---
@app.on_message(filters.command(["playlist", "myplaylist"]) & filters.private & ~BANNED_USERS)
async def my_playlist_cmd(client, message: Message):
    await show_my_playlists_menu(client, message)


# --- CALLBACK: STYLISH CREATE PLAYLIST UI ---
@app.on_callback_query(filters.regex("^ui_create_pl$"))
async def create_playlist_ui_cb(client, CallbackQuery: CallbackQuery):
    text = (
        "✨ **─── ｢ CREATE NEW PLAYLIST ｣ ───** ✨\n\n"
        "👉 **Tap di button dem na bɔtɔm fɔ quick setup:**\n\n"
        "1️⃣ **Create Playlist Command:**\n"
        "`/createplaylist <playlist_name>`\n"
        "_(Example: `/createplaylist my_favorite`)_\n\n"
        "2️⃣ **Add Songs Command:**\n"
        "`/addplaylist <playlist_name> <song_name>`\n"
        "_(Example: `/addplaylist my_favorite Kesariya`)_\n"
    )
    
    buttons = [
        [
            InlineKeyboardButton("➕ Create Command", switch_inline_query_current_chat="/createplaylist "),
            InlineKeyboardButton("🎵 Add Song Command", switch_inline_query_current_chat="/addplaylist ")
        ],
        [InlineKeyboardButton("◀️ Back to Playlists", callback_data="back_to_pl_menu")]
    ]
    await CallbackQuery.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))


# --- COMMAND: /createplaylist <playlist_name> ---
@app.on_message(filters.command(["createplaylist", "cplaylist", "newplaylist"]) & ~BANNED_USERS)
async def create_playlist_cmd(client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "❌ **Usage:** `/createplaylist <playlist_name>`\n"
            "Example: `/createplaylist krish`"
        )

    playlist_name = message.text.split(None, 1)[1].strip()
    user_id = message.from_user.id

    playlists = await get_user_playlists(user_id)
    if playlists and playlist_name in playlists:
        return await message.reply_text(f"❌ Playlist **{playlist_name}** pehle se exist karti hai!")

    try:
        await create_playlist(user_id, playlist_name)
    except Exception:
        pass

    await add_song_to_playlist(user_id, playlist_name, "Welcome Track", "dQw4w9WgXcQ")
    await remove_song_from_playlist(user_id, playlist_name, "dQw4w9WgXcQ")

    buttons = [
        [InlineKeyboardButton("🎵 Add Song Now", switch_inline_query_current_chat=f"/addplaylist {playlist_name} ")],
        [InlineKeyboardButton("📁 View My Playlists", callback_data="back_to_pl_menu")]
    ]

    await message.reply_text(
        f"✅ **Playlist '{playlist_name}' created successfully!**\n\n"
        f"Tap **'Add Song Now'** button below to add songs easily!",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# --- COMMAND: /addplaylist <playlist_name> <song_name/url> ---
@app.on_message(filters.command(["addplaylist", "addsong", "saveplaylist"]) & ~BANNED_USERS)
async def add_to_playlist_cmd(client, message: Message):
    args = message.text.split(None, 2)
    if len(args) < 3:
        return await message.reply_text(
            "❌ **Usage:** `/addplaylist <playlist_name> <song name or URL>`\n"
            "Example: `/addplaylist krish Kesariya`"
        )

    playlist_name = args[1].strip()
    query = args[2].strip()
    user_id = message.from_user.id

    mystic = await message.reply_text("🔎 **Searching song on YouTube...**")

    videoid = None
    title = query

    if youtube:
        try:
            results = await youtube.track(query)
            if results:
                details = results[0] if isinstance(results, list) else results
                videoid = details.get("vidid") or details.get("id")
                title = details.get("title", query)
        except Exception:
            pass

    if not videoid:
        if "youtube.com" in query or "youtu.be" in query:
            videoid = query.split("v=")[-1].split("&")[0] if "v=" in query else query.split("/")[-1]
        else:
            videoid = "dQw4w9WgXcQ"

    res = await add_song_to_playlist(user_id, playlist_name, title, videoid)
    
    buttons = [
        [InlineKeyboardButton("➕ Add Another Song", switch_inline_query_current_chat=f"/addplaylist {playlist_name} ")],
        [InlineKeyboardButton("▶️ Play in Group", switch_inline_query_current_chat=f"/playplaylist {playlist_name}")]
    ]

    if res == "duplicate":
        await mystic.edit_text(f"⚠️ **This song is already in '{playlist_name}'!**", reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await mystic.edit_text(
            f"✅ **Added to '{playlist_name}'!**\n\n"
            f"🎵 **Title:** `{title}`",
            reply_markup=InlineKeyboardMarkup(buttons)
        )


# --- CALLBACK: GENERATE COPYABLE COMMAND FOR GROUP CHAT ---
@app.on_callback_query(filters.regex(r"^play_pl_cmd:(.*)$"))
async def play_playlist_button_cb(client, CallbackQuery: CallbackQuery):
    playlist_name = CallbackQuery.data.split("play_pl_cmd:")[1]
    cmd_text = f"/playplaylist {playlist_name}"

    buttons = [
        [InlineKeyboardButton("▶️ Play in GC", switch_inline_query_current_chat=cmd_text)]
    ]

    await CallbackQuery.answer("Command generated!", show_alert=False)
    await CallbackQuery.message.reply_text(
        f"🎵 **Play Playlist in Group Chat**\n\n"
        f"Copy or tap the button below to paste in your **Group Chat**:\n\n"
        f"<code>{cmd_text}</code>",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


# --- CALLBACK: VIEW PLAYLIST CONTENTS ---
@app.on_callback_query(filters.regex(r"^view_pl:(.*)$"))
async def view_playlist_cb(client, CallbackQuery: CallbackQuery):
    playlist_name = CallbackQuery.data.split("view_pl:")[1]
    user_id = CallbackQuery.from_user.id

    playlists = await get_user_playlists(user_id)
    songs = playlists.get(playlist_name, [])

    if not songs:
        text = f"📁 **Playlist:** `{playlist_name}`\n\nThis playlist is empty."
    else:
        text = f"📁 **Playlist:** `{playlist_name}`\n\n**Songs List:**\n"
        for idx, song in enumerate(songs, 1):
            text += f"{idx}. {song.get('title')}\n"

    buttons = [
        [InlineKeyboardButton("➕ Add More Song", switch_inline_query_current_chat=f"/addplaylist {playlist_name} ")],
        [InlineKeyboardButton("🗑️ Delete Playlist", callback_data=f"delete_pl:{playlist_name}")],
        [InlineKeyboardButton("◀️ Back", callback_data="back_to_pl_menu")]
    ]

    await CallbackQuery.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))


# --- CALLBACK: DELETE PLAYLIST ---
@app.on_callback_query(filters.regex(r"^delete_pl:(.*)$"))
async def delete_playlist_cb(client, CallbackQuery: CallbackQuery):
    playlist_name = CallbackQuery.data.split("delete_pl:")[1]
    user_id = CallbackQuery.from_user.id

    await delete_playlist(user_id, playlist_name)
    await CallbackQuery.answer(f"Deleted playlist '{playlist_name}'!", show_alert=True)
    await show_my_playlists_menu(client, CallbackQuery)


# --- CALLBACK: BACK TO PLAYLIST MENU ---
@app.on_callback_query(filters.regex("^back_to_pl_menu$"))
async def back_to_pl_menu_cb(client, CallbackQuery: CallbackQuery):
    await show_my_playlists_menu(client, CallbackQuery)


# --- CALLBACK: OPEN START MENU BACK BUTTON FALLBACK ---
@app.on_callback_query(filters.regex("^open_start_menu$"))
async def open_start_menu_cb(client, CallbackQuery: CallbackQuery):
    await CallbackQuery.message.edit_text("🏠 **Main Menu**\n\nSend `/start` to view options.", reply_markup=None)


# --- GROUP COMMAND: /playplaylist <playlist_name> ---
@app.on_message(filters.command(["playplaylist", "playpl"]) & filters.group & ~BANNED_USERS)
async def play_user_playlist_in_gc(client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "❌ **Usage:** `/playplaylist <playlist_name>`\n"
            "Example: `/playplaylist krish`"
        )

    playlist_name = message.text.split(None, 1)[1].strip()
    user_id = message.from_user.id
    chat_id = message.chat.id

    playlists = await get_user_playlists(user_id)
    if not playlists or playlist_name not in playlists:
        return await message.reply_text(f"❌ Playlist **{playlist_name}** not found!")

    songs = playlists[playlist_name]
    if not songs:
        return await message.reply_text(f"❌ Your playlist **{playlist_name}** is empty!")

    mystic = await message.reply_text("🔄 **Skipping current song & starting playlist...**")

    try:
        language = await get_lang(chat_id)
        _ = get_string(language)
    except Exception:
        class DummyLang(dict):
            def __getitem__(self, item):
                return self.get(item, "")
        _ = DummyLang()

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

    user_name = message.from_user.first_name

    first_song = songs[0]
    videoid_0 = first_song.get("videoid")
    title_0 = first_song.get("title", "Playlist Song")
    url_0 = f"https://www.youtube.com/watch?v={videoid_0}" if videoid_0 else title_0

    default_thumb = (
        f"https://img.youtube.com/vi/{videoid_0}/hqdefault.jpg"
        if videoid_0
        else getattr(config, "YOUTUBE_IMG_URL", "https://telegra.ph/file/c8f2052028238627e1f33.jpg")
    )

    first_details = None
    if youtube and videoid_0:
        try:
            first_details, _id = await youtube.track(videoid_0, True)
        except Exception:
            first_details = None

    if not first_details:
        first_details = {
            "title": title_0,
            "link": url_0,
            "vidid": videoid_0 or "none",
            "duration_min": "03:00",
            "thumb": default_thumb,
            "by": user_name,
        }
    else:
        if "thumb" not in first_details or not first_details["thumb"]:
            first_details["thumb"] = default_thumb

    for song in songs[1:]:
        v_id = song.get("videoid")
        s_title = song.get("title", "Playlist Song")
        u_link = f"https://www.youtube.com/watch?v={v_id}" if v_id else s_title
        s_thumb = f"https://img.youtube.com/vi/{v_id}/hqdefault.jpg" if v_id else default_thumb

        d_item = {
            "title": s_title,
            "link": u_link,
            "vidid": v_id or "none",
            "duration_min": "03:00",
            "thumb": s_thumb,
            "user": user_name,
            "user_id": user_id,
            "streamtype": "youtube",
            "file": None,
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
            message.chat.id,
            video=None,
            streamtype="youtube",
            forceplay=True,
        )
    except Exception as e:
        print(f"Error streaming first song: {e}")
        await mystic.edit_text(f"❌ **Error starting stream:** `{e}`")


# --- GROUP COMMAND: /skippl OR /skipplaylist OR /pskip ---
@app.on_message(filters.command(["skippl", "skipplaylist", "pskip"]) & filters.group & ~BANNED_USERS)
async def skip_playlist_song_in_gc(client, message: Message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    user_name = message.from_user.first_name

    if chat_id not in db or not db[chat_id]:
        return await message.reply_text("❌ **Playlist queue me koi song bacha nahi hai!**")

    mystic = await message.reply_text("⏭️ **Skipping song & playing next from playlist...**")

    try:
        language = await get_lang(chat_id)
        _ = get_string(language)
    except Exception:
        class DummyLang(dict):
            def __getitem__(self, item):
                return self.get(item, "")
        _ = DummyLang()

    next_song = db[chat_id].pop(0)

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

    v_id = next_song.get("vidid") or next_song.get("videoid")
    s_title = next_song.get("title", "Playlist Song")
    u_link = next_song.get("link") or (f"https://www.youtube.com/watch?v={v_id}" if v_id and v_id != "none" else s_title)

    default_thumb = (
        f"https://img.youtube.com/vi/{v_id}/hqdefault.jpg"
        if v_id and v_id != "none"
        else getattr(config, "YOUTUBE_IMG_URL", "https://telegra.ph/file/c8f2052028238627e1f33.jpg")
    )

    next_details = None
    if youtube and v_id and v_id != "none":
        try:
            next_details, _id = await youtube.track(v_id, True)
        except Exception:
            next_details = None

    if not next_details:
        next_details = {
            "title": s_title,
            "link": u_link,
            "vidid": v_id or "none",
            "duration_min": next_song.get("duration_min", "03:00"),
            "thumb": next_song.get("thumb", default_thumb),
            "by": user_name,
        }
    else:
        if "thumb" not in next_details or not next_details["thumb"]:
            next_details["thumb"] = default_thumb

    try:
        await stream(
            _,
            mystic,
            user_id,
            next_details,
            chat_id,
            user_name,
            message.chat.id,
            video=None,
            streamtype="youtube",
            forceplay=True,
        )
    except Exception as e:
        print(f"Error skipping playlist song: {e}")
        await mystic.edit_text(f"❌ **Error playing next song:** `{e}`")
