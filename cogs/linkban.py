"""
cogs/linkban.py — Auto-ban members who post links to banned Discord servers.

Banned invite codes are stored in config.BANNED_INVITE_CODES (a set of strings).
The regex catches all common Discord invite URL formats:
  - discord.gg/<code>
  - discord.com/invite/<code>
  - discordapp.com/invite/<code>
"""

from __future__ import annotations
import asyncio
import logging
import re
from typing import TYPE_CHECKING

import discord
from discord.ext import commands
import config

if TYPE_CHECKING:
    from main import MyBot

log = logging.getLogger("cogs.linkban")

# Matches any Discord invite URL variant and captures the invite code.
# Handles optional angle brackets, http/https, and trailing punctuation.
_INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com/invite|discord\.gg)"
    r"/([A-Za-z0-9_\-]{2,30})",
    re.IGNORECASE,
)


class LinkBan(commands.Cog):
    """Monitors every message for banned Discord invite links and bans the poster."""

    def __init__(self, bot: "MyBot"):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore DMs and bot messages
        if not message.guild or message.author.bot:
            return

        # Skip users with manage_messages permission (mods/admins)
        if message.author.guild_permissions.manage_messages:
            return

        banned_codes: set[str] = getattr(config, "BANNED_INVITE_CODES", set())
        if not banned_codes:
            return

        # Extract all invite codes from the message
        found_codes = _INVITE_RE.findall(message.content)
        if not found_codes:
            return

        # Normalise to lowercase for case-insensitive comparison
        hit = next(
            (code for code in found_codes if code.lower() in banned_codes),
            None,
        )
        if hit is None:
            return

        member = message.author
        guild  = message.guild
        log.warning(
            "Banned invite link detected from %s (code: %s) in #%s — banning.",
            member,
            hit,
            message.channel.name,
        )

        # 1. Delete the offending message first (best-effort)
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
        log_channel_id: int = getattr(config, "LOG_CHANNEL_ID", 0)
        log_channel = self.bot.get_channel(log_channel_id)
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


def setup(bot: "MyBot"):
    bot.add_cog(LinkBan(bot))
