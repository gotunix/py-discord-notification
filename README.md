# Discord Notifier (Pyramid Webhook Proxy)

A Dockerized asynchronous Discord notifier that exposes a **Pyramid Webhook Server**. It concurrently runs a Discord bot to forward notifications (standard text, Tailscale status, or custom embeds) to your chosen channels or DM users.

## 🚀 Quick Start (Docker)

1. **Prepare Configuration**:
   ```bash
   cp .env.example .env
   # Open .env and set your DISCORD_BOT_TOKEN, NOTIFY_CHANNEL_ID, etc.
   ```

2. **Launch Container**:
   ```bash
   docker compose up -d
   ```

3. **Verify Health**:
   ```bash
   curl http://localhost:8765/health
   # Expected: {"status": "ok", "service": "discord-notifier-webhook", ...}
   ```

---

## 🛠️ Architecture

The app has been refactored for a clean containerized structure:

- **`/src`**: Contains all core Python logic (`main.py`, `bot.py`, `server.py`, `config.py`).
- **`Dockerfile`**: Streamlined Debian-based image (Python 3.11).
- **`docker-compose.yaml`**: Standard orchestration with health-checks and environment loading.

### Webhook Endpoints (Auth required via `WEBHOOK_SECRET`)

- **POST `/webhook/notify`**: Simple generic notifications.
- **POST `/webhook/tailscale`**: Formatted Tailscale node/user status events.
- **POST `/webhook/custom`**: Fully custom Discord embeds (supports colors, fields, footers).
- **GET `/webhook/test`**: Fires a test ping to all configured targets.

---

## 🐳 Running with Docker Compose

When you run `docker compose up`, the service internally binds to port `8765`. 
It uses **`unless-stopped`** restart policy to ensure your notification gateway stays alive through system reboots or crashes.

To check logs:
```bash
docker compose logs -f
```

To update the source code:
```bash
docker compose up --build -d
```
