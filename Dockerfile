# The app image: Django under gunicorn, run by Docker Compose behind Caddy.
# See "Architecture" in docs/DESIGN.md. Base images are pinned by digest
# (a tag can be moved); Dependabot keeps them current.

FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

# Build: install the app and its locked dependencies into /app/.venv, and
# collect the static files.
FROM python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb AS build
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
FROM python:3.13-slim@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb
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
