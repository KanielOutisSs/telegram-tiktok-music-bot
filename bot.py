import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PORT = int(os.getenv("PORT", "10000"))

MAX_OUTPUT_MB = int(os.getenv("MAX_OUTPUT_MB", "45"))
MAX_OUTPUT_BYTES = MAX_OUTPUT_MB * 1024 * 1024
MAX_DURATION_SECONDS = 15 * 60  # Raised to 15 mins for more media types

DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- RATE LIMITING ---
user_limits = {}

def check_rate_limit(user_id: int) -> tuple[bool, str]:
    today = date.today()
    now = time.time()
    user_data = user_limits.get(user_id, {'last_request': 0, 'count': 0, 'date': today})
    
    if user_data['date'] != today:
        user_data['count'] = 0
        user_data['date'] = today
        
    if now - user_data['last_request'] < 15:
        return False, "⏳ Vui lòng đợi 15 giây giữa các lần tải."
        
    if user_data['count'] >= 10:
        return False, "🛑 Bạn đã đạt giới hạn 10 lượt tải/ngày. Hãy quay lại vào ngày mai!"
        
    return True, ""

def update_rate_limit(user_id: int):
    today = date.today()
    now = time.time()
    user_data = user_limits.get(user_id, {'last_request': 0, 'count': 0, 'date': today})
    if user_data['date'] != today:
        user_data['count'] = 0
        user_data['date'] = today
    
    user_data['last_request'] = now
    user_data['count'] += 1
    user_limits[user_id] = user_data


# --- URL UTILS ---
def extract_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s]+", text)
    if not match:
        return None
    url = match.group(0).rstrip(".,);]}>\"'")
    if "tiktok.com" in url.lower():
        url = url.split("?")[0]
    return url

def is_supported_url(url: str) -> bool:
    supported = ["tiktok", "youtube", "youtu.be", "facebook", "fb.watch", "instagram"]
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"}:
            return False
        return any(s in hostname for s in supported)
    except ValueError:
        return False

def safe_filename(title: str) -> str:
    title = re.sub(r'[\\/:*?"<>|]', "", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title[:80] or "media_file"


# --- MEDIA UTILS ---
def get_video_info(url: str) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extractor_args": {"tiktok": {"app_info": [""]}},
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)

def download_media(url: str, output_dir: str, format_type: str) -> tuple[Path, dict]:
    base_options = {
        "outtmpl": str(Path(output_dir) / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "extractor_args": {"tiktok": {"app_info": [""]}},
    }

    if format_type == "mp3":
        base_options.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        })
    elif format_type == "m4a":
        base_options.update({
            "format": "bestaudio/best",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
            }],
        })
    elif format_type == "video":
        base_options.update({
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        })
    elif format_type == "ringtone":
        base_options.update({
            "format": "bestaudio/best",
            "postprocessor_args": [
                '-t', '30'
            ],
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
            }],
        })

    with yt_dlp.YoutubeDL(base_options) as ydl:
        info = ydl.extract_info(url, download=True)
        duration = info.get("duration")
        if duration and duration > MAX_DURATION_SECONDS:
            raise ValueError("VIDEO_TOO_LONG")

    downloaded_files = list(Path(output_dir).glob("*"))
    if not downloaded_files:
        raise FileNotFoundError("Không tìm thấy file đầu ra.")

    # Sort files by size, get largest in case yt-dlp left some temp files
    file_path = sorted(downloaded_files, key=lambda f: f.stat().st_size, reverse=True)[0]
    
    if format_type == "ringtone":
        new_path = file_path.with_suffix(".m4r")
        shutil.move(str(file_path), str(new_path))
        file_path = new_path

    return file_path, info


# --- HANDLERS ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 Chào mừng bạn đến với <b>Media Bot</b>!\n\n"
        "Bạn có thể gửi link từ <b>TikTok, YouTube (Shorts), Facebook (Reels), Instagram (Reels)</b>.\n"
        "Bot sẽ hỗ trợ tải MP3, M4A, Video không logo và cắt Nhạc chuông (30s).\n\n"
        "Dùng /help để xem hướng dẫn chi tiết."
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "💡 <b>Hướng dẫn sử dụng:</b>\n\n"
        "1️⃣ Gửi một link video công khai từ TikTok, YouTube, Facebook, Instagram.\n"
        "2️⃣ Bot sẽ trích xuất thông tin video (Tiêu đề, Tác giả, Thời lượng, Thumbnail).\n"
        "3️⃣ Chọn định dạng bạn muốn tải qua các nút bấm đính kèm.\n"
        "4️⃣ Chờ bot xử lý và gửi file về cho bạn.\n\n"
        "<i>Lưu ý: Chỉ tải nội dung bạn sở hữu hoặc được phép sử dụng. Không tải video quá dài (>15 phút).</i>"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    today = date.today()
    user_data = user_limits.get(user_id, {'last_request': 0, 'count': 0, 'date': today})
    
    count = user_data['count'] if user_data['date'] == today else 0
    remain = max(0, 10 - count)
    
    text = (
        f"⚙️ <b>Thông tin tài khoản</b>\n"
        f"👤 ID của bạn: <code>{user_id}</code>\n"
        f"📥 Số lượt tải hôm nay: {count}/10\n"
        f"✨ Lượt còn lại: {remain} lượt\n\n"
        f"<i>Hệ thống sẽ reset giới hạn mỗi ngày.</i>"
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message or not message.text:
        return

    url = extract_url(message.text)
    if not url:
        await message.reply_text("❌ Hãy gửi một link hợp lệ.")
        return

    if not is_supported_url(url):
        await message.reply_text("❌ Hiện bot chỉ hỗ trợ TikTok, YouTube, Facebook, Instagram.")
        return

    status = await message.reply_text("🔍 Đang lấy thông tin video...")
    
    try:
        info = await asyncio.wait_for(
            asyncio.to_thread(get_video_info, url),
            timeout=30
        )
    except Exception as e:
        logger.exception("Metadata error")
        await status.edit_text("❌ Không thể lấy thông tin. Có thể video bị xóa, riêng tư hoặc link lỗi.")
        return

    url_id = str(uuid.uuid4())[:8]
    context.user_data[f"url_{url_id}"] = url
    
    keyboard = [
        [
            InlineKeyboardButton("🎵 MP3", callback_data=f"dl|mp3|{url_id}"),
            InlineKeyboardButton("🎧 M4A", callback_data=f"dl|m4a|{url_id}")
        ],
        [
            InlineKeyboardButton("🎬 Video", callback_data=f"dl|video|{url_id}"),
            InlineKeyboardButton("🍎 Nhạc Chuông 30s", callback_data=f"dl|ringtone|{url_id}")
        ],
        [
            InlineKeyboardButton("❌ Hủy", callback_data="cancel")
        ]
    ]
    
    title = info.get("title") or "Không rõ"
    title = title[:60]
    uploader = info.get("uploader") or info.get("creator") or "Không rõ"
    duration = info.get("duration", 0)
    thumbnail = info.get("thumbnail")
    
    m, s = divmod(duration or 0, 60)
    duration_str = f"{m}:{s:02d}"
    
    text = (
        f"🎵 <b>{title}</b>\n"
        f"👤 Tác giả: {uploader}\n"
        f"⏱ Thời lượng: {duration_str}\n\n"
        f"👇 Chọn định dạng tải về:"
    )
    
    if thumbnail:
        try:
            await message.reply_photo(
                photo=thumbnail,
                caption=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
            await status.delete()
            return
        except Exception:
            pass
            
    await status.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    if data == "cancel":
        await query.message.edit_text("❌ Đã hủy thao tác lấy media.")
        return

    parts = data.split("|")
    if len(parts) != 3 or parts[0] != "dl":
        return
        
    format_type = parts[1]
    url_id = parts[2]
    
    url = context.user_data.get(f"url_{url_id}")
    if not url:
        await query.message.reply_text("❌ Link đã hết hạn, vui lòng gửi lại link ban đầu.")
        return

    user_id = update.effective_user.id
    is_allowed, reason = check_rate_limit(user_id)
    if not is_allowed:
        await query.message.reply_text(reason)
        return

    try:
        if query.message.photo:
            await query.edit_message_caption("📥 Đang tải dữ liệu media...", reply_markup=None)
        else:
            await query.edit_message_text("📥 Đang tải dữ liệu media...", reply_markup=None)
    except Exception:
        pass
        
    temp_dir = tempfile.mkdtemp(prefix="media_bot_")

    try:
        action = ChatAction.UPLOAD_VIDEO if format_type == "video" else ChatAction.UPLOAD_DOCUMENT
        await context.bot.send_chat_action(chat_id=query.message.chat_id, action=action)

        async with DOWNLOAD_SEMAPHORE:
            if query.message.photo:
                await query.edit_message_caption("🎧 Đang xử lý media (tải & chuyển đổi)...")
            else:
                await query.edit_message_text("🎧 Đang xử lý media (tải & chuyển đổi)...")
                
            file_path, info = await asyncio.wait_for(
                asyncio.to_thread(download_media, url, temp_dir, format_type),
                timeout=300,
            )

        if file_path.stat().st_size > MAX_OUTPUT_BYTES:
            msg = f"❌ File vượt quá giới hạn {MAX_OUTPUT_MB} MB."
            if query.message.photo:
                await query.edit_message_caption(msg)
            else:
                await query.edit_message_text(msg)
            return

        update_rate_limit(user_id)
        
        if query.message.photo:
            await query.edit_message_caption("📤 Đang gửi file lên Telegram...")
        else:
            await query.edit_message_text("📤 Đang gửi file lên Telegram...")
            
        title = safe_filename(info.get("title") or "Media")
        uploader = str(info.get("uploader") or info.get("creator") or "Unknown")[:64]
        duration = info.get("duration")

        with file_path.open("rb") as media_file:
            if format_type == "video":
                await query.message.reply_video(
                    video=media_file,
                    caption=f"🎬 {title}",
                    supports_streaming=True
                )
            elif format_type == "mp3":
                await query.message.reply_audio(
                    audio=media_file,
                    title=title,
                    performer=uploader,
                    duration=int(duration) if duration else None,
                    caption="🎵 Đã tách âm thanh MP3."
                )
            elif format_type == "m4a":
                await query.message.reply_audio(
                    audio=media_file,
                    title=title,
                    performer=uploader,
                    duration=int(duration) if duration else None,
                    caption="🎧 Nhạc M4A chất lượng cao."
                )
            elif format_type == "ringtone":
                await query.message.reply_document(
                    document=media_file,
                    filename=f"{title}.m4r",
                    caption="🍎 Nhạc chuông iPhone (30s)."
                )

        await query.message.delete()

    except asyncio.TimeoutError:
        err = "❌ Quá trình xử lý mất quá nhiều thời gian."
        if query.message.photo:
            await query.edit_message_caption(err)
        else:
            await query.edit_message_text(err)
    except ValueError as e:
        if str(e) == "VIDEO_TOO_LONG":
            err = f"❌ Video dài quá {MAX_DURATION_SECONDS//60} phút nên bot không hỗ trợ tải."
        else:
            err = "❌ Dữ liệu video không hợp lệ."
            logger.exception("Value error")
        
        if query.message.photo:
            await query.edit_message_caption(err)
        else:
            await query.edit_message_text(err)
    except Exception:
        logger.exception("Error processing download")
        err = "❌ Đã xảy ra lỗi khi tải. Có thể định dạng không hỗ trợ cho video này."
        if query.message.photo:
            await query.edit_message_caption(err)
        else:
            await query.edit_message_text(err)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

# --- WEB SERVER (HEALTH CHECK) ---
async def health_check(request):
    return web.Response(text="OK")

async def start_web_server() -> web.AppRunner:
    app = web.Application()
    app.router.add_get("/", health_check)
    app.router.add_get("/health", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    logger.info("Health server running on port %s", PORT)
    return runner

# --- MAIN ---
async def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Chưa đặt biến môi trường BOT_TOKEN.")

    telegram_app = Application.builder().token(BOT_TOKEN).concurrent_updates(4).build()

    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("help", help_command))
    telegram_app.add_handler(CommandHandler("settings", settings_command))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    telegram_app.add_handler(CallbackQueryHandler(handle_callback))

    web_runner = await start_web_server()

    try:
        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram Media Bot is running.")
        await asyncio.Event().wait()
    finally:
        try:
            if telegram_app.updater.running:
                await telegram_app.updater.stop()
        except Exception:
            pass
        try:
            await telegram_app.stop()
        except Exception:
            pass
        try:
            await telegram_app.shutdown()
        except Exception:
            pass
        await web_runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
