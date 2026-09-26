import asyncio
import time

from EsproMusic.misc import db
from EsproMusic.utils.formatters import seconds_to_min, time_to_seconds, dynamic_bar


async def progress_updater(_, chat_id, message, link, title, duration_min, interval=8):
    try:
        dur_sec = time_to_seconds(duration_min)
    except Exception:
        return
    if dur_sec <= 0:
        return

    while True:
        await asyncio.sleep(interval)

        track = db.get(chat_id)
        if not track:
            return
        current = track[0]

        if current.get("mystic") != message:
            return  # song change ho chuka, purana task band

        start_time = current.get("start_time")
        if not start_time:
            return

        played = int(time.time() - start_time)
        if played >= dur_sec:
            return

        bar = dynamic_bar(played, dur_sec)
        played_str = seconds_to_min(played)

        try:
            await message.edit_caption(
                caption=_["stream_1"].format(link, title[:23], duration_min, bar, played_str),
                reply_markup=message.reply_markup,
            )
        except Exception:
            return
