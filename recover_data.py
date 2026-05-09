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
        
        # Import config to get MOD_CHANNEL_ID
        import config
        mod_channel_id = config.MOD_CHANNEL_ID
        
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            # Select bot_msg_id and status. This is the bot's embed in the mod channel!
            async with db.execute("SELECT bot_msg_id, status FROM reports WHERE status IN ('resolved', 'dismissed') AND bot_msg_id IS NOT NULL") as cur:
                rows = await cur.fetchall()
                
        print(f"Found {len(rows)} resolved/dismissed reports with an embed in the database.")
        
        recovered_count = 0
        mod_channel = self.get_channel(mod_channel_id)
        if not mod_channel:
            try:
                mod_channel = await self.fetch_channel(mod_channel_id)
            except Exception as e:
                print(f"Could not fetch Mod Channel: {e}")
                await self.close()
                return

        with open("data/llm_training_data.jsonl", "a", encoding="utf-8") as f:
            for row in rows:
                try:
                    msg = await mod_channel.fetch_message(row["bot_msg_id"])
                    if msg.embeds:
                        text = msg.embeds[0].description
                        # Avoid saving if it has no text content
                        if text and text != "*[no text content]*":
                            label = "positive" if row["status"] == "resolved" else "negative"
                            f.write(json.dumps({"text": text, "label": label}) + "\n")
                            recovered_count += 1
                except discord.NotFound:
                    print(f"Mod Embed {row['bot_msg_id']} deleted or not found.")
                except discord.Forbidden:
                    print(f"No access to read message {row['bot_msg_id']}.")
                except Exception as e:
                    print(f"Error fetching message {row['bot_msg_id']}: {e}")
                    
                await asyncio.sleep(0.5) # avoid rate limits
                
        print(f"Successfully recovered {recovered_count} messages into data/llm_training_data.jsonl!")
        await self.close()

if __name__ == "__main__":
    intents = discord.Intents.default()
    client = RecoveryClient(intents=intents)
    client.run(TOKEN)
