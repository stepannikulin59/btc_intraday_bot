# Используем лёгкий официальный Python
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Amsterdam

# Системные зависимости для ta/scipy (минимум)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ \
    libatlas-base-dev gfortran \
    curl tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Установим зависимости отдельно — лучше кэшируется
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# Копируем проект
COPY . /app

# Папка для логов (маунтится томом/volume)
RUN mkdir -p /app/logs

# Healthcheck: проверим, что процесс жив
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import socket; print('ok')" || exit 1

# Запуск
CMD ["python", "-u", "bot.py"]
