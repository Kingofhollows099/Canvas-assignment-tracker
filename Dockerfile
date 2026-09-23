# The app is pure Python standard library, so the image needs nothing but Python.
FROM python:3.12-slim

# Don't buffer stdout/stderr, so `docker logs` shows output promptly.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Copy the application code (see .dockerignore for what's excluded).
COPY . .

# Run as a non-root user, and give it a writable data directory for the push store.
RUN useradd --uid 10001 --create-home appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /data /app
USER appuser

# Where the browser-sync store is kept; mount a volume here to persist it.
ENV PUSH_STORE_PATH=/data/pushed_assignments.json \
    HOST=0.0.0.0 \
    PORT=8000 \
    CANVAS_SOURCE=userscript

EXPOSE 8000

# TLS is terminated by the reverse proxy (Nginx Proxy Manager), so serve plain HTTP
# inside the container. Web-UI login is still required (see docker-compose.yml).
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/login').status==200 else 1)"

CMD ["python", "server.py", "--no-tls"]
