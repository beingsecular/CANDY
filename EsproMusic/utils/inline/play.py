from pyrogram.types import InlineKeyboardButton
from EsproMusic import app


def _autoplay_label(chat_id) -> str:
    try:
        from EsproMusic.plugins.tools.autoplay import autoplay_label
        label = autoplay_label(chat_id)
        if "OFF" in label:
            return "🔄 AutoPlay: OFF"
        return "🔄 AutoPlay: ON"
    except Exception:
        return "🔄 AutoPlay: OFF"


def track_markup(_, videoid, user_id, channel, fplay):
    buttons = [
        [
            InlineKeyboardButton(
                text=_["P_B_1"],
                callback_data=f"MusicStream {videoid}|{user_id}|a|{channel}|{fplay}",
            ),
            InlineKeyboardButton(
                text=_["P_B_2"],
                callback_data=f"MusicStream {videoid}|{user_id}|v|{channel}|{fplay}",
            ),
        ],
        [
            InlineKeyboardButton(
                text=_["CLOSE_BUTTON"],
                callback_data=f"forceclose {videoid}|{user_id}",
            )
        ],
    ]
    return buttons


def stream_markup_timer(_, chat_id, played, dur, videoid=""):
    buttons = [
        [
            InlineKeyboardButton(
                text=_autoplay_label(chat_id),
                callback_data=f"ADMIN AutoPlay|{chat_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="➕ Playlist",
                url=f"https://t.me/{app.username}?start=my_playlists"
            ),
            InlineKeyboardButton(
                text="🎵 Add Song",
                url=f"https://t.me/{app.username}?start=addpl_{videoid}" if videoid else f"https://t.me/{app.username}?start=my_playlists"
            )
        ],
        [
            InlineKeyboardButton(
                text="✖ Close",
                callback_data=f"ADMIN Close|{chat_id}"
            )
        ],
    ]
    return buttons


def stream_markup(_, chat_id, videoid=""):
    ap_text = _autoplay_label(chat_id)
    buttons = [
        [
            InlineKeyboardButton(
                text=ap_text,
                callback_data=f"ADMIN AutoPlay|{chat_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="➕ Playlist",
                url=f"https://t.me/{app.username}?start=my_playlists"
            ),
            InlineKeyboardButton(
                text="🎵 Add Song",
                url=f"https://t.me/{app.username}?start=addpl_{videoid}" if videoid else f"https://t.me/{app.username}?start=my_playlists"
            )
        ],
        [
            InlineKeyboardButton(
                text="✖ Close",
                callback_data=f"ADMIN Close|{chat_id}"
            )
        ],
    ]
    return buttons


def playlist_markup(_, videoid, user_id, ptype, channel, fplay):
    buttons = [
        [
            InlineKeyboardButton(
                text=_["P_B_1"],
                callback_data=f"RitikPlaylists {videoid}|{user_id}|{ptype}|a|{channel}|{fplay}",
            ),
            InlineKeyboardButton(
                text=_["P_B_2"],
                callback_data=f"RitikPlaylists {videoid}|{user_id}|{ptype}|v|{channel}|{fplay}",
            ),
        ],
        [
            InlineKeyboardButton(
                text=_["CLOSE_BUTTON"],
                callback_data=f"forceclose {videoid}|{user_id}",
            ),
        ],
    ]
    return buttons


def livestream_markup(_, videoid, user_id, mode, channel, fplay):
    buttons = [
        [
            InlineKeyboardButton(
                text=_["P_B_3"],
                callback_data=f"LiveStream {videoid}|{user_id}|{mode}|{channel}|{fplay}",
            ),
        ],
        [
            InlineKeyboardButton(
                text=_["CLOSE_BUTTON"],
                callback_data=f"forceclose {videoid}|{user_id}",
            ),
        ],
    ]
    return buttons


def slider_markup(_, videoid, user_id, query, query_type, channel, fplay):
    query = f"{query[:20]}"
    buttons = [
        [
            InlineKeyboardButton(
                text=_["P_B_1"],
                callback_data=f"MusicStream {videoid}|{user_id}|a|{channel}|{fplay}",
            ),
            InlineKeyboardButton(
                text=_["P_B_2"],
                callback_data=f"MusicStream {videoid}|{user_id}|v|{channel}|{fplay}",
            ),
        ],
        [
            InlineKeyboardButton(
                text="◁",
                callback_data=f"slider B|{query_type}|{query}|{user_id}|{channel}|{fplay}",
            ),
            InlineKeyboardButton(
                text=_["CLOSE_BUTTON"],
                callback_data=f"forceclose {query}|{user_id}",
            ),
            InlineKeyboardButton(
                text="▷",
                callback_data=f"slider F|{query_type}|{query}|{user_id}|{channel}|{fplay}",
            ),
        ],
    ]
    return buttons
