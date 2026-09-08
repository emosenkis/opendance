FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        libasound2 libdbus-1-3 libegl1 libfontconfig1 libgl1 libglib2.0-0 \
        libpipewire-0.3-0 libpulse0 libudev1 libva2 libwayland-client0 \
        libx11-6 libx11-xcb1 libxau6 \
        libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
        libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-shm0 \
        libxcb-sync1 libxcb-util1 libxcb-xfixes0 libxcb-xkb1 libxext6 \
        libxkbcommon0 libxkbcommon-x11-0 libxrandr2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN python -m pip install --no-cache-dir uv==0.12.7 \
    && uv sync --frozen --no-dev --extra vision --no-install-project \
    && uv cache clean
RUN mkdir -p /app/models \
    && python -c "from hashlib import sha256; from pathlib import Path; from urllib.request import urlretrieve; p=Path('/app/models/yolo26n-pose.pt'); urlretrieve('https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-pose.pt', p); assert sha256(p.read_bytes()).hexdigest() == 'eb3bb8268828aeaf515cec23a4bfafd793944a86fe9af94ba7823609c14522a9'"
COPY README.md LICENSE ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra vision --no-editable

ENV PATH="/app/.venv/bin:$PATH" \
    OPENDANCE_MODEL=/app/models/yolo26n-pose.pt \
    QT_MEDIA_BACKEND=ffmpeg \
    XDG_CACHE_HOME=/data/cache \
    XDG_CONFIG_HOME=/data/config
RUN opendance-extract --diagnostics
VOLUME ["/data"]
LABEL org.opencontainers.image.licenses="AGPL-3.0-only"
CMD ["opendance"]
