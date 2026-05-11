import logging
import os
import random
import sqlite3
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks

DAILY_HAMMERS = 4
CHICAGO = ZoneInfo("America/Chicago")
DB_PATH = Path(os.environ.get("HAMMERS_DB", Path(__file__).parent / "hammers.db"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("crushinghammer")


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS hammers ("
        f"  user_id   INTEGER PRIMARY KEY,"
        f"  remaining INTEGER NOT NULL DEFAULT {DAILY_HAMMERS}"
        f")"
    )
    conn.commit()
    return conn


def get_remaining(conn: sqlite3.Connection, user_id: int) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO hammers (user_id, remaining) VALUES (?, ?)",
        (user_id, DAILY_HAMMERS),
    )
    row = conn.execute(
        "SELECT remaining FROM hammers WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.commit()
    return row[0]


def spend_hammer(conn: sqlite3.Connection, user_id: int) -> bool:
    cur = conn.execute(
        "UPDATE hammers SET remaining = remaining - 1 "
        "WHERE user_id = ? AND remaining > 0",
        (user_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def reset_all(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE hammers SET remaining = ?", (DAILY_HAMMERS,))
    conn.commit()


class CrushingHammerBot(discord.Client):
    def __init__(self, guild_ids: list[int]) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.guild_ids = guild_ids
        self.db = db_connect()

    async def setup_hook(self) -> None:
        register_commands(self)
        if self.guild_ids:
            for gid in self.guild_ids:
                guild = discord.Object(id=gid)
                self.tree.copy_global_to(guild=guild)
                try:
                    await self.tree.sync(guild=guild)
                    log.info("Synced commands to guild %s", gid)
                except discord.Forbidden:
                    log.warning(
                        "Can't sync to guild %s — bot isn't in that server. Skipping.",
                        gid,
                    )
        else:
            await self.tree.sync()
            log.info("Synced commands globally")
        daily_reset.start(self)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")


@tasks.loop(time=time(hour=0, minute=0, tzinfo=CHICAGO))
async def daily_reset(bot: CrushingHammerBot) -> None:
    reset_all(bot.db)
    log.info("Daily hammer reset complete")


async def use_hammer(
    bot: CrushingHammerBot,
    interaction: discord.Interaction,
    target: discord.Message,
) -> None:
    if bot.user is not None and target.author.id == bot.user.id:
        await interaction.response.send_message(
            "Can't hammer the hammer.", ephemeral=True
        )
        return

    remaining = get_remaining(bot.db, interaction.user.id)
    if remaining <= 0:
        await interaction.response.send_message(
            "Out of hammers. Resets at midnight CT.", ephemeral=True
        )
        return

    if not spend_hammer(bot.db, interaction.user.id):
        await interaction.response.send_message(
            "Out of hammers. Resets at midnight CT.", ephemeral=True
        )
        return

    heads = random.random() < 0.5
    if heads:
        try:
            await target.delete()
        except discord.Forbidden:
            await interaction.response.send_message(
                "Heads, but I lack permission to delete that message.",
                ephemeral=True,
            )
            return
        except discord.NotFound:
            pass
        await interaction.response.send_message("**Heads.**")
    else:
        await interaction.response.send_message("**Tails.**")


def register_commands(bot: CrushingHammerBot) -> None:
    @bot.tree.context_menu(name="Crushing Hammer")
    async def crushing_hammer_ctx(
        interaction: discord.Interaction, message: discord.Message
    ) -> None:
        await use_hammer(bot, interaction, message)

    @bot.tree.command(
        name="crushinghammer",
        description="Flip a coin; heads deletes the message above.",
    )
    async def crushing_hammer_slash(interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread, discord.VoiceChannel)):
            await interaction.response.send_message(
                "I can't read this channel's history.", ephemeral=True
            )
            return
        target: discord.Message | None = None
        async for msg in channel.history(limit=5):
            if bot.user is None or msg.author.id != bot.user.id:
                target = msg
                break
        if target is None:
            await interaction.response.send_message(
                "Nothing to hammer above.", ephemeral=True
            )
            return
        await use_hammer(bot, interaction, target)

    @bot.tree.command(
        name="checkhammers",
        description="Check how many hammers you have left today.",
    )
    async def check_hammers(interaction: discord.Interaction) -> None:
        remaining = get_remaining(bot.db, interaction.user.id)
        await interaction.response.send_message(
            f"You have {remaining} hammer{'s' if remaining != 1 else ''}.",
            ephemeral=True,
        )


def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN not set")
    guild_ids_raw = os.environ.get("GUILD_IDS") or os.environ.get("GUILD_ID") or ""
    guild_ids = [int(x.strip()) for x in guild_ids_raw.split(",") if x.strip()]
    bot = CrushingHammerBot(guild_ids=guild_ids)
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
