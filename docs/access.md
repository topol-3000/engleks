# Access: inviting people and managing users

Engleks is invite-only. Someone joins by opening the bot with an invite code; until then
every message gets "You need an invite to use Engleks."

All commands below run against the running stack (`make up`). Without the stack, run the
same commands as `uv run engleks-admin ...` with `.env` pointing at the database.

## One-time setup

Put the bot's username (without `@`) in `.env` and restart the API, so new invites come
with a ready-to-send link:

```bash
API_BOT_USERNAME=engleks_bot
docker compose up -d api
```

## Invite someone

```bash
make invite note="Olena"                  # one person, never expires
make invite note="study group" uses=5     # up to 5 people
make invite note="trial" days=7           # expires in 7 days
```

It prints the code and, if `API_BOT_USERNAME` is set, a link:

```
K7QMXR2WNPA4
https://t.me/engleks_bot?start=K7QMXR2WNPA4
```

Send the link. They tap **Start** and they are in. Without the link they can send
`/start K7QMXR2WNPA4` to the bot themselves (any letter case works).

The `note` is only for you, so you can tell codes apart later. People who already have an
account don't spend a code if they open the link again.

## Manage invites

```bash
make admin a="invite list"                # code, note, uses, expiry, revoked
make admin a="invite revoke K7QMXR2WNPA4"
```

A revoked code stops working right away. People who already joined with it keep their
access.

## Manage users

Users are identified by their Telegram id.

```bash
make admin a="user list"                  # status, limit overrides, today's usage
make admin a="user disable 123456789"     # blocks them, including their old invite links
make admin a="user enable 123456789"
```

## Limits

Every user gets the defaults from `.env`:

| Setting | Default | Meaning |
|---|---|---|
| `API_RATE_LIMIT_PER_MINUTE` | 10 | messages per minute |
| `API_DAILY_TOKEN_BUDGET` | 200000 | LLM tokens per day, reset at 00:00 UTC |

To change the defaults, edit `.env` and run `docker compose up -d api`. To give one person
different limits:

```bash
make admin a="user set-limits 123456789 --daily-tokens 500000"
make admin a="user set-limits 123456789 --per-minute 20"   # keeps the daily override
make admin a="user set-limits 123456789 --reset"           # back to the defaults
```

When a user hits a limit, the bot tells them to try again in a minute, or that they've
reached today's limit.
