import re
from urllib.parse import urlparse

def detect_platform(url: str) -> str:
    domain = urlparse(url).netloc.lower()
    if "youtube.com" in domain or "youtu.be" in domain:
        return "youtube"
    if "tiktok.com" in domain:
        return "tiktok"
    if "facebook.com" in domain or "fb.watch" in domain:
        return "facebook"
    if "instagram.com" in domain:
        return "instagram"
    if "soundcloud.com" in domain:
        return "soundcloud"
    return "other"

def safe_filename(title: str) -> str:
    title = re.sub(r'[\\/*?:"<>|]', "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title[:100]
