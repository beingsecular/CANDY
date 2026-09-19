import re
from pyrogram import filters
from pyrogram.enums import ChatType, ParseMode
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
from config import BANNED_USERS
from EsproMusic import YouTube, app
from EsproMusic.utils.database import (
    add_to_playlist,
    delete_playlist,
    get_cmode,
    get_lang,
    get_playlist,
    get_playlist_count,
    is_active_chat,
    remove_from_playlist_by_index,
)
from EsproMusic.utils.inline import close_markup
from EsproMusic.utils.stream.stream import stream
from strings import get_string


# --- Helper: Build Playlist Keyboard ---
def playlist_markup(user_id: int):
    buttons = [
        [
            InlineKeyboardButton(
                text="▶️ Play Audio",
                callback_data=f"play_plist|audio|{user_id}",
            ),
            InlineKeyboardButton(
                text="🎥 Play Video",
                callback_data=f"play_plist|video|{user_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="🗑 Delete Playlist",
                callback_data=f"del_plist|confirm|{user_id}",
            ),
            InlineKeyboardButton(
                text="❌ Close",
                callback_data="close",
            ),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


# --- Command: /addplaylist or /save or /playlistadd ---
@app.on_message(
    filters.command(["addplaylist", "save", "playlistadd"]) & ~BANNED_USERS
)
async def add_playlist_cmd(client, message: Message):
    user_id = message.from_user.id
    current_count = await get_playlist_count(user_id)

    if current_count >= 50:
        return await message.reply_text(
            "❌ <b>Playlist Limit Reached!</b>\n\n"
            "You can only save up to <b>50 songs</b> in your personal playlist.\n"
            "Remove some songs using <code>/delplaylist &lt;number&gt;</code> before adding more.",
            parse_mode=ParseMode.HTML,
        )

    query = ""
    # Check if replied to a message
    if message.reply_to_message:
        replied = message.reply_to_message
        if replied.text:
            query = replied.text.strip()
        elif replied.caption:
            query = replied.caption.strip()
        elif replied.audio:
            query = replied.audio.title or replied.audio.file_name or ""
        elif replied.video:
            query = replied.video.file_name or ""

    # If argument supplied in command, override query
    if len(message.command) > 1:
        query = message.text.split(None, 1)[1].strip()

    if not query:
        return await message.reply_text(
            "🥀 <b>Usage:</b>\n\n"
            "• <code>/addplaylist [song name or YouTube URL]</code>\n"
            "• Reply to any message/audio with <code>/addplaylist</code>\n\n"
            "<b>Example:</b> <code>/addplaylist Kesariya</code>",
            parse_mode=ParseMode.HTML,
        )

    mystic = await message.reply_text("🔎 <i>Searching song on YouTube...</i>", parse_mode=ParseMode.HTML)

    try:
        details, vidid = await YouTube.track(query)
    except Exception:
        details, vidid = None, None

    if not details or not vidid:
        try:
            (
                title,
                duration_min,
                duration_sec,
                thumbnail,
                vidid,
            ) = await YouTube.details(query)
            if vidid and title:
                details = {
                    "title": title,
                    "duration_min": duration_min or "03:00",
                    "vidid": vidid,
                }
        except Exception:
            pass

    if not details or not vidid:
        return await mystic.edit_text(
            "❌ <b>Could not find that track on YouTube.</b>\nPlease try searching with a more specific song name or direct link.",
            parse_mode=ParseMode.HTML,
        )

    title = str(details.get("title", "Track"))[:60]
    duration = str(details.get("duration_min", "03:00"))

    success, status = await add_to_playlist(user_id, vidid, title, duration)

    if not success:
        if status == "already_exists":
            return await mystic.edit_text(
                f"⚠️ <b>Song Already in Playlist!</b>\n\n"
                f"📌 <b>Title:</b> <code>{title}</code>\n\n"
                "This song is already saved in your playlist.",
                parse_mode=ParseMode.HTML,
            )
        elif status == "limit_exceeded":
            return await mystic.edit_text(
                "❌ <b>Playlist Limit Reached (50 songs max)!</b>",
                parse_mode=ParseMode.HTML,
            )
        else:
            return await mystic.edit_text("❌ Failed to add song to playlist.")

    total_count = await get_playlist_count(user_id)
    info_link = f"https://t.me/{app.username}?start=info_{vidid}"

    await mystic.edit_text(
        f"✨ <b><u>Added to Your Playlist</u></b>\n\n"
        f"📌 <b>Title:</b> <a href='{info_link}'>{title}</a>\n"
        f"⏱ <b>Duration:</b> <code>{duration}</code>\n"
        f"🔢 <b>Total Songs in Playlist:</b> <code>{total_count}</code>\n\n"
        f"💡 <i>Tip: Play your playlist anytime with <code>/playplaylist</code>!</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# --- Command: /playlist or /myplaylist ---
@app.on_message(filters.command(["playlist", "myplaylist"]) & ~BANNED_USERS)
async def view_playlist_cmd(client, message: Message):
    user_id = message.from_user.id
    user_mention = message.from_user.mention
    playlist = await get_playlist(user_id)

    if not playlist:
        return await message.reply_text(
            f"🥀 <b>{user_mention}'s Playlist is Empty!</b>\n\n"
            "You haven't added any songs to your playlist yet.\n\n"
            "<b>To add songs:</b>\n"
            "• <code>/addplaylist [song name or link]</code>\n"
            "• Reply to an audio with <code>/addplaylist</code>",
            parse_mode=ParseMode.HTML,
        )

    text = f"🎧 <b><u>{user_mention}'s Saved Playlist</u></b>\n\n"
    for i, track in enumerate(playlist, 1):
        title = track.get("title", "Track")[:45]
        duration = track.get("duration", "03:00")
        vidid = track.get("vidid", "")
        text += f"<b>{i}.</b> <a href='https://t.me/{app.username}?start=info_{vidid}'>{title}</a> <code>[{duration}]</code>\n"

    text += f"\n📊 <b>Total Songs:</b> <code>{len(playlist)}</code>\n"
    text += f"🚀 <b>Single command to play:</b> <code>/playplaylist</code>"

    await message.reply_text(
        text,
        reply_markup=playlist_markup(user_id),
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


# --- Command: /delplaylist or /delmyplaylist ---
@app.on_message(filters.command(["delplaylist", "delmyplaylist"]) & ~BANNED_USERS)
async def del_playlist_cmd(client, message: Message):
    user_id = message.from_user.id
    user_mention = message.from_user.mention
    playlist = await get_playlist(user_id)

    if not playlist:
        return await message.reply_text(
            f"🥀 <b>{user_mention}'s Playlist is already empty.</b>",
            parse_mode=ParseMode.HTML,
        )

    if len(message.command) > 1:
        arg = message.command[1].strip().lower()
        if arg in ["all", "clear", "wipe"]:
            await delete_playlist(user_id)
            return await message.reply_text(
                "🗑 <b>Your entire playlist has been deleted successfully!</b>",
                parse_mode=ParseMode.HTML,
            )
        elif arg.isdigit():
            idx = int(arg)
            success, result = await remove_from_playlist_by_index(user_id, idx)
            if success:
                remaining = await get_playlist_count(user_id)
                return await message.reply_text(
                    f"🗑 <b>Removed Track #{idx}:</b> <code>{result}</code>\n"
                    f"🔢 Remaining songs: <code>{remaining}</code>",
                    parse_mode=ParseMode.HTML,
                )
            else:
                return await message.reply_text(
                    f"❌ Invalid track number <code>{idx}</code>. Your playlist has {len(playlist)} songs.\n"
                    f"Check numbers via <code>/playlist</code>.",
                    parse_mode=ParseMode.HTML,
                )
        else:
            return await message.reply_text(
                "<b>Usage:</b>\n"
                "• <code>/delplaylist all</code> - Delete entire playlist\n"
                "• <code>/delplaylist &lt;number&gt;</code> - Delete a specific song (e.g. <code>/delplaylist 1</code>)",
                parse_mode=ParseMode.HTML,
            )

    # If no argument, show confirmation keyboard
    upl = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="✅ Yes, Delete All",
                    callback_data=f"del_plist|yes|{user_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data=f"del_plist|cancel|{user_id}",
                ),
            ]
        ]
    )
    await message.reply_text(
        f"⚠️ <b>Delete Playlist Confirmation</b>\n\n"
        f"Are you sure you want to delete all <b>{len(playlist)}</b> songs from your playlist?",
        reply_markup=upl,
        parse_mode=ParseMode.HTML,
    )


# --- Command: /playplaylist or /cplayplaylist or /vplayplaylist ---
@app.on_message(
    filters.command(
        [
            "playplaylist",
            "cplayplaylist",
            "vplayplaylist",
            "cvplayplaylist",
            "myplaylistplay",
        ]
    )
    & ~BANNED_USERS
)
async def play_playlist_cmd(client, message: Message):
    if message.chat.type == ChatType.PRIVATE:
        return await message.reply_text(
            "❌ <b>You can only play playlists inside a Group or Channel voice chat!</b>\n\n"
            "Please add me to your group and start a voice chat to play.",
            parse_mode=ParseMode.HTML,
        )

    user_id = message.from_user.id
    user_name = message.from_user.first_name
    playlist = await get_playlist(user_id)

    if not playlist:
        return await message.reply_text(
            f"🥀 <b>Your playlist is empty!</b>\n\n"
            "Add songs first using <code>/addplaylist [song name]</code>.",
            parse_mode=ParseMode.HTML,
        )

    cmd = message.command[0].lower()
    video = True if ("vplay" in cmd or (len(message.command) > 1 and "-v" in message.text)) else None

    # Handle channel mode if command is cplayplaylist or cvplayplaylist
    chat_id = message.chat.id
    if cmd.startswith("c"):
        channel_id = await get_cmode(message.chat.id)
        if channel_id:
            chat_id = channel_id

    language = await get_lang(chat_id)
    _ = get_string(language)

    mystic = await message.reply_text(
        f"✨ <b>Loading {len(playlist)} songs from your playlist...</b>\n<i>Starting playback soon!</i>",
        parse_mode=ParseMode.HTML,
    )

    vidids = [track.get("vidid") for track in playlist if track.get("vidid")]
    if not vidids:
        return await mystic.edit_text("❌ No valid YouTube tracks found in your playlist.")

    try:
        await stream(
            _,
            mystic,
            user_id,
            vidids,
            chat_id,
            user_name,
            message.chat.id,
            video=video,
            streamtype="playlist",
        )
    except Exception as e:
        LOGGER(__name__).error(f"[PlayPlaylist Error]: {e}")
        return await mystic.edit_text(f"❌ <b>Playback Error:</b> <code>{e}</code>", parse_mode=ParseMode.HTML)


# --- Callback Query Handlers ---
@app.on_callback_query(filters.regex(r"^play_plist\|") & ~BANNED_USERS)
async def play_plist_callback(client, CallbackQuery: CallbackQuery):
    parts = CallbackQuery.data.split("|")
    mode = parts[1]  # "audio" or "video"
    owner_id = int(parts[2])

    if CallbackQuery.from_user.id != owner_id:
        return await CallbackQuery.answer("❌ This is not your playlist button!", show_alert=True)

    if CallbackQuery.message.chat.type == ChatType.PRIVATE:
        return await CallbackQuery.answer(
            "❌ Playlists can only be streamed in a Group voice chat!", show_alert=True
        )

    playlist = await get_playlist(owner_id)
    if not playlist:
        return await CallbackQuery.answer("🥀 Your playlist is empty!", show_alert=True)

    await CallbackQuery.answer("▶️ Starting your playlist playback...")

    user_id = CallbackQuery.from_user.id
    user_name = CallbackQuery.from_user.first_name
    chat_id = CallbackQuery.message.chat.id
    video = True if mode == "video" else None

    language = await get_lang(chat_id)
    _ = get_string(language)

    mystic = await CallbackQuery.message.reply_text(
        f"✨ <b>Streaming {len(playlist)} songs from your playlist...</b>",
        parse_mode=ParseMode.HTML,
    )

    vidids = [track.get("vidid") for track in playlist if track.get("vidid")]
    try:
        await stream(
            _,
            mystic,
            user_id,
            vidids,
            chat_id,
            user_name,
            chat_id,
            video=video,
            streamtype="playlist",
        )
    except Exception as e:
        LOGGER(__name__).error(f"[PlayPlaylist Callback Error]: {e}")
        return await mystic.edit_text(f"❌ <b>Playback Error:</b> <code>{e}</code>", parse_mode=ParseMode.HTML)


@app.on_callback_query(filters.regex(r"^del_plist\|") & ~BANNED_USERS)
async def del_plist_callback(client, CallbackQuery: CallbackQuery):
    parts = CallbackQuery.data.split("|")
    action = parts[1]  # "confirm", "yes", "cancel"
    owner_id = int(parts[2])

    if CallbackQuery.from_user.id != owner_id:
        return await CallbackQuery.answer("❌ This is not your playlist button!", show_alert=True)

    if action == "confirm":
        playlist = await get_playlist(owner_id)
        if not playlist:
            return await CallbackQuery.answer("🥀 Your playlist is already empty!", show_alert=True)

        upl = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="✅ Yes, Delete All",
                        callback_data=f"del_plist|yes|{owner_id}",
                    ),
                    InlineKeyboardButton(
                        text="❌ Cancel",
                        callback_data=f"del_plist|cancel|{owner_id}",
                    ),
                ]
            ]
        )
        return await CallbackQuery.edit_message_text(
            f"⚠️ <b>Delete Playlist Confirmation</b>\n\n"
            f"Are you sure you want to delete all <b>{len(playlist)}</b> songs from your playlist?",
            reply_markup=upl,
            parse_mode=ParseMode.HTML,
        )

    elif action == "yes":
        await delete_playlist(owner_id)
        await CallbackQuery.answer("🗑 Playlist deleted!", show_alert=True)
        return await CallbackQuery.edit_message_text(
            "🗑 <b>Your entire playlist has been completely deleted!</b>",
            reply_markup=close_markup(get_string("en")),
            parse_mode=ParseMode.HTML,
        )

    elif action == "cancel":
        await CallbackQuery.answer("Deletion cancelled.")
        playlist = await get_playlist(owner_id)
        if not playlist:
            return await CallbackQuery.edit_message_text("🥀 Your playlist is empty.")

        text = f"🎧 <b><u>{CallbackQuery.from_user.mention}'s Saved Playlist</u></b>\n\n"
        for i, track in enumerate(playlist, 1):
            title = track.get("title", "Track")[:45]
            duration = track.get("duration", "03:00")
            vidid = track.get("vidid", "")
            text += f"<b>{i}.</b> <a href='https://t.me/{app.username}?start=info_{vidid}'>{title}</a> <code>[{duration}]</code>\n"

        text += f"\n📊 <b>Total Songs:</b> <code>{len(playlist)}</code>\n"
        text += f"🚀 <b>Single command to play:</b> <code>/playplaylist</code>"

        return await CallbackQuery.edit_message_text(
            text,
            reply_markup=playlist_markup(owner_id),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
