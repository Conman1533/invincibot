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
MOD_CHANNEL_ID: int = 8379281537744240845
MOD_ROLE_ID: int = 839237255158956042  # Fill in the role ID to ping on new reports

# reporting.py / activity.py — where $add-money payouts are sent
PAYOUT_CHANNEL_ID: int = 840431174845071401

# voicepatrol.py — where transcription output is logged
LOG_CHANNEL_ID: int = 1500652094288298166

# activity.py — only messages in these channels count toward daily rewards
ALLOWED_CHANNEL_IDS: set[int] = {
    1500651913241301132,   # e.g. #general
    1500651996657746150,   # e.g. #off-topic
    824512448564035614,
    824510216511029263,
    824511312604299285,
    921816822779621416,
    1224548843018915943,
    824510216511029265,
    839964157624320058,
    839964567425253426,
    839995040494518282,
}

# ─────────────────────────────────────────────────────────────────────────────
#  REPORTING MODULE
# ─────────────────────────────────────────────────────────────────────────────

REPORT_EMOJI_NAME: str = "report"   # name of your custom :report: emoji
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

VOICE_PATROL_ENABLED: bool = False

WHISPER_MODEL_SIZE: str    = "base.en"
WHISPER_DEVICE: str        = "cuda"
WHISPER_COMPUTE_TYPE: str  = "float16"

BAD_WORDS: set[str] = {
    "squirrel",
}

# ─────────────────────────────────────────────────────────────────────────────
#  DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

DASHBOARD_HOST: str = "0.0.0.0"
DASHBOARD_PORT: int = 8081
# Simple shared secret — set via env var DASHBOARD_SECRET or change here
import os as _os
DASHBOARD_SECRET: str = _os.environ.get("DASHBOARD_SECRET", "changeme")

GITHUB_WEBHOOK_SECRET: str = _os.environ.get("GITHUB_WEBHOOK_SECRET", "")

# ─────────────────────────────────────────────────────────────────────────────
#  UNBELIEVABOAT API
# ─────────────────────────────────────────────────────────────────────────────

UNB_API_TOKEN: str = _os.environ.get("UNB_API_TOKEN", "")
GUILD_ID: int = 824510213909512192  # Fill in your Discord Server ID here
