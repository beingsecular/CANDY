import asyncio
import os
from datetime import datetime, timedelta
from typing import Union

from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup
from pytgcalls import PyTgCalls, StreamType
from pytgcalls.exceptions import (
    AlreadyJoinedError,
    NoActiveGroupCall,
    TelegramServerError,
)
from pytgcalls.types import Update
from pytgcalls.types.input_stream import AudioPiped, AudioVideoPiped
from pytgcalls.types.input_stream.quality import HighQualityAudio, MediumQualityVideo
from pytgcalls.types.stream import StreamAudioEnded

import config
from EsproMusic import LOGGER, YouTube, app
from EsproMusic.misc import db
from EsproMusic.utils.database import (
    add_active_chat,
    add_active_video_chat,
    get_lang,
    get_loop,
    group_assistant,
    is_autoend,
    Music_on,
    remove_active_chat,
    remove_active_video_chat,
    set_loop,
)
from EsproMusic.utils.exceptions import AssistantErr
from EsproMusic.utils.formatters import check_duration, seconds_to_min, speed_converter
from EsproMusic.utils.inline.play import stream_markup
from EsproMusic.utils.stream.autoclear import auto_clean
from EsproMusic.utils.thumbnails import get_thumb
from strings import get_string

autoend = {}
counter = {}


async def _clear_(chat_id):
    db[chat_id] = []
    await remove_active_video_chat(chat_id)
    await remove_active_chat(chat_id)


class Call(PyTgCalls):
    def __init__(self):
        self.userbot1 = Client(
            name="EsproAss1",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING1),
        )
        self.one = PyTgCalls(self.userbot1, cache_duration=100)

        self.userbot2 = Client(
            name="EsproAss2",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING2),
        )
        self.two = PyTgCalls(self.userbot2, cache_duration=100)

        self.userbot3 = Client(
            name="EsproAss3",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING3),
        )
        self.three = PyTgCalls(self.userbot3, cache_duration=100)

        self.userbot4 = Client(
            name="EsproAss4",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING4),
        )
        self.four = PyTgCalls(self.userbot4, cache_duration=100)

        self.userbot5 = Client(
            name="EsproAss5",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING5),
        )
        self.five = PyTgCalls(self.userbot5, cache_duration=100)

        self.clients = [
            (config.STRING1, self.one),
            (config.STRING2, self.two),
            (config.STRING3, self.three),
            (config.STRING4, self.four),
            (config.STRING5, self.five),
        ]

    async def pause_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        await assistant.pause_stream(chat_id)

    async def resume_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        await assistant.resume_stream(chat_id)

    async def stop_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        try:
            await _clear_(chat_id)
            await assistant.leave_group_call(chat_id)
        except Exception:
            pass

    async def stop_stream_force(self, chat_id: int):
        for string_cfg, client in self.clients:
            if string_cfg:
                try:
                    await client.leave_group_call(chat_id)
                except Exception:
                    pass
        try:
            await _clear_(chat_id)
        except Exception:
            pass

    async def speedup_stream(self, chat_id: int, file_path, speed, playing):
        assistant = await group_assistant(self, chat_id)
        if str(speed) != "1.0":
            base = os.path.basename(file_path)
            chatdir = os.path.join(os.getcwd(), "playback", str(speed))
            if not os.path.isdir(chatdir):
                os.makedirs(chatdir)
            out = os.path.join(chatdir, base)
            if not os.path.isfile(out):
                vs = 1.0
                if str(speed) == "0.5":
                    vs = 2.0
                elif str(speed) == "0.75":
                    vs = 1.35
                elif str(speed) == "1.5":
                    vs = 0.68
                elif str(speed) == "2.0":
                    vs = 0.5
                proc = await asyncio.create_subprocess_shell(
                    cmd=(
                        f'ffmpeg -i "{file_path}" -filter:v "setpts={vs}*PTS" '
                        f'-filter:a "atempo={speed}" "{out}"'
                    ),
                    stdin=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
        else:
            out = file_path

        dur = await asyncio.get_event_loop().run_in_executor(None, check_duration, out)
        dur = int(dur)
        played, con_seconds = speed_converter(playing[0]["played"], speed)
        duration = seconds_to_min(dur)
        stream = (
            AudioVideoPiped(
                out,
                audio_parameters=HighQualityAudio(),
                video_parameters=MediumQualityVideo(),
                additional_ffmpeg_parameters=f"-ss {played} -to {duration}",
            )
            if playing[0]["streamtype"] == "video"
            else AudioPiped(
                out,
                audio_parameters=HighQualityAudio(),
                additional_ffmpeg_parameters=f"-ss {played} -to {duration}",
            )
        )
        if str(db[chat_id][0]["file"]) == str(file_path):
            await assistant.change_stream(chat_id, stream)
            exis = (playing[0]).get("old_dur")
            if not exis:
                db[chat_id][0]["old_dur"] = db[chat_id][0]["dur"]
                db[chat_id][0]["old_second"] = db[chat_id][0]["seconds"]
            db[chat_id][0]["played"] = con_seconds
            db[chat_id][0]["dur"] = duration
            db[chat_id][0]["seconds"] = dur
            db[chat_id][0]["speed_path"] = out
            db[chat_id][0]["speed"] = speed
        else:
            raise AssistantErr("Umm")

    async def force_stop_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        try:
            check = db.get(chat_id)
            if check:
                check.pop(0)
        except Exception:
            pass
        await remove_active_video_chat(chat_id)
        await remove_active_chat(chat_id)
        try:
            await assistant.leave_group_call(chat_id)
        except Exception:
            pass

    async def skip_stream(
        self,
        chat_id: int,
        link: str,
        video: Union[bool, str] = None,
        image: Union[bool, str] = None,
    ):
        assistant = await group_assistant(self, chat_id)
        if video:
            stream = AudioVideoPiped(
                link,
                audio_parameters=HighQualityAudio(),
                video_parameters=MediumQualityVideo(),
            )
        else:
            stream = AudioPiped(link, audio_parameters=HighQualityAudio())
        await assistant.change_stream(chat_id, stream)

    async def seek_stream(self, chat_id, file_path, to_seek, duration, mode):
        assistant = await group_assistant(self, chat_id)
        stream = (
            AudioVideoPiped(
                file_path,
                audio_parameters=HighQualityAudio(),
                video_parameters=MediumQualityVideo(),
                additional_ffmpeg_parameters=f"-ss {to_seek} -to {duration}",
            )
            if mode == "video"
            else AudioPiped(
                file_path,
                audio_parameters=HighQualityAudio(),
                additional_ffmpeg_parameters=f"-ss {to_seek} -to {duration}",
            )
        )
        await assistant.change_stream(chat_id, stream)

    async def stream_call(self, link):
        assistant = await group_assistant(self, config.LOGGER_ID)
        await assistant.join_group_call(
            config.LOGGER_ID,
            AudioVideoPiped(link),
            stream_type=StreamType().pulse_stream,
        )
        await asyncio.sleep(0.2)
        await assistant.leave_group_call(config.LOGGER_ID)

    async def join_call(
        self,
        chat_id: int,
        original_chat_id: int,
        link,
        video: Union[bool, str] = None,
        image: Union[bool, str] = None,
    ):
        assistant = await group_assistant(self, chat_id)
        language = await get_lang(chat_id)
        _ = get_string(language)
        if video:
            stream = AudioVideoPiped(
                link,
                audio_parameters=HighQualityAudio(),
                video_parameters=MediumQualityVideo(),
            )
        else:
            stream = AudioPiped(link, audio_parameters=HighQualityAudio())

        try:
            await assistant.join_group_call(
                chat_id,
                stream,
                stream_type=StreamType().pulse_stream,
            )
        except NoActiveGroupCall:
            raise AssistantErr(_["call_8"])
        except AlreadyJoinedError:
            raise AssistantErr(_["call_9"])
        except TelegramServerError:
            raise AssistantErr(_["call_10"])

        await add_active_chat(chat_id)
        await Music_on(chat_id)
        if video:
            await add_active_video_chat(chat_id)
        if await is_autoend():
            counter[chat_id] = {}
            users = len(await assistant.get_participants(chat_id))
            if users == 1:
                autoend[chat_id] = datetime.now() + timedelta(minutes=1)

    async def change_stream(self, client, chat_id):
        check = db.get(chat_id)
        if not check:
            await _clear_(chat_id)
            try:
                return await client.leave_group_call(chat_id)
            except Exception:
                return

        loop = await get_loop(chat_id)
        popped = None
        try:
            if loop == 0:
                popped = check.pop(0)
            else:
                popped = check[0]
                loop = loop - 1
                await set_loop(chat_id, loop)

            if popped:
                await auto_clean(popped)

            if not check:
                try:
                    from EsproMusic.plugins.tools.autoplay import try_autoplay

                    if popped and await try_autoplay(chat_id, popped, client):
                        return
                except Exception as e:
                    LOGGER(__name__).error(f"AutoPlay hook error: {e}")
                await _clear_(chat_id)
                return await client.leave_group_call(chat_id)
        except Exception:
            try:
                await _clear_(chat_id)
                return await client.leave_group_call(chat_id)
            except Exception:
                return

        queued = check[0]["file"]
        language = await get_lang(chat_id)
        _ = get_string(language)
        title = (check[0]["title"]).title()
        user = check[0]["by"]
        original_chat_id = check[0]["chat_id"]
        streamtype = check[0]["streamtype"]
        videoid = check[0]["vidid"]
        db[chat_id][0]["played"] = 0
        exis = (check[0]).get("old_dur")
        if exis:
            db[chat_id][0]["dur"] = exis
            db[chat_id][0]["seconds"] = check[0]["old_second"]
            db[chat_id][0]["speed_path"] = None
            db[chat_id][0]["speed"] = 1.0

        video = True if str(streamtype) == "video" else False

        if "live_" in queued:
            n, link = await YouTube.video(videoid, True)
            if n == 0:
                return await app.send_message(original_chat_id, text=_["call_6"])
            stream = (
                AudioVideoPiped(
                    link,
                    audio_parameters=HighQualityAudio(),
                    video_parameters=MediumQualityVideo(),
                )
                if video
                else AudioPiped(link, audio_parameters=HighQualityAudio())
            )
            try:
                await client.change_stream(chat_id, stream)
            except Exception:
                return await app.send_message(original_chat_id, text=_["call_6"])

            img = await get_thumb(videoid)
            button = stream_markup(_, chat_id)
            run = await app.send_photo(
                chat_id=original_chat_id,
                photo=img,
                caption=_["stream_1"].format(
                    f"https://t.me/{app.username}?start=info_{videoid}",
                    title[:23],
                    check[0]["dur"],
                    user,
                ),
                reply_markup=InlineKeyboardMarkup(button),
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"

        elif "vid_" in queued:
            mystic = await app.send_message(original_chat_id, _["call_7"])
            try:
                file_path, direct = await YouTube.download(
                    videoid,
                    mystic,
                    videoid=True,
                    video=True if str(streamtype) == "video" else False,
                )
            except Exception:
                return await mystic.edit_text(
                    _["call_6"], disable_web_page_preview=True
                )

            stream = (
                AudioVideoPiped(
                    file_path,
                    audio_parameters=HighQualityAudio(),
                    video_parameters=MediumQualityVideo(),
                )
                if video
                else AudioPiped(file_path, audio_parameters=HighQualityAudio())
            )
            try:
                await client.change_stream(chat_id, stream)
            except Exception:
                return await app.send_message(original_chat_id, text=_["call_6"])

            img = await get_thumb(videoid)
            button = stream_markup(_, chat_id)
            await mystic.delete()
            run = await app.send_photo(
                chat_id=original_chat_id,
                photo=img,
                caption=_["stream_1"].format(
                    f"https://t.me/{app.username}?start=info_{videoid}",
                    title[:23],
                    check[0]["dur"],
                    user,
                ),
                reply_markup=InlineKeyboardMarkup(button),
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "stream"

        elif "index_" in queued:
            stream = (
                AudioVideoPiped(
                    videoid,
                    audio_parameters=HighQualityAudio(),
                    video_parameters=MediumQualityVideo(),
                )
                if str(streamtype) == "video"
                else AudioPiped(videoid, audio_parameters=HighQualityAudio())
            )
            try:
                await client.change_stream(chat_id, stream)
            except Exception:
                return await app.send_message(original_chat_id, text=_["call_6"])

            button = stream_markup(_, chat_id)
            run = await app.send_photo(
                chat_id=original_chat_id,
                photo=config.STREAM_IMG_URL,
                caption=_["stream_2"].format(user),
                reply_markup=InlineKeyboardMarkup(button),
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"

        else:
            stream = (
                AudioVideoPiped(
                    queued,
                    audio_parameters=HighQualityAudio(),
                    video_parameters=MediumQualityVideo(),
                )
                if video
                else AudioPiped(queued, audio_parameters=HighQualityAudio())
            )
            try:
                await client.change_stream(chat_id, stream)
            except Exception:
                return await app.send_message(original_chat_id, text=_["call_6"])

            if videoid == "telegram":
                button = stream_markup(_, chat_id)
                run = await app.send_photo(
                    chat_id=original_chat_id,
                    photo=config.TELEGRAM_AUDIO_URL
                    if str(streamtype) == "audio"
                    else config.TELEGRAM_VIDEO_URL,
                    caption=_["stream_1"].format(
                        config.SUPPORT_CHAT, title[:23], check[0]["dur"], user
                    ),
                    reply_markup=InlineKeyboardMarkup(button),
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "tg"
            elif videoid == "soundcloud":
                button = stream_markup(_, chat_id)
                run = await app.send_photo(
                    chat_id=original_chat_id,
                    photo=config.SOUNCLOUD_IMG_URL,
                    caption=_["stream_1"].format(
                        config.SUPPORT_CHAT, title[:23], check[0]["dur"], user
                    ),
                    reply_markup=InlineKeyboardMarkup(button),
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "tg"
            else:
                img = await get_thumb(videoid)
                button = stream_markup(_, chat_id)
                run = await app.send_photo(
                    chat_id=original_chat_id,
                    photo=img,
                    caption=_["stream_1"].format(
                        f"https://t.me/{app.username}?start=info_{videoid}",
                        title[:23],
                        check[0]["dur"],
                        user,
                    ),
                    reply_markup=InlineKeyboardMarkup(button),
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "stream"

    async def ping(self):
        pings = []
        for string_cfg, client in self.clients:
            if string_cfg:
                try:
                    pings.append(await client.ping)
                except Exception:
                    pass
        return str(round(sum(pings) / len(pings), 3)) if pings else "0.0"

    async def start(self):
        LOGGER(__name__).info("Starting PyTgCalls Client...\n")
        for string_cfg, client in self.clients:
            if string_cfg:
                await client.start()

    async def decorators(self):
        for string_cfg, client in self.clients:
            if string_cfg:

                @client.on_kicked()
                @client.on_closed_voice_chat()
                @client.on_left()
                async def stream_services_handler(_, chat_id: int):
                    await self.stop_stream(chat_id)

                @client.on_stream_end()
                async def stream_end_handler(c, update: Update):
                    if isinstance(update, StreamAudioEnded):
                        await self.change_stream(c, update.chat_id)


Ritik = Call()
