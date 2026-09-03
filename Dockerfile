FROM python:3.12.8-slim

# Install system dependencies (curl and 7zip)
RUN apt-get update && apt-get install -y \
    curl \
    p7zip-full \
    rclone \
    ffmpeg \
    gcc \
    python3-dev \
    libchromaprint-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy the local moviebox-api
COPY moviebox-api /moviebox-api

# Install python dependencies first (to leverage docker cache)
COPY requirements.txt .
# Use uv for blazingly fast installations
RUN pip install --no-cache-dir uv && \
    uv pip install --system /moviebox-api[cli] && \
    uv pip install --system -r requirements.txt

# Copy the bot code
COPY media_bot.py .
COPY auto_merger.py .
COPY bot/ bot/

CMD ["python", "media_bot.py"]
