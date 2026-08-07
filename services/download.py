import random
import time
import logging
from pathlib import Path
import yt_dlp

from config import MAX_DURATION_SECONDS
from services.metadata import BASE_HTTP_HEADERS

logger = logging.getLogger(__name__)

def build_download_options(output_dir: str, media_type: str) -> dict:
    base = {
        "outtmpl": str(Path(output_dir) / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "extractor_retries": 3,
        "concurrent_fragment_downloads": 4,
        "extractor_args": {
            "tiktok": {"app_info": [""]},
            "youtube": {"player_client": ["android", "web"]},
        },
        "http_headers": BASE_HTTP_HEADERS,
    }

    if media_type == "mp3":
        return {
            **base,
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "320",
                }
            ],
        }

    if media_type == "m4a":
        return {
            **base,
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "m4a",
                    "preferredquality": "0",
                }
            ],
        }

    if media_type == "video":
        return {
            **base,
            "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "merge_output_format": "mp4",
        }

    if media_type == "ringtone":
        return {
            **base,
            "format": "bestaudio/best",
        }

    raise ValueError("UNSUPPORTED_MEDIA_TYPE")


def download_media_from_info(info: dict, output_dir: str, media_type: str) -> tuple[Path, dict]:
    options = build_download_options(output_dir, media_type)
    last_error = None

    for attempt in range(3):
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                # Lấy lại link gốc để extract mới hoàn toàn, tránh lỗi hết hạn link CDN của TikTok
                url = info.get("webpage_url")
                if not url:
                    raise ValueError("Không tìm thấy webpage_url trong info")
                downloaded_info = ydl.extract_info(url, download=True)
                
            downloaded_files = list(Path(output_dir).glob("*"))
            if not downloaded_files:
                raise FileNotFoundError(f"Không tìm thấy file đầu ra cho {media_type}")

            file_path = sorted([f for f in downloaded_files if not f.name.endswith(".part")], key=lambda f: f.stat().st_size, reverse=True)[0]
            return file_path, downloaded_info

        except yt_dlp.utils.DownloadError as error:
            last_error = error
            logger.warning("Lần tải %s/3 thất bại với %s: %s", attempt + 1, media_type, error)

            for path in Path(output_dir).iterdir():
                if path.is_file():
                    path.unlink(missing_ok=True)

            if attempt < 2:
                time.sleep(2 + random.random())
        except Exception as e:
            raise e
            
    raise last_error
