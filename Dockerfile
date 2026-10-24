# The app image: Django under gunicorn, run by Docker Compose behind Caddy.
# See "Architecture" in docs/DESIGN.md. Base images are pinned by digest
# (a tag can be moved); Dependabot keeps them current.

FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv

# Build: install the app and its locked dependencies into /app/.venv, and
# collect the static files.
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
# Dependencies first, so they're cached when only the app changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable
RUN DJANGO_DEBUG=1 DATA_DIR=/tmp/data STATIC_ROOT=/app/static \
    /app/.venv/bin/django-admin collectstatic --noinput \
    --settings giftexchange.settings

# Run: the same base, without uv or the source, as an unprivileged user.
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home \
       --shell /usr/sbin/nologin app \
    && mkdir /data && chown app:app /data
COPY --from=build /app /app
COPY deploy/start.sh /app/start.sh
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DJANGO_SETTINGS_MODULE=giftexchange.settings \
    DATA_DIR=/data \
    STATIC_ROOT=/app/static \
    SECRETS_DIR=/run/secrets
USER app
WORKDIR /data
EXPOSE 8000
CMD ["/app/start.sh"]
