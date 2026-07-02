FROM python:3.11-slim-bookworm

# ─ Set work directory ────────────────────────────────────────────────────────
WORKDIR /app

# ─ Install system dependencies ───────────────────────────────────────────────
# Just basic build-essential if needed, but discord.py usually doesn't need much.
# Including curl for healthchecks.
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

# ─ Install python dependencies ───────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─ Copy source code ──────────────────────────────────────────────────────────
# We copy the entire root, excluding what's in .dockerignore (to be safe).
# But specifically, we want src/ to be in /app/src/
COPY src/ /app/src/

# ─ Set environment variables ─────────────────────────────────────────────────
# PYTHONDONTWRITEBYTECODE=1: Prevents Python from writing .pyc files
# PYTHONUNBUFFERED=1: Ensures that the python output is sent straight to terminal
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ─ Start the application ─────────────────────────────────────────────────────
# We run main.py from the /app directory so it can find its siblings easily.
CMD ["python", "src/main.py"]
