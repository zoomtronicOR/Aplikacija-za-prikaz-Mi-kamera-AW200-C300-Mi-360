FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

# ffmpeg: the recorder copies go2rtc's RTSP restream into segments and joins them into clips
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bootstrap is vendored at build time so the UI works without internet access.
ADD https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css app/static/vendor/bootstrap.min.css

COPY alembic.ini .
COPY migrations migrations
COPY app app

# fixed uid so the host can chown the tokens file (chmod 600) to this user
RUN useradd --system --no-create-home --uid 10001 camhub && chmod -R a+rX /srv
USER camhub

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips=127.0.0.1"]
