FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AICORTE_HOST=0.0.0.0 \
    AICORTE_PORT=11435 \
    AICORTE_DB_PATH=/data/ollama-panel.sqlite3 \
    OLLAMA_URL=http://ollama:11434

WORKDIR /app
COPY server.py ./server.py
COPY static ./static
RUN mkdir -p /data

EXPOSE 11435
CMD ["python", "server.py"]
