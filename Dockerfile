# Playwright's own image ships Chromium plus every system library it needs —
# installing those onto a plain python base is the usual source of pain.
FROM mcr.microsoft.com/playwright/python:v1.49.1-jammy

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PS5_STATE_PATH=/data/state.json \
    PS5_LOG_PATH=/data/run.log \
    CHECK_INTERVAL_SECONDS=3600

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# /data holds state.json — mount it as a volume so a redeploy does not forget
# what was already in stock and re-alert on everything.
RUN mkdir -p /data
VOLUME ["/data"]

RUN chmod +x entrypoint.sh

CMD ["./entrypoint.sh"]
