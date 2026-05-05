"""
cogs/voicepatrol.py — Real-time GPU voice transcription via faster-whisper.
All IDs and tunable values are imported from config.py.
"""

from __future__ import annotations
import asyncio
import io
import logging
import tempfile
import re
from typing import TYPE_CHECKING
import discord
from discord.ext import commands, tasks
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
        self._recording_tasks: dict[int, asyncio.Task] = {}

    @property
    def model(self) -> "WhisperModel":
        return self.bot.whisper_model

    def cog_unload(self):
        for task in self._recording_tasks.values():
            task.cancel()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return
            
        guild = member.guild
        vc = guild.voice_client
        
        # Someone joined a voice channel and we are not in one yet
        if after.channel and not vc:
            log.info("Auto-joining %s because %s joined.", after.channel.name, member)
            try:
                vc = await after.channel.connect()
                self._voice_clients[guild.id] = vc
                task = self.bot.loop.create_task(self._recording_loop(vc, guild.id))
                self._recording_tasks[guild.id] = task
            except Exception as e:
                log.error("Failed to connect to voice channel: %s", e)
                
        # Check if we should leave (everyone left the channel we are in)
        if before.channel and vc and vc.channel == before.channel:
            non_bots = [m for m in vc.channel.members if not m.bot]
            if not non_bots:
                log.info("Leaving %s because it's empty.", vc.channel.name)
                task = self._recording_tasks.pop(guild.id, None)
                if task:
                    task.cancel()
                await vc.disconnect(force=True)
                self._voice_clients.pop(guild.id, None)

    async def _recording_loop(self, vc: discord.VoiceClient, guild_id: int):
        try:
            while vc.is_connected():
                sink = WaveSink()
                
                # Callback to process audio chunks
                def finished_callback(sink, *args):
                    asyncio.run_coroutine_threadsafe(self._process_audio(sink, guild_id), self.bot.loop)
                    
                vc.start_recording(sink, finished_callback)
                
                # Record for 10 seconds, then stop and trigger callback
                await asyncio.sleep(10)
                
                if vc.is_connected():
                    vc.stop_recording()
        except asyncio.CancelledError:
            if vc.is_connected():
                vc.stop_recording()
            raise
        except Exception as e:
            log.error("Error in recording loop for guild %s: %s", guild_id, e)

    async def _process_audio(self, sink: WaveSink, guild_id: int):
        if not sink.audio_data:
            return
        log_channel = self.bot.get_channel(config.LOG_CHANNEL_ID)
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return
            
        tasks_list = []
        for uid, audio in sink.audio_data.items():
            tasks_list.append(self._transcribe_and_check(uid, audio.file, log_channel, guild))
            
        await asyncio.gather(*tasks_list)

    async def _transcribe_and_check(self, user_id: int, audio_file: io.BytesIO, log_channel, guild: discord.Guild):
        audio_file.seek(0)
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
            
        member = guild.get_member(user_id)
        display = str(member) if member else f"UserID:{user_id}"
        
        # Check for bad words
        text_lower = text.lower()
        found_bad_word = False
        
        for word in getattr(config, "BAD_WORDS", []):
            if re.search(rf'\b{re.escape(word)}\b', text_lower):
                found_bad_word = True
                break
                
        if found_bad_word:
            log.warning("Harmful speech detected from %s: %s", display, text)
            
            # Auto Mute
            if member:
                try:
                    await member.edit(mute=True, reason="VoicePatrol: Harmful speech detected.")
                except discord.Forbidden:
                    log.error("Missing permissions to server-mute %s", display)
            
            # Send to log channel
            if log_channel:
                audio_file.seek(0)  # Reset pointer to upload file
                df = discord.File(audio_file, filename=f"evidence_{user_id}.wav")
                embed = discord.Embed(
                    title="⚠️ Harmful Speech Detected", 
                    description=f"**Transcription:** {text}",
                    color=discord.Color.red()
                )
                embed.set_author(
                    name=display,
                    icon_url=member.display_avatar.url if member else discord.Embed.Empty,
                )
                embed.set_footer(text="User has been automatically server-muted.")
                try:
                    await log_channel.send(embed=embed, file=df)
                except Exception as e:
                    log.error("Failed to upload evidence to log channel: %s", e)


def setup(bot: "MyBot"):
    bot.add_cog(VoicePatrol(bot))
