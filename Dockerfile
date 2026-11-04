# syntax=docker/dockerfile:1
# One image for every service in compose.yaml: nginx + the built SPA, daphne, the workers. They only
# differ by command, so one commit is one thing to build, move and roll back.
#   docker buildx build --platform linux/amd64 --build-arg RELEASE=$(git rev-parse --short HEAD) -t jac:dev --load .
FROM node:24-alpine AS spa
WORKDIR /spa
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# Debian's nginx brings the realip and sub modules the config needs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx \
    && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default

# The lock is hashed, so pip verifies every download. psycopg-binary brings its own libpq.
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

COPY backend/ ./
COPY deploy/image/ ./
COPY --from=spa /spa/dist ./spa

# Static files are baked in. DEBUG=1 only so the prod guard lets this one command pass: no secret
# is needed for it, and none enters the image. Its RELEASE, SECRET_KEY and the rest come at run time.
RUN DEBUG=1 python manage.py collectstatic --noinput

# The uploads' mount point, owned by the app user.
RUN useradd --system --uid 10001 --home-dir /app jac \
    && mkdir -p /files \
    && chown jac:jac /files
USER jac

# Last, so a new commit only rebuilds this layer.
ARG RELEASE=dev
ENV RELEASE=${RELEASE}

EXPOSE 8000
# --proxy-headers-host X-Real-IP: REMOTE_ADDR = the client nginx saw, which allauth and DRF key their
# rate limits on. X-Forwarded-For's first entry is client-written, so it's never trusted.
# -v 0: no per-request access lines. The proxy in front keeps the one log with visitors' addresses,
# and it's the one the privacy page describes.
CMD ["daphne", "-v", "0", "-b", "0.0.0.0", "-p", "8000", "--proxy-headers", "--proxy-headers-host", "X-Real-IP", "lukehirsch.asgi:application"]
