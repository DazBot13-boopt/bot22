FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install Python deps first (better Docker layer caching)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the code
COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY run.py ./
COPY traders.json ./

# DigitalOcean App Platform / Droplet exposes 8080 by default; we listen on $PORT
ENV PORT=8080
EXPOSE 8080

# Use sh -c so $PORT is expanded at runtime
CMD ["sh", "-c", "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8080}"]
