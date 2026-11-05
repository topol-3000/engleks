# Deployment: running engleks in production

Production is one Hetzner Cloud CAX11 (arm64, Ubuntu 24.04) running the Compose stack.
CI builds arm64 images, scans them and pushes them to GHCR as
`ghcr.io/topol-3000/engleks-{api,bot,litellm}:<commit sha>`. Every green push to `main`
is deployed automatically by the **Deploy** workflow, which SSHes to the server; a forced
command there (`engleks-deploy`) checks out the commit, pulls its images, starts them and
rolls back if they do not come up healthy. Both databases and `.env` are backed up every
night, encrypted, to a Hetzner Storage Box.

Design: [the Deployment spec](superpowers/specs/2026-11-04-deployment-design.md).

On the server:

```
/opt/engleks/                  git checkout at the deployed commit, owned by deploy
  .env                         prod config and secrets (0600); the only copy besides your password manager
/usr/local/bin/engleks-deploy  deploy script (from deploy/server/, installed by setup.sh)
/usr/local/bin/engleks-backup  backup script, run by engleks-backup.timer at 03:00 UTC
/var/backups/engleks/          dump staging (0700), emptied after every backup run
```

Two accounts: yours (sudo with a password, SSH key only) and `deploy` (owns the checkout,
member of `docker`). CI's SSH key can only run `engleks-deploy`. You run everything as
`deploy`:

```bash
ssh <you>@<server>
sudo -iu deploy
cd /opt/engleks
```

All `docker compose` and `make` commands below run there. `.env` sets
`COMPOSE_FILE=compose.yaml:compose.prod.yaml`, so no `-f` flags are needed.

## Cost

Per month, before VAT: CAX11 about €6 (as reported by third-party price trackers),
Storage Box BX11 €3.20, plus the primary IPv4 address. Check the current prices on
Hetzner's pages. The LLM spend is capped at $10 per 30 days (below).

## First-time setup

Do these in order: the server pulls images, so they must exist and be public before it
starts anything.

### 1. Merge to main and make the images public

Merge the deployment branch. CI pushes the first images. The Deploy run that follows fails
because the `production` environment has no secrets yet; that is expected.

Then make the three packages public, so the server pulls without credentials: GitHub →
your profile → **Packages** → `engleks-api` → **Package settings** → **Change visibility**
→ **Public**. Repeat for `engleks-bot` and `engleks-litellm`.

### 2. Storage Box and healthchecks.io

- Order a **Storage Box BX11** in a different Hetzner location from the server. In its
  settings, turn on **SSH support**. Note its host name, `uNNNNNN.your-storagebox.de`.
- Create a free **healthchecks.io** check named `engleks-backup` with a period of
  **1 day** and a grace time of **2 hours**, and make sure it notifies your email. Copy
  its ping URL (`https://hc-ping.com/<uuid>`). Treat it as a secret: anyone with it can
  report a backup as done.

### 3. Anthropic spending limit

In the Anthropic console, set a monthly spending limit on the organisation (Settings →
Limits). This is the second cap, behind LiteLLM's.

### 4. CI's SSH key

On your machine:

```bash
ssh-keygen -t ed25519 -N "" -C engleks-ci -f engleks-ci
```

`engleks-ci.pub` goes to `setup.sh`; the private key `engleks-ci` becomes the
`DEPLOY_SSH_KEY` secret in step 8. Keep both (the public key is needed again when you
re-run `setup.sh`).

### 5. Server and firewall

In the Hetzner Cloud console:

- Create a **Firewall** with two inbound rules: TCP 22 and ICMP, from anywhere. Nothing
  else: every port Compose publishes is bound to 127.0.0.1.
- Create a **CAX11** server with **Ubuntu 24.04**, your SSH key, and the firewall.

### 6. Run setup.sh

```bash
ssh root@<server-ip>
curl -fsSLO https://raw.githubusercontent.com/topol-3000/engleks/main/deploy/server/setup.sh
less setup.sh
bash setup.sh <you> 'ssh-ed25519 AAAA... engleks-ci' uNNNNNN.your-storagebox.de
```

The second argument is the one line of `engleks-ci.pub`, in single quotes.

It installs Docker, restic and automatic security updates (with a reboot at 04:00 UTC
when one is needed), adds 2 GB of swap, creates your account (it asks you for a password,
which sudo will ask for) and `deploy`, turns off root and password login, clones the
repository to `/opt/engleks`, installs the deploy and backup scripts and the backup timer,
and creates `deploy`'s Storage Box key and the `storagebox` SSH alias.

It prints the Storage Box host key fingerprints: compare them with the ones Hetzner
publishes for Storage Boxes.

**Before closing the root session**, log in as yourself in a second terminal and check
that `sudo -v` works. Root login is now off.

### 7. Install the backup key on the Storage Box

As yourself on the server (it asks for the Storage Box password once):

```bash
sudo -iu deploy sh -c 'ssh -p 23 uNNNNNN@uNNNNNN.your-storagebox.de install-ssh-key < ~/.ssh/storagebox.pub'
sudo -iu deploy ssh storagebox ls   # works without a password now
```

### 8. Create .env

```bash
sudo -iu deploy /opt/engleks/deploy/server/bootstrap-env.sh
```

It generates every password and key, and asks for:

| Prompt | Answer |
| --- | --- |
| `BOT_TELEGRAM_TOKEN` | the token from @BotFather (not echoed) |
| `ANTHROPIC_API_KEY` | the Anthropic API key (not echoed) |
| `HEALTHCHECKS_BACKUP_URL` | the ping URL from step 2 (not echoed) |
| `RESTIC_REPOSITORY` | Enter for `sftp:storagebox:/engleks` |
| `LLM_UPSTREAM_MODEL`, `LLM_UPSTREAM_API_BASE` | Enter for the defaults (Anthropic) |
| `API_LLM_MODEL` | Enter for `claude-haiku-5-5` |
| `API_BOT_USERNAME` | the bot's username without `@`, so invites come with a link |

It then starts `postgres` and `litellm`, creates the API's LiteLLM key with a cap of $10
per 30 days, and initialises the restic repository on the Storage Box.

**Save a copy of `/opt/engleks/.env` in your password manager now.** The backups contain
`.env` and are encrypted with its `RESTIC_PASSWORD`: without a copy off the server, a lost
server means unreadable backups.

If it fails halfway, start over: `cd /opt/engleks && docker compose down -v && rm .env`,
then run it again.

### 9. GitHub environment

In the repository: **Settings → Environments → New environment** `production`.

- **Deployment branches and tags**: Selected branches → `main` only.
- Secrets:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | the server's IP address |
| `DEPLOY_SSH_KEY` | the contents of `engleks-ci` (the private key from step 4) |
| `DEPLOY_KNOWN_HOSTS` | the output of `ssh-keyscan -t ed25519 <server-ip>` |

Check the `DEPLOY_KNOWN_HOSTS` key before saving it: its fingerprint
(`ssh-keyscan -t ed25519 <server-ip> | ssh-keygen -lf -`) must match
`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` run on the server.

### 10. First deploy and first backup

- **Actions → Deploy → Run workflow**, from `main`, with `main`'s head commit SHA (full
  40 characters). The run streams the server's output and turns green when every service
  is healthy.
- `sudo systemctl start engleks-backup`, then check that the healthchecks.io check is green
  and `journalctl -u engleks-backup` shows no error.
- `sudo -iu deploy`, `cd /opt/engleks && make invite`, and redeem the code in Telegram.

## Day to day

### How a deploy works

A merge to `main` runs CI. When CI is green, the Deploy workflow runs
`ssh deploy@<server> <sha>`, and `engleks-deploy` on the server:

1. takes a lock (`/opt/engleks/.deploy.lock`), so deploys and backups never overlap;
2. `git fetch` and `git checkout --detach <sha>`;
3. sets `ENGLEKS_TAG=<sha>` in `.env`;
4. `docker compose pull`, then `docker compose up -d --wait --wait-timeout 180` (this runs
   `migrate` first);
5. on success, deletes the local images of older releases except the previous one.

If any step fails, it deploys the previous SHA again, from the local checkout and images
(so a GitHub or GHCR outage does not stop the rollback), and the run turns red. Read the
run's log for the cause. The server keeps the last deploy's output in
`/opt/engleks/.deploy.log`; a deploy finishes (or rolls back) even if the Actions run is
cancelled or loses its connection, and that file then shows how it ended. The bot is down for a few seconds per deploy; Telegram keeps the
messages sent meanwhile.

Deploys run one at a time. If several merges land while one deploys, only the newest
waiting one runs. Automatic deploys only deploy `main`'s current head: re-running an old CI
run does not move production backwards (its Deploy run ends green with a notice). Going
back is always a manual rollback.

**Migrations must work with the previous release's code.** A rollback restores the old
images but never downgrades the schema, so add columns as nullable or with a default, and
drop or rename things only in a later release, once no running code uses them.

### Rollback

**Actions → Deploy → Run workflow** with the SHA of an older commit on `main` (its images
are in GHCR). On the server, by hand:

```bash
sudo -iu deploy engleks-deploy <sha>
```

### Admin commands

```bash
sudo -iu deploy
cd /opt/engleks
make invite note="Olena"
make admin a="user list"
docker compose ps
docker compose logs -f api
```

See [access.md](access.md) for every admin command.

### LiteLLM admin UI

```bash
ssh -L 4000:localhost:4000 <you>@<server>
```

Open http://localhost:4000/ui and log in with `LITELLM_UI_USERNAME` and
`LITELLM_UI_PASSWORD` from `.env`. It shows spend, keys and request logs.

### The spending cap

The API's LiteLLM key may spend $10 per 30 days. When the cap is reached, LiteLLM rejects
its calls until the period resets: users get the bot's generic error message. To change
the cap without a deploy, edit the key's **Max Budget** in the LiteLLM UI (Virtual Keys),
or call `/key/update` on the server:

```bash
sudo -iu deploy
cd /opt/engleks
env_get() { sed -n "s/^$1=//p" .env | tail -n 1; }
curl -sS http://127.0.0.1:4000/key/update \
  -H @<(printf 'Authorization: Bearer %s\nContent-Type: application/json\n' "$(env_get LITELLM_MASTER_KEY)") \
  --data @<(printf '{"key": "%s", "max_budget": 20}' "$(env_get API_LLM_API_KEY)")
```

### Changing a secret

Compose passes secrets from `.env` into containers, but a changed value does not count as
a change, so recreate the services that use it. As `deploy` in `/opt/engleks`, edit
`.env`, then:

```bash
docker compose up -d --force-recreate --no-deps <service>...
```

| Secret in `.env` | Services | Also needed |
| --- | --- | --- |
| `BOT_SERVICE_TOKEN` | `api`, `bot` | |
| `BOT_TELEGRAM_TOKEN` | `bot` | |
| `API_LLM_API_KEY` | `api` | |
| `LITELLM_UI_USERNAME`, `LITELLM_UI_PASSWORD`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | `litellm` | |
| `LITELLM_MASTER_KEY` | `litellm` | it invalidates the API's key: create a new one (below), put it into `.env` as `API_LLM_API_KEY` and recreate `api` |
| `LITELLM_DB_PASSWORD` | `litellm` | change the role's password first (below) |
| `API_DB_PASSWORD` | `migrate`, `api` | change the role's password first (below) |
| `RESTIC_PASSWORD` | none | change the repository key first (below) |
| `HEALTHCHECKS_BACKUP_URL` | none | just `.env`; the next backup uses it |

After any change, update the copy in your password manager.

A new API key with the cap:

```bash
docker compose exec -T litellm python /deploy/generate_key.py --max-budget 10 --budget-duration 30d
```

Postgres sets passwords only when it creates a role, so a database password also has to
be changed inside Postgres, before recreating the services. With the new value already in
`.env`:

```bash
env_get() { sed -n "s/^$1=//p" .env | tail -n 1; }
# LITELLM_DB_PASSWORD
echo "ALTER ROLE litellm PASSWORD :'pw';" | docker compose exec -T postgres \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v pw="$1"' sh "$(env_get LITELLM_DB_PASSWORD)"
# API_DB_PASSWORD
echo "ALTER ROLE CURRENT_USER PASSWORD :'pw';" | docker compose exec -T postgres \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v pw="$1"' sh "$(env_get API_DB_PASSWORD)"
```

The restic password, with the **old** value still in `.env` (restic asks for the new one
twice), then put the new one into `.env`:

```bash
RESTIC_REPOSITORY=$(env_get RESTIC_REPOSITORY) RESTIC_PASSWORD=$(env_get RESTIC_PASSWORD) \
  restic key passwd
```

### Updating the server scripts

Deploys do not update the installed copies of `engleks-deploy`, `engleks-backup` or the
backup units. After a deploy of a commit that changes `deploy/server/engleks-*`, re-run
`setup.sh` from the checkout, with the same arguments:

```bash
sudo bash /opt/engleks/deploy/server/setup.sh <you> 'ssh-ed25519 AAAA... engleks-ci' uNNNNNN.your-storagebox.de
```

The key line is in `engleks-ci.pub` on your machine, and also in
`~deploy/.ssh/authorized_keys` on the server, after the `restrict` option.

## Backups

`engleks-backup` runs every night at 03:00 UTC (`engleks-backup.timer`; a night missed
while the server was off runs at the next boot). It dumps the `engleks` and `litellm`
databases, sends them and `.env` to the restic repository on the Storage Box (encrypted
on the server, so Hetzner cannot read users' texts), keeps 7 daily, 4 weekly and 6 monthly
snapshots, and pings healthchecks.io. A failed or missing run sends you an email.

```bash
sudo systemctl start engleks-backup     # run one now
journalctl -u engleks-backup -n 50      # its log
systemctl list-timers engleks-backup    # next run
```

To work with the repository by hand, as `deploy` in `/opt/engleks`:

```bash
env_get() { sed -n "s/^$1=//p" .env | tail -n 1; }
export RESTIC_REPOSITORY=$(env_get RESTIC_REPOSITORY) RESTIC_PASSWORD=$(env_get RESTIC_PASSWORD)
restic snapshots
```

### Restore on the same server

This replaces the live data with the snapshot's. As `deploy` in `/opt/engleks`, with the
two variables exported as above:

```bash
dir=$(mktemp -d)
restic restore latest --target "$dir"
docker compose stop api bot litellm
for db in engleks litellm; do
  docker compose exec -T postgres sh -c "pg_restore -U \"\$POSTGRES_USER\" -d $db --clean --if-exists" \
    < "$dir/var/backups/engleks/$db.dump"
done
rm -r "$dir"
docker compose up -d --wait
```

To restore an older snapshot, use its ID from `restic snapshots` instead of `latest`.

### Restore rehearsal

Do this once after setup and after big changes: it proves the backups can be read without
touching the live data.

```bash
dir=$(mktemp -d)
restic restore latest --target "$dir"
sql() { docker compose exec -T postgres sh -c "psql -U \"\$POSTGRES_USER\" -d $1 -tAc \"$2\""; }
sql engleks "CREATE DATABASE engleks_restore_test"
docker compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d engleks_restore_test' \
  < "$dir/var/backups/engleks/engleks.dump"
for table in app_user interaction; do
  echo "$table: live $(sql engleks "SELECT count(*) FROM $table"), restored $(sql engleks_restore_test "SELECT count(*) FROM $table")"
done
sql engleks "DROP DATABASE engleks_restore_test"
rm -r "$dir"
```

The restored counts can be lower than the live ones by whatever arrived since the
snapshot.

### Restore on a new server

You need the `.env` copy from your password manager (for `RESTIC_PASSWORD`) and access to
the Storage Box.

1. Do first-time setup steps 5–7 for the new server: `setup.sh`, then install its new
   Storage Box key.
2. As `deploy`, restore `.env` from the backup:

   ```bash
   cd /opt/engleks
   export RESTIC_REPOSITORY=sftp:storagebox:/engleks
   read -rs RESTIC_PASSWORD; export RESTIC_PASSWORD   # paste it from the password manager
   dir=$(mktemp -d)
   restic restore latest --target "$dir"
   install -m 600 "$dir/opt/engleks/.env" .env
   ```

3. Start an empty database (the init script creates the `litellm` role with the restored
   password), restore both dumps as in "Restore on the same server", and `rm -r "$dir"`:

   ```bash
   docker compose up -d --wait postgres
   ```

4. Update the GitHub environment's `DEPLOY_HOST` and `DEPLOY_KNOWN_HOSTS` for the new
   server, and deploy `main`'s head with **Run workflow**.
5. `sudo systemctl start engleks-backup` and check healthchecks.io.
