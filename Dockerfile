# Ouroboros — Docker image for local runtime
# Usage:
#   docker build -t ouroboros-web .
#   docker run --rm -p 8765:8765 ouroboros-web

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_HOME=/ouroboros

WORKDIR ${APP_HOME}

# System dependencies (git + Playwright/Chromium native libs installed via playwright install-deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl build-essential ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies from the repo's runtime requirements.
COPY requirements.txt requirements-diarization.txt requirements-whisperx.txt ./
ARG OUROBOROS_INSTALL_DIARIZATION=0
ARG OUROBOROS_INSTALL_WHISPERX=0
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt \
    && if [ "$OUROBOROS_INSTALL_DIARIZATION" = "1" ]; then \
         python -m pip install --no-cache-dir -r requirements-diarization.txt; \
       fi \
    && if [ "$OUROBOROS_INSTALL_WHISPERX" = "1" ]; then \
         python -m pip install --no-cache-dir -r requirements-whisperx.txt; \
       fi

# Install all Playwright native system dependencies for Chromium/WebKit (authoritative list from Playwright)
RUN python3 -m playwright install-deps chromium webkit

# Install Playwright Chromium/WebKit browser binaries so browser tools work out of the box
RUN PLAYWRIGHT_BROWSERS_PATH=0 python3 -m playwright install chromium webkit

# Copy application
COPY . .

# Default environment
ENV OUROBOROS_SERVER_HOST=0.0.0.0 \
    OUROBOROS_SERVER_PORT=8765 \
    OUROBOROS_DATA_DIR=/ouroboros/data \
    OUROBOROS_REPO_DIR=/ouroboros \
    OUROBOROS_FILE_BROWSER_DEFAULT=${APP_HOME}

EXPOSE 8765

ENTRYPOINT ["python", "-m", "ouroboros.cli"]
CMD ["server"]
