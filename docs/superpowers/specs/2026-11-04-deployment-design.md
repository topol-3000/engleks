# Deployment: Design

Status: draft for review · Date: 2026-11-04 · Related: [MVP Architecture](../../architecture.md), [Project Structure](2026-11-01-project-structure-design.md), [Access](2026-11-03-access-design.md)

## 1. Goal

Run the stack for early-access users and ship every change on `main` to them without manual steps. Access made this safe: signup is invite-only, each user has a rate limit and a daily token budget, and `engleks-admin` covers invites and users over SSH. This sub-project comes before the Admin dashboard (Foundation, Core slice, Bot, Access, **Deployment**, Admin dashboard).

Done means: a merge to `main` that passes CI is live on the server within minutes and visible as a green Deploy run in Actions; a failed deploy brings the previous release back and turns the run red; "Run workflow" with an older SHA rolls back; `make invite` works over SSH on the server; no application secret ever passes through GitHub.

## 2. Scope

In scope:

- `compose.prod.yaml`: an override that runs images from GHCR instead of building them.
- CI pushes the api, bot and litellm images to GHCR from `main`, tagged with the commit SHA.
- `deploy.yml`: deploys every green `main` commit over SSH; manual redeploy or rollback by SHA.
- Server scripts: one-time setup, one-time secrets bootstrap, the deploy wrapper.
- A global LLM spending cap on the API's LiteLLM key.
- `docs/deployment.md` (runbook) and updates to `AGENTS.md` and `architecture.md`.
- `shellcheck` for the server scripts; a CI check that the prod Compose config is valid.

Out of scope, with the reason:

| Item | Where | Why |
| --- | --- | --- |
| Database backups | Later | Owner's decision for early access. Losing the server loses users, invites and history; Hetzner's server backups (one checkbox, ~€1/month) are listed in the runbook as an optional stopgap |
| Domain, TLS, reverse proxy | Admin dashboard | The bot uses long polling; nothing needs to be reachable from outside until `/admin` exists |
| Terraform, Ansible | Later | One server; a re-runnable shell script and a runbook are enough |
| Tailscale (SSH closed to the internet) | Later | Adds an account and a CI auth key; key-only SSH with a forced command is enough for now |
| Staging environment | Later | One environment; CI and the automatic rollback are the safety net |
| Monitoring, alerting | Later | Failed deploys show in Actions; `restart: unless-stopped` restarts crashed services |

## 3. Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Host | One Hetzner Cloud CX23 (2 vCPU, 4 GB RAM, 40 GB disk, x86), Ubuntu 24.04 | The stack idles at ~0.9 GB without Ollama; 4 GB leaves room for LiteLLM's startup and a deploy pulling a 2 GB image. x86 because CI builds amd64 images |
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

## 4. Server layout

```
/opt/engleks/                 git checkout, detached at the deployed SHA, owned by deploy
  .env                        prod config + secrets, 0600, gitignored, untouched by checkout
  compose.yaml                base, unchanged
  compose.prod.yaml           override
/usr/local/bin/engleks-deploy deploy wrapper (installed by setup.sh, outside the checkout)
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
2. Prompts, without echo, for `BOT_TELEGRAM_TOKEN` and `ANTHROPIC_API_KEY`; `OPENAI_API_KEY` gets a placeholder unless the upstream model needs it. Prompts for the non-secret prod values with defaults (`LLM_UPSTREAM_MODEL`, `API_LLM_MODEL`, `API_BOT_USERNAME`).
3. Writes `.env` with umask 077, including `COMPOSE_FILE` and the `ENGLEKS_TAG` of the current checkout.
4. Starts `postgres` and `litellm`, waits until both are healthy, runs `generate_key.py --max-budget 10 --budget-duration 30d` in the litellm container and writes the printed `API_LLM_API_KEY` into `.env`.
5. Tells the owner to save `.env` in a password manager.

`generate_key.py` gains two optional arguments, `--max-budget` (USD) and `--budget-duration` (LiteLLM's duration format, e.g. `30d`), passed through to `/key/generate`. Without them it behaves as today, so `make llm-key` in dev is unchanged. When the cap is reached, LiteLLM rejects the API's calls until the period resets; the API returns its existing LLM-failure error and the bot shows its generic error message. The cap is changed later with LiteLLM's `/key/update` or its UI, without a deploy.

CI never sees any of these values. Changing a secret follows the procedure from the closed `docs/secret-rotation` PR, adapted for the server, in the runbook.

## 7. Pipeline

### CI (`ci.yml`)

- `images` (api, bot) and `trivy-litellm` build and scan exactly as now.
- On a `push` to `main` only, after the Trivy gate: log in to GHCR with `GITHUB_TOKEN`, tag the scanned image `ghcr.io/topol-3000/engleks-<app>:<sha>` and push it. `packages: write` is granted to these two jobs only; the workflow default stays `contents: read`.
- New job `compose-prod`: `docker compose -f compose.yaml -f compose.prod.yaml config --quiet` with dummy values for every variable the files require. A broken override fails CI, not the deploy.
- New `shellcheck` step over `deploy/server/*.sh` and `deploy/server/engleks-deploy`, also added as a pre-commit hook.
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

## 9. Server setup

`deploy/server/setup.sh`, run once as root on a fresh Ubuntu 24.04 CX23, safe to re-run. Arguments: the owner's username and CI's public deploy key.

- Creates the owner (sudo, copies root's `authorized_keys` that Hetzner installed) and `deploy`.
- Installs Docker Engine and the Compose plugin from Docker's apt repository.
- Writes `/etc/docker/daemon.json` with `"log-driver": "local"` (logs rotate by default).
- Adds a 2 GB swap file.
- SSH: a file in `/etc/ssh/sshd_config.d/` with `PasswordAuthentication no` and `PermitRootLogin no`.
- `unattended-upgrades` for security updates, with an automatic reboot at 04:00 UTC when one is required; the stack comes back through `restart: unless-stopped`.
- Clones the repo to `/opt/engleks` as `deploy`, installs `/usr/local/bin/engleks-deploy`, and adds CI's key to `deploy`'s `authorized_keys` with the forced command.

### First-time order

The server pulls images, so they must exist and be public before it starts anything:

1. Merge this sub-project to `main`. CI pushes the first images. The Deploy run that follows fails because the `production` environment has no secrets yet; that is expected.
2. Make the three GHCR packages public.
3. Create the server and firewall; run `setup.sh`. The checkout is at `main`'s head, whose images now exist.
4. Run `bootstrap-env.sh`.
5. Create the `production` environment and its secrets; deploy `main`'s head with "Run workflow".

## 10. Testing

Shell scripts and infrastructure do not fit pytest, so:

- **CI**: `compose-prod` (§7) and `shellcheck`.
- **Acceptance, once on the real server**:
  1. A merge to `main` deploys by itself; the Deploy run is green and `docker compose ps` shows the new SHA.
  2. `sudo -iu deploy`, `cd /opt/engleks && make invite` prints a code; redeeming it in Telegram works; a text gets a reply.
  3. Rollback rehearsal: merge a commit whose `api` fails its healthcheck; check that the previous SHA is running again and the run is red; then merge its revert.
  4. "Run workflow" with an older SHA deploys it.

## 11. Docs

- New `docs/deployment.md` (runbook): creating the server and firewall, `setup.sh`, `bootstrap-env.sh`, the GitHub Environment and its secrets, making the three GHCR packages public after the first push, the Anthropic console spending limit, how a deploy works, rollback, admin commands, the LiteLLM UI over an SSH tunnel, changing a secret, changing the spending cap, and Hetzner server backups as an optional stopgap.
- `AGENTS.md`: Deployment in the sub-project list; deploy and rollback in the commands table; the rule that every migration must work with the previous release's code; `deploy/server/*` and `deploy.yml` count as CI/Docker/Compose under Boundaries.
- `architecture.md` §9: production is one Hetzner CX23 running Compose, images in GHCR, automatic deploys from `main`.
- `README.md`: a link to the runbook.
