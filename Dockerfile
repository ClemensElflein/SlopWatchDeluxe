FROM python:3.12-slim

ARG SLOPWATCHDELUXE_BUILD
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SLOPWATCHDELUXE_DATABASE=/data/slopwatchdeluxe.db SLOPWATCHDELUXE_PORT=8765 \
    SLOPWATCHDELUXE_BUILD=${SLOPWATCHDELUXE_BUILD}
LABEL org.opencontainers.image.title="SlopWatchDeluxe" \
      org.opencontainers.image.source="https://github.com/ClemensElflein/SlopWatchDeluxe" \
      org.opencontainers.image.licenses="GPL-3.0-only"
WORKDIR /app
COPY pyproject.toml LICENSE ./
COPY server ./server
COPY client ./client
RUN pip install --no-cache-dir . \
    && groupadd --gid 10001 slopwatchdeluxe \
    && useradd --uid 10001 --gid slopwatchdeluxe --no-create-home slopwatchdeluxe \
    && mkdir /data && chown slopwatchdeluxe:slopwatchdeluxe /data
COPY scripts ./scripts
RUN python scripts/build-zipapp.py
USER slopwatchdeluxe
EXPOSE 8765
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('SLOPWATCHDELUXE_PORT','8765')+'/api/v1/health',timeout=2).read()"
CMD ["python", "-m", "server"]
