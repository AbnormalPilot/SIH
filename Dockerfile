# Pushpak Index pipeline + API + operations dashboard
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PUSHPAK_DB=/data/pushpak.db \
    PUSHPAK_AUDIT_LOG=/data/scrape_audit.jsonl

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pushpak/ ./pushpak/
COPY scripts/ ./scripts/
COPY tests/ ./tests/
# .dockerignore keeps the generated SQLite db and audit log out of the image.
COPY data/ ./data/
COPY pytest.ini ./

RUN mkdir -p /data

# Seed a labelled synthetic history at build time so the container is
# demoable the moment it starts. Every row is collection_mode=SYNTHETIC.
RUN python -m pushpak.cli seed --days 30 && python -m pushpak.cli index --tail 3

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://127.0.0.1:8000/api/v1/health').json()['status']=='ok' else 1)"

CMD ["uvicorn", "pushpak.api:app", "--host", "0.0.0.0", "--port", "8000"]
