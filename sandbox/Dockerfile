# Lightweight Python sandbox that exposes an HTTP API for executing
# code on behalf of the nexus-chat backend. The container runs as a
# non-root user with bounded CPU/memory (set in docker-compose.yml).
FROM python:3.11-slim

WORKDIR /sandbox

RUN pip install --no-cache-dir fastapi==0.115.0 uvicorn[standard]==0.30.6 \
    numpy pandas matplotlib plotly

COPY server.py /sandbox/server.py

EXPOSE 8500

CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8500"]
