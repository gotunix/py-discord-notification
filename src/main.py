"""
Main entry point — starts the Discord bot and the Pyramid webhook server
concurrently.

Architecture
------------

  ┌─────────────────────────────────────────────────────────┐
  │  main.py                                                 │
  │                                                          │
  │  ┌──────────────────────┐   asyncio event loop          │
  │  │  Discord Bot (async) │◄──────────────────────────┐   │
  │  │  bot.py              │                           │   │
  │  └──────────────────────┘                           │   │
  │                                                     │   │
  │  ┌──────────────────────┐   run_coroutine_threadsafe│   │
  │  │  Pyramid WSGI Server │───────────────────────────┘   │
  │  │  server.py           │  (WSGI thread → bot loop)     │
  │  │  wsgiref / waitress  │                               │
  │  └──────────────────────┘                               │
  └─────────────────────────────────────────────────────────┘

The bot runs inside an asyncio event loop on the **main thread**.
The WSGI server runs on a **background thread** via ThreadPoolExecutor.
When a webhook arrives the WSGI view calls schedule_notification() which
uses asyncio.run_coroutine_threadsafe() to safely post back to the bot loop.

Usage
-----
    python3 main.py

Or with gunicorn/waitress for the WSGI side:
    python3 main.py            # recommended (handles both in one process)
"""

import asyncio
import logging
import sys
import threading
from wsgiref.simple_server import make_server, WSGIRequestHandler

import config
import bot as discord_bot
import server as webhook_server

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("main")


# ─── Silent WSGI request logger ───────────────────────────────────────────────

class _QuietHandler(WSGIRequestHandler):
    """Log requests to the main logger instead of stderr."""
    def log_request(self, code="-", size="-"):
        log.info("WSGI %s %s → %s", self.command, self.path, code)

    def log_message(self, fmt, *args):
        pass  # suppress host-resolve noise


# ─── WSGI server thread ───────────────────────────────────────────────────────

def _run_wsgi(app, host: str, port: int, stop_event: threading.Event) -> None:
    """Run the Pyramid WSGI app in a blocking loop until stop_event is set."""
    httpd = make_server(host, port, app, handler_class=_QuietHandler)
    httpd.socket.settimeout(1.0)  # allow periodic stop checks
    log.info("Pyramid webhook server listening on http://%s:%d", host, port)

    while not stop_event.is_set():
        try:
            httpd.handle_request()
        except Exception:
            pass

    httpd.server_close()
    log.info("Pyramid webhook server stopped.")


# ─── Entry point ──────────────────────────────────────────────────────────────

async def main() -> None:
    # 1. Validate config
    try:
        config.validate()
    except RuntimeError as exc:
        log.critical("Configuration error: %s", exc)
        sys.exit(1)

    # 2. Build the Pyramid WSGI app
    wsgi_app = webhook_server.create_app()

    # 3. Wire the server module to the bot's loop + schedule function
    #    We get the *running* event loop here (we're already inside asyncio.run).
    loop = asyncio.get_running_loop()
    webhook_server.init(loop, discord_bot.schedule_notification)

    # 4. Start the WSGI thread
    stop_event = threading.Event()
    wsgi_thread = threading.Thread(
        target=_run_wsgi,
        args=(wsgi_app, config.SERVER_HOST, config.SERVER_PORT, stop_event),
        daemon=True,
        name="wsgi-webhook",
    )
    wsgi_thread.start()

    # 5. Print startup banner
    _print_banner()

    # 6. Start the Discord bot (blocks until disconnected / Ctrl-C)
    try:
        await discord_bot.start()
    except KeyboardInterrupt:
        log.info("Keyboard interrupt — shutting down …")
    finally:
        stop_event.set()
        wsgi_thread.join(timeout=5)
        log.info("Shutdown complete.")


def _print_banner() -> None:
    host = config.SERVER_HOST
    port = config.SERVER_PORT
    auth = "enabled" if config.WEBHOOK_SECRET else "DISABLED (no secret set)"
    sep  = "─" * 62

    print(f"\n  {sep}")
    print("  🤖  Discord Notifier Bot + Pyramid Webhook Server")
    print(f"  {sep}")
    print(f"  Webhook server  : http://{host}:{port}")
    print(f"  Auth            : {auth}")
    print()
    print(f"  GET  http://{host}:{port}/health")
    print(f"  POST http://{host}:{port}/webhook/notify")
    print(f"  POST http://{host}:{port}/webhook/tailscale")
    print(f"  POST http://{host}:{port}/webhook/custom")
    print(f"  GET  http://{host}:{port}/webhook/test   (fires test notification)")
    print(f"  {sep}")
    print()
    print("  Bot commands (DM only):")
    print("    !help   !status   !ping   !say <msg>")
    print("    !dm <uid> <msg>   !channel <id> <msg>   !targets")
    print(f"  {sep}\n")


if __name__ == "__main__":
    asyncio.run(main())
