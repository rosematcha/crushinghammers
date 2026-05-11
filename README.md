# Crushing Hammer

A tiny Discord bot themed on the Pokémon TCG card *Crushing Hammer*. Each member gets 4 hammers per day. Using a hammer flips a coin — **heads** deletes the targeted message, **tails** does nothing. Hammers reset at midnight America/Chicago (handles CST/CDT automatically).

## Commands

- **`/crushinghammer`** — targets the message directly above your slash command. Quickest path.
- **Right-click a message → Apps → Crushing Hammer** — pick an older message to hammer.
- **`/checkhammers`** — ephemeral; tells you how many hammers you have left.

The bot replies publicly only with `**Heads.**` or `**Tails.**`. Everything else is ephemeral.

## Discord app setup

1. Create an app at https://discord.com/developers/applications.
2. Under **Bot**: add a bot, copy the token.
3. Under **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `Send Messages`, `Manage Messages`, `Read Message History`
4. Open the generated URL and invite the bot to your server.

No privileged intents are required.

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

State lives in `hammers.db` (SQLite, one table). It's safe to delete — every user gets reset to 4 on next interaction.
