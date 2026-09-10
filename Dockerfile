FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    AGENTWATCH_DATABASE=/data/agentwatch.db AGENTWATCH_PORT=8765
WORKDIR /app
COPY pyproject.toml ./
COPY server ./server
COPY client ./client
RUN pip install --no-cache-dir . \
    && groupadd --gid 10001 agentwatch \
    && useradd --uid 10001 --gid agentwatch --no-create-home agentwatch \
    && mkdir /data && chown agentwatch:agentwatch /data
COPY scripts ./scripts
RUN python scripts/build-zipapp.py
USER agentwatch
EXPOSE 8765
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('AGENTWATCH_PORT','8765')+'/api/v1/health',timeout=2).read()"
CMD ["python", "-m", "server"]
