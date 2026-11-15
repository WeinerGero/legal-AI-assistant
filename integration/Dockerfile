FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY integration/requirements.txt /tmp/integration-requirements.txt
RUN pip install --no-cache-dir -r /tmp/integration-requirements.txt

COPY backend /app/backend
COPY core /app/core
COPY submission /app/submission
COPY integration /app/integration

CMD ["uvicorn", "integration.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "30"]
