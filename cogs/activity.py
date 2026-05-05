"""
cogs/activity.py — Daily activity rewards.
All IDs and tunable values are imported from config.py.
"""

from __future__ import annotations
import logging
from datetime import time
from typing import TYPE_CHECKING
import discord
from discord.ext import commands, tasks
import config

if TYPE_CHECKING:
    from main import MyBot

log = logging.getLogger("cogs.activity")


class Activity(commands.Cog):
    """Tracks daily message activity and rewards top contributors at midnight."""

    def __init__(self, bot: "MyBot"):
        self.bot = bot
        self._cache: dict[int, int] = {}
        self.midnight_reset.start()

    def cog_unload(self):
        self.midnight_reset.cancel()

    @property
    def db(self):
        return self.bot.db

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return
        if message.channel.id not in config.ALLOWED_CHANNEL_IDS:
            return
        self._cache[message.author.id] = self._cache.get(message.author.id, 0) + 1

    @tasks.loop(time=time(hour=0, minute=0))
    async def midnight_reset(self):
        log.info("Midnight reset triggered. Flushing %d entries.", len(self._cache))
        if not self._cache:
            return
        for user_id, count in self._cache.items():
            await self.db.execute(
                """
                INSERT INTO daily_activity (user_id, message_count) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    message_count = message_count + excluded.message_count
                """,
                (user_id, count),
            )
        await self.db.commit()
        async with self.db.execute(
            "SELECT user_id, message_count FROM daily_activity ORDER BY message_count DESC LIMIT ?",
            (config.ACTIVITY_TOP_N,),
        ) as cur:
            winners = await cur.fetchall()
        payout_channel = self.bot.get_channel(config.PAYOUT_CHANNEL_ID)
        if payout_channel and winners:
            for row in winners:
                await payout_channel.send(f"$add-money <@{row['user_id']}> {config.ACTIVITY_WINNER_REWARD}")
                log.info("Awarded %s to user %s", config.ACTIVITY_WINNER_REWARD, row["user_id"])
        await self.db.execute("DELETE FROM daily_activity")
        await self.db.commit()
        self._cache.clear()
        log.info("Daily activity reset complete.")

    @midnight_reset.before_loop
    async def before_midnight_reset(self):
        await self.bot.wait_until_ready()


def setup(bot: "MyBot"):
    bot.add_cog(Activity(bot))
