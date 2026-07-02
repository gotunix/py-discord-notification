"""
Discord Notifier Bot
====================

A discord.py bot that:

  * Responds to commands sent as **Direct Messages** (primary interface).
  * Can send messages to a configured **channel** on demand or via webhook event.
  * Forwards incoming Pyramid webhook events to Discord (DM and/or channel).

Available DM Commands
---------------------
!help              — Show this help message
!status            — Show bot + server status
!ping              — Latency check
!say <message>     — Post <message> to the configured notification channel
!dm <uid> <msg>    — Send a DM to Discord user <uid>
!channel <id> <m>  — Post to an arbitrary channel by ID (admin only)
"""

import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

import config

log = logging.getLogger(__name__)

# ─── Intents ─────────────────────────────────────────────────────────────────
# We need: message_content (to read DM commands), guilds, members, dm_messages.

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# ─── Bot Instance ─────────────────────────────────────────────────────────────

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,       # We supply our own !help
    description="Discord Notifier Bot — commands via DM, notifications via webhook.",
)

# ─── Helpers ─────────────────────────────────────────────────────────────────


def is_allowed_user(ctx: commands.Context) -> bool:
    """Return True if the author is in the ALLOWED_USER_IDS list (or the list is empty)."""
    if not config.ALLOWED_USER_IDS:
        return True
    return str(ctx.author.id) in config.ALLOWED_USER_IDS


def dm_only():
    """Custom check: command must be sent as a DM."""
    async def predicate(ctx: commands.Context) -> bool:
        if ctx.guild is not None:
            await ctx.send("⚠️  This command only works in Direct Messages.")
            return False
        return True
    return commands.check(predicate)


def allowed_user():
    """Custom check: sender must be in ALLOWED_USER_IDS."""
    async def predicate(ctx: commands.Context) -> bool:
        if not is_allowed_user(ctx):
            await ctx.send("🚫 You are not authorised to use this bot.")
            return False
        return True
    return commands.check(predicate)


def build_embed(
    title: str,
    description: str,
    color: discord.Color = discord.Color.blurple(),
    fields: list[tuple[str, str, bool]] | None = None,
    footer: str | None = None,
) -> discord.Embed:
    """Convenience wrapper around discord.Embed."""
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    for name, value, inline in (fields or []):
        embed.add_field(name=name, value=value, inline=inline)
    if footer:
        embed.set_footer(text=footer)
    return embed


# ─── Event: on_ready ──────────────────────────────────────────────────────────

@bot.event
async def on_ready() -> None:
    log.info("Logged in as %s (id=%s)", bot.user, bot.user.id)
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="for webhook events — !help",
        )
    )
    print(f"  ✅  Bot ready: {bot.user} ({bot.user.id})")


# ─── Event: on_message (raw DM passthrough) ──────────────────────────────────

@bot.event
async def on_message(message: discord.Message) -> None:
    """
    Forward messages through the command processor.
    Also log any DM that does *not* start with the command prefix so the
    operator can see what users are typing.
    """
    if message.author.bot:
        return

    # Process commands first
    await bot.process_commands(message)

    # Log non-command DMs
    if (
        isinstance(message.channel, discord.DMChannel)
        and not message.content.startswith(bot.command_prefix)
    ):
        log.info(
            "DM from %s (%s): %s",
            message.author,
            message.author.id,
            message.content[:100],
        )


# ─── Commands ─────────────────────────────────────────────────────────────────

@bot.command(name="help")
@dm_only()
@allowed_user()
async def cmd_help(ctx: commands.Context) -> None:
    """Show available commands."""
    embed = build_embed(
        title="🤖 Discord Notifier Bot",
        description=(
            "I accept commands via **Direct Message**. "
            "I also forward incoming webhook events to Discord."
        ),
        color=discord.Color.blurple(),
        fields=[
            ("!help",           "This help message", False),
            ("!status",         "Show bot & server status", False),
            ("!ping",           "Check bot latency", False),
            ("!say <message>",  "Post a message to the notification channel", False),
            ("!dm <uid> <msg>", "Send a DM to a Discord user by ID", False),
            ("!channel <id> <message>",
                                "Post to an arbitrary channel by ID", False),
            ("!targets",        "Show configured notification targets", False),
        ],
        footer="Commands work in DMs only.",
    )
    await ctx.send(embed=embed)


@bot.command(name="ping")
@dm_only()
@allowed_user()
async def cmd_ping(ctx: commands.Context) -> None:
    """Check bot latency."""
    latency_ms = round(bot.latency * 1000)
    await ctx.send(f"🏓 Pong! Latency: **{latency_ms} ms**")


@bot.command(name="status")
@dm_only()
@allowed_user()
async def cmd_status(ctx: commands.Context) -> None:
    """Show bot + configuration status."""
    channel_info = (
        f"<#{config.NOTIFY_CHANNEL_ID}> (`{config.NOTIFY_CHANNEL_ID}`)"
        if config.NOTIFY_CHANNEL_ID
        else "_not configured_"
    )
    dm_targets = (
        ", ".join(f"`{uid}`" for uid in config.NOTIFY_USER_IDS)
        if config.NOTIFY_USER_IDS
        else "_none_"
    )
    embed = build_embed(
        title="📊 Bot Status",
        description="Current configuration and connectivity.",
        color=discord.Color.green(),
        fields=[
            ("Bot User",             str(bot.user), True),
            ("Latency",              f"{round(bot.latency * 1000)} ms", True),
            ("Guilds",               str(len(bot.guilds)), True),
            ("Notify Channel",       channel_info, False),
            ("Notify DM Targets",    dm_targets, False),
            ("Webhook Server",
             f"`{config.SERVER_HOST}:{config.SERVER_PORT}`", False),
            ("Webhook Auth",
             "✅ Enabled" if config.WEBHOOK_SECRET else "⚠️ Disabled", True),
        ],
    )
    await ctx.send(embed=embed)


@bot.command(name="say")
@dm_only()
@allowed_user()
async def cmd_say(ctx: commands.Context, *, message: str) -> None:
    """Post a message to the configured notification channel.

    Usage:  !say Hello everyone!
    """
    if not config.NOTIFY_CHANNEL_ID:
        await ctx.send("❌ `NOTIFY_CHANNEL_ID` is not configured.")
        return

    channel = bot.get_channel(int(config.NOTIFY_CHANNEL_ID))
    if channel is None:
        await ctx.send(
            f"❌ Could not find channel `{config.NOTIFY_CHANNEL_ID}`. "
            "Make sure the bot is a member of that server/channel."
        )
        return

    await channel.send(message)
    await ctx.send(f"✅ Message sent to <#{config.NOTIFY_CHANNEL_ID}>.")


@bot.command(name="dm")
@dm_only()
@allowed_user()
async def cmd_dm(ctx: commands.Context, user_id: str, *, message: str) -> None:
    """Send a DM to any Discord user by ID.

    Usage:  !dm 123456789012345678 Hey there!
    """
    try:
        user = await bot.fetch_user(int(user_id))
    except (discord.NotFound, ValueError):
        await ctx.send(f"❌ Could not find user with ID `{user_id}`.")
        return

    try:
        await user.send(message)
        await ctx.send(f"✅ DM sent to **{user}** (`{user.id}`).")
    except discord.Forbidden:
        await ctx.send(
            f"❌ Cannot DM **{user}** — they may have DMs disabled."
        )


@bot.command(name="channel")
@dm_only()
@allowed_user()
async def cmd_channel(ctx: commands.Context, channel_id: str, *, message: str) -> None:
    """Post a message to an arbitrary channel by ID.

    Usage:  !channel 123456789012345678 Hello from the bot!
    """
    try:
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            channel = await bot.fetch_channel(int(channel_id))
    except (discord.NotFound, ValueError):
        await ctx.send(f"❌ Could not find channel with ID `{channel_id}`.")
        return

    try:
        await channel.send(message)
        await ctx.send(f"✅ Message sent to <#{channel_id}>.")
    except discord.Forbidden:
        await ctx.send(
            f"❌ Cannot post in channel `{channel_id}` — missing permissions."
        )


@bot.command(name="targets")
@dm_only()
@allowed_user()
async def cmd_targets(ctx: commands.Context) -> None:
    """Show where webhook notifications will be delivered."""
    lines: list[str] = []
    if config.NOTIFY_CHANNEL_ID:
        lines.append(f"📢 Channel: <#{config.NOTIFY_CHANNEL_ID}>")
    for uid in config.NOTIFY_USER_IDS:
        lines.append(f"💬 DM user: `{uid}`")
    if not lines:
        lines.append("_No targets configured._")
    await ctx.send("\n".join(lines))


# ─── Notification Helper (called by the Pyramid server) ───────────────────────

async def dispatch_notification(
    title: str,
    description: str,
    color: int = 0x5865F2,          # Discord blurple
    fields: list[tuple[str, str, bool]] | None = None,
    footer: str | None = None,
    channel_id: str | None = None,  # override NOTIFY_CHANNEL_ID
    user_ids: list[str] | None = None,  # override NOTIFY_USER_IDS
) -> None:
    """
    Send a rich embed notification to the configured channel and/or DM targets.

    This coroutine is scheduled from the Pyramid request thread via
    ``asyncio.run_coroutine_threadsafe()``.
    """
    embed = build_embed(
        title=title,
        description=description,
        color=discord.Color(color),
        fields=fields,
        footer=footer or "Discord Notifier • via webhook",
    )

    target_channel_id = channel_id or config.NOTIFY_CHANNEL_ID
    target_user_ids   = user_ids or config.NOTIFY_USER_IDS or config.ALLOWED_USER_IDS

    # Post to channel
    if target_channel_id:
        channel = bot.get_channel(int(target_channel_id))
        if channel:
            try:
                await channel.send(embed=embed)
                log.info("Notification sent to channel %s", target_channel_id)
            except Exception as exc:
                log.error("Failed to send to channel %s: %s", target_channel_id, exc)
        else:
            log.warning("Channel %s not found or bot not in guild.", target_channel_id)

    # DM each target user
    for uid in target_user_ids:
        try:
            user = await bot.fetch_user(int(uid))
            await user.send(embed=embed)
            log.info("Notification DM sent to user %s", uid)
        except Exception as exc:
            log.error("Failed to DM user %s: %s", uid, exc)


def schedule_notification(loop: asyncio.AbstractEventLoop, **kwargs) -> None:
    """
    Thread-safe bridge: schedule dispatch_notification() on the bot's event loop.

    Called from the Pyramid WSGI thread.
    """
    asyncio.run_coroutine_threadsafe(dispatch_notification(**kwargs), loop)


# ─── Runner ──────────────────────────────────────────────────────────────────

async def start() -> None:
    """Start the bot (called from main.py)."""
    await bot.start(config.DISCORD_BOT_TOKEN)
