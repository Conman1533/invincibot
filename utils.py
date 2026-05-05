import logging
import aiohttp
import config

log = logging.getLogger("utils.economy")

async def add_unb_money(bot, user_id: int, amount: int) -> bool:
    """
    Adds money to a user's UnbelievaBoat balance via the REST API.
    Returns True if successful, False otherwise.
    """
    if not config.UNB_API_TOKEN or not config.GUILD_ID:
        log.warning("UNB_API_TOKEN or GUILD_ID is not configured. Cannot add money to user %s.", user_id)
        return False
        
    if not hasattr(bot, 'session') or not bot.session:
        log.error("Bot HTTP session is not initialized. Cannot add money.")
        return False

    url = f"https://unbelievaboat.com/api/v1/guilds/{config.GUILD_ID}/users/{user_id}"
    headers = {
        "Authorization": config.UNB_API_TOKEN,
        "Content-Type": "application/json"
    }
    payload = {
        "cash": amount
    }

    try:
        async with bot.session.patch(url, headers=headers, json=payload) as response:
            if response.status == 200:
                log.info("Successfully added %s cash to user %s via UnbelievaBoat API.", amount, user_id)
                return True
            else:
                resp_text = await response.text()
                log.error("Failed to add money via UnbelievaBoat API (status %s): %s", response.status, resp_text)
                return False
    except Exception as exc:
        log.exception("Exception occurred while calling UnbelievaBoat API for user %s: %s", user_id, exc)
        return False
