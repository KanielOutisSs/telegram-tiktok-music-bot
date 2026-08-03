import logging
import os
import sys
import re
import asyncio
import tempfile
import yt_dlp
import time
from urllib.parse import urlparse
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters

try:
    MAX_OUTPUT_MB = float(os.environ.get("MAX_OUTPUT_MB", 45))
except ValueError:
    MAX_OUTPUT_MB = 45.0

download_semaphore = None
user_cooldowns = {}

# Cấu hình logging để dễ dàng theo dõi lỗi và hoạt động của bot
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class YTDLPLogger:
    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        logger.error(msg)

def duration_filter(info_dict, *, incomplete):
    duration = info_dict.get('duration')
    if duration and duration > 600:
        return "Video dài hơn 10 phút, không được hỗ trợ."
    return None

def _download_audio_sync(url: str, temp_dir: str):
    """Hàm đồng bộ để tải audio bằng yt-dlp."""
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(temp_dir, '%(id)s.%(ext)s'),
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 15,
        'retries': 3,
        'logger': YTDLPLogger(),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'keepvideo': False,
        'match_filter': duration_filter,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        
        # Lấy tên file gốc do yt-dlp tải (ví dụ: video123.m4a)
        original_filename = ydl.prepare_filename(info)
        
        # Xác định chính xác tên file MP3 sau khi FFmpeg xử lý
        base_name = os.path.splitext(original_filename)[0]
        mp3_filename = f"{base_name}.mp3"
        
        # Nếu không tìm thấy file MP3 thì raise lỗi rõ ràng
        if not os.path.exists(mp3_filename):
            raise FileNotFoundError(f"Không tìm thấy file MP3 đầu ra tại: {mp3_filename}")
            
        return mp3_filename, info

async def download_audio_async(url: str, temp_dir_name: str):
    """Hàm bất đồng bộ tải audio, truyền sẵn tên thư mục tạm."""
    filename, info = await asyncio.to_thread(_download_audio_sync, url, temp_dir_name)
    return filename, info

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hàm xử lý khi người dùng gõ lệnh /start"""
    try:
        welcome_message = "🎵 Gửi link TikTok, tôi sẽ tách nhạc thành MP3."
        await context.bot.send_message(chat_id=update.effective_chat.id, text=welcome_message)
        logger.info(f"Đã gửi tin nhắn chào mừng cho chat_id: {update.effective_chat.id}")
    except Exception as e:
        logger.error(f"Lỗi khi xử lý lệnh /start: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý tin nhắn văn bản từ người dùng"""
    global download_semaphore
    if download_semaphore is None:
        download_semaphore = asyncio.Semaphore(2)

    text = update.message.text
    if not text:
        return
        
    user_id = update.effective_user.id
    now = time.monotonic()
    
    # Dọn dẹp cooldown cũ để tránh rò rỉ bộ nhớ
    expired_users = [uid for uid, t in user_cooldowns.items() if now - t > 15]
    for uid in expired_users:
        del user_cooldowns[uid]
        
    # Kiểm tra chống spam (cooldown 15 giây)
    if user_id in user_cooldowns:
        wait_time = int(15 - (now - user_cooldowns[user_id]))
        if wait_time > 0:
            await update.message.reply_text(f"⏳ Bạn thao tác quá nhanh. Vui lòng đợi {wait_time} giây trước khi gửi link tiếp theo.")
            return

    # Tìm URL đầu tiên trong tin nhắn
    match = re.search(r'(https?://[^\s]+)', text)
    if not match:
        await update.message.reply_text("Vui lòng gửi link TikTok để tôi có thể tách nhạc nhé.")
        return

    url = match.group(1)
    
    # Dùng urllib.parse.urlparse để phân tích URL và lấy hostname
    parsed = urlparse(url)
    hostname = parsed.hostname

    # Chỉ chấp nhận hostname thuộc tiktok.com hoặc tên miền con của tiktok.com
    # (bao gồm www.tiktok.com, vm.tiktok.com, vt.tiktok.com...)
    if hostname == "tiktok.com" or (hostname and hostname.endswith(".tiktok.com")):
        # Ghi nhận thời gian gửi link hợp lệ để chống spam
        user_cooldowns[user_id] = now
        
        # Kiểm tra trạng thái semaphore để báo xếp hàng nếu bot bận
        if download_semaphore.locked():
            status_msg = await update.message.reply_text("⏳ Hệ thống đang bận, yêu cầu của bạn đang được xếp hàng chờ...")
        else:
            status_msg = await update.message.reply_text("⏳ Đang tải và tách nhạc...")
            
        temp_dir = tempfile.TemporaryDirectory()
        try:
            async with download_semaphore:
                # Nếu đã từng báo xếp hàng, cập nhật lại tin nhắn khi tới lượt
                if status_msg.text != "⏳ Đang tải và tách nhạc...":
                    try:
                        await status_msg.edit_text("⏳ Đang tải và tách nhạc...")
                    except Exception:
                        pass
                    
                # Bọc quá trình tải bằng timeout 180 giây
                filename, info = await asyncio.wait_for(
                    download_audio_async(url, temp_dir.name), timeout=180.0
                )
            
            # Kiểm tra kích thước file đầu ra so với cấu hình MAX_OUTPUT_MB
            file_size_mb = os.path.getsize(filename) / (1024 * 1024)
            if file_size_mb > MAX_OUTPUT_MB:
                await status_msg.edit_text(f"❌ Kích thước file đầu ra ({file_size_mb:.1f}MB) vượt quá giới hạn cho phép ({MAX_OUTPUT_MB}MB).")
                return
            
            # Lấy title, uploader và duration từ metadata yt-dlp
            raw_title = info.get('title', 'Tiktok_Audio')
            uploader = info.get('uploader', 'Unknown')
            duration = info.get('duration', 0)
            
            # Làm sạch tên file, loại bỏ ký tự không hợp lệ
            clean_title = re.sub(r'[\\/*?:"<>|]', "", raw_title)
            
            # Giới hạn tên file không quá 80 ký tự
            if len(clean_title) > 80:
                clean_title = clean_title[:80]
            display_filename = f"{clean_title}.mp3"
            
            # Gửi file âm thanh về Telegram
            with open(filename, 'rb') as audio_file:
                await update.message.reply_audio(
                    audio=audio_file,
                    filename=display_filename,
                    caption=f"🎵 {raw_title}\n👤 {uploader}",
                    title=clean_title,
                    performer=uploader,
                    duration=duration
                )
            
            # Xóa tin nhắn trạng thái sau khi đã gửi file thành công
            try:
                await status_msg.delete()
            except Exception:
                pass
            
        except asyncio.TimeoutError:
            try:
                await status_msg.edit_text("❌ Quá thời gian xử lý (180 giây). Vui lòng thử lại với video ngắn hơn.")
            except Exception:
                pass
        except Exception as e:
            try:
                if "Video dài hơn 10 phút" in str(e):
                    await status_msg.edit_text("❌ Video dài hơn 10 phút, bot không hỗ trợ tải.")
                else:
                    logger.exception(f"Lỗi hệ thống khi xử lý: {e}")
                    await status_msg.edit_text("❌ Rất tiếc, hệ thống đang gặp sự cố khi tải nhạc từ link này. Vui lòng thử lại sau nhé!")
            except Exception:
                pass
        finally:
            # Luôn đảm bảo xóa thư mục tạm dù thành công hay thất bại
            if temp_dir:
                temp_dir.cleanup()
    else:
        await update.message.reply_text("Xin lỗi, hiện tại bot chỉ hỗ trợ xử lý link từ TikTok.")

def main():
    """Hàm chính khởi chạy bot"""
    # Lấy token từ biến môi trường
    token = os.environ.get("BOT_TOKEN")
    
    if not token:
        logger.error("Biến môi trường 'BOT_TOKEN' chưa được thiết lập. Bot không thể khởi động.")
        sys.exit(1)

    try:
        # Khởi tạo Application (cách dùng chuẩn của v20+)
        application = Application.builder().token(token).build()

        # Đăng ký command handler
        start_handler = CommandHandler('start', start)
        application.add_handler(start_handler)

        # Đăng ký handler xử lý tin nhắn văn bản (bỏ qua commands)
        text_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
        application.add_handler(text_handler)

        # Chạy bot (long polling)
        logger.info("Bot đang chạy... Bấm Ctrl+C để dừng.")
        application.run_polling()

    except Exception as e:
        logger.critical(f"Lỗi nghiêm trọng không thể khởi động bot: {e}")

if __name__ == '__main__':
    main()
