FROM python:3.11-slim-bookworm

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY client.py .
COPY load_test.py .
COPY pdf_load_test.py .
COPY ramp_test.py .
COPY query.py .

CMD ["python3", "client.py"]
