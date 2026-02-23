#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

echo "🚀 Starting Quran Video Generator Services..."

# 1. Start Celery Worker in the background
# We use --concurrency=1 for free tier to save memory
echo "⚙️ Starting Celery Worker..."
celery -A app.tasks worker --loglevel=info --concurrency=1 &

# 2. (Optional) Start Celery Beat if needed for cleanup tasks
echo "⏱️ Starting Celery Beat..."
celery -A app.tasks beat --loglevel=info &

# 3. Start FastAPI Server
echo "🌐 Starting FastAPI Server..."
# Using uvicorn directly
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
