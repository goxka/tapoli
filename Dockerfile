FROM python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY app /app
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    TZ=Europe/Madrid \
    TAPOLI_CONFIG=/config/config.yaml \
    TAPOLI_DATA=/data \
    TAPOLI_CLIPS=/clips \
    TAPOLI_BUFFER=/buffer
