FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HAMMERS_DB=/data/hammers.db

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

RUN useradd --system --uid 1000 crushinghammer \
    && mkdir -p /data \
    && chown crushinghammer:crushinghammer /data
USER crushinghammer

VOLUME ["/data"]

CMD ["python", "bot.py"]
