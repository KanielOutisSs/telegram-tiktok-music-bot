# Sử dụng image gọn nhẹ của Python 3.12
FROM python:3.12-slim

# Thiết lập các biến môi trường
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Cài đặt ffmpeg cần thiết cho yt-dlp và xóa bộ nhớ đệm (cache) để giảm kích thước image
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Tạo user không phải root để tăng tính bảo mật
RUN useradd -m botuser

# Thiết lập thư mục làm việc
WORKDIR /app

# Copy tệp requirements.txt trước để tận dụng Docker cache (chỉ chạy lại bước cài đặt khi requirements thay đổi)
COPY requirements.txt .

# Cài đặt các thư viện Python
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ mã nguồn vào image
COPY bot.py .

# Cấp quyền cho botuser đối với thư mục /app
RUN chown -R botuser:botuser /app

# Chuyển sang tài khoản non-root
USER botuser

# Lệnh mặc định khởi chạy bot
CMD ["python", "bot.py"]
