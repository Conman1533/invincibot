"""
cogs/voicepatrol.py — Real-time GPU voice transcription via faster-whisper.
All IDs and tunable values are imported from config.py.
"""

from __future__ import annotations
import asyncio
import io
import logging
import tempfile
from typing import TYPE_CHECKING
import discord
from discord.ext import commands
from discord.sinks import WaveSink
import config

if TYPE_CHECKING:
    from main import MyBot
    from faster_whisper import WhisperModel

log = logging.getLogger("cogs.voicepatrol")


def _transcribe_sync(model: "WhisperModel", audio_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
        tmp.write(audio_bytes)
        tmp.flush()
        segments, _info = model.transcribe(tmp.name, vad_filter=True, beam_size=5)
        text = " ".join(seg.text.strip() for seg in segments).strip()
    return text


class VoicePatrol(commands.Cog):
    """Real-time voice channel transcription backed by faster-whisper on CUDA."""

    def __init__(self, bot: "MyBot"):
        self.bot = bot
        self._voice_clients: dict[int, discord.VoiceClient] = {}

    @property
    def model(self) -> "WhisperModel":
        return self.bot.whisper_model

    @commands.command(name="patrol")
    async def patrol(self, ctx: commands.Context):
        if not ctx.author.voice or not ctx.author.voice.channel:
            await ctx.send("You must be in a voice channel first.")
            return
        vc_channel = ctx.author.voice.channel
        guild_id = ctx.guild.id
        if guild_id in self._voice_clients:
            await self._voice_clients[guild_id].disconnect(force=True)
        vc = await vc_channel.connect()
        self._voice_clients[guild_id] = vc
        sink = WaveSink()

        def finished_callback(sink: WaveSink, vc: discord.VoiceClient, *args):
            asyncio.run_coroutine_threadsafe(self._process_audio(sink, vc), self.bot.loop)

        vc.start_recording(sink, finished_callback, vc)
        await ctx.send(f"Patrol started in **{vc_channel.name}**. Use `!stop` to end.")

    @commands.command(name="stop")
    async def stop(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        vc = self._voice_clients.get(guild_id)
        if not vc or not vc.is_connected():
            await ctx.send("I'm not currently patrolling in this server.")
            return
        vc.stop_recording()
        await ctx.send("Patrol stopped. Processing final audio...")

    async def _process_audio(self, sink: WaveSink, vc: discord.VoiceClient):
        log_channel = self.bot.get_channel(config.LOG_CHANNEL_ID)
        guild_id = vc.guild.id
        await vc.disconnect(force=False)
        self._voice_clients.pop(guild_id, None)
        if not sink.audio_data:
            return
        await asyncio.gather(*[
            self._transcribe_user(uid, audio.file, log_channel)
            for uid, audio in sink.audio_data.items()
        ])

    async def _transcribe_user(self, user_id: int, audio_file: io.BytesIO, log_channel):
        audio_bytes = audio_file.read()
        if not audio_bytes:
            return
        try:
            text = await asyncio.to_thread(_transcribe_sync, self.model, audio_bytes)
        except Exception as exc:
            log.exception("Transcription failed for user %s: %s", user_id, exc)
            return
        if not text:
            return
        user = self.bot.get_user(user_id)
        display = str(user) if user else f"UserID:{user_id}"
        log.info("[%s] %s", display, text)
        if log_channel:
            embed = discord.Embed(description=text, color=discord.Color.blurple())
            embed.set_author(
                name=display,
                icon_url=user.display_avatar.url if user else discord.Embed.Empty,
            )
            await log_channel.send(embed=embed)


def setup(bot: "MyBot"):
    bot.add_cog(VoicePatrol(bot))
