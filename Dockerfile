# Pushpak Index — pipeline, API, dashboard and landing page in one image.
#
# The landing page is built here and served by FastAPI, so the whole prototype
# is one HTTP service on one origin. That is what makes it a single container
# to point a Dokploy domain at, with no nginx tier to route around.

# ---- stage 1: build the React landing page -------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ---- stage 2: the application -------------------------------------------
FROM python:3.13-slim

# PUSHPAK_DB_URL is deliberately NOT set here. Baking a connection string with
# a password into an image is how credentials end up on a registry; it comes
# from the environment at run time, and the app fails loudly without it.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PUSHPAK_AUDIT_LOG=/data/scrape_audit.jsonl

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pushpak/ ./pushpak/
COPY scripts/ ./scripts/
# .dockerignore keeps the pre-Postgres SQLite file (81 MB) and the generated
# audit log out of this; what remains is the DGCA releases the index needs.
COPY data/ ./data/
COPY --from=web /web/dist ./web/dist

RUN mkdir -p /data && chmod +x scripts/entrypoint.sh

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://127.0.0.1:8000/api/v1/health').json()['status']=='ok' else 1)"

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["uvicorn", "pushpak.api:app", "--host", "0.0.0.0", "--port", "8000"]
