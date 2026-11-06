# Deployment: running engleks in production

Production is one Hetzner Cloud CAX11 (arm64, Ubuntu 26.04; 24.04 also works) running the Compose stack.
CI builds arm64 images, scans them and pushes them to GHCR as
`ghcr.io/topol-3000/engleks-{api,bot,litellm}:<commit sha>`. Every green push to `main`
is deployed automatically by the **Deploy** workflow, which SSHes to the server; a forced
command there (`engleks-deploy`) checks out the commit, pulls its images, starts them and
rolls back if they do not come up healthy. Both databases are backed up every night,
encrypted, to a Hetzner Storage Box.

Configuration has three homes, and nobody edits `.env` on the server: `engleks-deploy`
generates it on every deploy.

| What | Where | Changed by |
| --- | --- | --- |
| Plain production config (model, limits, bot username, backup repository) | `deploy/prod.env` in the repository | a PR; goes live with the automatic deploy |
| Secrets (tokens, API keys, passwords) | Infisical, project `engleks`, environment `prod`; their names are in `deploy/secrets.txt` | the Infisical UI, then **Run workflow** with no input |
| The server's Infisical credentials | `~deploy/.config/engleks/infisical` | `setup.sh` |

Design: [the Deployment spec](superpowers/specs/2026-11-04-deployment-design.md).

On the server:

```
/opt/engleks/                      git checkout at the deployed commit, owned by deploy
  .env                             generated on every deploy (0600); never edit it, never back it up
/usr/local/bin/engleks-deploy      deploy script (from deploy/server/, installed by setup.sh)
/usr/local/bin/engleks-render-env  builds .env from deploy/prod.env and Infisical (installed by setup.sh)
/usr/local/bin/engleks-backup      backup script, run by engleks-backup.timer at 03:00 UTC
/var/backups/engleks/              dump staging (0700), emptied after every backup run
~deploy/.config/engleks/infisical  the machine identity's credentials (0600); the only secret on disk besides .env
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
`COMPOSE_FILE=compose.yaml:compose.prod.yaml` (from `deploy/prod.env`), so no `-f` flags
are needed.

## Cost

Per month, before VAT: CAX11 about €6 (as reported by third-party price trackers),
Storage Box BX11 €3.20, plus the primary IPv4 address. Check the current prices on
Hetzner's pages. Infisical Cloud's free plan is enough. The LLM spend is capped at $10 per
30 days (below).

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
  report a backup as done. It becomes `HEALTHCHECKS_BACKUP_URL` in Infisical (step 6).

### 3. OpenAI API key and budget

Production runs on OpenAI's `gpt-6.1-terra`. In the OpenAI platform, create a project
for engleks and an API key in it, and set a monthly budget on the project (Settings →
Limits). This is the second guard, behind LiteLLM's cap. An Anthropic key is optional: it
only serves the `claude-*` aliases in `deploy/litellm/config.yaml`.

### 4. CI's SSH key

On your machine:

```bash
ssh-keygen -t ed25519 -N "" -C engleks-ci -f engleks-ci
```

`engleks-ci.pub` goes to `setup.sh`; the private key `engleks-ci` becomes the
`DEPLOY_SSH_KEY` secret in step 10. Keep both (the public key is needed again when you
re-run `setup.sh`).

### 5. Server and firewall

In the Hetzner Cloud console:

- Create a **Firewall** with two inbound rules: TCP 22 and ICMP, from anywhere. Nothing
  else: every port Compose publishes is bound to 127.0.0.1.
- Create a **CAX11** server with **Ubuntu 26.04** (or 24.04), your SSH key, and the firewall.
- Note the server's **IPv4 address** and its **IPv6 network** (shown as `2a01:...::/64`):
  step 7 needs both.

### 6. Infisical project and secrets

In Infisical (create an organization in the **EU** region, `eu.infisical.com`), create a
project `engleks` with an environment whose slug is `prod`. Note the **project ID**
(Project Settings): `setup.sh` asks for it.

On your machine, from a checkout of the repository, generate the secrets that engleks
makes up itself:

```bash
deploy/infisical/generate-secrets.sh
```

It prints seven `KEY=value` lines to stdout (`API_DB_PASSWORD`, `LITELLM_DB_PASSWORD`,
`BOT_SERVICE_TOKEN`, `LITELLM_MASTER_KEY`, `LITELLM_UI_PASSWORD`, `API_LLM_API_KEY`,
`RESTIC_PASSWORD`). In the project's `prod` environment, open the secrets page and use
**".env import"** to paste them, then clear your terminal.

**Save `RESTIC_PASSWORD` in your password manager now.** The backups are encrypted with it
and it cannot be re-issued: without it, a lost Infisical account means unreadable backups.
It is the only value that needs a copy outside Infisical.

Add these four by hand, as secrets named exactly so:

| Secret | Value |
| --- | --- |
| `BOT_TELEGRAM_TOKEN` | the token from @BotFather |
| `OPENAI_API_KEY` | the OpenAI API key from step 3 |
| `ANTHROPIC_API_KEY` | `unused`, or an Anthropic key for the `claude-*` aliases |
| `HEALTHCHECKS_BACKUP_URL` | the ping URL from step 2 |

The names in `deploy/secrets.txt` are exactly these eleven; every one is required. A value
must be non-empty and contain no whitespace, quotes, backticks, `$`, `#` or backslashes: a key pasted
with a trailing newline or space makes the deploy fail, naming the key. If you ever type
`API_LLM_API_KEY` by hand instead of generating it, it must start with `sk-` and be at least
16 characters long (LiteLLM refuses shorter keys).

### 7. Machine identity for the server

The server reads the secrets with a read-only machine identity. In Infisical (Organization
→ Access Control → Identities) create `engleks-prod-server`:

- **Authentication**: Universal Auth, with a short **access token TTL** (for example 10
  minutes; a deploy uses the token once).
- **Trusted IPs**: the server's IPv4 address **and its IPv6 /64** (from step 5; on an existing
  server, read them in the Hetzner console or with `ip -br addr`). Hetzner
  servers have IPv6 and `curl` prefers it, so with the IPv4 address alone the login comes
  from an address Infisical rejects. Universal Auth has two lists, **Client Secret Trusted
  IPs** and **Access Token Trusted IPs**: set both to these addresses (the client-secret
  one is the minimum, because it guards the login).
- Add the identity to the project `engleks` with a **read-only role on `prod`** (the
  built-in Viewer role, or a custom one limited to `prod`).
- Create a **client secret** for it. Keep its **client ID** and the secret at hand for
  step 8.

The credentials are the only secret on the server's disk. A copy is useless from another
address, and revoking the identity touches no application secret.

### 8. Run setup.sh

```bash
ssh root@<server-ip>
curl -fsSLO https://raw.githubusercontent.com/topol-3000/engleks/main/deploy/server/setup.sh
less setup.sh
bash setup.sh <you> 'ssh-ed25519 AAAA... engleks-ci' uNNNNNN.your-storagebox.de
```

The second argument is the one line of `engleks-ci.pub`, in single quotes.

It installs Docker, restic, jq and automatic security updates (with a reboot at 04:00 UTC
when one is needed), adds 2 GB of swap, creates your account (it asks you for a password,
which sudo will ask for) and `deploy`, turns off root and password login, clones the
repository to `/opt/engleks`, installs the deploy, render-env and backup scripts and the
backup timer, and creates `deploy`'s Storage Box key and the `storagebox` SSH alias.

If `~deploy/.config/engleks/infisical` does not exist, it asks for the machine identity's
credentials and writes the file (0600, owned by `deploy`):

| Prompt | Answer |
| --- | --- |
| Infisical project ID | the project ID from step 6 (echoed) |
| Infisical client ID | the identity's client ID from step 7 (echoed) |
| Infisical client secret | the client secret from step 7 (not echoed) |

Each answer may contain only letters, digits, `.`, `_` and `-`. A re-run of `setup.sh`
keeps an existing file; to enter new credentials, delete the file first.

It prints the Storage Box host key fingerprints: compare them with the ones Hetzner
publishes for Storage Boxes.

**Before closing the root session**, log in as yourself in a second terminal and check
that `sudo -v` works. Root login is now off.

### 9. Install the backup key on the Storage Box

As yourself on the server (it asks for the Storage Box password once):

```bash
sudo -iu deploy sh -c 'ssh -p 23 uNNNNNN@uNNNNNN.your-storagebox.de install-ssh-key < ~/.ssh/storagebox.pub'
sudo -iu deploy ssh storagebox ls   # works without a password now
```

### 10. GitHub environment

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

### 11. First deploy and first backup

- **Actions → Deploy → Run workflow**, from `main`, with the input left empty. The run
  deploys `main`'s head (whatever its CI status; here, wait for a green CI run on it first),
  streams the server's output and turns green when every service
  is healthy. A first deploy renders `.env` from `deploy/prod.env` and Infisical, starts
  the stack, runs `restic init` on the Storage Box and creates the API's LiteLLM key with
  the $10 per 30 days cap.
- **If the run fails at "check the restic repository"** on Ubuntu 24.04: its restic 0.16
  does not exit with the code 10 for a missing repository, so the deploy cannot tell
  "missing" from "wrong password" and stops. Nothing has changed on the server. Run
  `restic init` by hand once, as `deploy` (paste `RESTIC_PASSWORD` from your password
  manager, because there is no `.env` yet), then **Run workflow** again:

  ```bash
  sudo -iu deploy
  read -rs RESTIC_PASSWORD; export RESTIC_PASSWORD
  RESTIC_REPOSITORY=sftp:storagebox:engleks restic init
  ```

- `sudo systemctl start engleks-backup`, then check that the healthchecks.io check is green
  and `journalctl -u engleks-backup` shows no error.
- `sudo -iu deploy`, `cd /opt/engleks && make invite`, and redeem the code in Telegram.

## Day to day

### How a deploy works

A merge to `main` runs CI. When CI is green, the Deploy workflow runs
`ssh deploy@<server> <sha>`. **Actions → Deploy → Run workflow** does the same by hand:
with the input empty it deploys the head of the branch picked in "Use workflow from" (use
it to apply a changed secret; the `production` environment accepts only `main`), and with a
full SHA it rolls back to that commit. The head is deployed whatever its CI status: if it is
not the running release or its CI is red or running, enter the running SHA instead (see
"Changing a secret"). `engleks-deploy` on the server:

1. takes a lock (`/opt/engleks/.deploy.lock`), so deploys and backups never overlap, and
   deletes a `.env.previous` that a killed run left behind;
2. `git fetch` and `git checkout --detach <sha>`;
3. renders the new `.env` (`engleks-render-env`): it logs in to Infisical, reads the secrets
   and writes `deploy/prod.env`, the secrets, `ENGLEKS_TAG=<sha>` and
   `ENGLEKS_SECRETS_HASH` to `.env.next`, and saves the running `.env` as `.env.previous`.
   If Infisical is unreachable, a secret named in `deploy/secrets.txt` is missing or empty,
   or a value has whitespace, a quote, `$`, `#` or a backslash, the deploy stops here,
   naming the key, and the server keeps running the old release;
4. checks restic, when `RESTIC_REPOSITORY` or `RESTIC_PASSWORD` changed or on a first
   deploy: `restic cat config` with the new values. A missing repository (exit 10) is
   initialised; any other failure stops the deploy (see "Changing a secret");
5. moves `.env.next` over `.env`;
6. if `API_DB_PASSWORD` or `LITELLM_DB_PASSWORD` changed, starts `postgres` and runs
   `ALTER ROLE` for it;
7. `docker compose pull`, then `docker compose up -d --wait --wait-timeout 180` (this runs
   `migrate` first). Any changed secret recreates `migrate`, `api`, `litellm` and `bot`;
8. makes sure the API's LiteLLM key exists with the models in `generate_key.py` (a missing
   key is created with the cap; an existing key's budget is left alone) and, when
   `API_LLM_API_KEY` changed, deletes the old key;
9. on success, deletes `.env.previous` and the local images of older releases except the
   previous one.

If any step fails, it deploys the previous SHA again, from the local checkout and images,
with the previous `.env` restored over `.env` and the database passwords set back. The
rollback calls neither Infisical, GitHub, GHCR nor the Storage Box (so an outage there does
not stop it), and the run turns red. This holds for a redeploy of the running SHA too: a
failed secret change goes back to the old secrets. Read the run's log for the cause. The
server keeps the last deploy's output in `/opt/engleks/.deploy.log`; a deploy finishes (or
rolls back) even if the Actions run is cancelled or loses its connection, and that file then
shows how it ended. If the rollback fails before it restores `.env`, `.env.previous` (the
running secrets) stays for whoever repairs the server; after that point `.env` already holds
the previous values. The bot is down for a few seconds per deploy; Telegram keeps the messages sent
meanwhile.

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

This is an ordinary deploy of that commit: it renders `.env` from that commit's
`deploy/prod.env` and `deploy/secrets.txt` and today's secrets in Infisical, so Infisical
must be reachable. Because `deploy/prod.env` rolls back with the code, `API_LLM_MODEL` always
matches that commit's `deploy/litellm/config.yaml`; nothing needs switching first. Infisical
keys the old commit does not list are ignored, with a warning. The automatic rollback after a
failed deploy is different: it restores the previous `.env` without calling Infisical.

**Commits from before the Infisical move cannot be deployed.** They have no
`deploy/prod.env`, and their `generate_key.py` cannot ensure a key. The render step fails
with a message naming `deploy/prod.env`, and the server stays on the running release.

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

Open http://localhost:4000/ui and log in with `LITELLM_UI_USERNAME` (`deploy/prod.env`) and
`LITELLM_UI_PASSWORD` (Infisical; also in the server's `.env`). It shows spend, keys and
request logs.

### The spending cap

The API's LiteLLM key may spend $10 per 30 days. When the cap is reached, LiteLLM rejects
its calls until the period resets: users get the bot's generic error message. To change
the cap without a deploy, edit the key's **Max Budget** in the LiteLLM UI (Virtual Keys),
or call `/key/update` on the server. Deploys leave a changed budget alone; only a new key
(a rotated `API_LLM_API_KEY`) starts with the $10 cap and no spend.

```bash
sudo -iu deploy
cd /opt/engleks
env_get() { sed -n "s/^$1=//p" .env | tail -n 1; }
curl -sS http://127.0.0.1:4000/key/update \
  -H @<(printf 'Authorization: Bearer %s\nContent-Type: application/json\n' "$(env_get LITELLM_MASTER_KEY)") \
  --data @<(printf '{"key": "%s", "max_budget": 20}' "$(env_get API_LLM_API_KEY)")
```

### Switching the model

`API_LLM_MODEL` in `deploy/prod.env` names a `model_name` alias in
`deploy/litellm/config.yaml`. Open a PR:

- to switch to an alias that already exists, change `API_LLM_MODEL` in `deploy/prod.env`;
- for a new alias, add it to `deploy/litellm/config.yaml` and to `MODELS` in
  `deploy/litellm/generate_key.py` (the API's key only accepts the models listed there),
  and change `API_LLM_MODEL`;
- for another provider, also change `LLM_UPSTREAM_MODEL` and `LLM_UPSTREAM_API_BASE`, and
  put its key (`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`) in Infisical **before merging**.

The merge deploys it. The deploy syncs the key's model list, so there is no `/key/update`
to run by hand. Then send the bot a message and check the request in the LiteLLM UI (model
and spend). Going back is another PR (or a rollback to the earlier commit).

### Changing a secret

Secrets live in Infisical (project `engleks`, environment `prod`). Edit the value there,
then **Actions → Deploy → Run workflow** from `main` with the input empty. An empty input
deploys `main`'s head, whatever its CI status. If `main`'s head is not the running release
(`ENGLEKS_TAG` in the server's `.env`, or the SHA of the last green Deploy run), or its CI is
red or still running, enter the running SHA in the input instead, so that only the secrets
change. Nobody logs in to
the server, and nobody edits `.env`: the deploy renders it again, recreates `migrate`,
`api`, `litellm` and `bot` (the changed `ENGLEKS_SECRETS_HASH` label forces it; Compose
ignores a changed secret value on its own), and reconciles what holds a copy of the value:

| Secret | What the deploy does besides recreating the services |
| --- | --- |
| `BOT_SERVICE_TOKEN`, `BOT_TELEGRAM_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `LITELLM_UI_PASSWORD` | nothing |
| `LITELLM_MASTER_KEY` | nothing. It does not invalidate the API's key: virtual keys are stored in the database, hashed, and keep working |
| `API_LLM_API_KEY` | creates the new key in LiteLLM with the $10 cap (spend restarts at zero) and deletes the old one. A value typed by hand must start with `sk-` and be at least 16 characters; `generate-secrets.sh` makes a good one |
| `LITELLM_DB_PASSWORD`, `API_DB_PASSWORD` | runs `ALTER ROLE` on the Postgres role before recreating, because Postgres sets a password only when it creates the role |
| `HEALTHCHECKS_BACKUP_URL` | nothing; the next backup reads the new `.env` |
| `RESTIC_PASSWORD` | the repository's key must be changed first, by hand (below) |

A new random value: `openssl rand -hex 32` (`LITELLM_MASTER_KEY` and `API_LLM_API_KEY`
need an `sk-` in front). Values follow the rule from first-time setup step 6. If the deploy
fails, it restores the old `.env` and sets any database password back, so Infisical and the
server disagree until you fix the cause and run the workflow again.

Plain values (`LITELLM_UI_USERNAME`, the model, the limits) are not secrets: change them in
`deploy/prod.env` with a PR.

**The restic password** is the one change the deploy cannot make. Changing the repository's
key is rare, and a failed automatic re-key could lock the backups, so it stays by hand, in
this order. As `deploy` in `/opt/engleks`, with the **old** value still in `.env` (restic
asks for the new one twice; use the value you will put in Infisical):

```bash
env_get() { sed -n "s/^$1=//p" .env | tail -n 1; }
RESTIC_REPOSITORY=$(env_get RESTIC_REPOSITORY) RESTIC_PASSWORD=$(env_get RESTIC_PASSWORD) \
  restic key passwd
```

Then change `RESTIC_PASSWORD` in Infisical, update the copy in your password manager, and
**Run workflow**. The deploy opens the repository with the new password before it changes
anything, and refuses (the run fails, the server stays on the old release) if the
repository does not accept it. Do the re-key first: a deploy with the new value before it
fails and changes nothing. Do the three steps back to back: until the deploy installs the
new `.env`, the nightly backup and an automatic rollback still use the old password, which
the repository no longer accepts.

### Updating the server scripts

Deploys do not update the installed copies of `engleks-deploy`, `engleks-render-env`,
`engleks-backup` or the backup units. After a deploy of a commit that changes
`deploy/server/engleks-*`, re-run `setup.sh` from the checkout, with the same arguments. It
keeps the Infisical credentials file:

```bash
sudo bash /opt/engleks/deploy/server/setup.sh <you> 'ssh-ed25519 AAAA... engleks-ci' uNNNNNN.your-storagebox.de
```

The key line is in `engleks-ci.pub` on your machine, and also in
`~deploy/.ssh/authorized_keys` on the server, after the `restrict` option.

## Backups

`engleks-backup` runs every night at 03:00 UTC (`engleks-backup.timer`; a night missed
while the server was off runs at the next boot). It dumps the `engleks` and `litellm`
databases, sends the dumps to the restic repository on the Storage Box (encrypted on the
server, so Hetzner cannot read users' texts), keeps 7 daily, 4 weekly and 6 monthly
snapshots, and pings healthchecks.io. A failed or missing run sends you an email. `.env`
is not backed up: it is generated from `deploy/prod.env` and Infisical, and a backup of it
would only put every secret on the Storage Box.

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

You need `RESTIC_PASSWORD` (from Infisical or your password manager), the machine
identity's credentials and access to the Storage Box. There is no `.env` to restore: the
deploy generates it, and the dumps hold no role passwords.

1. Do first-time setup steps 5 and 7–9 for the new server: create it, add its IPv4 address
   and IPv6 /64 to the Trusted IPs of the identity `engleks-prod-server` (or create a new
   identity), run `setup.sh` (it asks for the credentials), and install its new Storage Box
   key.
2. Update the GitHub environment's `DEPLOY_HOST` and `DEPLOY_KNOWN_HOSTS` for the new
   server, and **Run workflow** with the input empty. The deploy renders `.env`, opens the
   existing restic repository (no `restic init`), and starts the stack on empty databases.
3. As `deploy` in `/opt/engleks`, restore both dumps as in "Restore on the same server".
   With the old Infisical project, `API_LLM_API_KEY` is unchanged and the restored
   `litellm` database holds that key. Restoring replaces the database the deploy just
   filled, though, so a key the deploy created (a new Infisical project has a new
   `API_LLM_API_KEY`) is gone again.
4. **Run workflow** once more, input empty. The deploy ensures the API's LiteLLM key
   against the restored database: it exists, or it is created with the cap.
5. `sudo systemctl start engleks-backup` and check healthchecks.io.

If the Infisical account itself is lost, create a new project, run
`generate-secrets.sh` again but keep the saved `RESTIC_PASSWORD` instead of the generated
one, and re-issue the third-party keys (BotFather token, OpenAI, Anthropic, healthchecks.io
URL). On a new server that is all; on a running server, first delete
`~deploy/.config/engleks/infisical` and re-run `setup.sh` with the new project ID and
identity (`setup.sh` keeps an existing credential file), and then the deploy applies the new
database passwords and LiteLLM key.

## Moving to Infisical

Once, for the server that runs today with a hand-made `.env`. Do the steps in this order.

1. **Merge** the branch. The installed (old) `engleks-deploy` deploys the new commit with
   the existing `.env`: it ignores `deploy/prod.env`, and the new `engleks.secrets` label
   falls back to `dev`. Nothing changes for users. Check that the merge's Deploy run is
   green before going on: if it rolled back to the pre-merge commit, a later failure would
   roll back to a commit whose `generate_key.py` lacks `--ensure-stdin`.
2. **Infisical.** Create the project, `prod` environment and the machine identity as in
   first-time setup steps 6 and 7 (the server already exists: read its IPv4 address and IPv6 /64
   in the Hetzner console or with `ip -br addr`), but **import today's values, do not generate new ones**.
   As `deploy` in `/opt/engleks`, print the secret lines of the current `.env`, copy them
   into Infisical's ".env import" with the same names and values, and clear the terminal.
   Render rejects an empty secret, so import any empty value (typically `ANTHROPIC_API_KEY`)
   as `unused`, or as a real value:

   ```bash
   grep -E '^(BOT_TELEGRAM_TOKEN|OPENAI_API_KEY|ANTHROPIC_API_KEY|HEALTHCHECKS_BACKUP_URL|API_DB_PASSWORD|LITELLM_DB_PASSWORD|BOT_SERVICE_TOKEN|LITELLM_MASTER_KEY|LITELLM_UI_PASSWORD|API_LLM_API_KEY|RESTIC_PASSWORD)=' .env
   ```

   Then compare the plain values of `.env` with `deploy/prod.env`. If one differs on
   purpose, change `deploy/prod.env` in a PR first, or step 4 will overwrite it:

   ```bash
   grep -E '^(COMPOSE_FILE|LLM_UPSTREAM_MODEL|LLM_UPSTREAM_API_BASE|API_LLM_MODEL|API_BOT_USERNAME|API_RATE_LIMIT_PER_MINUTE|API_DAILY_TOKEN_BUDGET|LITELLM_UI_USERNAME|RESTIC_REPOSITORY)=' .env
   ```
3. **Re-run `setup.sh`** from the checkout, with the same arguments as in "Updating the server
   scripts", and enter the credentials. This installs the new `engleks-deploy`,
   `engleks-render-env` and `engleks-backup`.
4. **Run workflow** (input empty). The values are the same, so nothing is reconciled: no
   `ALTER ROLE`, the LiteLLM key exists (its model list is synced), and the restic check is
   skipped because the repository and password are unchanged. The services are recreated
   once, because the label is new. Check:
   - the Deploy run is green (if the render step fails, the server stays on the running
     release; `/opt/engleks/.deploy.log` and the run's log name the cause);
   - `docker inspect --format '{{ index .Config.Labels "engleks.secrets" }}' "$(docker compose ps -q api)"`
     prints a 64-character hash, not `dev`;
   - the bot answers;
   - `sudo systemctl start engleks-backup` turns the healthchecks.io check green (this is
     the proof that restic still opens the repository).
5. **Clean up.** Delete the full `.env` copy from the password manager; keep only
   `RESTIC_PASSWORD`. Snapshots made before the move still contain `.env`; they age out under
   the retention (up to six months), or delete them with `restic forget --prune`.

Once step 4 is done, rolling back to a commit from before this change is not supported (see
"Rollback").
