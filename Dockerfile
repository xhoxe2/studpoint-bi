FROM python:3.13-slim

WORKDIR /app

# Системні залежності: gcc для curl_cffi, ca-certificates для HTTPS
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Спочатку залежності (для кешу)
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/backend/requirements.txt

# Код
COPY backend /app/backend
COPY frontend /app/frontend

ENV PORT=8000
ENV PYTHONUNBUFFERED=1

WORKDIR /app/backend
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
