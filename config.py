import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
PORT = int(os.getenv("PORT", "10000"))
MAX_OUTPUT_MB = int(os.getenv("MAX_OUTPUT_MB", "45"))
MAX_OUTPUT_BYTES = MAX_OUTPUT_MB * 1024 * 1024
MAX_DURATION_SECONDS = 15 * 60
