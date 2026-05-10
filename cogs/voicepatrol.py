"""
cogs/voicepatrol.py — Real-time GPU voice transcription via faster-whisper.
All IDs and tunable values are imported from config.py.

NOTE on DAVE (Discord E2EE — enforced March 2026):
  py-cord 2.7+ emits a RuntimeWarning when start/stop_recording is called in
  DAVE-encrypted channels because voice reception is not yet fully patched
  upstream (https://github.com/Pycord-Development/pycord/issues/3139).
  We suppress those warnings and log our own message to keep logs readable.
"""

from __future__ import annotations
import asyncio
import io
import logging
import tempfile
import re
import warnings
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


class _CompatWaveSink(WaveSink):
    """WaveSink with py-cord 2.8rc2 compatibility patch.

    start_recording in 2.8rc2 reads ``__sink_listeners__`` off the sink
    before setting it, causing an AttributeError if the attribute doesn't
    already exist.  We pre-initialise it here to an empty dict.
    """

    def __init__(self, vc: discord.VoiceClient | None = None):
        super().__init__()
        log.debug("Created _CompatWaveSink instance")
        self.vc = vc
        self.client = vc
        if not hasattr(self, "__sink_listeners__"):
            self.__sink_listeners__: dict = {}

    def walk_children(self):
        """Dummy method for py-cord 2.8rc2 compatibility."""
        yield from []

    def to_component_dict(self):
        return {}

    def _refresh_state(self, *args, **kwargs):
        pass

    def is_opus(self) -> bool:
        """WaveSink is a PCM/Wave sink, not Opus."""
        return False


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

    async def _vp_log(
        self,
        title: str,
        description: str,
        color: discord.Color = discord.Color.blurple(),
        *,
        fields: list[tuple[str, str, bool]] | None = None,
    ) -> None:
        """Send a status embed to the VoicePatrol activity log channel."""
        channel_id = getattr(config, "VOICE_PATROL_LOG_CHANNEL_ID", 0)
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return
        embed = discord.Embed(title=title, description=description, color=color)
        for name, value, inline in (fields or []):
            embed.add_field(name=name, value=value, inline=inline)
        embed.set_footer(text="VoicePatrol")
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            log.error("Failed to send VoicePatrol log embed: %s", exc)

    @commands.Cog.listener()
    async def on_ready(self):
        """On startup, join any voice channels that already have users in them."""
        if not getattr(config, "VOICE_PATROL_ENABLED", False):
            return
        guild_id_filter = getattr(config, "GUILD_ID", 0)
        guild = self.bot.get_guild(guild_id_filter) if guild_id_filter else None
        guilds = [guild] if guild else self.bot.guilds
        for g in guilds:
            if g.voice_client and g.voice_client.is_connected():
                continue  # already in a channel in this guild
            for vc_channel in g.voice_channels:
                non_bots = [m for m in vc_channel.members if not m.bot]
                if non_bots:
                    log.info(
                        "on_ready: users found in '%s' — joining.", vc_channel.name
                    )
                    try:
                        vc = await vc_channel.connect()
                        self._voice_clients[g.id] = vc
                        task = self.bot.loop.create_task(
                            self._recording_loop(vc, g.id)
                        )
                        self._recording_tasks[g.id] = task
                        await self._vp_log(
                            "🟢 Joined on Startup",
                            f"Joined **{vc_channel.name}** — {len(non_bots)} user(s) already present.",
                            discord.Color.green(),
                        )
                    except Exception:
                        log.exception("on_ready: failed to join '%s'.", vc_channel.name)
                        await self._vp_log(
                            "🔴 Startup Join Failed",
                            f"Could not join **{vc_channel.name}** — check bot permissions and logs.",
                            discord.Color.red(),
                        )
                    break  # only join one channel per guild

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if not getattr(config, "VOICE_PATROL_ENABLED", False):
            return

        if member.bot:
            return

        # Ignore events from other guilds if GUILD_ID is configured
        guild_id_filter = getattr(config, "GUILD_ID", 0)
        if guild_id_filter and member.guild.id != guild_id_filter:
            return

        guild = member.guild
        log.debug(
            "on_voice_state_update: %s | before=%s after=%s",
            member,
            before.channel.name if before.channel else None,
            after.channel.name if after.channel else None,
        )

        # guild.voice_client can be a stale (disconnected) VoiceClient object
        # after a crash/restart — always verify it's actually connected.
        vc = guild.voice_client
        bot_connected = vc is not None and vc.is_connected()

        # Clean up stale voice client so the guild state is fresh
        if vc is not None and not bot_connected:
            log.warning("Stale voice client found for guild %s — cleaning up.", guild.id)
            self._voice_clients.pop(guild.id, None)
            task = self._recording_tasks.pop(guild.id, None)
            if task:
                task.cancel()
            vc = None
            bot_connected = False
            await self._vp_log(
                "⚠️ Stale Connection Cleaned Up",
                "A disconnected voice client was found and removed. The bot will rejoin on next user event.",
                discord.Color.yellow(),
            )

        # ── Join: user is in a channel and bot is not connected ───────────────
        # Use after.channel (broadly) so we catch: fresh joins, moves between
        # channels, and server-deafen events where a user is still present.
        if after.channel is not None and not bot_connected:
            log.info("Auto-joining '%s' because %s is there.", after.channel.name, member)
            try:
                # self_deaf=False is required so we can receive incoming audio
                vc = await after.channel.connect()
                self._voice_clients[guild.id] = vc
                # Warn if the channel uses DAVE E2EE (py-cord 2.8+)
                is_dave = hasattr(vc, "is_dave_connection") and vc.is_dave_connection()
                if is_dave:
                    log.warning(
                        "Channel '%s' uses DAVE (E2EE). Voice reception is "
                        "experimental — audio may be empty until pycord#3139 lands.",
                        after.channel.name,
                    )
                task = self.bot.loop.create_task(self._recording_loop(vc, guild.id))
                self._recording_tasks[guild.id] = task
                await self._vp_log(
                    "🎙️ Joined Voice Channel",
                    f"Now monitoring **{after.channel.name}** — triggered by {member.mention}.",
                    discord.Color.green(),
                    fields=[
                        ("DAVE (E2EE)", "⚠️ Active — audio may be limited" if is_dave else "✅ Not active", True),
                        ("Model", getattr(config, "WHISPER_MODEL_SIZE", "?"), True),
                    ],
                )
            except Exception:
                # log.exception captures the full traceback, not just the message
                log.exception("Failed to connect to voice channel '%s'.", after.channel.name)
                await self._vp_log(
                    "🔴 Join Failed",
                    f"Could not join **{after.channel.name}** — check bot permissions and logs.",
                    discord.Color.red(),
                )
                return

        # ── Leave: the channel we're in just became empty ─────────────────────
        if before.channel and bot_connected and vc and vc.channel == before.channel:
            non_bots = [m for m in vc.channel.members if not m.bot]
            if not non_bots:
                log.info("Leaving '%s' because it's now empty.", vc.channel.name)
                channel_name = vc.channel.name
                task = self._recording_tasks.pop(guild.id, None)
                if task:
                    task.cancel()
                await vc.disconnect(force=True)
                self._voice_clients.pop(guild.id, None)
                await self._vp_log(
                    "🔇 Left Voice Channel",
                    f"**{channel_name}** is now empty — disconnected.",
                    discord.Color.greyple(),
                )

    async def _recording_loop(self, vc: discord.VoiceClient, guild_id: int):
        """10-second chunked recording loop.

        Errors are caught per-iteration so a single bad start_recording call
        (e.g. __sink_listeners__ AttributeError in py-cord 2.8rc2) retries
        after 5 s instead of killing the loop permanently.
        """
        while vc.is_connected():
            try:
                sink = _CompatWaveSink(vc)

                # py-cord >=2.7: callback is (exception,) — NOT (sink, *args).
                # Capture `sink` via closure so _process_audio can read its data.
                def finished_callback(exception: Exception | None) -> None:
                    if exception:
                        log.error(
                            "Recording error in guild %s: %s", guild_id, exception
                        )
                    asyncio.run_coroutine_threadsafe(
                        self._process_audio(sink, guild_id), self.bot.loop
                    )

                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    vc.start_recording(sink, finished_callback)

                # Record for 10 seconds, then stop and trigger the callback
                await asyncio.sleep(10)

                if vc.is_connected() and vc.is_recording():
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        vc.stop_recording()

            except asyncio.CancelledError:
                if vc.is_connected() and vc.is_recording():
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        vc.stop_recording()
                raise
            except Exception as e:
                log.error(
                    "Recording iteration error in guild %s: %s — retrying in 5s.",
                    guild_id, e,
                )
                await asyncio.sleep(5)


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

            # Post to VoicePatrol activity log channel
            await self._vp_log(
                "🚨 Harmful Speech Detected",
                f"**User:** {member.mention if member else display}\n**Transcription:** {text}",
                discord.Color.red(),
                fields=[("Action", "User has been server-muted", False)],
            )

            # Send evidence (audio clip) to the main log channel
            evidence_channel = self.bot.get_channel(config.LOG_CHANNEL_ID)
            if evidence_channel:
                audio_file.seek(0)  # Reset pointer to upload file
                df = discord.File(audio_file, filename=f"evidence_{user_id}.wav")
                embed = discord.Embed(
                    title="⚠️ Harmful Speech — Audio Evidence",
                    description=f"**Transcription:** {text}",
                    color=discord.Color.red()
                )
                embed.set_author(
                    name=display,
                    # py-cord 2.8: Embed.Empty removed — None is the safe default
                    icon_url=member.display_avatar.url if member else None,
                )
                embed.set_footer(text="User has been automatically server-muted.")
                try:
                    await evidence_channel.send(embed=embed, file=df)
                except Exception as e:
                    log.error("Failed to upload evidence to log channel: %s", e)


def setup(bot: "MyBot"):
    bot.add_cog(VoicePatrol(bot))
