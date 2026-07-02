"""
Configuration for the Discord Notifier Bot + Pyramid Webhook Server.

All settings are loaded from environment variables (or a .env file if
python-dotenv is installed).  See .env.example for reference.
"""

import os

# ── Try to load a .env file if python-dotenv is available ──────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── Discord Bot ────────────────────────────────────────────────────────────

# Bot token from the Discord Developer Portal.
DISCORD_BOT_TOKEN: str = os.environ.get("DISCORD_BOT_TOKEN", "")

# Comma-separated Discord user IDs allowed to send commands to the bot via DM.
# Example: "123456789012345678,987654321098765432"
ALLOWED_USER_IDS: list[str] = [
    uid.strip()
    for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
]

# ─── Notification Targets ───────────────────────────────────────────────────

# Default Discord channel ID to post webhook-triggered notifications to.
# If blank the bot will only send DMs (to NOTIFY_USER_IDS).
NOTIFY_CHANNEL_ID: str = os.environ.get("NOTIFY_CHANNEL_ID", "")

# Comma-separated Discord user IDs that always receive DM notifications when
# a webhook event arrives.
NOTIFY_USER_IDS: list[str] = [
    uid.strip()
    for uid in os.environ.get("NOTIFY_USER_IDS", "").split(",")
    if uid.strip()
]

# ─── Webhook Server (Pyramid) ────────────────────────────────────────────────

# Host/port for the Pyramid server.
SERVER_HOST: str = os.environ.get("SERVER_HOST", "0.0.0.0")
SERVER_PORT: int = int(os.environ.get("SERVER_PORT", "8765"))

# Optional shared secret for authenticating incoming webhook requests.
# Senders must include:  Authorization: Bearer <WEBHOOK_SECRET>
# Leave blank to disable auth (not recommended in production).
WEBHOOK_SECRET: str = os.environ.get("WEBHOOK_SECRET", "")

# ─── Validation ─────────────────────────────────────────────────────────────

def validate() -> None:
    """Raise an error if required configuration is missing."""
    if not DISCORD_BOT_TOKEN:
        raise RuntimeError(
            "DISCORD_BOT_TOKEN is not set. "
            "Add it to your environment or .env file."
        )
    if not NOTIFY_CHANNEL_ID and not NOTIFY_USER_IDS and not ALLOWED_USER_IDS:
        raise RuntimeError(
            "Set at least one of NOTIFY_CHANNEL_ID, NOTIFY_USER_IDS, or ALLOWED_USER_IDS "
            "so the bot knows where to send webhook notifications."
        )
