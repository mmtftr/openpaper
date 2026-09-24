# filepath: annotated-paper/server/gunicorn.conf.py
import os

# Bind address and port
# Use environment variable PORT if available, otherwise default to 8000
port = os.getenv("PORT", "8000")
bind = f"0.0.0.0:{port}"

# Number of worker processes. Single-user deployment: a few workers are plenty
# (the CPU-based default spawned ~19 and used ~5 GB of RAM).
workers = int(os.getenv("WEB_CONCURRENCY", "3"))

# Worker class for ASGI applications (FastAPI)
worker_class = "uvicorn.workers.UvicornWorker"

# Logging
# Use '-' for stdout/stderr
accesslog = "-"
errorlog = "-"
loglevel = os.getenv(
    "GUNICORN_LOG_LEVEL", "info"
)  # e.g., debug, info, warning, error, critical

# Reload workers when code changes (useful for development, disable in production)
# reload = True

# Other settings (optional)
timeout = 300  # Workers silent for more than this many seconds are killed and restarted
keepalive = 30  # The number of seconds to wait for requests on a Keep-Alive connection
worker_connections = 1000  # Max number of simultaneous clients per worker
threads = 1  # Number of threads per worker (Uvicorn handles concurrency well, often 1 is fine)

# Environment variables to pass to workers (if needed)
# raw_env = ["VAR1=value1", "VAR2=value2"]

# Forwarded headers (if behind a proxy like Nginx)
forwarded_allow_ips = "*"  # Trust all proxies, adjust if needed
proxy_headers = True  # Enable reading proxy headers (X-Forwarded-For, etc.)
