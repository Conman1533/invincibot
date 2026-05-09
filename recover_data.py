import asyncio
import json
import os
import aiosqlite
import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")
DB_PATH = "bot_database.db"

class RecoveryClient(discord.Client):
    async def on_ready(self):
        print(f"Logged in as {self.user} for data recovery.")
        
        os.makedirs("data", exist_ok=True)
        
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT message_id, channel_id, status FROM reports WHERE status IN ('resolved', 'dismissed')") as cur:
                rows = await cur.fetchall()
                
        print(f"Found {len(rows)} resolved/dismissed reports in the database.")
        
        recovered_count = 0
        with open("data/llm_training_data.jsonl", "a", encoding="utf-8") as f:
            for row in rows:
                channel = self.get_channel(row["channel_id"])
                if not channel:
                    try:
                        channel = await self.fetch_channel(row["channel_id"])
                    except discord.NotFound:
                        print(f"Channel {row['channel_id']} not found.")
                        continue
                    except discord.Forbidden:
                        print(f"No access to channel {row['channel_id']}.")
                        continue
                        
                if channel:
                    try:
                        msg = await channel.fetch_message(row["message_id"])
                        text = msg.content
                        if text:
                            label = "positive" if row["status"] == "resolved" else "negative"
                            f.write(json.dumps({"text": text, "label": label}) + "\n")
                            recovered_count += 1
                    except discord.NotFound:
                        print(f"Message {row['message_id']} deleted or not found.")
                    except discord.Forbidden:
                        print(f"No access to read message {row['message_id']}.")
                    except Exception as e:
                        print(f"Error fetching message {row['message_id']}: {e}")
                        
                await asyncio.sleep(0.5) # avoid rate limits
                
        print(f"Successfully recovered {recovered_count} messages into data/llm_training_data.jsonl!")
        await self.close()

if __name__ == "__main__":
    intents = discord.Intents.default()
    intents.message_content = True
    client = RecoveryClient(intents=intents)
    client.run(TOKEN)
