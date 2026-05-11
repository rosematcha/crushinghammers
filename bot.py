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
    cols = {row[1] for row in conn.execute("PRAGMA table_info(hammers)").fetchall()}
    if cols and "guild_id" not in cols:
        log.info("Migrating: dropping legacy hammers table without guild_id")
        conn.execute("DROP TABLE hammers")
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS hammers ("
        f"  guild_id  INTEGER NOT NULL,"
        f"  user_id   INTEGER NOT NULL,"
        f"  remaining INTEGER NOT NULL DEFAULT {DAILY_HAMMERS},"
        f"  PRIMARY KEY (guild_id, user_id)"
        f")"
    )
    conn.commit()
    return conn


def get_remaining(conn: sqlite3.Connection, guild_id: int, user_id: int) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO hammers (guild_id, user_id, remaining) VALUES (?, ?, ?)",
        (guild_id, user_id, DAILY_HAMMERS),
    )
    row = conn.execute(
        "SELECT remaining FROM hammers WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    conn.commit()
    return row[0]


def spend_hammer(conn: sqlite3.Connection, guild_id: int, user_id: int) -> bool:
    cur = conn.execute(
        "UPDATE hammers SET remaining = remaining - 1 "
        "WHERE guild_id = ? AND user_id = ? AND remaining > 0",
        (guild_id, user_id),
    )
    conn.commit()
    return cur.rowcount > 0


def reset_all(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE hammers SET remaining = ?", (DAILY_HAMMERS,))
    conn.commit()


def env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


class CrushingHammerBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.db = db_connect()
        self._cleared_guild_commands = False
        if env_truthy("RESET_ON_START"):
            reset_all(self.db)
            log.info("RESET_ON_START set; cleared all hammer counts")

    async def setup_hook(self) -> None:
        register_commands(self)
        await self.tree.sync()
        log.info("Synced commands globally")
        daily_reset.start(self)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")
        if env_truthy("CLEAR_GUILD_COMMANDS") and not self._cleared_guild_commands:
            self._cleared_guild_commands = True
            for guild in self.guilds:
                self.tree.clear_commands(guild=guild)
                try:
                    await self.tree.sync(guild=guild)
                    log.info("Cleared per-guild commands from guild %s", guild.id)
                except discord.Forbidden:
                    log.warning(
                        "Can't clear per-guild commands for guild %s — missing permission",
                        guild.id,
                    )


@tasks.loop(time=time(hour=0, minute=0, tzinfo=CHICAGO))
async def daily_reset(bot: CrushingHammerBot) -> None:
    reset_all(bot.db)
    log.info("Daily hammer reset complete")


async def use_hammer(
    bot: CrushingHammerBot,
    interaction: discord.Interaction,
    target: discord.Message,
) -> None:
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Crushing Hammer only works in servers.", ephemeral=True
        )
        return
    if bot.user is not None and target.author.id == bot.user.id:
        await interaction.response.send_message(
            "Can't hammer the hammer.", ephemeral=True
        )
        return

    guild_id = interaction.guild_id
    remaining = get_remaining(bot.db, guild_id, interaction.user.id)
    if remaining <= 0:
        await interaction.response.send_message(
            "Out of hammers. Resets at midnight CT.", ephemeral=True
        )
        return

    if not spend_hammer(bot.db, guild_id, interaction.user.id):
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
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Crushing Hammer only works in servers.", ephemeral=True
            )
            return
        remaining = get_remaining(bot.db, interaction.guild_id, interaction.user.id)
        await interaction.response.send_message(
            f"You have {remaining} hammer{'s' if remaining != 1 else ''}.",
            ephemeral=True,
        )


def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN not set")
    bot = CrushingHammerBot()
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
