from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, CallbackQuery

from EsproMusic import app
from EsproMusic.misc import db

# Safe import for Call instance
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

# Safe import for language strings
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

# Safe import for YouTube helper
try:
    from EsproMusic.platforms import YouTube
    youtube = YouTube()
except Exception:
    youtube = None

# Temporary cache for songs waiting to be saved
PENDING_ADD_SONG = {}


# --- PLAYLIST MENU FUNCTION ---
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

    buttons.append([InlineKeyboardButton("➕ Create New Playlist", callback_data="ui_create_pl")])
    buttons.append([InlineKeyboardButton("◀️ Back", callback_data="open_start_menu")])

    text = "🎵 **My Playlists**\n\nChoose a playlist to play or manage:"

    if isinstance(message_or_cb, Message):
        await message_or_cb.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message_or_cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))


# --- COMMAND: /playlist OR /myplaylist IN PRIVATE CHAT ---
@app.on_message(filters.command(["playlist", "myplaylist"]) & filters.private & ~BANNED_USERS)
async def my_playlist_cmd(client, message: Message):
    await show_my_playlists_menu(client, message)


# --- CALLBACK: GENERATE COPYABLE COMMAND FOR GROUP CHAT ---
@app.on_callback_query(filters.regex(r"^play_pl_cmd:(.*)$"))
async def play_playlist_button_cb(client, CallbackQuery: CallbackQuery):
    playlist_name = CallbackQuery.data.split("play_pl_cmd:")[1]
    cmd_text = f"/playplaylist {playlist_name}"

    await CallbackQuery.answer("Command generated!", show_alert=False)
    await CallbackQuery.message.reply_text(
        f"🎵 **Play Playlist in Group Chat**\n\n"
        f"Copy the command below and paste it in your **Group Chat (GC)**:\n\n"
        f"<code>{cmd_text}</code>\n\n"
        f"⚡ *Sending this command in your group will stop the current track and start playing your playlist.*"
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


# --- CALLBACK: SAVE SONG TO PLAYLIST ---
@app.on_callback_query(filters.regex(r"^save_to_pl:(.*)$"))
async def save_to_pl_cb(client, CallbackQuery: CallbackQuery):
    playlist_name = CallbackQuery.data.split("save_to_pl:")[1]
    user_id = CallbackQuery.from_user.id

    videoid = PENDING_ADD_SONG.get(user_id)
    if not videoid:
        return await CallbackQuery.answer("No pending song found to add!", show_alert=True)

    res = await add_song_to_playlist(user_id, playlist_name, f"Song ({videoid})", videoid)
    if res == "duplicate":
        await CallbackQuery.answer("Song is already in this playlist!", show_alert=True)
    else:
        await CallbackQuery.answer(f"Added to '{playlist_name}'!", show_alert=True)
        PENDING_ADD_SONG.pop(user_id, None)

    await CallbackQuery.message.delete()


# --- CALLBACK: CANCEL PLAYLIST ACTION ---
@app.on_callback_query(filters.regex("^cancel_pl$"))
async def cancel_pl_cb(client, CallbackQuery: CallbackQuery):
    user_id = CallbackQuery.from_user.id
    PENDING_ADD_SONG.pop(user_id, None)
    await CallbackQuery.message.delete()


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

    # Load Language dictionary
    try:
        language = await get_lang(chat_id)
        _ = get_string(language)
    except Exception:
        class DummyLang(dict):
            def __getitem__(self, item):
                return self.get(item, "")
        _ = DummyLang()

    # 1. Clear old queue
    db[chat_id] = []

    # 2. Stop ongoing stream & reset active state
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

    # 3. Process first song safely
    first_song = songs[0]
    videoid_0 = first_song.get("videoid")
    title_0 = first_song.get("title", "Playlist Song")
    url_0 = f"https://www.youtube.com/watch?v={videoid_0}" if videoid_0 else title_0

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
        }

    # 4. Add remaining playlist songs to background queue (`db[chat_id]`)
    for song in songs[1:]:
        v_id = song.get("videoid")
        s_title = song.get("title", "Playlist Song")
        u_link = f"https://www.youtube.com/watch?v={v_id}" if v_id else s_title

        d_item = {
            "title": s_title,
            "link": u_link,
            "vidid": v_id or "none",
            "duration_min": "03:00",
            "user": user_name,
            "user_id": user_id,
            "streamtype": "youtube",
            "file": None,
        }
        db[chat_id].append(d_item)

    # 5. Play first song
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
        await mystic.edit_text(
            "❌ **Failed to fetch track from YouTube.**\n"
            "Please update `yt-dlp` or add `cookies.txt` on your VPS."
        )
