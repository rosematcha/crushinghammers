# Crushing Hammer

A tiny Discord bot themed on the Pokémon TCG card *Crushing Hammer*. Each member gets 4 hammers per day. Using a hammer flips a coin — **heads** deletes the targeted message, **tails** does nothing. Hammers reset at midnight America/Chicago (handles CST/CDT automatically).

## Commands

- **`/crushinghammer`** — targets the message directly above your slash command. Quickest path.
- **Right-click a message → Apps → Crushing Hammer** — pick an older message to hammer.
- **`/checkhammers`** — ephemeral; tells you how many hammers you have left.

The bot replies publicly only with `**Heads.**` or `**Tails.**`. Everything else is ephemeral.

## Server settings

Anyone with the **Manage Server** permission can tune how hammers behave in their server via `/hammersettings`:

- **`/hammersettings show`** — view current settings.
- **`/hammersettings dailyhammers <count>`** — daily hammer allotment per member (1–50, default 4). Takes effect at the next midnight CT reset.
- **`/hammersettings doublejeopardy <off|per-user|per-server>`** — controls whether the same message can be hammered more than once in a day. `off` (default) places no limit; `per-user` stops one member from hammering the same message twice; `per-server` stops anyone in the server from hammering an already-attempted message. Resets at midnight CT alongside hammer counts.
- **`/hammersettings protect <channel>`** — protects a channel (rules, announcements, etc.) from hammers. Threads inside a protected channel are protected too. Use `/hammersettings unprotect <channel>` to remove and `/hammersettings protectedchannels` to list.

Settings are per server and persist in `hammers.db`.

## Discord app setup

1. Create an app at https://discord.com/developers/applications.
2. Under **Bot**: add a bot, copy the token.
3. Under **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `Send Messages`, `Manage Messages`, `Read Message History`
4. Open the generated URL and invite the bot to your server.

No privileged intents are required.

Don't add Administrator. `Manage Messages` is sufficient to delete messages, and routing through Administrator in Discord's UI exposes a per-command permission editor that's easy to misconfigure and lock regular users out of `/crushinghammer`. If that has already happened, open Server Settings → Integrations → Crushing Hammer and clear any per-command role/member overrides.

## Local run

```sh
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in DISCORD_TOKEN
set -a; . ./.env; set +a
venv/bin/python bot.py
```

Commands sync globally, so the bot works in any server it's invited to. After a deploy that changes commands, Discord can take up to ~1 hour to propagate; servers that join the bot afterward see the existing commands immediately.

Hammer counts are per-server, so a user's count in one server doesn't bleed into another. The midnight Chicago reset clears every server at once.

Set `RESET_ON_START=1` and redeploy to wipe everyone's count back to 4 immediately. Unset it again afterward, otherwise every restart will wipe counts.

## Hetzner deployment

```sh
# as root
adduser --system --group --home /opt/crushinghammer crushinghammer
apt install -y python3-venv git
git clone <your repo url> /opt/crushinghammer
chown -R crushinghammer:crushinghammer /opt/crushinghammer
sudo -u crushinghammer python3 -m venv /opt/crushinghammer/venv
sudo -u crushinghammer /opt/crushinghammer/venv/bin/pip install -r /opt/crushinghammer/requirements.txt

# secrets
install -m 600 /dev/stdin /etc/crushinghammer.env <<'EOF'
DISCORD_TOKEN=...
EOF

# service
cp /opt/crushinghammer/deploy/crushinghammer.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now crushinghammer
journalctl -u crushinghammer -f
```

## Data

State lives in `hammers.db` (SQLite). It's safe to delete — every user gets reset to their server's daily count on next interaction, and per-server settings (daily count, double-jeopardy mode, protected channels) revert to defaults.
