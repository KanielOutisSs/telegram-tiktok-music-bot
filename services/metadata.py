import time
import yt_dlp

BASE_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

import urllib.request
import urllib.parse
import json

def fetch_tikwm(url: str) -> dict:
    api_url = "https://www.tikwm.com/api/"
    data = urllib.parse.urlencode({"url": url, "hd": 1}).encode('utf-8')
    req = urllib.request.Request(api_url, data=data, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as response:
        res = json.loads(response.read().decode())
        if res.get('code') == 0 and 'data' in res:
            d = res['data']
            images = d.get('images', [])
            if images:
                return {
                    "is_photo_slide": True,
                    "title": d.get('title') or "Album Ảnh TikTok",
                    "uploader": d.get('author', {}).get('nickname') or "Không rõ",
                    "images": images,
                    "music": d.get('music'),
                    "webpage_url": url,
                    "duration": d.get('music_info', {}).get('duration') or 0
                }
    return None

def extract_metadata(url: str) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "socket_timeout": 30,
        "retries": 3,
        "extractor_retries": 3,
        "cachedir": False,
        "extractor_args": {
            "tiktok": {"app_info": [""]},
            "youtube": {"player_client": ["android", "web"]},
        },
        "http_headers": BASE_HTTP_HEADERS,
    }

    last_error = None
    info = None

    for _ in range(3):
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
                break
        except Exception as e:
            last_error = e
            time.sleep(1)

    if not info:
        if "tiktok.com" in url:
            try:
                tikwm_info = fetch_tikwm(url)
                if tikwm_info:
                    return tikwm_info
            except Exception:
                pass
                
        if last_error:
            raise last_error
        raise RuntimeError("Failed to extract metadata")

    return {
        "id": str(info.get("id") or ""),
        "title": info.get("title") or "Không có tiêu đề",
        "uploader": (
            info.get("uploader")
            or info.get("channel")
            or info.get("creator")
            or "Không rõ"
        ),
        "duration": int(info.get("duration") or 0),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,
    }
