# Лёгкий официальный Python с актуальной Debian (bookworm)
FROM python:3.11-slim-bookworm

# Базовые env
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Amsterdam

# Системные зависимости:
#  - build-essential/gcc/g++/gfortran — для сборки нативных расширений при необходимости
#  - libopenblas-dev, liblapack-dev — BLAS/LAPACK для numpy/scipy
#  - tzdata — таймзона
#  - curl — диагностика/healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential gcc g++ gfortran \
    libopenblas-dev liblapack-dev \
    tzdata curl \
 && rm -rf /var/lib/apt/lists/*

# Рабочая директория
WORKDIR /app

# Устанавливаем зависимости отдельно — для лучшего кэширования
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip \
 && pip install -r /app/requirements.txt

# Копируем весь проект
COPY . /app

# Папка для логов (маппится томом в docker-compose)
RUN mkdir -p /app/logs

# Healthcheck (простой пинг python)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "print('ok')" || exit 1

# Запуск бота
CMD ["python", "-u", "bot.py"]
