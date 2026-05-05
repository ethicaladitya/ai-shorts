FROM python:3.12-slim

WORKDIR /app

# System dependencies: FFmpeg (video encoding), curl (health check), build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    build-essential \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Ensure setuptools + wheel are present before anything else
RUN pip install --upgrade pip setuptools wheel

# Python dependencies (split for better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# Create data directories
RUN mkdir -p /app/data/output /app/data/temp /app/data/assets

# Static directory
RUN mkdir -p /app/app/static

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
