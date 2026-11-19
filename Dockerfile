# Container for Cloud Run. Build and deploy steps are in the README.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    OSAERU_HOST=0.0.0.0

WORKDIR /app
COPY requirements.txt requirements-cloudrun.txt ./
RUN pip install --no-cache-dir -r requirements-cloudrun.txt

COPY src/ src/

# Cloud Run sets $PORT; osaeru.web reads it.
CMD ["python", "-m", "osaeru.web"]
