# Migration job image: applies database/migrations/*.sql forward-only, once
# each, recording versions in schema_migrations. Runs on every
# `docker compose up`, exits 0 when nothing is pending.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /migrate

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY migrate.py ./
COPY migrations ./migrations
COPY tests ./tests

RUN useradd --create-home --uid 10001 migrator && chown -R migrator:migrator /migrate
USER migrator

CMD ["python", "migrate.py"]
