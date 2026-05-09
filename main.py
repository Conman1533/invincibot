"""
main.py — Bot entry point.
Loads faster-whisper onto the V100, initialises the database,
starts the web dashboard, and dynamically loads every cog.
"""
import dotenv
dotenv.load_dotenv()

import asyncio
import logging
import os
from pathlib import Path

import aiohttp
import aiosqlite
import discord
from discord.ext import commands
from faster_whisper import WhisperModel

import config

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot")

BOT_TOKEN: str = os.environ["DISCORD_BOT_TOKEN"]
DB_PATH: str   = "bot_database.db"
COGS_DIR: Path = Path(__file__).parent / "cogs"

# ─── Database ─────────────────────────────────────────────────────────────────
CREATE_TABLES_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS reports (
    report_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL UNIQUE,
    channel_id INTEGER NOT NULL,
    status     TEXT    NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS reporters (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(report_id),
    user_id   INTEGER NOT NULL,
    UNIQUE(report_id, user_id)
);

CREATE TABLE IF NOT EXISTS daily_activity (
    user_id       INTEGER PRIMARY KEY,
    message_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS subscriptions (
    user_id INTEGER PRIMARY KEY,
    months_subscribed INTEGER NOT NULL DEFAULT 1,
    last_rewarded_at TIMESTAMP
);
"""


async def init_db(path: str) -> aiosqlite.Connection:
    db = await aiosqlite.connect(path)
    db.row_factory = aiosqlite.Row
    await db.executescript(CREATE_TABLES_SQL)
    
    # Safe migration
    try:
        await db.execute("ALTER TABLE reports ADD COLUMN bot_msg_id INTEGER")
    except Exception:
        pass
        
    await db.commit()
    log.info("Database initialised at %s", path)
    return db


# ─── Bot class ────────────────────────────────────────────────────────────────
class MyBot(commands.Bot):
    def __init__(self, whisper_model: WhisperModel, db: aiosqlite.Connection):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)
        self.whisper_model = whisper_model
        self.db = db

    async def start(self, *args, **kwargs):
        self.session = aiohttp.ClientSession()
        await super().start(*args, **kwargs)

    async def on_ready(self):
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)

    async def close(self):
        if hasattr(self, 'session') and self.session:
            await self.session.close()
        await self.db.close()
        log.info("Database connection closed.")
        await super().close()


# ─── Entry point ──────────────────────────────────────────────────────────────
async def main():
    log.info("Loading faster-whisper model onto CUDA ...")
    whisper_model = WhisperModel(
        config.WHISPER_MODEL_SIZE,
        device=config.WHISPER_DEVICE,
        compute_type=config.WHISPER_COMPUTE_TYPE,
    )
    log.info("Whisper model ready.")

    db = await init_db(DB_PATH)
    bot = MyBot(whisper_model=whisper_model, db=db)

    for cog_file in sorted(COGS_DIR.glob("*.py")):
        if cog_file.stem == "__init__":
            continue
        extension = f"cogs.{cog_file.stem}"
        try:
            bot.load_extension(extension)
            log.info("Loaded extension: %s", extension)
        except Exception as exc:
            log.exception("Failed to load extension %s: %s", extension, exc)

    # Start the dashboard alongside the bot
    from dashboard.server import create_app
    import uvicorn
    app = create_app(bot)
    dashboard_cfg = uvicorn.Config(
        app,
        host=config.DASHBOARD_HOST,
        port=config.DASHBOARD_PORT,
        log_level="info",
    )
    server = uvicorn.Server(dashboard_cfg)

    await asyncio.gather(
        bot.start(BOT_TOKEN),
        server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
