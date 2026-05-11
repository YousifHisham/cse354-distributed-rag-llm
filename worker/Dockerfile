FROM python:3.11-slim

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY main.py .
COPY models.py .

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${WORKER_PORT:-8001}"]
