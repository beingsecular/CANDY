from pyrogram import filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from youtubesearchpython.__future__ import VideosSearch

from EsproMusic import app
from EsproMusic.utils.database.playlist import (
    add_song_to_playlist,
    create_playlist,
    delete_playlist,
    get_user_playlists,
    remove_song_from_playlist,
)

AWAITING_INPUT = {}

async def show_my_playlists_menu(client, message_or_query):
    user_id = message_or_query.from_user.id
    playlists = await get_user_playlists(user_id)
    
    buttons = []
    if playlists:
        for p_name, songs in playlists.items():
            buttons.append([
                InlineKeyboardButton(f"📁 {p_name} ({len(songs)} songs)", callback_data=f"view_pl:{p_name}"),
                InlineKeyboardButton("▶️ Play", callback_data=f"play_pl_cb:{p_name}")
            ])
            
    buttons.append([InlineKeyboardButton("➕ Create New Playlist", callback_data="ui_create_pl")])
    buttons.append([InlineKeyboardButton("◀️ Back", callback_data="cancel_pl")])
    
    text = "🎵 **My Playlists**\nChoose a playlist to play or manage."
    
    if isinstance(message_or_query, CallbackQuery):
        await message_or_query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await message_or_query.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))


@app.on_message(filters.command(["myplaylist", "playlist"]) & filters.private)
async def my_playlist_cmd(client, message: Message):
    await show_my_playlists_menu(client, message)


@app.on_callback_query(filters.regex("^ui_create_pl$"))
async def prompt_create_pl(client, query: CallbackQuery):
    user_id = query.from_user.id
    AWAITING_INPUT[user_id] = "create_name"
    
    buttons = [
        [InlineKeyboardButton("◀️ Back", callback_data="my_playlists_cb"), InlineKeyboardButton("❌ CANCEL", callback_data="cancel_pl")]
    ]
    await query.message.edit_text(
        "📁 **Create Playlist**\nEnter the name of your playlist.\n\nExample: *Chill Vibes, Study Mix, My Fav Songs...*",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@app.on_message(filters.private & filters.text & ~filters.command(["start", "myplaylist", "playlist", "playplaylist"]))
async def handle_user_text_inputs(client, message: Message):
    user_id = message.from_user.id
    state = AWAITING_INPUT.get(user_id)
    
    if state == "create_name":
        pl_name = message.text.strip()
        success = await create_playlist(user_id, pl_name)
        AWAITING_INPUT[user_id] = None
        
        if not success:
            return await message.reply_text("❌ Ye playlist naam pehle se exist karta hai!")
            
        from EsproMusic.plugins.bot.start import PENDING_ADD_SONG
        videoid = PENDING_ADD_SONG.get(user_id)
        
        if videoid:
            await add_song_to_playlist(user_id, pl_name, f"Song ({videoid})", videoid)
            del PENDING_ADD_SONG[user_id]
            
        buttons = [
            [InlineKeyboardButton("➕ Add Songs", callback_data=f"add_song_prompt:{pl_name}"), InlineKeyboardButton("📖 View Playlist", callback_data=f"view_pl:{pl_name}")],
            [InlineKeyboardButton("◀️ Back", callback_data="my_playlists_cb"), InlineKeyboardButton("❌ Cancel", callback_data="cancel_pl")]
        ]
        await message.reply_text(
            f"✅ **Playlist Created!**\nYour playlist **\"{pl_name}\"** has been created.",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif isinstance(state, str) and state.startswith("add_song_to:"):
        pl_name = state.split("add_song_to:")[1]
        query_text = message.text.strip()
        
        results = VideosSearch(query_text, limit=1)
        res = await results.next()
        if not res["result"]:
            return await message.reply_text("❌ Song nahi mil saka. Phir se sahi naam ya link bhejein.")
            
        song_title = res["result"][0]["title"]
        videoid = res["result"][0]["id"]
        
        await add_song_to_playlist(user_id, pl_name, song_title, videoid)
        AWAITING_INPUT[user_id] = None
        
        buttons = [
            [InlineKeyboardButton("➕ Add More", callback_data=f"add_song_prompt:{pl_name}"), InlineKeyboardButton("📖 View Playlist", callback_data=f"view_pl:{pl_name}")],
            [InlineKeyboardButton("◀️ Back", callback_data=f"view_pl:{pl_name}"), InlineKeyboardButton("❌ Cancel", callback_data="cancel_pl")]
        ]
        await message.reply_text(
            f"✅ **Song Added!**\nAdded to playlist: 📁 **{pl_name}**\n\n🎵 **{song_title}**",
            reply_markup=InlineKeyboardMarkup(buttons)
        )


@app.on_callback_query(filters.regex("^save_to_pl:"))
async def save_to_pl_cb(client, query: CallbackQuery):
    user_id = query.from_user.id
    pl_name = query.data.split(":")[1]
    
    from EsproMusic.plugins.bot.start import PENDING_ADD_SONG
    videoid = PENDING_ADD_SONG.get(user_id)
    
    if not videoid:
        return await query.answer("❌ Request expire ho gayi hai. Group se dobara try karein.", show_alert=True)
        
    res = await add_song_to_playlist(user_id, pl_name, f"Track {videoid}", videoid)
    if res == "duplicate":
        await query.message.edit_text(f"⚠️ Ye song pehle se **{pl_name}** me maujood hai!")
    else:
        await query.message.edit_text(f"✅ Song **{pl_name}** playlist me successfully add ho gaya!")
        
    if user_id in PENDING_ADD_SONG:
        del PENDING_ADD_SONG[user_id]


@app.on_callback_query(filters.regex("^add_song_prompt:"))
async def prompt_add_song(client, query: CallbackQuery):
    user_id = query.from_user.id
    pl_name = query.data.split(":")[1]
    AWAITING_INPUT[user_id] = f"add_song_to:{pl_name}"
    
    buttons = [
        [InlineKeyboardButton("◀️ Back", callback_data=f"view_pl:{pl_name}")],
        [InlineKeyboardButton("❌ CANCEL", callback_data="cancel_pl")]
    ]
    await query.message.edit_text(
        f"🎵 **Add Songs**\nSend me the song name, link or search query.\n\nExample: *\"Kesariya\"* or *\"https://youtu.be/...\"*",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


@app.on_callback_query(filters.regex("^view_pl:"))
async def view_pl_cb(client, query: CallbackQuery):
    user_id = query.from_user.id
    pl_name = query.data.split(":")[1]
    playlists = await get_user_playlists(user_id)
    
    songs = playlists.get(pl_name, [])
    
    text = f"📁 **Playlist Menu ({pl_name})**\n{len(songs)} songs • Created by you\n\n"
    for idx, song in enumerate(songs, 1):
        text += f"{idx}. {song['title']}\n"
        
    buttons = [
        [InlineKeyboardButton("▶️ Play Playlist", callback_data=f"play_pl_cb:{pl_name}")],
        [InlineKeyboardButton("➕ Add Song", callback_data=f"add_song_prompt:{pl_name}"), InlineKeyboardButton("🗑️ Delete Playlist", callback_data=f"del_pl:{pl_name}")],
        [InlineKeyboardButton("◀️ Back", callback_data="my_playlists_cb")]
    ]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))


@app.on_callback_query(filters.regex("^del_pl:"))
async def del_pl_cb(client, query: CallbackQuery):
    user_id = query.from_user.id
    pl_name = query.data.split(":")[1]
    
    await delete_playlist(user_id, pl_name)
    await query.answer("🗑️ Playlist deleted!", show_alert=True)
    await show_my_playlists_menu(client, query)


@app.on_callback_query(filters.regex("^my_playlists_cb$"))
async def back_my_pl(client, query: CallbackQuery):
    await show_my_playlists_menu(client, query)


@app.on_callback_query(filters.regex("^cancel_pl$"))
async def cancel_pl_cb(client, query: CallbackQuery):
    await query.message.delete()


@app.on_message(filters.command(["playplaylist", "playpl"]))
@app.on_callback_query(filters.regex("^play_pl_cb:"))
async def play_playlist_stream(client, message_or_query):
    is_cb = isinstance(message_or_query, CallbackQuery)
    user_id = message_or_query.from_user.id
    
    if is_cb:
        pl_name = message_or_query.data.split(":")[1]
        chat_id = message_or_query.message.chat.id
    else:
        if len(message_or_query.command) < 2:
            return await message_or_query.reply_text("Usage: `/playplaylist <playlist_name>`")
        pl_name = message_or_query.text.split(None, 1)[1].strip()
        chat_id = message_or_query.chat.id
        
    playlists = await get_user_playlists(user_id)
    if pl_name not in playlists or not playlists[pl_name]:
        msg = "❌ Ye playlist nahi mili ya isme koi song nahi hai."
        return await message_or_query.answer(msg, show_alert=True) if is_cb else await message_or_query.reply_text(msg)
        
    songs = playlists[pl_name]
    
    msg_text = f"▶️ Playlist **{pl_name}** se **{len(songs)} songs** queue me stream hona shuru ho rahe hain..."
    if is_cb:
        await message_or_query.message.edit_text(msg_text)
    else:
        await message_or_query.reply_text(msg_text)

    # EsproMusic play stream queue call
    from EsproMusic.plugins.play.play import stream
    for song in songs:
        try:
            await stream(
                client,
                message_or_query.message if is_cb else message_or_query,
                user_id,
                song["videoid"],
                chat_id,
                user_id,
                "a",
                None,
                None,
            )
        except Exception:
            pass
