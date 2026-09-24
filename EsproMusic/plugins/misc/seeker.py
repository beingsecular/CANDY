import asyncio
from EsproMusic.misc import db

async def timer():
    while True:
        await asyncio.sleep(2)
        try:
            chats = list(db.keys())
            for chat_id in chats:
                try:
                    if not db.get(chat_id):
                        continue
                    playing = db[chat_id]
                    if not playing or len(playing) == 0:
                        continue
                    
                    # Safe check for 'seconds' key to prevent KeyError
                    seconds = playing[0].get("seconds")
                    if not seconds:
                        dur_str = playing[0].get("dur", "03:00")
                        try:
                            parts = list(map(int, dur_str.split(":")))
                            if len(parts) == 2:
                                seconds = parts[0] * 60 + parts[1]
                            elif len(parts) == 3:
                                seconds = parts[0] * 3600 + parts[1] * 60 + parts[2]
                            else:
                                seconds = 180
                        except Exception:
                            seconds = 180
                    
                    duration = int(seconds)
                    
                    if "played" not in playing[0]:
                        playing[0]["played"] = 0
                        
                except Exception:
                    pass
        except Exception:
            await asyncio.sleep(3)
