# Resolve the Python tag to a reviewed digest during provisioning; never use a
# mutable registry tag in service.json. Build this image on the dedicated VM.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG NODE_SHA256
USER root
RUN sed -i 's@http://@https://@g' /etc/apt/sources.list.d/debian.sources \
 && apt-get update && apt-get install -y --no-install-recommends \
    bash git curl ca-certificates build-essential pkg-config ffmpeg \
    tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
RUN test -n "$NODE_SHA256" \
 && curl --proto '=https' --tlsv1.2 -fsSL \
    https://nodejs.org/dist/v20.19.4/node-v20.19.4-linux-x64.tar.gz -o /tmp/node.tar.gz \
 && printf '%s  /tmp/node.tar.gz\n' "$NODE_SHA256" | sha256sum --check \
 && tar -xzf /tmp/node.tar.gz --strip-components=1 --no-same-owner -C /usr/local \
 && rm /tmp/node.tar.gz
# The package matches requirements.txt. Browser downloads are baked into the
# image, readable by UID 1000, and not shared as a mutable host cache.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/browsers PYTHONUTF8=1 PYTHONUNBUFFERED=1
RUN python3 -m pip install --no-cache-dir playwright==1.61.0 \
 && python3 -m playwright install --with-deps chromium \
 && chmod -R a+rX /opt/browsers \
 && python3 -c 'import sys; assert sys.version_info[:2] == (3, 11)' \
 && node -e 'if (process.versions.node !== "20.19.4") process.exit(1)' \
 && mkdir -p /work /snapshot /opt/pruefdienst \
 && chown 1000:1000 /work
COPY tools/ci/container-entry.sh /opt/pruefdienst/container-entry.sh
WORKDIR /work
USER 1000:1000
ENTRYPOINT []
