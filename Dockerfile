FROM python:3.13-slim

# tzdata нужен по-настоящему: бот считает даты вылета и пишет время опроса.
# Без него контейнер живёт в UTC и «сегодня» разъезжается с твоим.
# Шрифты — для карточек канала: в slim-образе нет ни одного с кириллицей.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata fonts-inter fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Moscow \
    AVIA_DB=/data/avia.db \
    AVIA_PLACES=/data/places.json \
    AVIA_ROUTES=/data/routes.json \
    AVIA_TG_SESSION=/data/avia_session

WORKDIR /app

# зависимости отдельным слоем — правка кода не заставляет качать их заново
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py otello.json ./
COPY handlers/ ./handlers/

# всё состояние в /data: база, справочник, сессия Telegram
RUN useradd --create-home --uid 1000 avia \
    && mkdir -p /data && chown avia:avia /data
USER avia
VOLUME ["/data"]

# контейнер считается больным, если опрос источников встал
HEALTHCHECK --interval=5m --timeout=15s --start-period=3m --retries=2 \
    CMD ["python", "health.py"]

CMD ["python", "run.py"]
