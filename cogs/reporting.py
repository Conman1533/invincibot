"""
cogs/reporting.py — Bounty / report system.
All IDs and tunable values are imported from config.py.
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING
import discord
from discord.ext import commands
import config

if TYPE_CHECKING:
    from main import MyBot

log = logging.getLogger("cogs.reporting")


class Reporting(commands.Cog):
    """Handles community reports and mod resolution bounties."""

    def __init__(self, bot: "MyBot"):
        self.bot = bot
        # bot embed message_id -> report_id (in-memory; see notes in main README)
        self._embed_to_report: dict[int, int] = {}

    @property
    def db(self):
        return self.bot.db

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _get_or_create_report(self, message_id: int, channel_id: int) -> tuple[int, bool]:
        async with self.db.execute(
            "SELECT report_id FROM reports WHERE message_id = ?", (message_id,)
        ) as cur:
            row = await cur.fetchone()
        if row:
            return row["report_id"], False
        async with self.db.execute(
            "INSERT INTO reports (message_id, channel_id, status) VALUES (?, ?, 'pending')",
            (message_id, channel_id),
        ) as cur:
            report_id = cur.lastrowid
        await self.db.commit()
        return report_id, True

    async def _add_reporter(self, report_id: int, user_id: int) -> bool:
        try:
            await self.db.execute(
                "INSERT INTO reporters (report_id, user_id) VALUES (?, ?)",
                (report_id, user_id),
            )
            await self.db.commit()
            return True
        except Exception:
            return False

    async def _build_report_embed(self, report_id: int, reported_message: discord.Message) -> discord.Embed:
        embed = discord.Embed(
            title=f"New Report  [ID: {report_id}]",
            description=reported_message.content or "*[no text content]*",
            color=discord.Color.yellow(),
            timestamp=reported_message.created_at,
        )
        embed.set_author(
            name=str(reported_message.author),
            icon_url=reported_message.author.display_avatar.url,
        )
        embed.add_field(
            name="Reported User",
            value=f"{reported_message.author.mention} (ID: `{reported_message.author.id}`)",
            inline=False
        )
        embed.add_field(
            name="Channel",
            value=f"{reported_message.channel.mention} (ID: `{reported_message.channel.id}`)",
            inline=False
        )
        embed.add_field(name="Jump to Message", value=f"[Click here]({reported_message.jump_url})", inline=False)
        
        if reported_message.attachments:
            first_img = next((a for a in reported_message.attachments if a.content_type and a.content_type.startswith("image/")), None)
            if first_img:
                embed.set_image(url=first_img.url)
                
            urls = "\n".join(f"[{a.filename}]({a.url})" for a in reported_message.attachments)
            embed.add_field(name="Attachments", value=urls, inline=False)
        
        async with self.db.execute(
            "SELECT user_id FROM reporters WHERE report_id = ?", (report_id,)
        ) as cur:
            rows = await cur.fetchall()
            
        if rows:
            reporters_str = ", ".join(f"<@{row['user_id']}>" for row in rows)
            embed.add_field(name=f"Reporters ({len(rows)})", value=reporters_str, inline=False)
            
        embed.set_footer(text=f"React {config.RESOLVE_EMOJI} to pay bounties. React {getattr(config, 'DISMISS_EMOJI', '❌')} to dismiss.")
        return embed

    def _log_training_data(self, content: str, label: str) -> None:
        import json
        import os
        os.makedirs("data", exist_ok=True)
        with open("data/llm_training_data.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"text": content, "label": label}) + "\n")

    def _get_gif_urls(self, message: discord.Message) -> list[str]:
        urls = []
        for e in message.embeds:
            if e.url and (e.type == "gifv" or "tenor.com" in e.url or "giphy.com" in e.url):
                urls.append(e.url)
        return urls

    async def _send_mod_embed(self, report_id: int, reported_message: discord.Message) -> None:
        mod_channel = self.bot.get_channel(config.MOD_CHANNEL_ID)
        if not mod_channel:
            log.warning("MOD_CHANNEL_ID %s not found.", config.MOD_CHANNEL_ID)
            return
            
        embed = await self._build_report_embed(report_id, reported_message)
        
        content = ""
        if getattr(config, "MOD_ROLE_ID", 0):
            content = f"<@&{config.MOD_ROLE_ID}> New Report!"
            
        try:
            bot_msg = await mod_channel.send(content=content, embed=embed)
            self._embed_to_report[bot_msg.id] = report_id
            
            # Save to DB for persistence across reboots
            await self.db.execute("UPDATE reports SET bot_msg_id = ? WHERE report_id = ?", (bot_msg.id, report_id))
            await self.db.commit()
            
            log.info("Mod embed sent (msg_id=%s) for report_id=%s", bot_msg.id, report_id)
            
            gif_urls = self._get_gif_urls(reported_message)
            if gif_urls:
                await bot_msg.reply(content="\n".join(gif_urls))
                
        except discord.Forbidden:
            log.error("Missing permissions to send messages or embed links in MOD_CHANNEL_ID (%s).", config.MOD_CHANNEL_ID)

    async def _update_mod_embed(self, report_id: int, reported_message: discord.Message) -> None:
        mod_channel = self.bot.get_channel(config.MOD_CHANNEL_ID)
        if not mod_channel:
            return
            
        bot_msg_id = next((k for k, v in self._embed_to_report.items() if v == report_id), None)
        if not bot_msg_id:
            # Fallback to DB
            async with self.db.execute("SELECT bot_msg_id FROM reports WHERE report_id = ?", (report_id,)) as cur:
                row = await cur.fetchone()
                if row and row["bot_msg_id"]:
                    bot_msg_id = row["bot_msg_id"]
                    
        if not bot_msg_id:
            return
            
        try:
            bot_msg = await mod_channel.fetch_message(bot_msg_id)
            embed = await self._build_report_embed(report_id, reported_message)
            await bot_msg.edit(embed=embed)
        except Exception as exc:
            log.warning("Could not update reporters in mod embed: %s", exc)

    async def _reward_mod_private(self, user_id: int, action: str) -> None:
        """Sends a private DM to the moderator about their reward."""
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            await user.send(f"👮 You've been rewarded with {config.MOD_REWARD_AMOUNT} coins (deposited to bank) for {action} a report.")
        except Exception:
            log.warning("Could not send private reward DM to user %s.", user_id)

    # ── listeners ────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):   
        log.info("Reaction received: emoji_name=%s emoji_id=%s channel=%s user=%s", payload.emoji.name, payload.emoji.id, payload.channel_id, payload.user_id)
        
        if payload.user_id == self.bot.user.id:
            return

        emoji = payload.emoji
        if emoji.name == config.REPORT_EMOJI_NAME:
            await self._handle_report_reaction(payload)
        elif str(emoji) == config.RESOLVE_EMOJI and payload.channel_id == config.MOD_CHANNEL_ID:
            await self._handle_resolve_reaction(payload)
        elif str(emoji) == getattr(config, "DISMISS_EMOJI", "❌") and payload.channel_id == config.MOD_CHANNEL_ID:
            await self._handle_dismiss_reaction(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id:
            return

        emoji = payload.emoji
        if emoji.name == config.REPORT_EMOJI_NAME:
            async with self.db.execute(
                "SELECT report_id FROM reports WHERE message_id = ?", (payload.message_id,)
            ) as cur:
                row = await cur.fetchone()
            
            if not row:
                return
                
            report_id = row["report_id"]
            
            await self.db.execute(
                "DELETE FROM reporters WHERE report_id = ? AND user_id = ?",
                (report_id, payload.user_id)
            )
            await self.db.commit()
            
            channel = self.bot.get_channel(payload.channel_id)
            if channel:
                try:
                    message = await channel.fetch_message(payload.message_id)
                    await self._update_mod_embed(report_id, message)
                except discord.NotFound:
                    pass

    async def _handle_report_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        channel = self.bot.get_channel(payload.channel_id)
        if channel is None:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return
        report_id, is_new = await self._get_or_create_report(payload.message_id, payload.channel_id)
        async with self.db.execute(
            "SELECT status FROM reports WHERE report_id = ?", (report_id,)
        ) as cur:
            row = await cur.fetchone()
        if row and row["status"] in ("resolved", "dismissed"):
            return
        newly_added = await self._add_reporter(report_id, payload.user_id)
        if is_new:
            await self._send_mod_embed(report_id, message)
        elif newly_added:
            log.info("User %s added to existing report (id=%s)", payload.user_id, report_id)
            await self._update_mod_embed(report_id, message)

    async def _handle_resolve_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        report_id = self._embed_to_report.get(payload.message_id)
        if report_id is None:
            # Fallback to DB
            async with self.db.execute("SELECT report_id FROM reports WHERE bot_msg_id = ?", (payload.message_id,)) as cur:
                row = await cur.fetchone()
                if row:
                    report_id = row["report_id"]
                    
        if report_id is None:
            return
            
        await self.db.execute(
            "UPDATE reports SET status = 'resolved' WHERE report_id = ?", (report_id,)
        )
        await self.db.commit()
        async with self.db.execute(
            "SELECT user_id FROM reporters WHERE report_id = ?", (report_id,)
        ) as cur:
            rows = await cur.fetchall()
        from utils import add_unb_money
        payout_channel = self.bot.get_channel(config.PAYOUT_CHANNEL_ID)
        paid_users = []
        for row in rows:
            if await add_unb_money(self.bot, row['user_id'], config.BOUNTY_AMOUNT):
                paid_users.append(row['user_id'])
                
        mod_paid = await add_unb_money(self.bot, payload.user_id, config.MOD_REWARD_AMOUNT, target="bank")
        
        if payout_channel and (paid_users or mod_paid):
            mentions = " ".join(f"<@{uid}>" for uid in paid_users)
            msg = f"✅ Paid {config.BOUNTY_AMOUNT} coins to {mentions}." if paid_users else "✅ Report resolved."
            if mod_paid:
                await self._reward_mod_private(payload.user_id, "resolving")
            msg += " Make sure to deposit your coins with $dep all"
            try:
                await payout_channel.send(msg)
            except discord.Forbidden:
                pass
        log.info("Report %s resolved; paid %s reporters via API.", report_id, len(paid_users))
        mod_channel = self.bot.get_channel(config.MOD_CHANNEL_ID)
        if mod_channel:
            try:
                bot_msg = await mod_channel.fetch_message(payload.message_id)
                resolved_embed = bot_msg.embeds[0].copy()
                
                # Log for LLM training
                text = resolved_embed.description
                if text and text != "*[no text content]*":
                    self._log_training_data(text, "positive")
                    
                resolved_embed.color = discord.Color.green()
                resolved_embed.title = resolved_embed.title.replace("New Report", "Resolved")
                await bot_msg.edit(embed=resolved_embed)
            except Exception as exc:
                log.warning("Could not update mod embed: %s", exc)
        self._embed_to_report.pop(payload.message_id, None)

    async def _handle_dismiss_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        report_id = self._embed_to_report.get(payload.message_id)
        if report_id is None:
            # Fallback to DB
            async with self.db.execute("SELECT report_id FROM reports WHERE bot_msg_id = ?", (payload.message_id,)) as cur:
                row = await cur.fetchone()
                if row:
                    report_id = row["report_id"]
                    
        if report_id is None:
            return
            
        await self.db.execute(
            "UPDATE reports SET status = 'dismissed' WHERE report_id = ?", (report_id,)
        )
        await self.db.commit()
        
        from utils import add_unb_money
        mod_paid = await add_unb_money(self.bot, payload.user_id, config.MOD_REWARD_AMOUNT, target="bank")
        if mod_paid:
            await self._reward_mod_private(payload.user_id, "dismissing")
        
        log.info("Report %s dismissed.", report_id)
        
        mod_channel = self.bot.get_channel(config.MOD_CHANNEL_ID)
        if mod_channel:
            try:
                bot_msg = await mod_channel.fetch_message(payload.message_id)
                dismissed_embed = bot_msg.embeds[0].copy()
                
                # Log for LLM training
                text = dismissed_embed.description
                if text and text != "*[no text content]*":
                    self._log_training_data(text, "negative")
                    
                dismissed_embed.color = discord.Color.red()
                dismissed_embed.title = dismissed_embed.title.replace("New Report", "Dismissed")
                await bot_msg.edit(embed=dismissed_embed)
            except Exception as exc:
                log.warning("Could not update mod embed: %s", exc)
                
        self._embed_to_report.pop(payload.message_id, None)


def setup(bot: "MyBot"):
    bot.add_cog(Reporting(bot))
