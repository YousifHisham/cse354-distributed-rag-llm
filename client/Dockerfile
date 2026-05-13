FROM python:3.11-slim-bookworm

WORKDIR /app

COPY client/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/common.py .
COPY scripts/single_request.py .

CMD ["python3", "single_request.py"]
