import asyncio
import logging
import os
import shutil
import tempfile
import uuid
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

from pathlib import Path
from config import BOT_TOKEN, PORT, MAX_OUTPUT_BYTES, MAX_OUTPUT_MB, MAX_DURATION_SECONDS
from utils.url import extract_url, expand_tiktok_url
from utils.format import detect_platform, safe_filename
from services.metadata import extract_metadata
from services.download import download_media_from_info
from services.converter import extract_ringtone

DOWNLOAD_SEMAPHORE = asyncio.Semaphore(2)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- HANDLERS ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "👋 Chào mừng bạn đến với <b>Media Bot (Phiên bản Mới)</b>!\n\n"
        "Bạn có thể gửi link từ <b>TikTok, YouTube (Shorts), Facebook (Reels), Instagram (Reels)</b>.\n"
        "Bot luôn chọn luồng âm thanh có chất lượng cao nhất mà nền tảng cung cấp.\n\n"
        "Dùng /help để xem hướng dẫn chi tiết."
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "💡 <b>Hướng dẫn sử dụng:</b>\n\n"
        "1️⃣ Gửi một link video công khai từ TikTok, YouTube, Facebook, Instagram.\n"
        "2️⃣ Chọn định dạng bạn muốn tải qua các nút bấm đính kèm.\n"
        "3️⃣ Chờ bot xử lý và gửi file về cho bạn.\n\n"
        "<i>Lưu ý: Chỉ tải nội dung bạn sở hữu hoặc được phép sử dụng. Không tải video quá dài (>15 phút).</i>"
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
        
    url = await expand_tiktok_url(url)

    if not detect_platform(url):
        await message.reply_text("❌ Hiện bot chỉ hỗ trợ TikTok, YouTube, Facebook, Instagram.")
        return

    status = await message.reply_text("🔍 Đang lấy thông tin video...")
    
    metadata_failed = False
    try:
        info = await asyncio.wait_for(
            asyncio.to_thread(extract_metadata, url),
            timeout=30
        )
    except Exception as error:
        logger.error("Metadata error: %s", error)
        metadata_failed = True

    if metadata_failed:
        await status.edit_text("❌ Không lấy được thông tin video từ liên kết này.")
        return

    request_id = str(uuid.uuid4())[:8]
    context.user_data[request_id] = {
        "user_id": update.effective_user.id,
        "url": info.get("webpage_url") or url,
        "info": info
    }
    
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎵 MP3", callback_data=f"download:mp3:{request_id}"),
                InlineKeyboardButton("🎧 M4A", callback_data=f"download:m4a:{request_id}"),
            ],
            [
                InlineKeyboardButton("🎬 Video", callback_data=f"download:video:{request_id}"),
                InlineKeyboardButton("🍎 Nhạc chuông", callback_data=f"download:ringtone:{request_id}"),
            ],
            [
                InlineKeyboardButton("❌ Hủy", callback_data="cancel"),
            ],
        ]
    )

    title = safe_filename(info.get("title") or "Video")
    uploader = str(info.get("uploader") or info.get("channel") or info.get("creator") or "Không rõ")[:64]
    duration = info.get("duration")
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
                reply_markup=keyboard,
                parse_mode=ParseMode.HTML
            )
            await status.delete()
            return
        except Exception:
            pass
            
    await status.edit_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query

    if not query:
        return

    await query.answer()

    data = query.data
    if data == "cancel":
        try:
            await query.message.delete()
        except Exception as e:
            logger.error("Failed to delete message on cancel: %s", e)
        return

    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "download":
        return
        
    format_type = parts[1]
    request_id = parts[2]
    
    request_data = context.user_data.get(request_id)
    if not request_data:
        await query.message.reply_text("❌ Yêu cầu đã hết hạn. Hãy gửi lại link.")
        return
        
    if query.from_user.id != request_data["user_id"]:
        await query.answer("Bạn không thể dùng nút của người khác.", show_alert=True)
        return

    cached_info = request_data["info"]
    temp_dir = tempfile.mkdtemp(prefix="media_bot_")

    format_names = {
        "mp3": "MP3",
        "m4a": "M4A",
        "video": "Video",
        "ringtone": "Nhạc chuông 30 giây",
    }
    display_name = format_names.get(format_type, format_type.upper())

    status = await query.message.reply_text(f"⏳ Đang xử lý {display_name}...")
        
    try:
        async with DOWNLOAD_SEMAPHORE:
            file_path, downloaded_info = await asyncio.wait_for(
                asyncio.to_thread(download_media_from_info, cached_info, temp_dir, format_type),
                timeout=180
            )

        if not file_path.exists():
            raise FileNotFoundError("Không tìm thấy file đầu ra.")

        if format_type == "ringtone":
            output_file_path = Path(temp_dir) / f"{uuid.uuid4().hex}.m4r"
            await status.edit_text(f"🎧 Đang cắt {display_name}...")
            await asyncio.to_thread(extract_ringtone, file_path, output_file_path, 0)
            file_path = output_file_path
        
        if file_path.stat().st_size > MAX_OUTPUT_BYTES:
            await status.edit_text(f"❌ File vượt quá giới hạn {MAX_OUTPUT_MB} MB.")
            return

        await status.edit_text(f"📤 Đang gửi {display_name} lên Telegram...")
            
        title = safe_filename(downloaded_info.get("title") or "Media")
        uploader = str(downloaded_info.get("uploader") or downloaded_info.get("creator") or "Unknown")[:64]
        duration = downloaded_info.get("duration")

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
                    caption=(
                        "🍎 Nhạc chuông iPhone 30 giây đã sẵn sàng.\n"
                        "Tải file về rồi mở bằng ứng dụng Tệp hoặc GarageBand."
                    )
                )

        await status.delete()

    except asyncio.TimeoutError:
        await status.edit_text("❌ Xử lý quá lâu. Hãy thử lại.")
    except yt_dlp.utils.DownloadError as e:
        logger.exception("yt-dlp download error for %s: %s", format_type, e)
        await status.edit_text(f"❌ Không tải được {display_name}. Hãy thử lại sau.")
    except Exception as e:
        logger.exception("Conversion/Upload error: %s", e)
        await status.edit_text(f"❌ Không tải được {display_name}.")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

# --- WEBHOOK ROUTES ---
async def health_check(request: web.Request) -> web.Response:
    return web.Response(text="Bot is running!")

# --- MAIN LOOP ---
def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    webhook_url = os.environ.get("WEBHOOK_URL")
    
    async def run_server():
        app = web.Application()
        app.router.add_get("/", health_check)
        app.router.add_get("/health", health_check)
        
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        logger.info(f"Dummy web server started on port {PORT} to satisfy Render health check")
        
        await application.initialize()
        if webhook_url:
            logger.info(f"Starting webhook with URL {webhook_url}")
            await application.bot.set_webhook(url=webhook_url)
            await application.start()
        else:
            logger.info("Starting polling")
            await application.bot.delete_webhook()
            await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            await application.start()
            
        await asyncio.Event().wait()
        
    asyncio.run(run_server())

if __name__ == "__main__":
    main()
