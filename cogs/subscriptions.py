"""
cogs/subscriptions.py — Handles server subscription auto-rewards.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING
import discord
from discord.ext import commands, tasks
import config
from utils import add_unb_money

if TYPE_CHECKING:
    from main import MyBot

log = logging.getLogger("cogs.subscriptions")


class Subscriptions(commands.Cog):
    """Tracks and rewards users for their premium server subscriptions."""

    def __init__(self, bot: "MyBot"):
        self.bot = bot
        self.daily_subscription_check.start()

    def cog_unload(self):
        self.daily_subscription_check.cancel()

    @property
    def db(self):
        return self.bot.db

    async def _reward_user(self, user_id: int, months: int, source: str) -> None:
        """Helper to calculate and issue reward, then update DB."""
        # Check if already rewarded recently
        async with self.db.execute(
            "SELECT last_rewarded_at FROM subscriptions WHERE user_id = ?",
            (user_id,)
        ) as cur:
            row = await cur.fetchone()

        now = datetime.now(timezone.utc)
        if row and row["last_rewarded_at"]:
            try:
                # Convert string back to datetime
                last_rewarded = datetime.fromisoformat(row["last_rewarded_at"])
                if (now - last_rewarded).days < 25:
                    log.info("Skipping subscription reward for %s; already rewarded recently.", user_id)
                    return
            except ValueError:
                pass

        reward_amount = config.SUBSCRIPTION_BASE_REWARD + (months * config.SUBSCRIPTION_MULTIPLIER)
        
        # Give money
        paid = await add_unb_money(self.bot, user_id, reward_amount, target="bank")
        if paid:
            # Update DB
            await self.db.execute(
                """
                INSERT INTO subscriptions (user_id, months_subscribed, last_rewarded_at) 
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET 
                    months_subscribed = excluded.months_subscribed,
                    last_rewarded_at = excluded.last_rewarded_at
                """,
                (user_id, months, now.isoformat())
            )
            await self.db.commit()
            log.info("Rewarded user %s with %s coins for %s months of subscription (source: %s).", user_id, reward_amount, months, source)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Listen for the system message from Discord
        if message.type == discord.MessageType.role_subscription_purchase:
            user_id = message.author.id
            months = 1
            if hasattr(message, "role_subscription_data") and message.role_subscription_data:
                months = message.role_subscription_data.total_months_subscribed

            log.info("Caught subscription message for user %s (months: %s)", user_id, months)
            await self._reward_user(user_id, months, "system_message")

    @tasks.loop(hours=24)
    async def daily_subscription_check(self):
        """Active fallback to reward users who have the role but didn't share the system message."""
        log.info("Running daily active subscription check.")
        guild = self.bot.get_guild(config.GUILD_ID)
        if not guild:
            return

        role = guild.get_role(config.PREMIUM_ROLE_ID)
        if not role:
            log.warning("Premium role %s not found in guild %s.", config.PREMIUM_ROLE_ID, config.GUILD_ID)
            return

        now = datetime.now(timezone.utc)
        
        for member in role.members:
            # Check DB
            async with self.db.execute(
                "SELECT months_subscribed, last_rewarded_at FROM subscriptions WHERE user_id = ?",
                (member.id,)
            ) as cur:
                row = await cur.fetchone()

            if not row:
                # First time seeing this user with the role!
                await self._reward_user(member.id, 1, "daily_check_new")
            else:
                last_rewarded_at = row["last_rewarded_at"]
                months_subscribed = row["months_subscribed"]
                
                if last_rewarded_at:
                    try:
                        last_rewarded = datetime.fromisoformat(last_rewarded_at)
                        if (now - last_rewarded).days >= 30:
                            # Time for the next month's reward!
                            new_months = months_subscribed + 1
                            await self._reward_user(member.id, new_months, "daily_check_renew")
                    except ValueError:
                        pass

    @daily_subscription_check.before_loop
    async def before_daily_check(self):
        await self.bot.wait_until_ready()


def setup(bot: "MyBot"):
    bot.add_cog(Subscriptions(bot))
