# The app image: Django under gunicorn, run by Docker Compose behind Caddy.
# See "Architecture" in docs/DESIGN.md. Base images are pinned by digest
# (a tag can be moved); Dependabot keeps them current.

FROM ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 AS uv

# Build: install the app and its locked dependencies into /app/.venv, and
# collect the static files.
FROM python:3.13-slim@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc AS build
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
FROM python:3.13-slim@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc
# Stopgap until the base image is rebuilt with Debian's zlib fix (for
# CVE-2026-76844 and CVE-2026-85091, which fail the scan): install that
# version, pinned, so a commit still decides the image's zlib. Once the
# base has it, this fails the build, so the step gets removed rather
# than lingering. See docs/CI-FAILURES.md.
ARG ZLIB_FIXED=1:1.3.dfsg+really1.3.1-1+deb13u1
RUN if dpkg --compare-versions "$(dpkg-query -W -f '${Version}' zlib1g)" \
         ge "$ZLIB_FIXED"; then \
      echo "The base image now has zlib $ZLIB_FIXED: remove this step" >&2; \
      exit 1; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends "zlib1g=$ZLIB_FIXED" \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home \
       --shell /usr/sbin/nologin app \
    && mkdir /data && chown app:app /data
COPY --from=build /app /app
COPY deploy/start.sh /app/start.sh
# LANG: the image's locale is set here, not left to the base image
# (C.UTF-8 is the only one installed).
ENV PATH=/app/.venv/bin:$PATH \
    LANG=C.UTF-8 \
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
