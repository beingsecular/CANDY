#
# Copyright (C) 2021-2022 by TheAloneteam@Github, < https://github.com/TheAloneTeam >.
#
# All rights reserved.

import asyncio
import os
import re
import json
import glob
import random
import logging
import urllib.parse
import time
from typing import Union

import httpx
import yt_dlp

# Use environment variables for configuration
API_URL = os.getenv("API_URL", "https://api.riteshyt.in").rstrip("/")
API_KEY = os.getenv("API_KEY", "riteshfree3bd8a4d5fbfb23599db12fd9")

# --- Dynamic Compatibility / Fallbacks for Environment Safety ---
try:
    from pyrogram.enums import MessageEntityType
    from pyrogram.types import Message
except ImportError:
    class MessageEntityType:
        URL = "url"
        TEXT_LINK = "text_link"
    class Message:
        pass

try:
    from youtubesearchpython.__future__ import VideosSearch, Playlist
except ImportError:
    VideosSearch = None
    Playlist = None

try:
    from AloneMusic.utils.database import is_on_off
except ImportError:
    async def is_on_off(*args, **kwargs):
        return True

try:
    from AloneMusic.utils.formatters import time_to_seconds
except ImportError:
    def time_to_seconds(time_str: str) -> int:
        if not time_str:
            return 0
        try:
            parts = list(map(int, time_str.split(":")))
            if len(parts) == 3:
                return parts[0] * 3600 + parts[1] * 60 + parts[2]
            elif len(parts) == 2:
                return parts[0] * 60 + parts[1]
            elif len(parts) == 1:
                return parts[0]
        except Exception:
            pass
        return 0


# --- Original Local Helper Functions (Preserved for compatibility and fallback) ---

async def check_file_size(link):
    async def get_format_info(link):
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "-J",
            link,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            print(f'Error:\n{stderr.decode()}')
            return None
        return json.loads(stdout.decode())

    def parse_size(formats):
        total_size = 0
        for format in formats:
            if 'filesize' in format:
                total_size += format['filesize']
        return total_size

    info = await get_format_info(link)
    if info is None:
        return None

    formats = info.get('formats', [])
    if not formats:
        print("No formats found.")
        return None

    total_size = parse_size(formats)
    return total_size


# --- Utility Functions ---

def extract_vidid(query: str) -> str:
    if not query:
        return None
    if re.match(r"^[a-zA-Z0-9_-]{11}$", query):
        return query
    regex = r"(?:youtube\.com\/(?:[^\/]+\/.+\/|(?:v|e(?:mbed)?)\/|shorts\/|.*[?&]v=)|youtu\.be\/)([^\"&?\/\s]{11})"
    match = re.search(regex, query)
    return match.group(1) if match else None


async def download_assistant(query: str, dl_type: str) -> str:
    """Helper to get stream URL from the API"""
    safe_query = urllib.parse.quote(query)
    ext = "mp3" if dl_type == "audio" else "mp4"
    if API_KEY:
        url = f"{API_URL}/downloads/{API_KEY}/{safe_query}.{ext}"
    else:
        url = f"{API_URL}/downloads/stream?query={safe_query}&dl_type={dl_type}"
    return url


class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self._recent_prefetches = {} # vidid -> timestamp
        self.regex = r"(?:youtube\.com|youtu\.be)"
        self.status = "https://www.youtube.com/oembed?url="
        self.listbase = "https://youtube.com/playlist?list="
        self.reg = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
        self._client = None

    async def get_client(self):
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0), follow_redirects=True)
        return self._client

    async def exists(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        return bool(re.search(self.regex, link))

    async def url(self, message_1: Message) -> Union[str, None]:
        messages = [message_1]
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)
        for message in messages:
            if getattr(message, "entities", None):
                for entity in message.entities:
                    if entity.type == MessageEntityType.URL:
                        text = message.text or message.caption
                        return text[entity.offset: entity.offset + entity.length]
            elif getattr(message, "caption_entities", None):
                for entity in message.caption_entities:
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
        return None

    def _clean_link(self, link: str):
        if not link:
            return ""
        link = str(link)
        if "&" in link:
            link = link.split("&")[0]
        if "?si=" in link:
            link = link.split("?si=")[0]
        elif "&si=" in link:
            link = link.split("&si=")[0]
        return link

    async def _fetch_details(self, link: str):
        link = self._clean_link(link)
        client = await self.get_client()
        params = {"link": link}
        if API_KEY:
            params["api_key"] = API_KEY
        try:
            response = await client.get(f"{API_URL}/details", params=params)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logging.warning(f"Error fetching details from API: {e}")
        return None

    async def details(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link = self._clean_link(link)

        # API first
        if API_URL:
            data = await self._fetch_details(link)
            if data:
                return (
                    data.get("title"),
                    data.get("duration_min"),
                    data.get("duration_sec", 0),
                    data.get("thumbnail"),
                    data.get("vidid")
                )

        # Fallback to local
        if VideosSearch:
            try:
                results = VideosSearch(link, limit=1)
                res = await results.next()
                if res and res.get("result"):
                    result = res["result"][0]
                    title = result["title"]
                    duration_min = result["duration"]
                    thumbnail = result["thumbnails"][0]["url"].split("?")[0]
                    vidid = result["id"]
                    duration_sec = int(time_to_seconds(duration_min)) if duration_min and duration_min != "None" else 0
                    return title, duration_min, duration_sec, thumbnail, vidid
            except Exception as e:
                logging.warning(f"Local VideosSearch fallback failed in details: {e}")
        return None, None, 0, None, None

    async def title(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link = self._clean_link(link)

        # API first
        if API_URL:
            data = await self._fetch_details(link)
            if data and data.get("title"):
                return data["title"]

        # Fallback to local
        if VideosSearch:
            try:
                results = VideosSearch(link, limit=1)
                res = await results.next()
                if res and res.get("result"):
                    return res["result"][0]["title"]
            except Exception as e:
                logging.warning(f"Local VideosSearch fallback failed in title: {e}")
        return None

    async def duration(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link = self._clean_link(link)

        # API first
        if API_URL:
            data = await self._fetch_details(link)
            if data and data.get("duration_min"):
                return data["duration_min"]

        # Fallback to local
        if VideosSearch:
            try:
                results = VideosSearch(link, limit=1)
                res = await results.next()
                if res and res.get("result"):
                    return res["result"][0]["duration"]
            except Exception as e:
                logging.warning(f"Local VideosSearch fallback failed in duration: {e}")
        return None

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link = self._clean_link(link)

        # API first
        if API_URL:
            data = await self._fetch_details(link)
            if data and data.get("thumbnail"):
                return data["thumbnail"]

        # Fallback to local
        if VideosSearch:
            try:
                results = VideosSearch(link, limit=1)
                res = await results.next()
                if res and res.get("result"):
                    return res["result"][0]["thumbnails"][0]["url"].split("?")[0]
            except Exception as e:
                logging.warning(f"Local VideosSearch fallback failed in thumbnail: {e}")
        return None

    async def video(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link = self._clean_link(link)

        # Download video locally from API (safe against NoAudioSourceFound)
        try:
            video_id = extract_vidid(link) or link
            fpath = f"downloads/{video_id}.mp4"

            # Use download method to download locally
            res = await self.download(link, None, video=True)
            if res and isinstance(res, tuple) and res[0]:
                return 1, res[0]
        except Exception as e:
            logging.warning(f"Downloading API video locally failed: {e}")

        # Fallback to local yt-dlp -g
        try:
            cmd = ["yt-dlp", "-g", "-f", "best[height<=?720][width<=?1280]", link]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if stdout:
                return 1, stdout.decode().split("\n")[0]
            else:
                return 0, stderr.decode()
        except Exception as e:
            return 0, str(e)

    async def playlist(self, link, limit, user_id, videoid: Union[bool, str] = None):
        if videoid:
            link = self.listbase + link
        link = self._clean_link(link)

        client = await self.get_client()
        params = {"link": link, "limit": limit}
        if API_KEY:
            params["api_key"] = API_KEY
        try:
            response = await client.get(f"{API_URL}/playlist", params=params)
            if response.status_code == 200:
                data = response.json()
                return data.get("videos")
            else:
                LOGGER(__name__).error(f"API Playlist Error ({response.status_code}): {response.text}")
        except Exception as e:
            LOGGER(__name__).error(f"Error fetching playlist from API: {e}")
        return None

    async def track(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link = self._clean_link(link)

        # API first
        if API_URL:
            data = await self._fetch_details(link)
            if data:
                track_details = {
                    "title": data.get("title"),
                    "link": data.get("link"),
                    "vidid": data.get("vidid"),
                    "duration_min": data.get("duration_min"),
                    "thumb": data.get("thumbnail"),
                }
                return track_details, data.get("vidid")

        # Fallback to local
        if VideosSearch:
            try:
                results = VideosSearch(link, limit=1)
                res = await results.next()
                if res and res.get("result"):
                    result = res["result"][0]
                    track_details = {
                        "title": result["title"],
                        "link": result["link"],
                        "vidid": result["id"],
                        "duration_min": result["duration"],
                        "thumb": result["thumbnails"][0]["url"].split("?")[0],
                    }
                    return track_details, result["id"]
            except Exception as e:
                logging.warning(f"Local track fallback failed: {e}")
        return None, None

    async def formats(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link = self._clean_link(link)

        # API first
        if API_URL:
            client = await self.get_client()
            params = {"link": link}
            if API_KEY:
                params["api_key"] = API_KEY
            try:
                response = await client.get(f"{API_URL}/formats", params=params)
                if response.status_code == 200:
                    data = response.json()
                    formats = data.get("formats", [])
                    for f in formats:
                        f["yturl"] = link
                    return formats, link
            except Exception as e:
                logging.warning(f"Error fetching formats from API: {e}")

        # Local formats extraction
        def _extract():
            ytdl_opts = {"quiet": True}
            ydl = yt_dlp.YoutubeDL(ytdl_opts)
            with ydl:
                return ydl.extract_info(link, download=False)

        try:
            r = await asyncio.to_thread(_extract)
            formats_available = []
            for format in r.get("formats", []):
                try:
                    if "dash" not in str(format.get("format", "")).lower():
                        formats_available.append(
                            {
                                "format": format.get("format"),
                                "filesize": format.get("filesize"),
                                "format_id": format.get("format_id"),
                                "ext": format.get("ext"),
                                "format_note": format.get("format_note"),
                                "yturl": link,
                            }
                        )
                except Exception:
                    continue
            return formats_available, link
        except Exception as e:
            logging.warning(f"Formats extraction failed: {e}")
            return [], link

    async def slider(self, link: str, query_type: int, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        link = self._clean_link(link)

        # API first
        if API_URL:
            client = await self.get_client()
            params = {"query": link, "limit": 10}
            if API_KEY:
                params["api_key"] = API_KEY
            try:
                response = await client.get(f"{API_URL}/search", params=params)
                if response.status_code == 200:
                    result_data = response.json()
                    result = result_data.get("result", [])
                    if result and len(result) > query_type:
                        target = result[query_type]
                        title = target["title"]
                        duration_min = target["duration"]
                        vidid = target["id"]
                        thumbnail = target["thumbnails"][0]["url"].split("?")[0] if target.get("thumbnails") else None
                        return title, duration_min, thumbnail, vidid
            except Exception as e:
                logging.warning(f"Error in slider/search from API: {e}")

        # Fallback to local VideosSearch
        if VideosSearch:
            try:
                a = VideosSearch(link, limit=10)
                res = await a.next()
                result = res.get("result")
                if result and len(result) > query_type:
                    title = result[query_type]["title"]
                    duration_min = result[query_type]["duration"]
                    vidid = result[query_type]["id"]
                    thumbnail = result[query_type]["thumbnails"][0]["url"].split("?")[0]
                    return title, duration_min, thumbnail, vidid
            except Exception as e:
                logging.warning(f"Local slider fallback failed: {e}")
        return None, None, None, None

    async def prefetch(self, link: str, video: bool = False):
        """Triggers background pre-fetching on the API"""
        if not API_URL:
            return False
        dl_type = "video" if video else "audio"
        link = self._clean_link(link)

        # Avoid redundant prefetches within 30 seconds
        now = time.time()
        vidid = extract_vidid(link) or link

        cache_key = f"{vidid}_{dl_type}"
        if cache_key in self._recent_prefetches:
            if now - self._recent_prefetches[cache_key] < 30:
                return True

        self._recent_prefetches[cache_key] = now

        # Cleanup old prefetches (keep cache small)
        if len(self._recent_prefetches) > 100:
            self._recent_prefetches = {k: v for k, v in self._recent_prefetches.items() if now - v < 300}

        client = await self.get_client()
        params = {"query": link, "dl_type": dl_type, "prefetch": "true"}
        if API_KEY:
            params["api_key"] = API_KEY
        try:
            await client.get(f"{API_URL}/download", params=params)
            return True
        except Exception as e:
            logging.warning(f"Prefetch failed for {link}: {e}")
        return False

    async def prefetch_queue(self, queries: list, video: bool = False):
        """Triggers bulk background pre-fetching on the API for a queue"""
        if not API_URL or not queries:
            return False
        dl_type = "video" if video else "audio"
        client = await self.get_client()
        payload = {"queries": queries, "dl_type": dl_type}
        params = {}
        if API_KEY:
            params["api_key"] = API_KEY

        try:
            await client.post(f"{API_URL}/prefetch_bulk", json=payload, params=params)
            return True
        except Exception as e:
            logging.warning(f"Bulk prefetch failed: {e}")
        return False

    async def download(
        self,
        link: str,
        mystic,
        video: Union[bool, str] = None,
        videoid: Union[bool, str] = None,
        songaudio: Union[bool, str] = None,
        songvideo: Union[bool, str] = None,
        format_id: Union[bool, str] = None,
        title: Union[bool, str] = None,
    ) -> Union[str, tuple]:
        if videoid:
            link = self.base + link

        # Helper to download from API
        async def download_from_api(query_link: str, dl_type: str, filepath: str) -> bool:
            if not API_URL:
                return False
            vidid_extracted = extract_vidid(query_link) or query_link
            params = {"query": vidid_extracted, "dl_type": dl_type}
            if API_KEY:
                params["api_key"] = API_KEY
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            try:
                # IMPORTANT: Use follow_redirects=True to handle API 307 redirects to stream URLs!
                async with httpx.AsyncClient(timeout=600.0, follow_redirects=True) as client:
                    async with client.stream("GET", f"{API_URL}/download", params=params) as resp:
                        if resp.status_code != 200:
                            return False
                        with open(filepath, "wb") as f:
                            async for chunk in resp.aiter_bytes(131072):
                                f.write(chunk)
                return os.path.exists(filepath) and os.path.getsize(filepath) > 0
            except Exception as e:
                logging.warning(f"API download failed for {vidid_extracted}: {e}")
                if os.path.exists(filepath):
                    try: os.remove(filepath)
                    except Exception: pass
                return False

        # API-first Direct File Downloading System (Prevents NoAudioSourceFound)
        if API_URL:
            dl_type = "video" if (video or songvideo) else "audio"
            link = self._clean_link(link)
            vidid_extracted = extract_vidid(link) or link
            ext = "mp4" if dl_type == "video" else "mp3"

            if songvideo:
                fpath = f"downloads/{title}.mp4"
                success = await download_from_api(link, "video", fpath)
                if success:
                    return fpath
            elif songaudio:
                fpath = f"downloads/{title}.mp3"
                success = await download_from_api(link, "audio", fpath)
                if success:
                    return fpath
            else:
                # Play download: Actually download locally (zero-latency, 100% stable, resolves NoAudioSourceFound)
                fpath = f"downloads/{vidid_extracted}.{ext}"
                # Background prefetch to warm cache
                asyncio.create_task(self.prefetch(link, video=bool(dl_type == "video")))
                success = await download_from_api(link, dl_type, fpath)
                if success:
                    return fpath, True

        # Local Fallbacks (user's original implementation)
        loop = asyncio.get_running_loop()
        def audio_dl():
            ydl_optssx = {
                "format": "bestaudio/best",
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            info = x.extract_info(link, False)
            xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
            if os.path.exists(xyz):
                return xyz
            x.download([link])
            return xyz

        def video_dl():
            ydl_optssx = {
                "format": "(bestvideo[height<=?720][width<=?1280][ext=mp4])+(bestaudio[ext=m4a])",
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            info = x.extract_info(link, False)
            xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
            if os.path.exists(xyz):
                return xyz
            x.download([link])
            return xyz

        def song_video_dl():
            formats = f"{format_id}+140"
            fpath = f"downloads/{title}"
            ydl_optssx = {
                "format": formats,
                "outtmpl": fpath,
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "prefer_ffmpeg": True,
                "merge_output_format": "mp4",
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            x.download([link])

        def song_audio_dl():
            fpath = f"downloads/{title}.%(ext)s"
            ydl_optssx = {
                "format": format_id,
                "outtmpl": fpath,
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "prefer_ffmpeg": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            x.download([link])

        if songvideo:
            await loop.run_in_executor(None, song_video_dl)
            fpath = f"downloads/{title}.mp4"
            return fpath
        elif songaudio:
            await loop.run_in_executor(None, song_audio_dl)
            fpath = f"downloads/{title}.mp3"
            return fpath
        elif video:
            if await is_on_off(1):
                direct = True
                downloaded_file = await loop.run_in_executor(None, video_dl)
            else:
                proc = await asyncio.create_subprocess_exec(
                    "yt-dlp",
                    "-g",
                    "-f",
                    "best[height<=?720][width<=?1280]",
                    f"{link}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if stdout:
                    downloaded_file = stdout.decode().split("\n")[0]
                    direct = False
                else:
                   file_size = await check_file_size(link)
                   if not file_size:
                     print("None file Size")
                     return
                   total_size_mb = file_size / (1024 * 1024)
                   if total_size_mb > 250:
                     print(f"File size {total_size_mb:.2f} MB exceeds the 100MB limit.")
                     return None
                   direct = True
                   downloaded_file = await loop.run_in_executor(None, video_dl)
            return downloaded_file, direct
        else:
            direct = True
            downloaded_file = await loop.run_in_executor(None, audio_dl)
            return downloaded_file, direct

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()


YouTube = YouTubeAPI()
