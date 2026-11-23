# syntax=docker/dockerfile:1.7

# Base image pinned by version AND digest: every rebuild uses exactly the image the
# release was tested with (a moving tag can change underneath you). To update, pick
# a newer tag and take its digest from `docker buildx imagetools inspect python:<tag>`.
ARG PYTHON_IMAGE=python:3.12.16-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

# ---- build: install Python deps into a venv (wheels only, no compilers) ----
FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends binutils && rm -rf /var/lib/apt/lists/*
RUN python -m venv /venv
COPY server/requirements.txt /tmp/requirements.txt
# requirements.txt is a lock: exact versions of every package with hashes, so pip
# installs exactly what the tests ran against or fails loudly (never "latest").
# Slim the venv: no pip, no test suites, no .pyc, stripped native libraries.
RUN /venv/bin/pip install --no-compile --only-binary=:all: --require-hashes -r /tmp/requirements.txt \
 && /venv/bin/pip uninstall -y -q pip setuptools \
 && find /venv -depth -type d \( -name tests -o -name __pycache__ \) -exec rm -rf {} + \
 && find /venv -type f -name '*.so*' -exec strip --strip-unneeded {} +
COPY web /web
# Pre-compress static files once; aiohttp serves the .gz sibling automatically.
RUN find /web -type f \( -name '*.html' -o -name '*.js' -o -name '*.css' -o -name '*.svg' \) \
      -exec gzip -9 -k -n {} +

# ---- runtime ----
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PATH=/venv/bin:$PATH \
    DATA_DIR=/data \
    WEB_DIR=/app/web \
    PORT=8080
# libportaudio2: sound card capture, libopus0 + libmp3lame0: audio for phones.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libportaudio2 libopus0 libmp3lame0 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --gid audio --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /data && chown app:audio /data
COPY --from=build /venv /venv
WORKDIR /app/server
COPY server/app ./app
COPY --from=build /web /app/web
USER app
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/healthz',timeout=2)" || exit 1
CMD ["python", "-m", "app"]
