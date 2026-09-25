import os

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

os.makedirs("downloads", exist_ok=True)


@app.on_message(
    filters.command(["skip", "cskip", "next", "cnext"])
    & filters.group
    & ~BANNED_USERS
)
@AdminRightsCheck
async def skip(cli, message: Message, _, chat_id):

    user_mention = (
        message.from_user.mention
        if message.from_user
        else "User"
    )

    chat_title = (
        message.chat.title
        if message.chat
        else "Chat"
    )

    # ============================================================
    # 1. /skip NUMBER (e.g., /skip 3)
    # ============================================================
    if len(message.command) >= 2:

        loop = await get_loop(chat_id)

        if loop != 0:
            return await message.reply_text(_["admin_8"])

        state = message.text.split(None, 1)[1].strip()

        if not state.isnumeric():
            return await message.reply_text(_["admin_9"])

        state = int(state)

        queue = db.get(chat_id)

        if not queue:
            return await message.reply_text(_["queue_2"])

        if state < 1 or state > len(queue):
            return await message.reply_text(
                _["admin_11"].format(len(queue))
            )

        # Remove 'state' number of tracks from db (including current playing track)
        for _ in range(state):
            if not db.get(chat_id):
                break

            popped = db[chat_id].pop(0)

            if popped:
                try:
                    await auto_clean(popped)
                except Exception:
                    pass

        # If queue empty after bulk skip -> Try Autoplay or Stop Stream
        if not db.get(chat_id):
            try:
                from EsproMusic.plugins.tools.autoplay import try_autoplay

                if await try_autoplay(chat_id, None):
                    return
            except Exception:
                pass

            try:
                await message.reply_text(
                    text=_["admin_6"].format(
                        user_mention,
                        chat_title,
                    ),
                    reply_markup=close_markup(_),
                )
            except Exception:
                pass

            try:
                return await Ritik.stop_stream(chat_id)
            except Exception:
                return

    # ============================================================
    # 2. NORMAL /skip OR NEXT TRACK PROCESS
    # ============================================================
    else:
        # Check current queue availability
        queue = db.get(chat_id)

        if not queue or len(queue) == 0:
            try:
                from EsproMusic.plugins.tools.autoplay import try_autoplay

                if await try_autoplay(chat_id, None):
                    return
            except Exception:
                pass

            try:
                await message.reply_text(
                    text=_["admin_6"].format(
                        user_mention,
                        chat_title,
                    ),
                    reply_markup=close_markup(_),
                )
            except Exception:
                pass

            try:
                return await Ritik.stop_stream(chat_id)
            except Exception:
                return

        # Pop current playing song/playlist track
        current_song = db[chat_id].pop(0)

        try:
            await auto_clean(current_song)
        except Exception:
            pass

    # Re-check queue after popping
    if not db.get(chat_id):
        try:
            from EsproMusic.plugins.tools.autoplay import try_autoplay

            if await try_autoplay(chat_id, None):
                return
        except Exception:
            pass

        try:
            await message.reply_text(
                text=_["admin_6"].format(
                    user_mention,
                    chat_title,
                ),
                reply_markup=close_markup(_),
            )
        except Exception:
            pass

        try:
            return await Ritik.stop_stream(chat_id)
        except Exception:
            return

    # ============================================================
    # 3. PLAY NEXT TRACK IN QUEUE / PLAYLIST
    # ============================================================
    next_song = db[chat_id][0]

    queued = next_song.get("file", "")
    title = str(
        next_song.get(
            "title",
            "Unknown Track",
        )
    ).title()

    user = next_song.get(
        "by",
        user_mention,
    )

    streamtype = next_song.get(
        "streamtype",
        "audio",
    )

    videoid = next_song.get(
        "vidid",
        "",
    )

    status = (
        True
        if str(streamtype) == "video"
        else None
    )

    # Reset metadata state for next item
    try:
        db[chat_id][0]["played"] = 0
    except Exception:
        pass

    exis = next_song.get("old_dur")
    if exis:
        try:
            db[chat_id][0]["dur"] = exis
            db[chat_id][0]["seconds"] = next_song.get(
                "old_second",
                180,
            )
            db[chat_id][0]["speed_path"] = None
            db[chat_id][0]["speed"] = 1.0
        except Exception:
            pass

    # A. Live Stream Handling
    if "live_" in str(queued):
        n, link = await YouTube.video(
            videoid,
            True,
        )

        if n == 0:
            return await message.reply_text(
                _["admin_7"].format(title)
            )

        try:
            image = await YouTube.thumbnail(
                videoid,
                True,
            )
        except Exception:
            image = None

        try:
            await Ritik.skip_stream(
                chat_id,
                link,
                video=status,
                image=image,
            )
        except Exception as e:
            return await message.reply_text(
                f"❌ **Skip failed:** `{e}`"
            )

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

        db[chat_id][0]["mystic"] = run
        db[chat_id][0]["markup"] = "tg"
        return

    # B. Index Stream Handling
    elif "index_" in str(queued):
        try:
            await Ritik.skip_stream(
                chat_id,
                videoid,
                video=status,
            )
        except Exception as e:
            return await message.reply_text(
                f"❌ **Skip failed:** `{e}`"
            )

        button = stream_markup(_, chat_id)

        run = await message.reply_photo(
            photo=config.STREAM_IMG_URL,
            caption=_["stream_2"].format(user),
            reply_markup=InlineKeyboardMarkup(button),
        )

        db[chat_id][0]["mystic"] = run
        db[chat_id][0]["markup"] = "tg"
        return

    # C. YouTube Track / Playlist Track Handling
    elif (
        videoid
        and videoid not in [
            "telegram",
            "soundcloud",
            "none",
        ]
    ):
        mystic = await message.reply_text(
            _["call_7"],
            disable_web_page_preview=True,
        )

        file_path = (
            queued
            if queued and os.path.exists(queued)
            else None
        )

        if not file_path:
            try:
                file_path, direct = await YouTube.download(
                    videoid,
                    mystic,
                    videoid=True,
                    video=status,
                )
            except Exception as e:
                return await mystic.edit_text(
                    f"❌ **Download failed:** `{e}`"
                )

        if not file_path or not os.path.exists(file_path):
            return await mystic.edit_text(
                "❌ **Audio file download nahi ho payi.**"
            )

        try:
            image = await YouTube.thumbnail(
                videoid,
                True,
            )
        except Exception:
            image = None

        try:
            await Ritik.skip_stream(
                chat_id,
                file_path,
                video=status,
                image=image,
            )
        except Exception as e:
            return await mystic.edit_text(
                f"❌ **Skip stream failed:** `{e}`"
            )

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

        db[chat_id][0]["mystic"] = run
        db[chat_id][0]["markup"] = "stream"

        try:
            await mystic.delete()
        except Exception:
            pass

        return

    # D. Telegram / SoundCloud / Local Files
    else:
        if (
            queued
            and not str(queued).startswith("http")
            and not os.path.exists(queued)
            and videoid not in ["telegram", "soundcloud"]
        ):
            return await message.reply_text(
                "❌ **Track file lost on server. Skipping to next...**"
            )

        image = None
        if videoid not in ["telegram", "soundcloud", "none"]:
            try:
                image = await YouTube.thumbnail(
                    videoid,
                    True,
                )
            except Exception:
                image = None

        try:
            await Ritik.skip_stream(
                chat_id,
                queued,
                video=status,
                image=image,
            )
        except Exception as e:
            return await message.reply_text(
                f"❌ **Skip failed:** `{e}`"
            )

        button = stream_markup(_, chat_id)

        if videoid == "telegram":
            run = await message.reply_photo(
                photo=(
                    config.TELEGRAM_AUDIO_URL
                    if str(streamtype) == "audio"
                    else config.TELEGRAM_VIDEO_URL
                ),
                caption=_["stream_1"].format(
                    config.SUPPORT_CHAT,
                    title[:23],
                    next_song.get("dur", "03:00"),
                    user,
                ),
                reply_markup=InlineKeyboardMarkup(button),
            )

        elif videoid == "soundcloud":
            run = await message.reply_photo(
                photo=(
                    config.SOUNDCLOUD_IMG_URL
                    if str(streamtype) == "audio"
                    else config.TELEGRAM_VIDEO_URL
                ),
                caption=_["stream_1"].format(
                    config.SUPPORT_CHAT,
                    title[:23],
                    next_song.get("dur", "03:00"),
                    user,
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

        db[chat_id][0]["mystic"] = run
        db[chat_id][0]["markup"] = "stream"
        return
