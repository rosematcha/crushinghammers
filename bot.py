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


DOUBLE_JEOPARDY_MODES = ("off", "user", "server")


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
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS guild_settings ("
        f"  guild_id          INTEGER PRIMARY KEY,"
        f"  max_daily_hammers INTEGER NOT NULL DEFAULT {DAILY_HAMMERS},"
        f"  double_jeopardy   TEXT    NOT NULL DEFAULT 'off'"
        f")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS protected_channels ("
        "  guild_id   INTEGER NOT NULL,"
        "  channel_id INTEGER NOT NULL,"
        "  PRIMARY KEY (guild_id, channel_id)"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS hammer_attempts ("
        "  guild_id   INTEGER NOT NULL,"
        "  message_id INTEGER NOT NULL,"
        "  user_id    INTEGER NOT NULL,"
        "  PRIMARY KEY (guild_id, message_id, user_id)"
        ")"
    )
    conn.commit()
    return conn


def get_max_daily(conn: sqlite3.Connection, guild_id: int) -> int:
    row = conn.execute(
        "SELECT max_daily_hammers FROM guild_settings WHERE guild_id = ?",
        (guild_id,),
    ).fetchone()
    return row[0] if row else DAILY_HAMMERS


def get_remaining(conn: sqlite3.Connection, guild_id: int, user_id: int) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO hammers (guild_id, user_id, remaining) VALUES (?, ?, ?)",
        (guild_id, user_id, get_max_daily(conn, guild_id)),
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
    conn.execute(
        "UPDATE hammers SET remaining = COALESCE("
        "  (SELECT max_daily_hammers FROM guild_settings "
        "   WHERE guild_settings.guild_id = hammers.guild_id), ?)",
        (DAILY_HAMMERS,),
    )
    conn.execute("DELETE FROM hammer_attempts")
    conn.commit()


def get_settings(conn: sqlite3.Connection, guild_id: int) -> tuple[int, str]:
    conn.execute(
        "INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)",
        (guild_id,),
    )
    row = conn.execute(
        "SELECT max_daily_hammers, double_jeopardy FROM guild_settings WHERE guild_id = ?",
        (guild_id,),
    ).fetchone()
    conn.commit()
    return row[0], row[1]


def set_max_daily(conn: sqlite3.Connection, guild_id: int, n: int) -> None:
    conn.execute(
        "INSERT INTO guild_settings (guild_id, max_daily_hammers) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET max_daily_hammers = excluded.max_daily_hammers",
        (guild_id, n),
    )
    conn.commit()


def set_double_jeopardy(conn: sqlite3.Connection, guild_id: int, mode: str) -> None:
    conn.execute(
        "INSERT INTO guild_settings (guild_id, double_jeopardy) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET double_jeopardy = excluded.double_jeopardy",
        (guild_id, mode),
    )
    conn.commit()


def is_channel_protected(
    conn: sqlite3.Connection, guild_id: int, channel_id: int
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM protected_channels WHERE guild_id = ? AND channel_id = ?",
        (guild_id, channel_id),
    ).fetchone()
    return row is not None


def add_protected_channel(
    conn: sqlite3.Connection, guild_id: int, channel_id: int
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO protected_channels (guild_id, channel_id) VALUES (?, ?)",
        (guild_id, channel_id),
    )
    conn.commit()


def remove_protected_channel(
    conn: sqlite3.Connection, guild_id: int, channel_id: int
) -> bool:
    cur = conn.execute(
        "DELETE FROM protected_channels WHERE guild_id = ? AND channel_id = ?",
        (guild_id, channel_id),
    )
    conn.commit()
    return cur.rowcount > 0


def list_protected_channels(conn: sqlite3.Connection, guild_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT channel_id FROM protected_channels WHERE guild_id = ? ORDER BY channel_id",
        (guild_id,),
    ).fetchall()
    return [r[0] for r in rows]


def was_attempted(
    conn: sqlite3.Connection,
    guild_id: int,
    message_id: int,
    user_id: int | None,
) -> bool:
    if user_id is None:
        row = conn.execute(
            "SELECT 1 FROM hammer_attempts WHERE guild_id = ? AND message_id = ? LIMIT 1",
            (guild_id, message_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM hammer_attempts "
            "WHERE guild_id = ? AND message_id = ? AND user_id = ?",
            (guild_id, message_id, user_id),
        ).fetchone()
    return row is not None


def record_attempt(
    conn: sqlite3.Connection, guild_id: int, message_id: int, user_id: int
) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO hammer_attempts (guild_id, message_id, user_id) "
        "VALUES (?, ?, ?)",
        (guild_id, message_id, user_id),
    )
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

    channel = target.channel
    effective_channel_id = (
        channel.parent_id
        if isinstance(channel, discord.Thread) and channel.parent_id is not None
        else channel.id
    )
    if is_channel_protected(bot.db, guild_id, effective_channel_id):
        await interaction.response.send_message(
            "That channel is protected from hammers.", ephemeral=True
        )
        return

    _, double_jeopardy = get_settings(bot.db, guild_id)
    if double_jeopardy == "user" and was_attempted(
        bot.db, guild_id, target.id, interaction.user.id
    ):
        await interaction.response.send_message(
            "You already hammered that message today.", ephemeral=True
        )
        return
    if double_jeopardy == "server" and was_attempted(
        bot.db, guild_id, target.id, None
    ):
        await interaction.response.send_message(
            "Someone already hammered that message today.", ephemeral=True
        )
        return

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

    record_attempt(bot.db, guild_id, target.id, interaction.user.id)

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

    settings_group = app_commands.Group(
        name="hammersettings",
        description="Configure how hammers work in this server.",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    DJ_LABELS = {"off": "off", "user": "per-user", "server": "per-server"}

    def admin_guard(interaction: discord.Interaction) -> int | None:
        user = interaction.user
        if (
            interaction.guild_id is None
            or not isinstance(user, discord.Member)
            or not user.guild_permissions.manage_guild
        ):
            return None
        return interaction.guild_id

    async def deny_admin(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "You need the Manage Server permission to change hammer settings.",
            ephemeral=True,
        )

    @settings_group.command(name="show", description="Show current hammer settings.")
    async def settings_show(interaction: discord.Interaction) -> None:
        guild_id = admin_guard(interaction)
        if guild_id is None:
            await deny_admin(interaction)
            return
        max_daily, dj = get_settings(bot.db, guild_id)
        channel_ids = list_protected_channels(bot.db, guild_id)
        protected = ", ".join(f"<#{cid}>" for cid in channel_ids) if channel_ids else "none"
        await interaction.response.send_message(
            f"**Hammer settings:**\n"
            f"- Daily hammers per member: **{max_daily}**\n"
            f"- Double jeopardy: **{DJ_LABELS[dj]}**\n"
            f"- Protected channels: {protected}",
            ephemeral=True,
        )

    @settings_group.command(
        name="dailyhammers",
        description="Set how many hammers each member gets per day (1-50).",
    )
    @app_commands.describe(count="New daily hammer count, between 1 and 50.")
    async def settings_daily(interaction: discord.Interaction, count: int) -> None:
        guild_id = admin_guard(interaction)
        if guild_id is None:
            await deny_admin(interaction)
            return
        if count < 1 or count > 50:
            await interaction.response.send_message(
                "Daily hammer count must be between 1 and 50.", ephemeral=True
            )
            return
        set_max_daily(bot.db, guild_id, count)
        await interaction.response.send_message(
            f"Daily hammers set to **{count}**. Takes effect at the next midnight CT reset.",
            ephemeral=True,
        )

    @settings_group.command(
        name="doublejeopardy",
        description="Limit how often the same message can be targeted.",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="off", value="off"),
            app_commands.Choice(name="per-user", value="user"),
            app_commands.Choice(name="per-server", value="server"),
        ]
    )
    async def settings_dj(
        interaction: discord.Interaction, mode: app_commands.Choice[str]
    ) -> None:
        guild_id = admin_guard(interaction)
        if guild_id is None:
            await deny_admin(interaction)
            return
        set_double_jeopardy(bot.db, guild_id, mode.value)
        await interaction.response.send_message(
            f"Double jeopardy set to **{mode.name}**.", ephemeral=True
        )

    @settings_group.command(name="protect", description="Protect a channel from hammers.")
    @app_commands.describe(
        channel="The channel to protect. Threads inside it inherit protection."
    )
    async def settings_protect(
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.VoiceChannel | discord.ForumChannel,
    ) -> None:
        guild_id = admin_guard(interaction)
        if guild_id is None:
            await deny_admin(interaction)
            return
        add_protected_channel(bot.db, guild_id, channel.id)
        await interaction.response.send_message(
            f"{channel.mention} is now protected. Threads inside it are protected too.",
            ephemeral=True,
        )

    @settings_group.command(
        name="unprotect", description="Stop protecting a channel from hammers."
    )
    async def settings_unprotect(
        interaction: discord.Interaction,
        channel: discord.TextChannel | discord.VoiceChannel | discord.ForumChannel,
    ) -> None:
        guild_id = admin_guard(interaction)
        if guild_id is None:
            await deny_admin(interaction)
            return
        removed = remove_protected_channel(bot.db, guild_id, channel.id)
        if removed:
            await interaction.response.send_message(
                f"{channel.mention} is no longer protected.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"{channel.mention} wasn't protected.", ephemeral=True
            )

    @settings_group.command(
        name="protectedchannels",
        description="List protected channels in this server.",
    )
    async def settings_list_protected(interaction: discord.Interaction) -> None:
        guild_id = admin_guard(interaction)
        if guild_id is None:
            await deny_admin(interaction)
            return
        channel_ids = list_protected_channels(bot.db, guild_id)
        if not channel_ids:
            await interaction.response.send_message(
                "No protected channels.", ephemeral=True
            )
            return
        body = "\n".join(f"- <#{cid}>" for cid in channel_ids)
        await interaction.response.send_message(
            f"**Protected channels:**\n{body}", ephemeral=True
        )

    bot.tree.add_command(settings_group)


def main() -> None:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN not set")
    bot = CrushingHammerBot()
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
