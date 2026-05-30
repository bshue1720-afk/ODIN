"""
ODIN — Discord Bot (Inbound Command Interface)
Lets Brock (and team) send ODIN commands from Discord, just like Slack.

Commands work identically to Slack:
  blast tags:he,tax-delinquent workflow:<id> limit:100
  leads
  status
  scorecard <address> arv:X
  buyers
  ... (all 58+ commands supported)

Setup:
  1. Bot token set in Railway env var: DISCORD_BOT_TOKEN
  2. DISCORD_COMMAND_CHANNEL_ID — channel ID where ODIN listens (optional)
     If not set, ODIN responds to DMs and any channel it can read.
  3. Bot must have Message Content Intent enabled in Discord Developer Portal.

Run:
  Called from app.py in a background thread via start_bot()
"""

import os
import threading
import asyncio
import discord
from discord.ext import commands

DISCORD_BOT_TOKEN       = os.environ.get('DISCORD_BOT_TOKEN', '')
DISCORD_COMMAND_CHANNEL = os.environ.get('DISCORD_COMMAND_CHANNEL_ID', '')

# Authorized Discord user IDs — only these users can send ODIN commands
# Set DISCORD_AUTHORIZED_IDS as comma-separated user IDs in Railway env vars
# If empty, anyone in the server can use ODIN (not recommended)
_AUTH_IDS_RAW = os.environ.get('DISCORD_AUTHORIZED_IDS', '')
AUTHORIZED_IDS = set(_AUTH_IDS_RAW.split(',')) if _AUTH_IDS_RAW else set()

intents = discord.Intents.default()
intents.message_content = True
intents.messages        = True
intents.dm_messages     = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)


@bot.event
async def on_ready():
    print(f'[discord_bot] ODIN bot online as {bot.user} (ID: {bot.user.id})')


@bot.event
async def on_message(message: discord.Message):
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return

    # Authorization check
    if AUTHORIZED_IDS and str(message.author.id) not in AUTHORIZED_IDS:
        return

    is_dm      = isinstance(message.channel, discord.DMChannel)
    is_mention = bot.user in (message.mentions or [])

    # Respond to:
    #   1. DMs to the bot
    #   2. Mentions (@ODIN <command>)
    #   3. Messages in the designated command channel
    if not (is_dm or is_mention or
            (DISCORD_COMMAND_CHANNEL and str(message.channel.id) == DISCORD_COMMAND_CHANNEL)):
        return

    # Strip the @mention prefix if present
    content = message.content
    if is_mention:
        content = content.replace(f'<@{bot.user.id}>', '').replace(f'<@!{bot.user.id}>', '').strip()

    if not content:
        await message.channel.send('👋 Ready. Type a command or `help` to see what I can do.')
        return

    # Import here to avoid circular imports (app.py imports discord_bot)
    try:
        from utils.slack_commands import parse_command
        from utils import discord_notify
        cmd = parse_command(content)
    except Exception as e:
        await message.channel.send(f'❌ Parse error: {e}')
        return

    # Send typing indicator while processing
    async with message.channel.typing():
        try:
            reply = await asyncio.get_event_loop().run_in_executor(
                None, _execute_command, cmd, message
            )
        except Exception as e:
            reply = f'❌ Error: {e}'

    if reply:
        # Discord has a 2000 char limit per message — split if needed
        for chunk in _split(reply, 1900):
            await message.channel.send(chunk)


def _execute_command(cmd: dict, message: discord.Message) -> str:
    """
    Run the parsed command and return a reply string.
    Reuses app.py's command logic by calling the same underlying functions.
    Returns the reply as a string (Discord sends it back).
    """
    from app import _handle_discord_command
    return _handle_discord_command(cmd, discord_user=str(message.author))


def _split(text: str, limit: int) -> list:
    """Split a long message into chunks under the Discord limit."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind('\n', 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip('\n')
    return chunks


def start_bot():
    """
    Start the Discord bot in a background thread.
    Called from app.py after Flask starts.
    """
    if not DISCORD_BOT_TOKEN:
        print('[discord_bot] DISCORD_BOT_TOKEN not set — bot disabled.')
        return

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(bot.start(DISCORD_BOT_TOKEN))
        except Exception as e:
            print(f'[discord_bot] Bot crashed: {e}')

    t = threading.Thread(target=_run, name='discord-bot', daemon=True)
    t.start()
    print('[discord_bot] Bot thread started.')
