"""
config.py — Single source of truth for all bot configuration.
Edit this file instead of digging through individual cogs.

The dashboard can also hot-reload these values at runtime via
  importlib.reload(config)
"""

# ─────────────────────────────────────────────────────────────────────────────
#  CHANNEL IDs
# ─────────────────────────────────────────────────────────────────────────────

# reporting.py — where moderator report embeds are posted
MOD_CHANNEL_ID: int = 1500652032200016085

# reporting.py / activity.py — where $add-money payouts are sent
PAYOUT_CHANNEL_ID: int = 1500928000714211460

# voicepatrol.py — where transcription output is logged
LOG_CHANNEL_ID: int = 1500652094288298166

# activity.py — only messages in these channels count toward daily rewards
ALLOWED_CHANNEL_IDS: set[int] = {
    1500651913241301132,   # e.g. #general
    1500651996657746150,   # e.g. #off-topic
}

# ─────────────────────────────────────────────────────────────────────────────
#  REPORTING MODULE
# ─────────────────────────────────────────────────────────────────────────────

REPORT_EMOJI_NAME: str = "🐀"   # name of your custom :report: emoji
RESOLVE_EMOJI: str     = "✅"
BOUNTY_AMOUNT: int     = 100        # currency awarded per reporter on resolution

# ─────────────────────────────────────────────────────────────────────────────
#  ACTIVITY MODULE
# ─────────────────────────────────────────────────────────────────────────────

ACTIVITY_TOP_N: int        = 2     # number of daily winners
ACTIVITY_WINNER_REWARD: int = 500  # currency per winner

# ─────────────────────────────────────────────────────────────────────────────
#  VOICE PATROL MODULE
# ─────────────────────────────────────────────────────────────────────────────

WHISPER_MODEL_SIZE: str    = "base.en"
WHISPER_DEVICE: str        = "cuda"
WHISPER_COMPUTE_TYPE: str  = "float16"

# ─────────────────────────────────────────────────────────────────────────────
#  DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

DASHBOARD_HOST: str = "0.0.0.0"
DASHBOARD_PORT: int = 8080
# Simple shared secret — set via env var DASHBOARD_SECRET or change here
import os as _os
DASHBOARD_SECRET: str = _os.environ.get("DASHBOARD_SECRET", "changeme")

# ─────────────────────────────────────────────────────────────────────────────
#  UNBELIEVABOAT API
# ─────────────────────────────────────────────────────────────────────────────

UNB_API_TOKEN: str = _os.environ.get("UNB_API_TOKEN", "")
GUILD_ID: int = 0  # Fill in your Discord Server ID here
