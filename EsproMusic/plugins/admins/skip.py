from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, Message

import config
from EsproMusic import YouTube, app
from EsproMusic.core.call import Ritik
from EsproMusic.misc import db
from EsproMusic.utils.database import get_loop
from EsproMusic.utils.decorators import AdminRightsCheck
from EsproMusic.utils.inline import close_markup, stream_markup
from EsproMusic.utils.stream.autoclear import auto_clean
from EsproMusic.utils.thumbnails import get_thumb
from config import BANNED_USERS


@app.on_message(
    filters.command(["skip", "cskip", "next", "cnext"]) & filters.group & ~BANNED_USERS
)
@AdminRightsCheck
async def skip(cli, message: Message, _, chat_id):
    user_mention = message.from_user.mention if message.from_user else "User"
    chat_title = message.chat.title if message.chat else "Chat"

    # Specific skip count e.g. /skip 2
    if len(message.command) >= 2:
        loop = await get_loop(chat_id)
        if loop != 0:
            return await message.reply_text(_["admin_8"])
        state = message.text.split(None, 1)[1].strip()
        if state.isnumeric():
            state = int(state)
            check = db.get(chat_id)
            if check:
                count = len(check)
                if count >= 1:
                    if 1 <= state <= count:
                        for x in range(state):
                            popped = None
                            try:
                                popped = db[chat_id].pop(0)
                            except Exception:
                                return await message.reply_text(_["admin_12"])
                            if popped:
                                await auto_clean(popped)
                    else:
                        return await message.reply_text(_["admin_11"].format(count))
                else:
                    return await message.reply_text(_["admin_10"])
            else:
                return await message.reply_text(_["queue_2"])
        else:
            return await message.reply_text(_["admin_9"])

    # Normal /skip command
    check = db.get(chat_id)

    if not check or len(check) == 0:
        # No more songs in queue
        try:
            from EsproMusic.plugins.tools.autoplay import try_autoplay
            if await try_autoplay(chat_id, None):
                return
        except Exception:
            pass

        try:
            await message.reply_text(
                text=_["admin_6"].format(user_mention, chat_title),
                reply_markup=close_markup(_),
            )
        except Exception:
            await message.reply_text("⏭️ **Stream skipped / Stopped.**")

        try:
            return await Ritik.stop_stream(chat_id)
        except Exception:
            return

    # Pop the NEXT song to play directly from global DB queue
    next_song = db[chat_id].pop(0)
    await auto_clean(next_song)

    queued = next_song.get("file", "")
    title = str(next_song.get("title", "Unknown Track")).title()
    user = next_song.get("by", user_mention)
    streamtype = next_song.get("streamtype", "audio")
    videoid = next_song.get("vidid", "")
    status = True if str(streamtype) == "video" else None

    # Reset duration/played status for new song if items remaining in queue
    if chat_id in db and len(db[chat_id]) > 0:
        db[chat_id][0]["played"] = 0
        exis = next_song.get("old_dur")
        if exis:
            db[chat_id][0]["dur"] = exis
            db[chat_id][0]["seconds"] = next_song.get("old_second", 180)
            db[chat_id][0]["speed_path"] = None
            db[chat_id][0]["speed"] = 1.0

    if "live_" in queued:
        n, link = await YouTube.video(videoid, True)
        if n == 0:
            return await message.reply_text(_["admin_7"].format(title))
        try:
            image = await YouTube.thumbnail(videoid, True)
        except Exception:
            image = None
        try:
            await Ritik.skip_stream(chat_id, link, video=status, image=image)
        except Exception as e:
            return await message.reply_text(f"❌ **Skip failed:** `{e}`")

        button = stream_markup(_, chat_id)
        img = await get_thumb(videoid)
        run = await message.reply_photo(
            photo=img,
            caption=_["stream_1"].format(
                f"https://t.me/{app.username}?start=info_{videoid}",
                title[:23],
                next_song.get("dur", "03:00"),
                user,
            ),
            reply_markup=InlineKeyboardMarkup(button),
        )
        if chat_id in db and len(db[chat_id]) > 0:
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"

    elif "vid_" in queued or queued.startswith("vid_"):
        mystic = await message.reply_text(_["call_7"], disable_web_page_preview=True)
        try:
            file_path, direct = await YouTube.download(
                videoid,
                mystic,
                videoid=True,
                video=status,
            )
        except Exception as e:
            return await mystic.edit_text(f"❌ **Download failed:** `{e}`")

        try:
            image = await YouTube.thumbnail(videoid, True)
        except Exception:
            image = None

        try:
            await Ritik.skip_stream(chat_id, file_path, video=status, image=image)
        except Exception as e:
            return await mystic.edit_text(f"❌ **Skip stream failed:** `{e}`")

        button = stream_markup(_, chat_id)
        img = await get_thumb(videoid)
        run = await message.reply_photo(
            photo=img,
            caption=_["stream_1"].format(
                f"https://t.me/{app.username}?start=info_{videoid}",
                title[:23],
                next_song.get("dur", "03:00"),
                user,
            ),
            reply_markup=InlineKeyboardMarkup(button),
        )
        if chat_id in db and len(db[chat_id]) > 0:
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "stream"
        await mystic.delete()

    elif "index_" in queued:
        try:
            await Ritik.skip_stream(chat_id, videoid, video=status)
        except Exception as e:
            return await message.reply_text(f"❌ **Skip failed:** `{e}`")
        button = stream_markup(_, chat_id)
        run = await message.reply_photo(
            photo=config.STREAM_IMG_URL,
            caption=_["stream_2"].format(user),
            reply_markup=InlineKeyboardMarkup(button),
        )
        if chat_id in db and len(db[chat_id]) > 0:
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"

    else:
        if videoid in ["telegram", "soundcloud"]:
            image = None
        else:
            try:
                image = await YouTube.thumbnail(videoid, True)
            except Exception:
                image = None
        try:
            await Ritik.skip_stream(chat_id, queued, video=status, image=image)
        except Exception as e:
            return await message.reply_text(f"❌ **Skip failed:** `{e}`")

        button = stream_markup(_, chat_id)
        if videoid == "telegram":
            run = await message.reply_photo(
                photo=config.TELEGRAM_AUDIO_URL
                if str(streamtype) == "audio"
                else config.TELEGRAM_VIDEO_URL,
                caption=_["stream_1"].format(
                    config.SUPPORT_CHAT, title[:23], next_song.get("dur", "03:00"), user
                ),
                reply_markup=InlineKeyboardMarkup(button),
            )
        elif videoid == "soundcloud":
            run = await message.reply_photo(
                photo=config.SOUNCLOUD_IMG_URL
                if str(streamtype) == "audio"
                else config.TELEGRAM_VIDEO_URL,
                caption=_["stream_1"].format(
                    config.SUPPORT_CHAT, title[:23], next_song.get("dur", "03:00"), user
                ),
                reply_markup=InlineKeyboardMarkup(button),
            )
        else:
            img = await get_thumb(videoid)
            run = await message.reply_photo(
                photo=img,
                caption=_["stream_1"].format(
                    f"https://t.me/{app.username}?start=info_{videoid}",
                    title[:23],
                    next_song.get("dur", "03:00"),
                    user,
                ),
                reply_markup=InlineKeyboardMarkup(button),
            )
        if chat_id in db and len(db[chat_id]) > 0:
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "stream"
