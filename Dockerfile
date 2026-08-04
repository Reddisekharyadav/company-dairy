# ── WorkSense AI — Docker Image ──────────────────────────────────────────────
# Provides the web dashboard only (no native Windows tray icon).
# Suitable for company-team deployments where users access via browser.
#
# Build:  docker build -t worksense .
# Run:    docker run -p 8000:8000 -v worksense_data:/data worksense

FROM python:3.11-slim

LABEL maintainer="WorkSense AI"
LABEL description="Productivity tracker for researchers, developers & teams"

# Set environment for headless mode
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WS_HEADLESS=1 \
    WS_DATA_DIR=/data \
    APPDATA=/data

WORKDIR /app

# System deps (for Pillow, psutil)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps (excluding Windows-only pywin32/pywinauto)
COPY requirements.txt .
RUN pip install --no-cache-dir \
    fastapi uvicorn[standard] sqlalchemy psutil \
    GitPython Pillow python-docx reportlab jinja2 \
    requests python-dotenv python-multipart rich \
    && pip install --no-cache-dir -r requirements.txt || true

# Copy source
COPY . .

# Persistent data volume
VOLUME ["/data"]

# Expose dashboard port
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8000/ || exit 1

# Run with headless mode (no tkinter UI)
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
