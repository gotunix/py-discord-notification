"""
Pyramid Webhook Server
======================

Receives HTTP POST / WebSocket-compatible webhook events from external services
(Tailscale, Alertmanager, generic scripts, etc.) and forwards them to Discord
via the running bot.

Endpoints
---------
GET  /health              — Liveness probe (no auth)
POST /webhook/notify      — Generic JSON notification → Discord
POST /webhook/tailscale   — Tailscale-formatted event → Discord
POST /webhook/custom      — Fully custom embed payload → Discord
GET  /webhook/test        — Fire a test notification (auth required)

Authentication
--------------
All /webhook/* routes (except /health) require:

    Authorization: Bearer <WEBHOOK_SECRET>

Set WEBHOOK_SECRET="" to disable (not recommended in production).

Payload Reference
-----------------
POST /webhook/notify
    {
        "title":       "optional title",
        "description": "required — body text",
        "severity":    "info|success|warning|error|critical",   // optional
        "channel_id":  "123456...",   // override NOTIFY_CHANNEL_ID
        "user_ids":    ["uid1", ...]  // override NOTIFY_USER_IDS
    }

POST /webhook/tailscale
    Standard Tailscale webhook event body:
    {
        "timestamp": "2024-01-01T00:00:00Z",
        "version":   1,
        "type":      "tailnet-member-added",
        "tailnet":   "example.com",
        "message":   "New node joined: laptop-01"
    }

POST /webhook/custom
    Full Discord embed:
    {
        "title":       "str",
        "description": "str",
        "color":       16711680,         // decimal int (optional)
        "fields":      [["Name","Val",true], ...],
        "footer":      "str (optional)",
        "channel_id":  "...",
        "user_ids":    [...]
    }
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from functools import wraps

from pyramid.config import Configurator
from pyramid.request import Request
from pyramid.response import Response
from pyramid.httpexceptions import HTTPBadRequest, HTTPUnauthorized, HTTPForbidden

import config

log = logging.getLogger(__name__)

# ─── Severity → colour mapping ───────────────────────────────────────────────

_SEVERITY_MAP: dict[str, tuple[int, str]] = {
    "info":     (0x3498DB, "ℹ️  Info"),
    "success":  (0x2ECC71, "✅ Success"),
    "warning":  (0xE67E22, "⚠️  Warning"),
    "error":    (0xE74C3C, "❌ Error"),
    "critical": (0xFF0000, "🚨 Critical"),
}

# ─── Tailscale event type → severity ─────────────────────────────────────────

_TAILSCALE_SEVERITY: dict[str, str] = {
    "tailnet-member-added":            "success",
    "tailnet-member-expired":          "warning",
    "tailnet-member-approved":         "success",
    "tailnet-member-removed":          "warning",
    "tailnet-member-updated":          "info",
    "node-created":                    "success",
    "node-deleted":                    "warning",
    "node-key-expiry-disabled":        "info",
    "node-key-expired":                "warning",
    "user-created":                    "success",
    "user-deleted":                    "warning",
    "user-approved":                   "success",
    "user-suspended":                  "error",
    "user-role-updated":               "info",
    "user-invited-to-tailnet":         "info",
    "dns-settings-updated":            "info",
    "acl-updated":                     "info",
    "acl-approved":                    "success",
    "posture-integration-added":       "info",
    "posture-integration-removed":     "warning",
}

# ─── The bot loop reference ──────────────────────────────────────────────────
# Injected at startup from main.py so the WSGI thread can schedule coroutines.

_bot_loop: asyncio.AbstractEventLoop | None = None
_schedule_notification = None   # Callable from bot.py


def init(loop: asyncio.AbstractEventLoop, schedule_fn) -> None:
    """Called once by main.py after the bot loop is ready."""
    global _bot_loop, _schedule_notification
    _bot_loop = loop
    _schedule_notification = schedule_fn


# ─── Auth helper ─────────────────────────────────────────────────────────────

def _check_auth(request: Request) -> None:
    """Raise HTTPUnauthorized/HTTPForbidden if the secret doesn't match."""
    if not config.WEBHOOK_SECRET:
        return  # Auth disabled

    # 1. Check query parameter ?token=secret
    query_token = request.GET.get("token") or request.GET.get("t")
    if query_token:
        # Check against webhook secret
        if query_token == config.WEBHOOK_SECRET:
            return  # Valid
        else:
            log.warning("Webhook auth failed: invalid token in query string.")
            raise HTTPForbidden(json_body={"error": "Invalid webhook secret in query parameters."})

    # 2. Check Authorization header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        log.warning("Webhook auth failed: missing or invalid Bearer header.")
        raise HTTPUnauthorized(
            json_body={"error": "Missing Authorization header or ?token= URL parameter."}
        )
    
    token = auth_header[7:].strip()
    if token != config.WEBHOOK_SECRET:
        log.warning("Webhook auth failed: invalid Bearer token.")
        raise HTTPForbidden(json_body={"error": "Invalid webhook secret."})


def require_auth(fn):
    """Decorator that calls _check_auth before the view."""
    @wraps(fn)
    def wrapper(request: Request) -> Response:
        _check_auth(request)
        return fn(request)
    return wrapper


# ─── JSON response helper ─────────────────────────────────────────────────────

def _json(data: dict, status: int = 200) -> Response:
    return Response(
        json_body=data,
        content_type="application/json",
        status=status,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Views ────────────────────────────────────────────────────────────────────

def health(request: Request) -> Response:
    """GET /health — No auth, simple liveness probe."""
    return _json({
        "status": "ok",
        "service": "discord-notifier-webhook",
        "timestamp": _now(),
        "bot_loop_alive": _bot_loop is not None and _bot_loop.is_running(),
    })


@require_auth
def webhook_notify(request: Request) -> Response:
    """
    POST /webhook/notify

    Generic text notification.  Body fields:
      - description (required)
      - title       (optional, default "Notification")
      - severity    (optional: info|success|warning|error|critical)
      - channel_id  (optional override)
      - user_ids    (optional override, JSON array of strings)
    """
    try:
        body = request.json_body
    except Exception:
        raise HTTPBadRequest(json_body={"error": "Request body must be valid JSON."})

    # Accommodate payloads that are arrays of objects
    events = body if isinstance(body, list) else [body]
    processed_count = 0

    for event in events:
        if not isinstance(event, dict):
            continue

        description = event.get("description", "").strip()
        if not description:
            continue  # ignore items in the array without a description

        severity_key = event.get("severity", "info").lower()
        color, prefix = _SEVERITY_MAP.get(severity_key, _SEVERITY_MAP["info"])
        title = event.get("title") or prefix

        channel_id = event.get("channel_id") or None
        user_ids   = event.get("user_ids") or None

        _fire(
            title=title,
            description=description,
            color=color,
            channel_id=channel_id,
            user_ids=user_ids,
            footer=f"Source: generic webhook • {_now()}",
        )
        processed_count += 1

    if processed_count == 0:
        raise HTTPBadRequest(json_body={"error": "No valid events with 'description' found in payload."})

    return _json({"status": "queued", "processed_events": processed_count})


@require_auth
def webhook_tailscale(request: Request) -> Response:
    """
    POST /webhook/tailscale

    Handles Tailscale's standard webhook envelope.
    Configure your Tailscale webhook URL as:
        http://<host>:<port>/webhook/tailscale
    with the same secret set as WEBHOOK_SECRET.
    """
    try:
        body = request.json_body
    except Exception:
        raise HTTPBadRequest(json_body={"error": "Request body must be valid JSON."})

    # Tailscale sends an array of events
    if not isinstance(body, list):
        # Fallback in case they ever send a single object
        events = [body]
    else:
        events = body

    processed = []
    for event in events:
        if not isinstance(event, dict):
            continue

        event_type = event.get("type", "unknown")
        tailnet    = event.get("tailnet", "")
        message    = event.get("message", "")
        timestamp  = event.get("timestamp", _now())

        severity = _TAILSCALE_SEVERITY.get(event_type, "info")
        color, _ = _SEVERITY_MAP[severity]

        title = f"🔒 Tailscale — {event_type.replace('-', ' ').title()}"
        description = message or f"Event `{event_type}` received from tailnet `{tailnet}`."

        fields: list[tuple[str, str, bool]] = [
            ("Event Type", f"`{event_type}`", True),
        ]
        if tailnet:
            fields.append(("Tailnet", tailnet, True))
        if timestamp:
            fields.append(("Event Time", timestamp, False))

        _fire(
            title=title,
            description=description,
            color=color,
            fields=fields,
            footer=f"Tailscale webhook • received {_now()}",
        )
        processed.append(event_type)

    return _json({"status": "queued", "processed_events": processed})


@require_auth
def webhook_custom(request: Request) -> Response:
    """
    POST /webhook/custom

    Send a fully customised Discord embed.  Body fields:
      - title        (required)
      - description  (required)
      - color        (optional int, e.g. 16711680 for red)
      - fields       (optional [[name, value, inline], ...])
      - footer       (optional str)
      - channel_id   (optional override)
      - user_ids     (optional override, array of strings)
    """
    try:
        body = request.json_body
    except Exception:
        raise HTTPBadRequest(json_body={"error": "Request body must be valid JSON."})

    events = body if isinstance(body, list) else [body]
    processed_count = 0

    for event in events:
        if not isinstance(event, dict):
            continue

        title = (event.get("title") or "").strip()
        description = (event.get("description") or "").strip()
        if not title or not description:
            continue

        try:
            color = int(event.get("color", 0x5865F2))
        except (ValueError, TypeError):
            color = 0x5865F2

        raw_fields = event.get("fields", [])
        fields: list[tuple[str, str, bool]] = [
            (f[0], f[1], bool(f[2]) if len(f) > 2 else True)
            for f in raw_fields
            if isinstance(f, (list, tuple)) and len(f) >= 2
        ]
        footer     = event.get("footer")
        channel_id = event.get("channel_id") or None
        user_ids   = event.get("user_ids") or None

        _fire(
            title=title,
            description=description,
            color=color,
            fields=fields if fields else None,
            footer=footer,
            channel_id=channel_id,
            user_ids=user_ids,
        )
        processed_count += 1

    if processed_count == 0:
        raise HTTPBadRequest(json_body={"error": "No valid custom events with 'title' and 'description' found."})

    return _json({"status": "queued", "processed_events": processed_count})


@require_auth
def webhook_test(request: Request) -> Response:
    """GET /webhook/test — Fire a test notification to all configured targets."""
    _fire(
        title="🧪 Test Notification",
        description="Webhook pipeline is working correctly.",
        color=0x2ECC71,
        fields=[
            ("Server", f"{config.SERVER_HOST}:{config.SERVER_PORT}", True),
            ("Auth",   "Enabled" if config.WEBHOOK_SECRET else "Disabled", True),
            ("Time",   _now(), False),
        ],
        footer="Triggered via GET /webhook/test",
    )
    return _json({"status": "queued", "message": "Test notification fired."})


# ─── Internal dispatch ────────────────────────────────────────────────────────

def _fire(**kwargs) -> None:
    """Send kwargs to the bot's notification dispatcher."""
    if _schedule_notification is None or _bot_loop is None:
        log.error("Bot loop not initialised — cannot dispatch notification.")
        return
    _schedule_notification(_bot_loop, **kwargs)


# ─── App Factory ─────────────────────────────────────────────────────────────

def create_app() -> object:
    """Build and return the Pyramid WSGI application."""
    with Configurator() as cfg:
        # Health
        cfg.add_route("health",           "/health")
        cfg.add_view(health, route_name="health", request_method="GET",
                     renderer="json")

        # Webhook endpoints
        cfg.add_route("webhook_notify",    "/webhook/notify")
        cfg.add_route("webhook_tailscale", "/webhook/tailscale")
        cfg.add_route("webhook_custom",    "/webhook/custom")
        cfg.add_route("webhook_test",      "/webhook/test")

        cfg.add_view(webhook_notify,    route_name="webhook_notify",
                     request_method="POST")
        cfg.add_view(webhook_tailscale, route_name="webhook_tailscale",
                     request_method="POST")
        cfg.add_view(webhook_custom,    route_name="webhook_custom",
                     request_method="POST")
        cfg.add_view(webhook_test,      route_name="webhook_test",
                     request_method="GET")

        app = cfg.make_wsgi_app()
    return app
