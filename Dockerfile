FROM python:3.11-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libmagic-dev \
    libpq-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project
COPY . .

# Create necessary directories and set permissions
RUN mkdir -p storage temp output && \
    chmod -R 777 storage temp output

# Make the start script executable
RUN chmod +x start.sh

# Koyeb and Render will use the PORT environment variable
EXPOSE $PORT

# Start all services via the start script
CMD ["./start.sh"]