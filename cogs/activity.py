"""
cogs/activity.py — Daily activity rewards.
All IDs and tunable values are imported from config.py.
"""

from __future__ import annotations
import logging
from datetime import time, timezone
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

    async def _flush_cache(self):
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
        self._cache.clear()

    @commands.command(name="activity")
    async def cmd_activity(self, ctx: commands.Context):
        """Displays the top 5 most active users today."""
        await self._flush_cache()
        async with self.db.execute(
            "SELECT user_id, message_count FROM daily_activity ORDER BY message_count DESC LIMIT 5"
        ) as cur:
            top_users = await cur.fetchall()
            
        if not top_users:
            await ctx.send("No activity recorded yet today!")
            return
            
        embed = discord.Embed(title="Top 5 Most Active Users (Today)", color=discord.Color.blue())
        for idx, row in enumerate(top_users, start=1):
            embed.add_field(
                name=f"#{idx}", 
                value=f"<@{row['user_id']}>: {row['message_count']} messages", 
                inline=False
            )
        await ctx.send(embed=embed)

    @commands.command(name="activitymanualtest")
    @commands.has_role(824513909129084938)
    async def cmd_activitymanualtest(self, ctx: commands.Context):
        """Manually tests the activity payout to the top 2 chatters (Bank deposit)."""
        await self._flush_cache()
        async with self.db.execute(
            "SELECT user_id FROM daily_activity ORDER BY message_count DESC LIMIT 2"
        ) as cur:
            top_users = await cur.fetchall()
            
        if not top_users:
            await ctx.send("No activity recorded yet today!")
            return
            
        from utils import add_unb_money
        
        user_1 = top_users[0]['user_id']
        await add_unb_money(self.bot, user_1, 300, target="bank")
        mentions = [f"<@{user_1}> (300 coins)"]
        
        if len(top_users) > 1:
            user_2 = top_users[1]['user_id']
            await add_unb_money(self.bot, user_2, 200, target="bank")
            mentions.append(f"<@{user_2}> (200 coins)")
            
        await ctx.send(f"✅ Manual activity test complete! Deposited into bank for: {', '.join(mentions)}")

    @tasks.loop(time=time(hour=0, minute=0, tzinfo=timezone.utc))
    async def midnight_reset(self):
        log.info("Midnight reset triggered. Flushing %d entries.", len(self._cache))
        await self._flush_cache()
        
        async with self.db.execute(
            "SELECT user_id, message_count FROM daily_activity ORDER BY message_count DESC LIMIT ?",
            (config.ACTIVITY_TOP_N,),
        ) as cur:
            winners = await cur.fetchall()
            
        from utils import add_unb_money
        payout_channel = self.bot.get_channel(config.PAYOUT_CHANNEL_ID)
        if winners:
            paid_users = []
            for row in winners:
                if await add_unb_money(self.bot, row['user_id'], config.ACTIVITY_WINNER_REWARD):
                    paid_users.append(row['user_id'])
                    log.info("Awarded %s to user %s via API", config.ACTIVITY_WINNER_REWARD, row["user_id"])
            if payout_channel and paid_users:
                mentions = " ".join(f"<@{uid}>" for uid in paid_users)
                try:
                    await payout_channel.send(f"🎉 Midnight reset complete! Rewarded {mentions} with {config.ACTIVITY_WINNER_REWARD} coins each.")
                except discord.Forbidden:
                    pass
                
        await self.db.execute("DELETE FROM daily_activity")
        await self.db.commit()
        log.info("Daily activity reset complete.")

    @midnight_reset.before_loop
    async def before_midnight_reset(self):
        await self.bot.wait_until_ready()


def setup(bot: "MyBot"):
    bot.add_cog(Activity(bot))
