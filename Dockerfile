# syntax=docker/dockerfile:1.7

# ---- build: install Python deps into a venv (wheels only, no compilers) ----
FROM python:3.12-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends binutils && rm -rf /var/lib/apt/lists/*
RUN python -m venv /venv
COPY server/requirements.txt /tmp/requirements.txt
# Slim the venv: no pip, no test suites, no .pyc, stripped native libraries.
RUN /venv/bin/pip install --no-compile --only-binary=:all: -r /tmp/requirements.txt \
 && /venv/bin/pip uninstall -y -q pip setuptools \
 && find /venv -depth -type d \( -name tests -o -name __pycache__ \) -exec rm -rf {} + \
 && find /venv -type f -name '*.so*' -exec strip --strip-unneeded {} +
COPY web /web
# Pre-compress static files once; aiohttp serves the .gz sibling automatically.
RUN find /web -type f \( -name '*.html' -o -name '*.js' -o -name '*.css' -o -name '*.svg' \) \
      -exec gzip -9 -k -n {} +

# ---- runtime ----
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
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
