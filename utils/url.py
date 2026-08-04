import re
import aiohttp
import logging

logger = logging.getLogger(__name__)

def extract_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s]+", text)
    return match.group(0) if match else None

async def expand_tiktok_url(url: str) -> str:
    """Expand shortened TikTok URLs like vt.tiktok.com."""
    if "vt.tiktok.com" not in url and "vm.tiktok.com" not in url:
        return url
        
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, 
                allow_redirects=True, 
                timeout=10, 
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"}
            ) as response:
                return str(response.url)
    except Exception as e:
        logger.warning(f"Could not expand TikTok URL {url}: {e}")
        return url
