FROM python:3.12-slim-bookworm

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY app ./app
COPY migrations .//migrations
COPY alembic.ini ./
COPY shell.py ./
COPY gunicorn.config.py ./
COPY app/scripts/run_migrations.py ./app/scripts/run_migrations.py

RUN pip install uv
# Install exactly what uv.lock pins (what the test suite ran against), not a
# fresh resolve of pyproject.toml's ranges.
RUN uv export --frozen --no-dev --no-emit-project --no-hashes -o /tmp/requirements.txt \
    && uv pip install --system -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

CMD ["gunicorn", "-c", "gunicorn.config.py", "app.main:app"]
