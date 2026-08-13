import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

def convert_to_mp3(input_path: Path, output_path: Path) -> None:
    command = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vn", "-c:a", "libmp3lame", "-b:a", "320k",
        str(output_path)
    ]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=120)
    if result.returncode != 0:
        logger.error(f"FFmpeg MP3 error: {result.stderr}")
        raise RuntimeError("FFMPEG_MP3_FAILED")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise FileNotFoundError("Không tạo được file MP3.")


def convert_to_m4a(input_path: Path, output_path: Path) -> None:
    command = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vn", "-c:a", "aac", "-b:a", "256k",
        str(output_path)
    ]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=120)
    if result.returncode != 0:
        logger.error(f"FFmpeg M4A error: {result.stderr}")
        raise RuntimeError("FFMPEG_M4A_FAILED")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise FileNotFoundError("Không tạo được file M4A.")


def extract_ringtone(input_path: Path, output_path: Path, start_seconds: int = 0) -> None:
    command = [
        "ffmpeg", "-y", "-ss", str(max(start_seconds, 0)),
        "-i", str(input_path), "-t", "30",
        "-vn", "-c:a", "aac", "-b:a", "256k",
        "-ar", "44100", "-ac", "2", "-f", "ipod",
        str(output_path)
    ]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=120)
    if result.returncode != 0:
        logger.error(f"FFmpeg ringtone error: {result.stderr}")
        raise RuntimeError("FFMPEG_RINGTONE_FAILED")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise FileNotFoundError("Không tạo được file M4R.")


def cut_audio(input_path: Path, output_path: Path, start_seconds: int, end_seconds: int) -> None:
    duration = max(1, end_seconds - start_seconds)
    command = [
        "ffmpeg", "-y", "-ss", str(max(start_seconds, 0)),
        "-i", str(input_path), "-t", str(duration),
        "-vn", "-c:a", "libmp3lame", "-b:a", "320k",
        str(output_path)
    ]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=120)
    if result.returncode != 0:
        logger.error(f"FFmpeg cut audio error: {result.stderr}")
        raise RuntimeError("FFMPEG_CUT_FAILED")
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise FileNotFoundError("Không tạo được file cắt MP3.")

