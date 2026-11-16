FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY integration/requirements.txt /tmp/integration-requirements.txt
RUN pip install --no-cache-dir -r /tmp/integration-requirements.txt

COPY backend /app/backend
COPY core /app/core
COPY submission /app/submission
COPY integration /app/integration
COPY data/organizers/knowledge-base /app/data/knowledge-base
RUN python -c "from pathlib import Path; import zipfile; root=Path('/app/data/knowledge-base'); archive=zipfile.ZipFile('/app/data/knowledge-base.zip','w',zipfile.ZIP_DEFLATED); [archive.write(path, path.relative_to(root).as_posix()) for path in sorted(root.rglob('*')) if path.is_file()]; archive.close()"

CMD ["python", "-m", "integration.run"]
