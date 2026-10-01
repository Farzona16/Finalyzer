# syntax=docker/dockerfile:1
# Multi-stage: Node builds the SPA, Python serves API + SPA. Streamlit is not in this image.

FROM node:22-bookworm-slim AS frontend
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
COPY shared /shared
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    PYTHONPATH=/app

RUN useradd --create-home --uid 10001 finalyzer
WORKDIR /app

COPY backend/requirements.txt ./backend/requirements.txt
COPY requirements.txt requirements.lock.txt ./
RUN pip install -r backend/requirements.txt

COPY --chown=finalyzer:finalyzer core ./core
COPY --chown=finalyzer:finalyzer backend ./backend
COPY --chown=finalyzer:finalyzer shared ./shared
COPY --chown=finalyzer:finalyzer data/demo ./data/demo
COPY --chown=finalyzer:finalyzer scripts ./scripts
COPY --from=frontend --chown=finalyzer:finalyzer /web/dist ./frontend/dist

USER finalyzer
EXPOSE 8000
WORKDIR /app/backend
CMD ["sh", "-c", "exec python -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
