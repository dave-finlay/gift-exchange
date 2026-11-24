# The app image: Django under gunicorn, run by Docker Compose behind Caddy.
# See "Architecture" in docs/DESIGN.md. Base images are pinned by digest
# (a tag can be moved); Dependabot keeps them current.

FROM ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d AS uv

# Build: install the app and its locked dependencies into /app/.venv, and
# collect the static files.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS build
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
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
# Stopgap until the base image is rebuilt with Debian's fixes for zlib
# (CVE-2026-76844 and CVE-2026-85091) and glibc (CVE-2026-5435 and
# CVE-2026-19499), which fail the scan: install those versions, pinned,
# so a commit still decides the image's packages. Once the base has a
# package's fix, this fails the build, so its pin gets removed rather
# than lingering.
ARG FIXED="zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1 \
    libc6=2.41-12+deb13u5 libc-bin=2.41-12+deb13u5"
RUN for pin in $FIXED; do \
      package=${pin%%=*} version=${pin#*=}; \
      if dpkg --compare-versions \
           "$(dpkg-query -W -f '${Version}' "$package")" ge "$version"; then \
        echo "The base image now has $package $version: remove its pin" >&2; \
        exit 1; \
      fi; \
    done \
    && apt-get update \
    && apt-get install -y --no-install-recommends $FIXED \
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
