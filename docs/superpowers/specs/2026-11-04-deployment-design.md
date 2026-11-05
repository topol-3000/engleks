# Deployment: Design

Status: draft for review · Date: 2026-11-04 · Related: [MVP Architecture](../../architecture.md), [Project Structure](2026-11-01-project-structure-design.md), [Access](2026-11-03-access-design.md)

## 1. Goal

Run the stack for early-access users and ship every change on `main` to them without manual steps. Access made this safe: signup is invite-only, each user has a rate limit and a daily token budget, and `engleks-admin` covers invites and users over SSH. This sub-project comes before the Admin dashboard (Foundation, Core slice, Bot, Access, **Deployment**, Admin dashboard).

Done means: a merge to `main` that passes CI is live on the server within minutes and visible as a green Deploy run in Actions; a failed deploy brings the previous release back and turns the run red; "Run workflow" with an older SHA rolls back; `make invite` works over SSH on the server; no application secret ever passes through GitHub; both databases are backed up off the server every night, a missed backup sends an email, and a restore has been rehearsed.

## 2. Scope

In scope:

- `compose.prod.yaml`: an override that runs images from GHCR instead of building them.
- CI builds arm64 images on native ARM runners and pushes the api, bot and litellm images to GHCR from `main`, tagged with the commit SHA.
- `deploy.yml`: deploys every green `main` commit over SSH; manual redeploy or rollback by SHA.
- Server scripts: one-time setup, one-time secrets bootstrap, the deploy wrapper.
- A global LLM spending cap on the API's LiteLLM key.
- Nightly encrypted backups of both databases and `.env` to a Hetzner Storage Box, monitored by healthchecks.io.
- `docs/deployment.md` (runbook) and updates to `AGENTS.md` and `architecture.md`.
- `shellcheck` for the server scripts; a CI check that the prod Compose config is valid.

Out of scope, with the reason:

| Item | Where | Why |
| --- | --- | --- |
| Backup before each migration, point-in-time recovery (WAL archiving) | Later | A nightly dump is enough for early access; at most a day of data is at risk |
| amd64 images in CI | None | Production is arm64; dev machines build their own amd64 images with `make up` |
| Domain, TLS, reverse proxy | Admin dashboard | The bot uses long polling; nothing needs to be reachable from outside until `/admin` exists |
| Terraform, Ansible | Later | One server; a re-runnable shell script and a runbook are enough |
| Tailscale (SSH closed to the internet) | Later | Adds an account and a CI auth key; key-only SSH with a forced command is enough for now |
| Staging environment | Later | One environment; CI and the automatic rollback are the safety net |
| Monitoring, alerting (beyond the backup check) | Later | Failed deploys show in Actions; `restart: unless-stopped` restarts crashed services |

## 3. Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Host | One Hetzner Cloud CAX11 (2 Ampere vCPU, arm64, 4 GB RAM, 40 GB disk), Ubuntu 24.04 | Owner's choice. The stack idles at ~0.9 GB without Ollama; 4 GB leaves room for LiteLLM's startup and a deploy pulling a 2 GB image |
| CPU architecture | arm64 images only, built on GitHub's native `ubuntu-24.04-arm` runners | Every pinned base image (`python:3.14-slim`, `litellm:v1.106.1`, `uv:0.13.6`, `postgres:18.6`) is a multi-platform index with `linux/arm64`, so no Dockerfile or Compose change. Native runners are free for public repos; QEMU emulation would make the LiteLLM patch step very slow |
| Orchestration | Docker Compose, `compose.yaml` + `compose.prod.yaml` override | Architecture §9; the base file stays the single source of truth, so dev and prod cannot drift |
| Registry | GHCR, public packages, tag = full commit SHA | The repo is public: the server pulls without credentials and CI pushes with `GITHUB_TOKEN` |
| What gets deployed | The image CI built and Trivy scanned, pushed only after the gate passes | What runs is exactly what was checked |
| Deploy trigger | Automatically on every green `main` push; `workflow_dispatch` with a SHA for rollback | CI already gates `main` (lint, types, tests, Trivy) |
| Deploy mechanism | CI runs `ssh deploy@host <sha>`; a forced command on the server does the rest | Immediate, logged in Actions; CI's key can do nothing else |
| Secrets | Only in `/opt/engleks/.env` on the server (mode 0600), created once by a bootstrap script | The smallest attack surface for a public repo, no new tools. CI's only secrets are the SSH key, host and pinned host key |
| Code on the server | A git checkout of the public repo, detached at the deployed SHA | LiteLLM mounts `deploy/litellm/`, Postgres mounts its init script, and `make invite` needs the Makefile; config always matches the images |
| Global spending cap | `max_budget` $10 per 30 days on the API's LiteLLM key, plus a spending limit in the Anthropic console | Per-user budgets do not cap the total. A request costs ~$0.0008 (~4,700 tokens on `claude-haiku-5-5`); 10 users using their full daily budget every day cost ~$10/month |
| Failed deploy | Automatic rollback to the previous SHA, run marked failed | Users get the old release back without waiting for a person |
| Migrations | Forward only; each must work with the previous release's code | A rollback restores old images but never downgrades the schema |
| Backup destination | Hetzner Storage Box BX11 (1 TB, €3.20/month excl. VAT), in a different location from the server | Same account, SSH-key access, unlimited traffic. Backblaze B2 or Cloudflare R2 would be ~€0 and a separate provider, but add an account and an API key |
| Backup tool | `restic` over SFTP | Encrypts on the server, so Hetzner cannot read users' texts; deduplicates, compresses and applies retention itself |
| Backup monitoring | healthchecks.io (free tier), one check pinged by the backup job | A silently failing nightly job is the usual way to discover there is no backup; a missed or failed ping sends an email |

## 4. Server layout

```
/opt/engleks/                 git checkout, detached at the deployed SHA, owned by deploy
  .env                        prod config + secrets, 0600, gitignored, untouched by checkout
  compose.yaml                base, unchanged
  compose.prod.yaml           override
/usr/local/bin/engleks-deploy deploy wrapper (installed by setup.sh, outside the checkout)
/usr/local/bin/engleks-backup nightly backup (installed by setup.sh), run by engleks-backup.timer
/var/backups/engleks/         dump staging, 0700, owned by deploy, emptied after each run
```

Users:

- **Owner**: a personal sudo account, SSH key only. Root login and password login are disabled.
- **`deploy`**: owns `/opt/engleks`, member of `docker`, so effectively root on this server. CI's key in its `authorized_keys` is limited to `command="/usr/local/bin/engleks-deploy",restrict`. The owner runs admin commands as `sudo -iu deploy`, then `cd /opt/engleks && make invite note=...`.

Inbound traffic: a Hetzner Cloud Firewall allows TCP 22 and ICMP only. Every published port in `compose.yaml` is already bound to `127.0.0.1`. The LiteLLM admin UI is reached with `ssh -L 4000:localhost:4000`.

## 5. Production Compose

`compose.prod.yaml` holds only what differs from the base:

| Service | Change |
| --- | --- |
| `api`, `bot`, `litellm` | `image: ghcr.io/topol-3000/engleks-<app>:${ENGLEKS_TAG:?set ENGLEKS_TAG}`; `build: !reset null` |
| `migrate` | `image: ghcr.io/topol-3000/engleks-api:${ENGLEKS_TAG:?set ENGLEKS_TAG}`; `pull_policy: missing` |

`!reset` needs Docker Compose 2.24 or newer; `setup.sh` installs a current Compose plugin. Ollama stays off because `COMPOSE_PROFILES` is not set in prod. No other service changes.

The server's `.env` sets `COMPOSE_FILE=compose.yaml:compose.prod.yaml` and `ENGLEKS_TAG=<sha>`, so every `docker compose` and `make` command on the server uses both files without flags. `engleks-deploy` rewrites only the `ENGLEKS_TAG` line.

The prod `.env` also sets `LLM_UPSTREAM_MODEL`, `LLM_UPSTREAM_API_BASE`, `API_LLM_MODEL`, `API_BOT_USERNAME`, the limit defaults and the secrets. `COMPOSE_PROFILES` is absent.

## 6. Secrets

`deploy/server/bootstrap-env.sh`, run once by hand as `deploy` in `/opt/engleks`, refuses to run if `.env` exists. It:

1. Generates random values for `API_DB_PASSWORD`, `LITELLM_DB_PASSWORD` (letters and digits only: it goes into a URL), `BOT_SERVICE_TOKEN`, `LITELLM_MASTER_KEY` (`sk-` prefix) and `LITELLM_UI_PASSWORD`; sets `LITELLM_UI_USERNAME=admin`.
   Also generates `RESTIC_PASSWORD`.
2. Prompts, without echo, for `BOT_TELEGRAM_TOKEN`, `ANTHROPIC_API_KEY` and `HEALTHCHECKS_BACKUP_URL` (the check's ping URL is itself a secret: anyone with it can report success); prompts for `RESTIC_REPOSITORY` (`sftp:storagebox:/engleks`, using the SSH alias from §9); `OPENAI_API_KEY` gets a placeholder unless the upstream model needs it. Prompts for the non-secret prod values with defaults (`LLM_UPSTREAM_MODEL`, `API_LLM_MODEL`, `API_BOT_USERNAME`).
3. Writes `.env` with umask 077, including `COMPOSE_FILE` and the `ENGLEKS_TAG` of the current checkout.
4. Starts `postgres` and `litellm`, waits until both are healthy, runs `generate_key.py --max-budget 10 --budget-duration 30d` in the litellm container and writes the printed `API_LLM_API_KEY` into `.env`.
5. Runs `restic init` on the Storage Box repository.
6. Tells the owner to save `.env` in a password manager. This matters most for `RESTIC_PASSWORD`: the backups contain `.env`, so without a copy outside the server they cannot be decrypted.

`RESTIC_*` and `HEALTHCHECKS_BACKUP_URL` are read by the host's backup script, not by any container, so they are not Compose secrets; Compose ignores them.

`generate_key.py` gains two optional arguments, `--max-budget` (USD) and `--budget-duration` (LiteLLM's duration format, e.g. `30d`), passed through to `/key/generate`. Without them it behaves as today, so `make llm-key` in dev is unchanged. When the cap is reached, LiteLLM rejects the API's calls until the period resets; the API returns its existing LLM-failure error and the bot shows its generic error message. The cap is changed later with LiteLLM's `/key/update` or its UI, without a deploy.

CI never sees any of these values. Changing a secret follows the procedure from the closed `docs/secret-rotation` PR, adapted for the server, in the runbook.

## 7. Pipeline

### CI (`ci.yml`)

- `images` (api, bot) and `trivy-litellm` move to `runs-on: ubuntu-24.04-arm` and otherwise build, smoke-import and scan as now, so the arm64 image that runs in production is the one CI checks. The other jobs stay on x86.
- The LiteLLM Dockerfile pins patched packages (`ca-certificates-bundle=…-r3`, `ddtrace`, `aiohttp`, …). The first ARM run shows whether each exists for arm64; if one does not, the implementation picks the nearest version that has the fix on both architectures.
- On a `push` to `main` only, after the Trivy gate: log in to GHCR with `GITHUB_TOKEN`, tag the scanned image `ghcr.io/topol-3000/engleks-<app>:<sha>` and push it. `packages: write` is granted to these two jobs only; the workflow default stays `contents: read`.
- New job `compose-prod`: `docker compose -f compose.yaml -f compose.prod.yaml config --quiet` with dummy values for every variable the files require. A broken override fails CI, not the deploy.
- New `shellcheck` step over `deploy/server/*.sh`, `deploy/server/engleks-deploy` and `deploy/server/engleks-backup`, also added as a pre-commit hook.
- Pull requests still build and scan without pushing.

### Deploy (`deploy.yml`)

- Triggers:
  - `workflow_run` on CI `completed`, with `conclusion == 'success'`, `head_branch == 'main'` and `event == 'push'`; the SHA is `workflow_run.head_sha`.
  - `workflow_dispatch` with a required `sha` input (40 hex characters).
- `concurrency: { group: production, cancel-in-progress: false }`: deploys run one at a time and are never cancelled. It is a separate workflow because CI cancels in-progress runs on every push, which must never interrupt a deploy.
- Runs in the GitHub Environment `production`, limited to `main`, holding `DEPLOY_HOST`, `DEPLOY_SSH_KEY` and `DEPLOY_KNOWN_HOSTS`. The host key is pinned; `StrictHostKeyChecking` is never disabled.
- The only step: `ssh deploy@$DEPLOY_HOST <sha>`, streaming the server's output into the job log. The job fails if the command exits non-zero.

## 8. Deploy on the server

`engleks-deploy` (the forced command, source in `deploy/server/engleks-deploy`, installed by `setup.sh`). It lives outside the checkout, so a deploy never rewrites the script that is running it.

1. Reads the SHA from `$SSH_ORIGINAL_COMMAND` and accepts only `^[0-9a-f]{40}$`.
2. Takes an exclusive `flock` on `/opt/engleks/.deploy.lock`, so two deploys cannot overlap even if started by hand.
3. Records `previous` = the current `ENGLEKS_TAG`.
4. Runs `deploy(sha)`. On failure, if `previous` is set and differs, runs `deploy(previous)`, then exits non-zero either way.

`deploy(sha)`:

1. `git fetch origin` and `git checkout --detach <sha>`.
2. Set `ENGLEKS_TAG=<sha>` in `.env`.
3. `docker compose pull`.
4. `docker compose up -d --wait --wait-timeout 180`. The 180 s cover LiteLLM's 90 s start period. The implementation checks that `--wait` accepts the one-shot `migrate` exiting with 0; if it does not, it waits on the long-running services explicitly.
5. On success, remove local `ghcr.io/topol-3000/engleks-*` images whose tag is neither `<sha>` nor `previous`.

Failure cases:

| What fails | What happens |
| --- | --- |
| Unknown SHA or image not in GHCR | `fetch`/`pull` fails before any container changes; rollback is a no-op restore of checkout and tag |
| `migrate` fails | Postgres DDL is transactional, so the schema is unchanged; Compose stops before recreating `api`; rollback restores the old checkout and tag |
| A service not healthy within 180 s | Rollback redeploys the previous images. A migration that already ran stays applied (§3) |
| First deploy fails | No `previous`; the run is red and the owner investigates |

The bot is down for a few seconds per deploy. Telegram keeps updates for long polling, so no message is lost.

## 9. Backups

`engleks-backup` (source in `deploy/server/engleks-backup`), run as `deploy` by `engleks-backup.service` from `engleks-backup.timer` at 03:00 UTC daily (`Persistent=true`, so a night missed during a reboot runs at boot). 03:00 is before the 04:00 reboot window.

1. Pings `$HEALTHCHECKS_BACKUP_URL/start`.
2. Takes the same `flock` as `engleks-deploy`, waiting up to 10 minutes, so a dump never runs while a deploy recreates containers.
3. Dumps both databases through the running postgres container into `/var/backups/engleks/`: `pg_dump --format=custom --compress=0` for `engleks` and `litellm`. The dumps are left uncompressed so restic can deduplicate and compress them.
4. `restic backup /var/backups/engleks /opt/engleks/.env --tag nightly`.
5. `restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune`.
6. Empties `/var/backups/engleks/`.
7. Pings `$HEALTHCHECKS_BACKUP_URL` on success, or `$HEALTHCHECKS_BACKUP_URL/fail` with the exit status on any error.

The healthchecks.io check has a period of 1 day and a grace of 2 hours: no ping or a failure ping sends the owner an email.

Storage Box: a BX11 in a different Hetzner location from the server, with SSH support turned on. `setup.sh` generates `deploy`'s backup key (`~deploy/.ssh/storagebox`, ed25519) and an SSH alias `storagebox` (port 23, that key, the box's host key pinned in `known_hosts`). The owner installs the public key on the box once, as the runbook describes. Restic is installed from Ubuntu's apt repository, so security updates arrive with `unattended-upgrades`.

The LiteLLM database is backed up too. It holds the API's key and its budget, and the spend logs with stored prompts (`store_prompts_in_spend_logs`), which are why encryption matters.

Restore (runbook): `restic restore latest --target <dir>`, then `pg_restore --clean --if-exists` into each database with the stack's `api`, `bot` and `litellm` stopped. On a new server, `.env` comes from the backup and only `RESTIC_PASSWORD` and the Storage Box key are needed to start.

Expected size: an interaction plus its LiteLLM spend log is tens of KB; even at 1,000 requests a day the nightly upload after deduplication is a few MB, far inside 1 TB.

## 10. Server setup

`deploy/server/setup.sh`, run once as root on a fresh Ubuntu 24.04 CAX11, safe to re-run. Arguments: the owner's username, CI's public deploy key and the Storage Box hostname (`uNNNNNN.your-storagebox.de`).

- Creates the owner (sudo, copies root's `authorized_keys` that Hetzner installed) and `deploy`.
- Installs Docker Engine and the Compose plugin from Docker's apt repository.
- Writes `/etc/docker/daemon.json` with `"log-driver": "local"` (logs rotate by default).
- Adds a 2 GB swap file.
- SSH: a file in `/etc/ssh/sshd_config.d/` with `PasswordAuthentication no` and `PermitRootLogin no`.
- `unattended-upgrades` for security updates, with an automatic reboot at 04:00 UTC when one is required; the stack comes back through `restart: unless-stopped`.
- Clones the repo to `/opt/engleks` as `deploy`, installs `/usr/local/bin/engleks-deploy`, and adds CI's key to `deploy`'s `authorized_keys` with the forced command.
- Installs `restic`, `/usr/local/bin/engleks-backup` and the `engleks-backup` service and timer (enabled); creates `/var/backups/engleks/` (0700, `deploy`); generates the Storage Box key and SSH alias (§9) and prints the public key.
### First-time order

The server pulls images, so they must exist and be public before it starts anything:

1. Merge this sub-project to `main`. CI pushes the first images. The Deploy run that follows fails because the `production` environment has no secrets yet; that is expected.
2. Make the three GHCR packages public.
3. Order the Storage Box (other location, SSH support on) and create the healthchecks.io check.
4. Create the server and firewall; run `setup.sh`. The checkout is at `main`'s head, whose images now exist. Install the printed backup key on the Storage Box.
5. Run `bootstrap-env.sh`.
6. Create the `production` environment and its secrets; deploy `main`'s head with "Run workflow".
7. Run `sudo systemctl start engleks-backup` once and check the healthchecks.io check turns green.

## 11. Testing

Shell scripts and infrastructure do not fit pytest, so:

- **CI**: `compose-prod` (§7) and `shellcheck`.
- **Acceptance, once on the real server**:
  1. A merge to `main` deploys by itself; the Deploy run is green and `docker compose ps` shows the new SHA.
  2. `sudo -iu deploy`, `cd /opt/engleks && make invite` prints a code; redeeming it in Telegram works; a text gets a reply.
  3. Rollback rehearsal: merge a commit whose `api` fails its healthcheck; check that the previous SHA is running again and the run is red; then merge its revert.
  4. "Run workflow" with an older SHA deploys it.
  5. Restore rehearsal: restore the latest snapshot, `pg_restore` the `engleks` dump into a scratch database `engleks_restore_test`, compare row counts of `app_user` and `interaction` with the live database, then drop it.
  6. Backup alert: stop the timer for a day (or point the job at a wrong repository once) and check that healthchecks.io sends an email; then restore the setting.

## 12. Docs

- New `docs/deployment.md` (runbook): creating the server and firewall, `setup.sh`, `bootstrap-env.sh`, the GitHub Environment and its secrets, making the three GHCR packages public after the first push, the Anthropic console spending limit, how a deploy works, rollback, admin commands, the LiteLLM UI over an SSH tunnel, changing a secret, changing the spending cap, ordering the Storage Box and installing its key, the healthchecks.io check, running a backup by hand, restoring, and the monthly cost (CAX11 ~€6 per third-party trackers, BX11 €3.20, plus the primary IPv4 and VAT; check Hetzner's pages).
- `AGENTS.md`: Deployment in the sub-project list; deploy and rollback in the commands table; the rule that every migration must work with the previous release's code; `deploy/server/*` and `deploy.yml` count as CI/Docker/Compose under Boundaries.
- `architecture.md` §9: production is one Hetzner CAX11 (arm64) running Compose, images in GHCR, automatic deploys from `main`, nightly restic backups to a Storage Box.
- `README.md`: a link to the runbook.
