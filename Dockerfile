# VOXORYL API — CPU-friendly container for GitHub Packages (ghcr.io).
# Desktop orb / Win32 computer-use need a host install; see Releases for Win/Mac zips.
# Image: ghcr.io/vishalgaur1/voxoryl

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VOXORYL_HOST=0.0.0.0 \
    VOXORYL_PORT=3847 \
    COMPUTER_USE_ENABLED=false

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
    && rm -rf /var/lib/apt/lists/*

# Core API deps (no pywebview / pyautogui — those are host-desktop only)
COPY requirements-docker.txt .
RUN pip install --upgrade pip && pip install -r requirements-docker.txt

COPY pyproject.toml README.md LICENSE ./
COPY voxoryl ./voxoryl
COPY setup ./setup
COPY scripts ./scripts
COPY run.py ./

RUN useradd --create-home --uid 10001 voxy \
    && mkdir -p /app/data \
    && chown -R voxy:voxy /app

USER voxy

EXPOSE 3847

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:3847/api/doctor" || exit 1

CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "3847"]
