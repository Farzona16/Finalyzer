FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 10001 finalyzer
WORKDIR /app

COPY requirements.lock.txt ./
RUN pip install -r requirements.lock.txt

COPY --chown=finalyzer:finalyzer . .
RUN python scripts/generate_demo_data.py \
    && chown -R finalyzer:finalyzer /app

USER finalyzer
ENV PORT=8501
EXPOSE 8501

CMD ["sh", "-c", "exec streamlit run app/main.py --server.port ${PORT:-8501} --server.address 0.0.0.0 --server.headless true"]
