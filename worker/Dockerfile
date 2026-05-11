FROM python:3.11-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY models.py .

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${WORKER_PORT:-8001}"]
