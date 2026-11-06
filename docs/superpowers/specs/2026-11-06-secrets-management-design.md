# Secrets Management: Design

Status: draft for review · Date: 2026-11-06 · Related: [Deployment](2026-11-04-deployment-design.md), [Runbook](../../deployment.md)

## 1. Goal

Today every production value lives in `/opt/engleks/.env`, edited by hand over SSH, with a full copy in the owner's password manager that must be kept in sync. Changing the model, rotating an API key, rotating a database password and rebuilding the server all need a login and a careful sequence of `sed`, `ALTER ROLE` and `docker compose up --force-recreate`.

This design moves plain config into the repository and secrets into Infisical Cloud, and makes the server build `.env` itself on every deploy. Security does not get weaker: GitHub still never sees an application secret, and the only credential on the server's disk is a read-only, IP-bound machine identity that can be revoked from the Infisical UI.

Done means:

- a production config change (model, limits, bot username) is a PR to `deploy/prod.env` and goes live with the automatic deploy;
- a secret change is an edit in Infisical followed by **Run workflow** with no input; nobody logs into the server;
- a database password or the API's LiteLLM key can be rotated by changing it in Infisical; the deploy applies it to Postgres and LiteLLM;
- **Run workflow** with no input deploys the head of the selected branch; an optional SHA still rolls back;
- a new server needs `setup.sh`, the machine identity's credentials, the Storage Box key and one deploy;
- `.env` is a generated file: no one edits it, the backups no longer contain it, and the password manager keeps only `RESTIC_PASSWORD`.

## 2. Scope

In scope:

- `deploy/prod.env` (committed production config) and `deploy/secrets.txt` (committed list of secret names).
- `engleks-render-env`: builds `.env` from `prod.env`, Infisical and the deployed SHA.
- `engleks-deploy`: renders `.env`, reconciles Postgres, restic and LiteLLM, rolls back `.env` with the release.
- `generate_key.py`: create-or-update a key with a given value.
- `deploy/infisical/generate-secrets.sh`: prints the generated secrets for import into Infisical.
- `setup.sh`: installs `jq` and the new script, stores the machine identity's credentials.
- `deploy.yml`: optional `sha` input.
- `compose.yaml`: a secrets-hash label on the four app services.
- `engleks-backup`: stops backing up `.env`.
- Removing `bootstrap-env.sh` and its tests.
- Runbook, `AGENTS.md`, `.env.example` and a pointer in the Deployment spec.

Out of scope, with the reason:

| Item | Why |
| --- | --- |
| Dev `.env` from Infisical | Dev values are placeholders; a local file is simpler and needs no account |
| Infisical for CI | CI holds only the SSH key, host and host key; nothing there changes |
| Automatic restic re-keying | Rare, and a failed automatic re-key could lock the backups; it stays a documented manual step, guarded by the deploy |
| Writing secrets back to Infisical from the server | Would need a write-capable identity on the server; every generated value is created on the owner's machine instead |
| Self-hosted Infisical | One person, one server; the MIT core keeps it open as an exit path |

## 3. Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Secret store | Infisical Cloud, EU region; project `engleks`, environment `prod` | Owner's choice. Versioning and an audit log per secret, dotenv-shaped data, free plan covers 5 identities, MIT core can be self-hosted later |
| Plain config | `deploy/prod.env` in the repo | Reviewed and versioned with the code, so a rollback also restores a matching `API_LLM_MODEL` |
| Server credential | One machine identity, Universal Auth, read-only on `prod`, Trusted IPs = the server's IP, short access-token TTL | The only secret left on disk; a copy is useless elsewhere, and revoking it touches no app secret |
| Infisical client on the server | `curl` + `jq` against the REST API | No vendor apt repository with root on the server; `curl` is already stubbed in `deploy/tests` |
| When `.env` is built | On every deploy, before anything changes | A secret change is a redeploy; a failed fetch leaves the server untouched |
| Recreating services on a secret change | `ENGLEKS_SECRETS_HASH` in `.env`, used as a label on `migrate`, `api`, `litellm`, `bot` | Compose ignores changed secret values; a changed label recreates the service. Postgres reads its passwords only on first init, so it is not labelled |
| Database password rotation | The deploy runs `ALTER ROLE` when a value changed | Declarative: the value in Infisical is the truth |
| API's LiteLLM key | Declarative: Infisical holds the value, the deploy creates it in LiteLLM if missing and syncs its model list | Removes the two-phase bootstrap and the manual `/key/update` when switching models |
| Rollback | Restores the previous `.env` and checkout without calling Infisical | An Infisical outage must not block a rollback |
| Manual deploy input | Branch from "Use workflow from"; optional SHA for rollback | No SHA to copy for a redeploy; rollback stays instant because the old images are in GHCR |
| Backups | Dumps only; `.env` no longer included | `.env` is generated; backing it up only puts every secret into the Storage Box |
| Break-glass | `RESTIC_PASSWORD` also in the owner's password manager | The one value that cannot be re-issued if the Infisical account is lost |

## 4. Where each value lives

| Where | Values | Changed by |
| --- | --- | --- |
| `deploy/prod.env` (committed) | `COMPOSE_FILE`, `LLM_UPSTREAM_MODEL`, `LLM_UPSTREAM_API_BASE`, `API_LLM_MODEL`, `API_BOT_USERNAME`, `API_RATE_LIMIT_PER_MINUTE`, `API_DAILY_TOKEN_BUDGET`, `LITELLM_UI_USERNAME`, `RESTIC_REPOSITORY` | a PR |
| Infisical `engleks` / `prod` (names in `deploy/secrets.txt`) | `BOT_TELEGRAM_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `HEALTHCHECKS_BACKUP_URL`, `API_DB_PASSWORD`, `LITELLM_DB_PASSWORD`, `BOT_SERVICE_TOKEN`, `LITELLM_MASTER_KEY`, `LITELLM_UI_PASSWORD`, `API_LLM_API_KEY`, `RESTIC_PASSWORD` | the Infisical UI |
| `~deploy/.config/engleks/infisical` (0600) | `INFISICAL_API_URL` (default `https://eu.infisical.com`), `INFISICAL_PROJECT_ID`, `INFISICAL_ENVIRONMENT` (`prod`), `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET` | `setup.sh` |
| `.env` (generated, 0600) | all of the above except the credential file, plus `ENGLEKS_TAG` and `ENGLEKS_SECRETS_HASH` | `engleks-deploy` only |

`deploy/secrets.txt` has one name per line; `#` starts a comment. Every name is required. `ANTHROPIC_API_KEY` holds `unused` when no Anthropic key is used, as today. The project ID lives in the server's credential file rather than the repo, so the repo names no Infisical account.

`.env` keeps today's format (unquoted `KEY=value`, never sourced) and stays the file Compose, `make` and `engleks-backup` read, so admin commands on the server do not change.

## 5. `engleks-render-env`

Installed as `/usr/local/bin/engleks-render-env` by `setup.sh`, outside the checkout like `engleks-deploy`. Usage: `engleks-render-env <sha> <output>`, run as `deploy` in `/opt/engleks` with the checkout already at `<sha>`.

1. Reads the credential file. A missing file or field is an error.
2. Logs in: `POST $INFISICAL_API_URL/api/v1/auth/universal-auth/login` with the client ID and secret, and gets a short-lived access token. The request body and the `Authorization` header are passed to `curl` through process substitution (`--data @<(...)`, `-H @<(...)`), never as arguments, so no secret appears in the process list.
3. Fetches the environment's secrets as JSON from the raw-secrets endpoint (the implementation checks the current API version; v3 `GET /api/v3/secrets/raw?workspaceId=&environment=&secretPath=/` at the time of writing).
4. Builds the file:
   - a header: `# Generated by engleks-render-env from deploy/prod.env and Infisical. Do not edit; changes are lost on the next deploy.`
   - the lines of `deploy/prod.env` (comments and blank lines dropped);
   - one line per name in `deploy/secrets.txt`, in that order, with its value from Infisical;
   - `ENGLEKS_TAG=<sha>`;
   - `ENGLEKS_SECRETS_HASH=<sha256 of the secret lines>`.
5. Validates before writing anything; each failure names the key, never the value:
   - every name in `secrets.txt` exists in Infisical;
   - no name appears in both `prod.env` and `secrets.txt`;
   - every value passes today's `check_value` rule (no whitespace, quotes, `$`, `#` or backslashes);
   - `<sha>` is a 40-character lowercase SHA.
6. Writes `<output>` atomically: a `mktemp` file next to it, created 0600 under `umask 077`, then `mv`.

Infisical keys not listed in `secrets.txt` are ignored, with their names listed in one warning line, so an older commit's list can be rendered against today's project. The script prints only step names, key names and counts.

Exit codes: 0 written, 1 any failure (the output file is not touched).

For the tests, like `ENGLEKS_DIR` elsewhere, the credential file's path comes from `ENGLEKS_INFISICAL_CREDENTIALS` when set; in production it is always the default.

## 6. `engleks-deploy`

Unchanged contract: one validated 40-character SHA, from the forced command or the command line; the lock, the `tee` to `.deploy.log` and the exit codes stay. A missing `.env` is no longer an error: it means a first deploy, with no previous SHA to roll back to. The `deploy()` steps become:

1. `git fetch` (online only) and `git checkout --detach <sha>`.
2. **Render** (online only): `engleks-render-env <sha> .env.next`. The current `.env` is copied to `.env.previous` (0600). Offline (the rollback), `.env.previous` is the file to deploy and nothing is rendered.
3. **Reconcile Postgres.** For each of `API_DB_PASSWORD` and `LITELLM_DB_PASSWORD` whose value differs between the running `.env` and the one about to be deployed: `docker compose up -d --wait postgres`, then `ALTER ROLE` through `docker compose exec -T postgres psql` over the local socket, with the value passed as a psql variable on stdin (as in today's runbook), never as an argument. The API role is `CURRENT_USER` (the `POSTGRES_USER`), the proxy's role is `litellm`. With no running `.env` (the first deploy), nothing is reconciled.
4. **Check restic** when `RESTIC_PASSWORD` differs from the running `.env`, or there is no running `.env`: `restic cat config` with the new values. Exit 0: fine. Exit 10 (repository does not exist): `restic init`. Anything else: fail, with a pointer to the runbook's re-keying procedure. This needs the Storage Box only on a first deploy or a rotation.
5. Move `.env.next` over `.env` (online); restore `.env.previous` over `.env` (offline).
6. `docker compose pull` (online only), then `docker compose up -d --wait --wait-timeout 180`.
7. **Ensure the API's LiteLLM key**: `printf '%s' "$API_LLM_API_KEY" | docker compose exec -T litellm python /deploy/generate_key.py --ensure-stdin --max-budget 10 --budget-duration 30d`. If the key changed, the old value is deleted the same way (`--delete-stdin`) after the new one is in place; a failed delete is a warning, not a failed deploy.

On success, `.env.previous` is deleted (it holds a full set of secrets and is no longer needed) and old images are removed as today.

On failure, the rollback runs `deploy <previous> offline`. Step 3 then compares the failed `.env` with `.env.previous` and sets the database passwords back; step 7 makes sure the previous key exists. If no `.env.previous` exists because the failure came before step 2 finished, the running `.env` is left as it is and only the checkout and images roll back, as today.

A manual `engleks-deploy <sha>` on the server behaves the same.

## 7. `generate_key.py`

Two new modes, standard library only as today; the master key still comes from `/run/secrets/litellm_master_key`, and key values arrive on stdin, never in arguments:

- `--ensure-stdin`: reads a key value. `GET /key/info?key=` with it; if the key exists, `POST /key/update` with today's `MODELS` list (the budget is left alone, so a cap changed in the UI survives deploys); if it does not, `POST /key/generate` with `key` set to that value, the models, the metadata and the budget flags. Prints `created` or `updated`.
- `--delete-stdin`: reads a key value and `POST /key/delete`s it. A key that no longer exists is not an error.

The existing mode (generate a random key and print it) stays for `make llm-key` in dev.

A rotated key starts with no spend, so the $10 per 30 days cap restarts with it. Whether changing `LITELLM_MASTER_KEY` invalidates existing virtual keys is checked during implementation; either way the next deploy's `--ensure-stdin` brings the API's key back.

## 8. Compose and the Deploy workflow

`compose.yaml`: `migrate`, `api`, `litellm` and `bot` get

```yaml
labels:
  engleks.secrets: ${ENGLEKS_SECRETS_HASH:-dev}
```

In dev the label is the constant `dev`, so nothing changes there. Any changed secret changes the hash and recreates all four services; a code deploy already recreates them, because the image tag changes.

`deploy.yml`: the `sha` input becomes optional (`required: false`, description "Empty: deploy the head of the branch picked above. A full SHA: roll back to that commit"). For `workflow_dispatch`, `SHA` is `inputs.sha || github.sha`. The 40-character check, the `main`-head check for `workflow_run` events and the SSH step stay. The `production` environment still accepts only `main`, so another branch fails at GitHub's environment check.

## 9. Setup, recovery and backups

New server (replaces `bootstrap-env.sh`, which is deleted):

1. In Infisical (EU), create the project `engleks` with the environment `prod`. On the owner's machine, `deploy/infisical/generate-secrets.sh` prints dotenv lines for `API_DB_PASSWORD`, `LITELLM_DB_PASSWORD` (hex: letters and digits only, it goes into a URL), `BOT_SERVICE_TOKEN`, `LITELLM_MASTER_KEY` (`sk-` + hex), `LITELLM_UI_PASSWORD`, `API_LLM_API_KEY` (`sk-` + hex) and `RESTIC_PASSWORD`, each from `openssl rand -hex 32`. Paste them into Infisical's ".env import". Add `BOT_TELEGRAM_TOKEN`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (or `unused`) and `HEALTHCHECKS_BACKUP_URL` by hand. Save `RESTIC_PASSWORD` in the password manager.
2. Create the machine identity `engleks-prod-server`: Universal Auth, Trusted IPs = the server's IP, a short access-token TTL, a read-only role on `prod`. Create its client secret.
3. `setup.sh` (same arguments as today) also installs `jq` and `engleks-render-env`. If `~deploy/.config/engleks/infisical` does not exist, it asks for the project ID and client ID (echoed) and the client secret (not echoed), and writes the file 0600, owned by `deploy`. Re-runs keep the existing file.
4. Create the GitHub environment as today, then **Run workflow**. The first deploy renders `.env`, starts the stack, runs `restic init` and creates the API's LiteLLM key with the cap.

Recovery on a new server: steps 2 and 3 (a new identity, or the new IP added to the existing one), install the Storage Box key, deploy, then restore both dumps as the runbook describes today. `.env` is never restored from a backup.

`engleks-backup` backs up `/var/backups/engleks` only; it still reads `RESTIC_*` and `HEALTHCHECKS_BACKUP_URL` from the generated `.env`. Losing the Infisical account is survivable: third-party keys can be re-issued, the dumps contain no role passwords, the LiteLLM key is recreated by the deploy, and `RESTIC_PASSWORD` has its break-glass copy.

## 10. Moving the current server over

Once, in this order:

1. Merge. The installed (old) `engleks-deploy` deploys the new commit with the existing `.env`: it ignores `prod.env`, and the new label falls back to `dev`.
2. Import the secret lines of today's `.env` into Infisical, with the same names and values; create the machine identity.
3. Re-run `setup.sh` from the checkout and enter the credentials. This installs the new `engleks-deploy` and `engleks-render-env`.
4. **Run workflow** with no input. The values are the same, so nothing is reconciled: no `ALTER ROLE`, the key exists (its model list is synced), restic opens. The services are recreated once, because the hash label is new.
5. Delete the full `.env` copy from the password manager; keep `RESTIC_PASSWORD`.

Rolling back to a commit from before this change is not supported once step 4 is done (it has no `deploy/prod.env`); the runbook says so.

## 11. Testing

All in `deploy/tests`, with the existing `server` fixture; no test calls Infisical, LiteLLM or restic for real. `jq` is the real binary; the `curl` stub returns canned Infisical JSON (login and secrets), configurable per test.

- `test_engleks_render_env.py`: merges `prod.env`, the secrets, `ENGLEKS_TAG` and `ENGLEKS_SECRETS_HASH`; the hash changes when a secret changes and not otherwise; a name missing in Infisical fails; a name in both sources fails; a value with `$`, a quote or whitespace fails; unknown Infisical keys are ignored with a warning; the output is 0600; on any failure the output file is untouched; a failed login or fetch exits 1; no secret value appears in the script's output or in the stub's logged arguments.
- `test_engleks_deploy.py`, new cases: a redeploy of the running SHA works; a changed DB password runs `ALTER ROLE` before `up`, an unchanged one does not; a failed deploy restores `.env.previous` and sets the password back; an Infisical failure leaves `.env`, the checkout and the containers untouched; the key is ensured on every deploy and the old one deleted after a rotation; restic is initialised on exit 10 and the deploy fails on any other `restic cat config` error; `.env.previous` is gone after a success. Existing cases are updated for the render step.
- `test_engleks_backup.py`: `.env` is no longer in the `restic backup` call.
- `test_generate_key.py`: `--ensure-stdin` creates a missing key with the given value and the budget, updates the models of an existing one without touching its budget; `--delete-stdin` deletes and tolerates a missing key; the key value never appears in a URL.
- `test_generate_secrets.py`: prints every expected name once, with the right formats.
- A consistency test: every variable that `compose.yaml` maps to a secret (`secrets.*.environment`) is in `deploy/secrets.txt` or `deploy/prod.env`; the two files share no name; `secrets.txt` has no duplicates.
- `test_bootstrap_env.py` is removed with the script.
- All new scripts are shellcheck-clean (`make lint`).

Manual check during step 4 of the move: the Deploy run is green, `docker compose ps` shows the new label, the bot answers, and a backup run turns the healthchecks.io check green.

## 12. Documentation

- `docs/deployment.md`: setup steps rewritten (Infisical project and import, machine identity, `setup.sh` prompts; no `bootstrap-env.sh`); "Changing a secret" becomes "edit in Infisical, Run workflow", plus the manual restic re-key; "Switching the model" becomes a PR to `deploy/prod.env` (and to `MODELS` in `generate_key.py` for a new alias); recovery and backups updated; a one-time "Moving to Infisical" section; rollback by SHA is unchanged, and the no-input Run workflow is explained.
- `AGENTS.md`: layout gains `deploy/prod.env`, `deploy/secrets.txt` and `deploy/infisical/`; the "a config value" row says a production value goes in `deploy/prod.env`; a new row: "a production secret → a Compose secret, its name in `deploy/secrets.txt`, its value in Infisical before merging".
- `.env.example`: the production comment points to `deploy/prod.env` and Infisical.
- Deployment spec §6: a one-line note that this design replaces the bootstrap.
