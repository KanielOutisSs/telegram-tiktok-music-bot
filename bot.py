import asyncio
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from aiohttp import web
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PORT = int(os.getenv("PORT", "10000"))

MAX_OUTPUT_MB = int(os.getenv("MAX_OUTPUT_MB", "45"))
MAX_OUTPUT_BYTES = MAX_OUTPUT_MB * 1024 * 1024
MAX_DURATION_SECONDS = 10 * 60

DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


def extract_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s]+", text)

    if not match:
        return None

    return match.group(0).rstrip(".,);]}>\"'")


def is_tiktok_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()

        return (
            parsed.scheme in {"http", "https"}
            and (
                hostname == "tiktok.com"
                or hostname.endswith(".tiktok.com")
            )
        )
    except ValueError:
        return False


def safe_filename(title: str) -> str:
    title = re.sub(r'[\\/:*?"<>|]', "", title)
    title = re.sub(r"\s+", " ", title).strip()

    return title[:80] or "tiktok-audio"


def download_audio(url: str, output_dir: str) -> tuple[Path, dict]:
    output_template = str(Path(output_dir) / "%(id)s.%(ext)s")

    options = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

        duration = info.get("duration")

        if duration and duration > MAX_DURATION_SECONDS:
            raise ValueError("VIDEO_TOO_LONG")

        info = ydl.extract_info(url, download=True)

    mp3_files = list(Path(output_dir).glob("*.mp3"))

    if not mp3_files:
        raise FileNotFoundError("Không tìm thấy file MP3 đầu ra.")

    return mp3_files[0], info


async def start_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.effective_message.reply_text(
        "🎵 Gửi link TikTok, tôi sẽ tách âm thanh và trả lại MP3."
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    await update.effective_message.reply_text(
        "Cách dùng:\n"
        "1. Sao chép link video TikTok công khai.\n"
        "2. Gửi link vào đây.\n"
        "3. Chờ bot trả lại file MP3.\n\n"
        "Chỉ tải nội dung bạn sở hữu hoặc được phép sử dụng."
    )


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.effective_message

    if not message or not message.text:
        return

    url = extract_url(message.text)

    if not url:
        await message.reply_text("❌ Hãy gửi một link TikTok.")
        return

    if not is_tiktok_url(url):
        await message.reply_text("❌ Hiện bot chỉ hỗ trợ link TikTok.")
        return

    status = await message.reply_text("⏳ Đang tải và tách âm thanh...")
    temp_dir = tempfile.mkdtemp(prefix="tiktok_audio_")

    try:
        await context.bot.send_chat_action(
            chat_id=message.chat_id,
            action=ChatAction.UPLOAD_AUDIO,
        )

        async with DOWNLOAD_SEMAPHORE:
            mp3_path, info = await asyncio.wait_for(
                asyncio.to_thread(download_audio, url, temp_dir),
                timeout=180,
            )

        if mp3_path.stat().st_size > MAX_OUTPUT_BYTES:
            await status.edit_text(
                f"❌ File MP3 vượt quá giới hạn {MAX_OUTPUT_MB} MB."
            )
            return

        title = safe_filename(info.get("title") or "TikTok Audio")
        uploader = str(
            info.get("uploader")
            or info.get("creator")
            or "TikTok"
        )[:64]

        duration = info.get("duration")

        with mp3_path.open("rb") as audio_file:
            await message.reply_audio(
                audio=audio_file,
                title=title,
                performer=uploader,
                duration=int(duration) if duration else None,
                filename=f"{title}.mp3",
                caption="🎵 Đã tách âm thanh thành công.",
            )

        await status.delete()

    except asyncio.TimeoutError:
        await status.edit_text(
            "❌ Video xử lý quá lâu. Hãy thử video ngắn hơn."
        )

    except ValueError as error:
        if str(error) == "VIDEO_TOO_LONG":
            await status.edit_text(
                "❌ Video dài quá 10 phút nên bot không xử lý."
            )
        else:
            logger.exception("Value error")
            await status.edit_text("❌ Dữ liệu video không hợp lệ.")

    except yt_dlp.utils.DownloadError:
        logger.exception("yt-dlp download error")
        await status.edit_text(
            "❌ Không tải được video. Video có thể riêng tư, "
            "đã bị xóa hoặc TikTok đang chặn yêu cầu."
        )

    except Exception:
        logger.exception("Unexpected processing error")
        await status.edit_text("❌ Có lỗi xảy ra khi xử lý video.")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "ok",
            "service": "telegram-tiktok-music-bot",
        }
    )


async def start_web_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", health_handler)
    app.router.add_get("/health", health_handler)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        host="0.0.0.0",
        port=PORT,
    )

    await site.start()
    logger.info("Health server running on port %s", PORT)

    return runner


async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Chưa đặt biến môi trường BOT_TOKEN.")

    telegram_app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(4)
        .build()
    )

    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("help", help_command))
    telegram_app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    web_runner = await start_web_server()

    try:
        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.updater.start_polling(
            drop_pending_updates=True
        )

        logger.info("Telegram bot is running.")

        await asyncio.Event().wait()

    finally:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()
        await web_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
