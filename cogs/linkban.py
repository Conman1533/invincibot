"""
cogs/linkban.py — Auto-ban members who post links to banned Discord servers.

Static banned codes live in config.BANNED_INVITE_CODES.
Dynamically added codes (via !addbanlink) are stored in banned_invites.json
alongside this file and merged in at startup, so they survive restarts.

URL formats caught:
  - discord.gg/<code>
  - discord.com/invite/<code>
  - discordapp.com/invite/<code>
"""

from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord.ext import commands
import config

if TYPE_CHECKING:
    from main import MyBot

log = logging.getLogger("cogs.linkban")

# Path to the JSON file that stores dynamically added codes
_DYNAMIC_FILE = Path(__file__).parent.parent / "banned_invites.json"

# Matches any Discord invite URL variant and captures the invite code.
_INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com/invite|discord\.gg)"
    r"/([A-Za-z0-9_\-]{2,30})",
    re.IGNORECASE,
)


def _load_dynamic_codes() -> set[str]:
    """Load dynamically added codes from banned_invites.json (lowercase)."""
    if not _DYNAMIC_FILE.exists():
        return set()
    try:
        data = json.loads(_DYNAMIC_FILE.read_text())
        return {c.lower() for c in data.get("codes", [])}
    except Exception as exc:
        log.error("Failed to read %s: %s", _DYNAMIC_FILE, exc)
        return set()


def _save_dynamic_codes(codes: set[str]) -> None:
    """Persist the current dynamic code set to banned_invites.json."""
    try:
        _DYNAMIC_FILE.write_text(json.dumps({"codes": sorted(codes)}, indent=2))
    except Exception as exc:
        log.error("Failed to write %s: %s", _DYNAMIC_FILE, exc)


class LinkBan(commands.Cog):
    """Monitors every message for banned Discord invite links and bans the poster."""

    def __init__(self, bot: "MyBot"):
        self.bot = bot
        # Merge static config codes with dynamically saved codes at startup
        static: set[str] = {c.lower() for c in getattr(config, "BANNED_INVITE_CODES", set())}
        dynamic: set[str] = _load_dynamic_codes()
        self._banned_codes: set[str] = static | dynamic
        log.info("LinkBan loaded with %d banned invite codes.", len(self._banned_codes))

    # ── Auto-ban listener ────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore DMs and bot messages
        if not message.guild or message.author.bot:
            return

        # Skip users with manage_messages permission (mods/admins)
        if message.author.guild_permissions.manage_messages:
            return

        if not self._banned_codes:
            return

        # Extract all invite codes from the message
        found_codes = _INVITE_RE.findall(message.content)
        if not found_codes:
            return

        # Case-insensitive match against the banned set
        hit = next(
            (code for code in found_codes if code.lower() in self._banned_codes),
            None,
        )
        if hit is None:
            return

        member = message.author
        guild  = message.guild
        log.warning(
            "Banned invite link detected from %s (code: %s) in #%s — banning.",
            member, hit, message.channel.name,
        )

        # 1. Delete the offending message (best-effort)
        try:
            await message.delete()
        except discord.HTTPException as e:
            log.error("Could not delete message from %s: %s", member, e)

        # 2. Ban the member
        ban_reason = f"LinkBan: Posted banned invite discord.gg/{hit}"
        try:
            await guild.ban(member, reason=ban_reason, delete_message_days=1)
            log.info("Banned %s for posting banned invite code '%s'.", member, hit)
        except discord.Forbidden:
            log.error("Missing permissions to ban %s.", member)
            return
        except discord.HTTPException as e:
            log.error("Failed to ban %s: %s", member, e)
            return

        # 3. Log to the mod/log channel
        log_channel = self.bot.get_channel(getattr(config, "LOG_CHANNEL_ID", 0))
        if log_channel:
            embed = discord.Embed(
                title="🔨 Member Auto-Banned",
                description=(
                    f"**User:** {member.mention} (`{member}` — ID: `{member.id}`)\n"
                    f"**Reason:** Posted a banned Discord invite\n"
                    f"**Invite code:** `discord.gg/{hit}`\n"
                    f"**Channel:** {message.channel.mention}"
                ),
                color=discord.Color.dark_red(),
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text="LinkBan — message deleted, member banned with 1 day purge")
            try:
                await log_channel.send(embed=embed)
            except discord.HTTPException as e:
                log.error("Failed to send ban log embed: %s", e)

    # ── Mod command ──────────────────────────────────────────────────────────

    @commands.command(name="addbanlink")
    async def addbanlink(self, ctx: commands.Context, invite: str):
        """Add a Discord invite link to the banned list.

        Usage: !addbanlink <discord invite URL or code>
        Requires: Mod role (MOD_ROLE_ID from config)
        """
        # Permission check — must have the mod role
        mod_role_id: int = getattr(config, "MOD_ROLE_ID", 0)
        if not any(r.id == mod_role_id for r in ctx.author.roles):
            await ctx.message.delete()
            return

        # Parse the invite code from whatever format was provided
        match = _INVITE_RE.search(invite)
        if match:
            code = match.group(1).lower()
        else:
            # Maybe they typed just the raw code with no URL
            raw = invite.strip().lstrip("/").lower()
            if re.fullmatch(r"[a-z0-9_\-]{2,30}", raw):
                code = raw
            else:
                await ctx.send(
                    "❌ Couldn't parse an invite code from that. "
                    "Try: `!addbanlink discord.gg/example`",
                    delete_after=10,
                )
                return

        if code in self._banned_codes:
            await ctx.send(
                f"ℹ️ `discord.gg/{code}` is already on the banned list.",
                delete_after=10,
            )
            return

        # Add to in-memory set immediately (takes effect on next message scan)
        self._banned_codes.add(code)

        # Persist to JSON so it survives a restart
        dynamic = _load_dynamic_codes()
        dynamic.add(code)
        _save_dynamic_codes(dynamic)

        log.info("Mod %s added banned invite code '%s' via !addbanlink.", ctx.author, code)

        await ctx.send(
            f"✅ `discord.gg/{code}` added to the banned link list. "
            f"Anyone posting it will be auto-banned.",
            delete_after=15,
        )
        # Delete the command invocation to keep mod channel tidy
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass


def setup(bot: "MyBot"):
    bot.add_cog(LinkBan(bot))
