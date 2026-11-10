# One image for the service and the tests. Dev dependencies are installed because the
# reviewer runs `docker compose run --rm tests` in this same image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "bulk_import.main:app", "--host", "0.0.0.0", "--port", "8000"]
