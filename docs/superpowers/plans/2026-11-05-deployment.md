# Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every green `main` commit runs on one Hetzner CAX11 within minutes, with automatic rollback, manual rollback by SHA, a $10/30-day LLM cap and nightly encrypted backups.

**Architecture:** CI builds arm64 images on native ARM runners and pushes the scanned ones to GHCR as `<sha>`. A separate `deploy.yml` SSHes to the server, where a forced command (`engleks-deploy`) checks out the SHA, rewrites `ENGLEKS_TAG` in `.env`, pulls and `up --wait`s, rolling back on failure. `compose.prod.yaml` swaps builds for GHCR images. A systemd timer runs `engleks-backup` (pg_dump → restic → Storage Box, pinging healthchecks.io). The server scripts are bash, checked by shellcheck and by pytest tests that run them against stub `git`/`docker`/`restic`/`curl` binaries.

**Tech Stack:** Docker Compose ≥ 2.24, GitHub Actions (`ubuntu-24.04-arm`), GHCR, bash + flock, systemd timers, restic over SFTP, healthchecks.io, pytest (subprocess) for the scripts.

**Spec:** [docs/superpowers/specs/2026-11-04-deployment-design.md](../specs/2026-11-04-deployment-design.md)

## Global Constraints

- The spec is the owner's approval for the CI, Compose and `deploy/` changes listed here (AGENTS.md "Ask before"). Any other Dockerfile change than an arm64 pin swap in `deploy/litellm/Dockerfile` (Task 6) needs a new ask. `compose.yaml` itself stays unchanged.
- Images: `ghcr.io/topol-3000/engleks-{api,bot,litellm}:<40-char lowercase commit SHA>`, nothing else (no `latest`).
- Server paths: checkout `/opt/engleks`, lock `/opt/engleks/.deploy.lock`, `/usr/local/bin/engleks-deploy`, `/usr/local/bin/engleks-backup`, staging `/var/backups/engleks` (0700, `deploy`), `.env` mode 0600.
- Scripts: `#!/usr/bin/env bash`, `set -euo pipefail`, shellcheck-clean. No secret value in argv, in output or in the logs (pass to restic via env, prompt with `read -rs`).
- Scripts read `.env` with a local `env_get KEY` helper (last `^KEY=` line, value verbatim); they never `source` it.
- Server scripts take path overrides from env, defaulting to the real paths: `ENGLEKS_DIR=/opt/engleks`, `BACKUP_DIR=/var/backups/engleks`, `ENGLEKS_LOCK_TIMEOUT` (seconds; deploy 900, backup 600). The forced command gets no client env (`restrict`), so these are test seams only.
- Values from the spec: `--wait-timeout 180`; budget `--max-budget 10 --budget-duration 30d`; retention `--keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune`; timer 03:00 UTC with `Persistent=true`; reboot 04:00 UTC.
- No new GitHub Actions: GHCR login and SSH use the `docker` and `ssh` CLIs. No new runtime dependency; restic, make, git, curl come from Ubuntu apt.
- Commit messages: conventional style as in `git log`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- `set -e` does nothing inside a function called as `if ! deploy "$sha"`. Without explicit checks a failed `docker compose pull` would carry on to `up`. Every step in `deploy()` must `|| return 1`. Pinned in Task 3 (`test_failed_pull_skips_up`).
- Rewriting `ENGLEKS_TAG` must keep `.env` at mode 0600 and leave every other line byte-for-byte. Pinned in Task 3 (`test_deploy_keeps_rest_of_env_and_mode`).
- A failed backup must not leave unencrypted dumps of users' texts in `/var/backups/engleks`. The staging directory is emptied on every exit, not only on success. Pinned in Task 4 (`test_restic_failure_pings_fail_and_cleans_up`).
- `--max-budget` without `--budget-duration` would quietly become a lifetime cap that never resets. Each flag requires the other. Pinned in Task 1 (`test_budget_flags_need_each_other`).
- If healthchecks.io is down or slow, the backup must still run and succeed. A ping failure is ignored. Pinned in Task 4 (`test_ping_failure_does_not_fail_backup`).

---

### Task 1: Budget flags for `generate_key.py`

**Files:**
- Modify: `deploy/litellm/generate_key.py`, `pyproject.toml` (`testpaths` += `"deploy/tests"`)
- Test: `deploy/tests/test_generate_key.py`

**Interfaces:**
- Produces: `python /deploy/generate_key.py [--max-budget USD --budget-duration DUR]` prints `API_LLM_API_KEY=<key>` as today. In the module: `parse_args(argv: list[str]) -> argparse.Namespace` (`max_budget: float | None`, `budget_duration: str | None`) and `build_payload(max_budget: float | None, budget_duration: str | None) -> dict[str, object]`. Side effects (reading the master key, HTTP) only in `main()` under `if __name__ == "__main__"`.

- [ ] **Step 1: Write the failing tests.** Load the script with `importlib.util.spec_from_file_location` (it is not a package).

```python
MODELS = ["engleks-rewrite", "claude-sonnet-5-5", "claude-haiku-5-5", "mistral-small"]

def test_payload_without_budget_is_unchanged():
    assert gk.build_payload(None, None) == {"models": MODELS, "metadata": {"service": "engleks-api"}}

def test_payload_with_budget():
    p = gk.build_payload(10.0, "30d")
    assert p["max_budget"] == 10.0 and p["budget_duration"] == "30d"

@pytest.mark.parametrize("argv", [["--max-budget", "10"], ["--budget-duration", "30d"]])
def test_budget_flags_need_each_other(argv):
    with pytest.raises(SystemExit) as e:
        gk.parse_args(argv)
    assert e.value.code == 2

@pytest.mark.parametrize("value", ["0", "-1", "ten"])
def test_budget_must_be_positive_number(value):
    with pytest.raises(SystemExit):
        gk.parse_args(["--max-budget", value, "--budget-duration", "30d"])

def test_no_flags():
    a = gk.parse_args([])
    assert (a.max_budget, a.budget_duration) == (None, None)
```

- [ ] **Step 2: Run them to make sure they fail.** Run `uv run pytest deploy/tests -q`. Expected: FAIL (no `build_payload`).
- [ ] **Step 3: Implement.** Use `argparse`, with `parser.error(...)` for the pairing and positive checks (exit code 2). Keep it stdlib only. Update the docstring to show the flags.
- [ ] **Step 4: Verify.** Run `make lint && uv run pytest deploy/tests -q`. Expected: clean, all pass. If the dev stack is up, `make llm-key` still prints a key.
- [ ] **Step 5: Commit.** Message: `feat(litellm): optional budget cap in generate_key.py`.

---

### Task 2: `compose.prod.yaml` and the `compose-prod` CI job

**Files:**
- Create: `compose.prod.yaml`
- Modify: `.github/workflows/ci.yml` (new job `compose-prod`), `.env.example` (comment only)

**Interfaces:**
- Produces: with `COMPOSE_FILE=compose.yaml:compose.prod.yaml` and `ENGLEKS_TAG=<sha>`, `api`/`bot`/`litellm` run `ghcr.io/topol-3000/engleks-<app>:${ENGLEKS_TAG:?set ENGLEKS_TAG}` with `build: !reset null`. `migrate` runs the api image with `pull_policy: missing`.

- [ ] **Step 1: Write the check first.** Add the CI job `compose-prod` (`ubuntu-24.04`, checkout with `persist-credentials: false`) that runs:

```bash
export ENGLEKS_TAG=0000000000000000000000000000000000000000
PROD="docker compose --env-file .env.example -f compose.yaml -f compose.prod.yaml"
$PROD config --quiet
! $PROD config --images | grep -v '^ghcr.io/topol-3000/engleks-\|^postgres:\|^ollama/'
```

The `.env.example` values are the spec's "dummy values", and any variable a later change requires has to be added there anyway.
- [ ] **Step 2: Run it locally to make sure it fails.** Run the Step 1 commands. `--env-file` keeps your real `.env` out of it. Expected: error, `compose.prod.yaml` does not exist.
- [ ] **Step 3: Write `compose.prod.yaml`.** It holds only the four service overrides from spec §5. Start it with a comment saying the server's `.env` selects it through `COMPOSE_FILE`.
- [ ] **Step 4: Verify.** The Step 2 command exits 0. `config --images` prints only `ghcr.io/topol-3000/engleks-*:000…` plus postgres and ollama. `config` shows no `build:` key. Without `ENGLEKS_TAG` it fails with `set ENGLEKS_TAG`.
- [ ] **Step 5: Add to `.env.example`** a comment block at the end. It says the server `.env` (written by `deploy/server/bootstrap-env.sh`) also has `COMPOSE_FILE`, `ENGLEKS_TAG`, `RESTIC_REPOSITORY`, `RESTIC_PASSWORD` and `HEALTHCHECKS_BACKUP_URL`, and that they are unused in dev.
- [ ] **Step 6: Commit.** Message: `feat(deploy): compose.prod.yaml override for GHCR images`.

---

### Task 3: Script test harness, shellcheck, `engleks-deploy`

**Files:**
- Create: `deploy/tests/conftest.py`, `deploy/tests/test_engleks_deploy.py`, `deploy/server/engleks-deploy` (mode 755)
- Modify: `.pre-commit-config.yaml`, `Makefile` (`lint`), `.github/workflows/ci.yml` (`lint` job), `.gitignore` (`.deploy.lock`)

**Interfaces:**
- Produces, for Tasks 4–5: the `server` fixture returns a `Server` with:
  - `root` (ENGLEKS_DIR), `backups` (BACKUP_DIR) and `log`
  - `write_env(text)`, which writes `root/.env` at 0600
  - `env_text()`, `tag() -> str | None` and `calls() -> list[str]` (the stub log lines)
  - `run(script, *args, input=None, **env) -> CompletedProcess[str]`, which runs `bash deploy/server/<script>` with `cwd=root`

  The base env has `PATH=<stub bin>:/usr/bin:/bin`, `HOME=tmp`, `ENGLEKS_DIR`, `BACKUP_DIR`, `STUB_LOG` and `ENGLEKS_LOCK_TIMEOUT=1`. Constants: `PREV = "a" * 40`, `NEW = "b" * 40`, `HC = "https://hc-ping.com/test-uuid"`. The default `.env` has `COMPOSE_FILE=compose.yaml:compose.prod.yaml`, `ENGLEKS_TAG=PREV`, `API_DB_PASSWORD=secret`, `RESTIC_REPOSITORY=sftp:storagebox:/engleks`, `RESTIC_PASSWORD=s3cret-restic` and `HEALTHCHECKS_BACKUP_URL=HC`.
- Produces: `engleks-deploy` reads the SHA from `$SSH_ORIGINAL_COMMAND`, or from `$1` when that is unset (for running by hand). It exits 2 on a malformed SHA, 1 on a failed deploy or a lock timeout, and 0 on success.

The stub is one bash script installed as `git`, `docker`, `restic` and `curl`. Its behaviour is the contract for every script test, so here it is in full:

```bash
#!/usr/bin/env bash
name=$(basename "$0"); line="$name $*"
tag=$(sed -n 's/^ENGLEKS_TAG=//p' "$ENGLEKS_DIR/.env" 2>/dev/null | tail -n1)
extra=""; if [[ $name == restic && -n ${STUB_LS-} ]]; then extra=" [$(ls "$STUB_LS" | sort | xargs)]"; fi
echo "$line @$tag$extra" >> "$STUB_LOG"
if [[ -n ${STUB_FAIL-} && $line =~ $STUB_FAIL && ( -z ${STUB_FAIL_TAG-} || $tag == "${STUB_FAIL_TAG}" ) ]]; then exit 1; fi
case $line in
  "docker image ls"*) [[ -n ${STUB_IMAGES-} ]] && printf '%s\n' $STUB_IMAGES ;;
  *pg_dump*) echo dump ;;
  *generate_key.py*) echo "API_LLM_API_KEY=sk-generated" ;;
  "git rev-parse HEAD") echo "${STUB_HEAD-}" ;;
esac
exit 0
```

- [ ] **Step 1: Add shellcheck.** Add the `shellcheck-py/shellcheck-py` pre-commit hook, pinned to its latest tag (look it up with `gh api repos/shellcheck-py/shellcheck-py/tags --jq '.[0].name'`), with `files: ^deploy/server/` so files without a `.sh` suffix are matched by shebang. Add `uv run pre-commit run shellcheck --all-files` to `make lint` and as a step in CI's `lint` job.
- [ ] **Step 2: Check the `--wait` assumption (spec §8 step 4).** With the dev stack (`make up`), run `docker compose up -d --wait --wait-timeout 180; echo $?`.
  - If it prints `0` while `migrate` shows `Exited (0)`, `deploy()` uses the plain form.
  - If not, `deploy()` uses `docker compose up -d --wait --wait-timeout 180 postgres litellm api bot`.

  Record which one in a comment in the script.
- [ ] **Step 3: Write the failing tests** in `deploy/tests/test_engleks_deploy.py` (run with `SSH_ORIGINAL_COMMAND=<sha>` unless noted):
  - `test_rejects_malformed_sha`: parametrize over `""`, `"abc"`, `"B" * 40`, `NEW + "; rm -rf /"` and `NEW + "0"`. Expect rc 2, `calls() == []` and `.env` unchanged.
  - `test_deploys_sha`: rc 0. The calls start with, in order: `git fetch origin @PREV`, `git checkout --detach NEW @PREV`, `docker compose pull @NEW` and `docker compose up -d --wait --wait-timeout 180… @NEW`. Then `tag() == NEW`.
  - `test_sha_from_argument`: no `SSH_ORIGINAL_COMMAND`, `run("engleks-deploy", NEW)`. Expect rc 0 and `tag() == NEW`.
  - `test_deploy_keeps_rest_of_env_and_mode`: after a deploy, every line except `ENGLEKS_TAG` is unchanged, the line order is the same, and the `.env` mode is `0o600`.
  - `test_failed_up_rolls_back`: `STUB_FAIL='^docker compose up'`, `STUB_FAIL_TAG=NEW`. Expect rc 1, `git checkout --detach PREV` and a `compose up … @PREV` after the failed one, and `tag() == PREV`.
  - `test_failed_pull_skips_up`: `STUB_FAIL='^docker compose pull'`, `STUB_FAIL_TAG=NEW`. Expect rc 1, no call matching `compose up.* @NEW`, and `tag() == PREV`.
  - `test_unknown_sha_rolls_back`: `STUB_FAIL='^git checkout --detach b'`. Expect rc 1, no `docker compose pull @NEW`, and `tag() == PREV`.
  - `test_failure_without_previous_exits_nonzero`: `.env` has no `ENGLEKS_TAG` line, and `up` fails. Expect rc 1, exactly one `git checkout`, and the `ENGLEKS_TAG=NEW` line appended.
  - `test_redeploy_same_sha_failure_no_rollback`: `.env` tag is already `NEW`, and `up` fails. Expect rc 1 and exactly one `git checkout`.
  - `test_removes_old_images_on_success`: `STUB_IMAGES` lists `ghcr.io/topol-3000/engleks-api:` with each of `"c"*40`, `PREV` and `NEW`. Expect exactly one `docker image rm` call, for the `"c"*40` one.
  - `test_failed_image_cleanup_does_not_fail_deploy`: same images, `STUB_FAIL='^docker image rm'`. Expect rc 0 and `tag() == NEW`.
  - `test_no_cleanup_after_failed_deploy`: `up` fails for `NEW`. Expect no `docker image rm`.
  - `test_waits_for_lock_then_gives_up`: hold `root/.deploy.lock` with `subprocess.Popen(["flock", lock, "sleep", "5"])`. Expect rc 1, `calls() == []`, and kill the holder afterwards.
- [ ] **Step 4: Run them to make sure they fail.** Run `uv run pytest deploy/tests/test_engleks_deploy.py -q`. Expected: FAIL (the script does not exist).
- [ ] **Step 5: Implement `deploy/server/engleks-deploy`** as in spec §8, with the seams from Global Constraints. Write `deploy()` so every command ends in `|| return 1`. Write `set_tag SHA` (replace the line, or append it when absent) so the mode stays 0600. Lock with `exec 9>"$ENGLEKS_DIR/.deploy.lock"; flock -w "$timeout" 9`. List images with `docker image ls --format '{{.Repository}}:{{.Tag}}' --filter 'reference=ghcr.io/topol-3000/engleks-*'`. Make a failed `docker image rm` a warning. Print `==> <step>` lines so the Actions log reads well.
- [ ] **Step 6: Verify.** Run `uv run pytest deploy/tests -q && make lint`. Expected: all pass, and shellcheck is clean.
- [ ] **Step 7: Commit.** Message: `feat(deploy): engleks-deploy forced command with rollback`.

---

### Task 4: `engleks-backup` and its systemd units

**Files:**
- Create: `deploy/server/engleks-backup` (mode 755), `deploy/server/engleks-backup.service`, `deploy/server/engleks-backup.timer`
- Test: `deploy/tests/test_engleks_backup.py`

**Interfaces:**
- Consumes: the `server` fixture, `HC`, `STUB_LS`, `STUB_FAIL` (Task 3).
- Produces: `engleks-backup` (no arguments) exits 0 on success and non-zero on any failure. It writes `engleks.dump` (the database `$POSTGRES_DB`) and `litellm.dump` to `BACKUP_DIR`. The units are `engleks-backup.service` (`Type=oneshot`, `User=deploy`, `After=docker.service network-online.target`) and `engleks-backup.timer` (`OnCalendar=*-*-* 03:00:00 UTC`, `Persistent=true`, `WantedBy=timers.target`).

- [ ] **Step 1: Write the failing tests** (all with `STUB_LS=<backups>`):
  - `test_success`: rc 0. The calls, in order:
    1. `curl … HC/start`
    2. two `docker compose exec -T postgres …` calls containing `pg_dump` and `--format=custom --compress=0`
    3. `restic backup <backups> <root>/.env --tag nightly … [engleks.dump litellm.dump]`
    4. `restic forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune`
    5. `curl … HC/0` (healthchecks' exit-status endpoint: `0` means success)

    After the run the `backups` directory is empty.
  - `test_restic_failure_pings_fail_and_cleans_up`: `STUB_FAIL='^restic backup'`. Expect rc ≠ 0, a last `curl` to `HC/<n>` with `n ≠ 0`, `backups` empty, and no `restic forget`.
  - `test_dump_failure_pings_fail`: `STUB_FAIL='pg_dump'`. Expect rc ≠ 0, no `restic backup`, a fail ping, and `backups` empty.
  - `test_ping_failure_does_not_fail_backup`: `STUB_FAIL='^curl'`. Expect rc 0 and `restic backup` was called.
  - `test_waits_for_deploy_lock`: hold the lock as in Task 3. Expect rc ≠ 0, no `docker` call, and a fail ping.
  - `test_missing_config_exits_nonzero`: parametrize over removing `RESTIC_PASSWORD`, `RESTIC_REPOSITORY` or `HEALTHCHECKS_BACKUP_URL` from `.env`. Expect rc 1 and no `restic`/`docker` call.
  - `test_password_not_in_argv`: no logged call contains `s3cret-restic`, the `RESTIC_PASSWORD` value.
- [ ] **Step 2: Run them to make sure they fail.** Run `uv run pytest deploy/tests/test_engleks_backup.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement it** following spec §9.
  - Run in `$ENGLEKS_DIR`, so Compose reads `.env`. Dump with `docker compose exec -T postgres sh -c '…'`. The command is single-quoted so the container expands the variables: `pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --compress=0` for the app database, and the same with `-d litellm` for the proxy's.
  - Export `RESTIC_REPOSITORY` and `RESTIC_PASSWORD` for restic.
  - Ping with `curl -fsS -m 10 --retry 3 -o /dev/null <url> || true`.
  - One `EXIT` trap empties `BACKUP_DIR` and pings `HC/$?`. It is set only after the config check, so a missing URL cannot be pinged.
  - Take the lock with `flock -w "${ENGLEKS_LOCK_TIMEOUT:-600}"` on the same file as `engleks-deploy`.
- [ ] **Step 4: Write the two units.** Check them with `systemd-analyze verify deploy/server/engleks-backup.*` if it is available. Missing-binary warnings from the dev machine are fine.
- [ ] **Step 5: Verify.** Run `uv run pytest deploy/tests -q && make lint`. Expected: all pass.
- [ ] **Step 6: Commit.** Message: `feat(deploy): nightly restic backup with healthchecks ping`.

---

### Task 5: `bootstrap-env.sh` and `setup.sh`

**Files:**
- Create: `deploy/server/bootstrap-env.sh`, `deploy/server/setup.sh` (both mode 755)
- Test: `deploy/tests/test_bootstrap_env.py`

**Interfaces:**
- Consumes: the `server` fixture and stubs (`STUB_HEAD`, the `generate_key.py` output); Task 1's flags; Task 4's units.
- Produces: `bootstrap-env.sh` (run as `deploy`; works in `$ENGLEKS_DIR`). It reads, in this order and one line each from stdin:
  1. `BOT_TELEGRAM_TOKEN`
  2. `ANTHROPIC_API_KEY`
  3. `HEALTHCHECKS_BACKUP_URL`
  4. `RESTIC_REPOSITORY` [`sftp:storagebox:/engleks`]
  5. `LLM_UPSTREAM_MODEL` [`anthropic/claude-haiku-5-5`]
  6. `LLM_UPSTREAM_API_BASE` [`https://api.anthropic.com`]
  7. `API_LLM_MODEL` [`claude-haiku-5-5`]
  8. `API_BOT_USERNAME` [empty]
  9. then `OPENAI_API_KEY`, only when `LLM_UPSTREAM_MODEL` starts with `openai/`; otherwise it is set to `unused`.

  An empty answer takes the default in brackets. An empty answer to a secret prompt (1–3) exits 1 before anything is written. The defaults put the API on the `claude-haiku-5-5` alias, the one spec §3 prices; the upstream values only have to satisfy Compose.
- Produces: `setup.sh <owner> <ci-public-key> <storagebox-host>`, run as root.

- [ ] **Step 1: Write the failing tests** in `deploy/tests/test_bootstrap_env.py`. Start each one with no `.env`, `STUB_HEAD=NEW`, and stdin `"123:tg\nsk-ant-x\nhttps://hc/x\n\n\n\n\n\n"`.
  - `test_refuses_when_env_exists`: Expect rc 1, `.env` untouched, and `calls() == []`.
  - `test_writes_env`: Expect rc 0 and `.env` mode 0600, with each key on exactly one line. The set of keys equals `API_DB_PASSWORD`, `LITELLM_DB_PASSWORD`, `BOT_SERVICE_TOKEN`, `LITELLM_MASTER_KEY`, `LITELLM_UI_USERNAME`, `LITELLM_UI_PASSWORD`, `RESTIC_PASSWORD`, `BOT_TELEGRAM_TOKEN`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `HEALTHCHECKS_BACKUP_URL`, `RESTIC_REPOSITORY`, `LLM_UPSTREAM_MODEL`, `LLM_UPSTREAM_API_BASE`, `API_LLM_MODEL`, `API_BOT_USERNAME`, `API_RATE_LIMIT_PER_MINUTE`, `API_DAILY_TOKEN_BUDGET`, `COMPOSE_FILE`, `ENGLEKS_TAG` and `API_LLM_API_KEY`. These lines are present: `ENGLEKS_TAG=NEW`, `COMPOSE_FILE=compose.yaml:compose.prod.yaml`, `API_LLM_MODEL=claude-haiku-5-5`, `RESTIC_REPOSITORY=sftp:storagebox:/engleks`, `LITELLM_UI_USERNAME=admin`, `OPENAI_API_KEY=unused`, `API_RATE_LIMIT_PER_MINUTE=10`, `API_DAILY_TOKEN_BUDGET=200000` and `API_LLM_API_KEY=sk-generated` (the `pending` line was replaced).
  - `test_generated_values`: `LITELLM_DB_PASSWORD` matches `^[A-Za-z0-9]{32,}$`, `LITELLM_MASTER_KEY` starts with `sk-`, and the generated values are all different.
  - `test_creates_capped_key_and_restic_repo`: the calls include `docker compose up -d --wait postgres litellm`, then one whose text contains `generate_key.py --max-budget 10 --budget-duration 30d`, then `restic init`.
  - `test_secrets_not_echoed`: stdout + stderr contain neither `123:tg` nor `sk-ant-x` nor the generated `RESTIC_PASSWORD`. The output does tell the owner to save `.env` in a password manager.
  - `test_empty_secret_aborts`: stdin `"\n…"`. Expect rc 1 and no `.env`.
  - `test_openai_key_prompted_for_openai_upstream`: answer `openai/gpt-x` to prompt 5 and add a ninth line `sk-openai`. Expect `OPENAI_API_KEY=sk-openai`.
- [ ] **Step 2: Run them to make sure they fail.** Run `uv run pytest deploy/tests/test_bootstrap_env.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement `bootstrap-env.sh`** following spec §6.
  - Generate values with `openssl rand -hex 32` (letters and digits, so they are safe in a URL).
  - Write `.env` under `umask 077` with `API_LLM_API_KEY=pending`, because Compose needs every secret's variable set before `up`. Then replace that line with the generated key's.
  - Pass the restic password only through the environment.
  - If any step after `.env` exists fails, print the recovery steps (`docker compose down -v && rm .env`, then run again) and exit 1.
- [ ] **Step 4: Verify.** Run `uv run pytest deploy/tests -q`. Expected: all pass.
- [ ] **Step 5: Implement `setup.sh`** following spec §10 and §9. Every step checks before it acts, so the script can be re-run. Choices the spec leaves open:
  - Check that it was given 3 arguments and runs as root. `apt-get install` also installs `git make curl openssl restic unattended-upgrades`.
  - Docker comes from `download.docker.com` (keyring in `/etc/apt/keyrings/docker.asc`, `arch=$(dpkg --print-architecture)`, the `noble` suite). After installing, fail if `docker compose version --short` is below 2.24.
  - Write `daemon.json` only when it differs from the wanted content, then restart docker.
  - The owner gets `adduser --disabled-password`, then `passwd <owner>` interactively, only when `passwd -S` shows no password. Sudo then asks for a password, so a leaked SSH key alone does not give root.
  - `deploy` is a normal user with `/bin/bash`, a disabled password and the `docker` group.
  - Write `/etc/ssh/sshd_config.d/10-engleks.conf` and run `sshd -t` before `systemctl reload ssh`. Print a reminder to test the owner's login in a second session before closing this one.
  - Configure unattended-upgrades in `/etc/apt/apt.conf.d/52engleks` with `Automatic-Reboot "true"` and `Automatic-Reboot-Time "04:00"`. Set the timezone with `timedatectl set-timezone UTC`.
  - Clone `https://github.com/topol-3000/engleks.git` as `deploy` only if `/opt/engleks/.git` is missing. Then run `install -m 0755 -o root -g root` for both scripts from the checkout, and copy the units to `/etc/systemd/system` and run `systemctl daemon-reload && systemctl enable --now engleks-backup.timer`. The script does this on every run, which is how a script change reaches the server.
  - Write `~deploy/.ssh/authorized_keys` (0600) as exactly `command="/usr/local/bin/engleks-deploy",restrict <ci-public-key>`.
  - Create the Storage Box key only when it is missing. Write `~deploy/.ssh/config` with `Host storagebox`, `HostName <host>`, `User <first label of host>`, `Port 23`, `IdentityFile ~/.ssh/storagebox` and `IdentitiesOnly yes`. Get the host key with `ssh-keyscan -p 23` into `known_hosts` only when it is not already there, and print its fingerprint for comparison with Hetzner's published ones.
  - Finish by printing the Storage Box public key and the next steps (spec §10, steps 4–5).
- [ ] **Step 6: Verify.** Run `make lint` (shellcheck clean) and `bash -n deploy/server/setup.sh`. The full run happens on the server in Task 9.
- [ ] **Step 7: Commit.** Message: `feat(deploy): server setup and one-time secrets bootstrap`.

---

### Task 6: CI builds arm64 and pushes to GHCR

**Files:**
- Modify: `.github/workflows/ci.yml` (`images`, `trivy-litellm`). Maybe also `deploy/litellm/Dockerfile`, but only to swap pins (see Step 3).

**Interfaces:**
- Produces: on `push` to `main`, `ghcr.io/topol-3000/engleks-{api,bot,litellm}:${{ github.sha }}`, built for `linux/arm64` and pushed only after its Trivy gate passed. Task 7 relies on CI's workflow name staying `CI`.

- [ ] **Step 1: Edit `ci.yml`.**
  - `images` and `trivy-litellm` get `runs-on: ubuntu-24.04-arm` and job-level `permissions: { contents: read, packages: write }`.
  - Each `docker build` gets `--label org.opencontainers.image.source=https://github.com/${{ github.repository }}`, which links the package to the repo.
  - After the gate, add a step `Push to GHCR` with `if: github.event_name == 'push' && github.ref == 'refs/heads/main'`. It runs `docker login ghcr.io -u "$GITHUB_ACTOR" --password-stdin` fed from `env: GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}`, then `docker tag` the `:ci` image to `ghcr.io/topol-3000/engleks-<app>:$GITHUB_SHA` and `docker push`.
  - The workflow-level permissions stay `contents: read`.
- [ ] **Step 2: Lint the workflow.** Run `docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:latest`. Expected: no errors for `ci.yml`.
- [ ] **Step 3: Check on ARM.** With the owner's go-ahead, push the branch and open or update the PR. Expected: `images (api)`, `images (bot)` and `trivy-litellm` run on ARM and are green, with no push step executed.
  - If a pinned package in `deploy/litellm/Dockerfile` is missing for arm64, replace it with the nearest version that has the fix on both architectures. Then check that `make scan` is still clean on the amd64 dev machine.
  - Note any pin you change in the commit body.
- [ ] **Step 4: Commit.** Message: `ci: build arm64 images and push them to GHCR from main`.

---

### Task 7: `deploy.yml`

**Files:**
- Create: `.github/workflows/deploy.yml`

**Interfaces:**
- Consumes: the CI workflow `CI` (Task 6) and the server forced command (Task 3).
- Produces: the workflow `Deploy`, environment `production`, with secrets `DEPLOY_HOST`, `DEPLOY_SSH_KEY` and `DEPLOY_KNOWN_HOSTS`.

- [ ] **Step 1: Write `deploy.yml`** following spec §7.
  - Triggers: `workflow_run: { workflows: [CI], types: [completed], branches: [main] }`, and `workflow_dispatch` with a required string input `sha`.
  - Settings: `permissions: {}` and `concurrency: { group: production, cancel-in-progress: false }`.
  - One job with `environment: production`, `timeout-minutes: 20`, and `if: github.event_name == 'workflow_dispatch' || (github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.event == 'push')`.
  - One step. Every value comes in through `env:`, never through `${{ }}` inside `run:`, with `SHA: ${{ github.event.workflow_run.head_sha || inputs.sha }}`. The step:
    1. fails unless `[[ $SHA =~ ^[0-9a-f]{40}$ ]]`
    2. writes the key (0600) and `known_hosts` into `~/.ssh`
    3. runs `ssh -i <key> -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=<file> -o ServerAliveInterval=30 "deploy@$DEPLOY_HOST" "$SHA"`

  Add a header comment explaining why this is a separate workflow (CI cancels in-progress runs). Note there that GitHub keeps only the newest pending deploy, so a queued intermediate SHA may be skipped; that is fine.
- [ ] **Step 2: Lint.** Run the actionlint command from Task 6. Expected: no errors.
- [ ] **Step 3: Commit.** Message: `ci: deploy green main commits over SSH`.

---

### Task 8: Docs

**Files:**
- Create: `docs/deployment.md`
- Modify: `AGENTS.md`, `docs/architecture.md` (§9), `README.md`

- [ ] **Step 1: Write `docs/deployment.md`** covering every item in spec §12, in the order of spec §10 "First-time order", then day-to-day operations, then recovery. Points the spec does not spell out:
  - Generate CI's key on the owner's machine: `ssh-keygen -t ed25519 -N "" -f engleks-ci`. The public key goes to `setup.sh` and the private key to `DEPLOY_SSH_KEY`.
  - `DEPLOY_KNOWN_HOSTS` is `ssh-keyscan -t ed25519 <ip>`, checked against `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the server.
  - Set the `production` environment's deployment branches to `main` only.
  - Install the Storage Box key with `sudo -iu deploy sh -c 'ssh -p 23 <user>@<host> install-ssh-key < ~/.ssh/storagebox.pub'`.
  - If `bootstrap-env.sh` fails halfway: `docker compose down -v`, `rm .env`, then run it again.
  - After a merge that changes `deploy/server/engleks-*` or the units, re-run `setup.sh` (as root, same arguments). Deploys do not update installed copies.
  - Deploy by hand on the server: `sudo -iu deploy engleks-deploy <sha>`.
  - Changing a secret: adapt the table and the `ALTER ROLE` commands from the closed PR #6 (`git show origin/docs/secret-rotation:README.md`) to `sudo -iu deploy`, `cd /opt/engleks`. Add rows for `RESTIC_PASSWORD` (`restic key passwd`, then update `.env` and the password manager) and `HEALTHCHECKS_BACKUP_URL` (just `.env`).
  - Changing the cap: `/key/update` with `max_budget`, or the LiteLLM UI over `ssh -L 4000:localhost:4000`.
  - Restore: follow spec §9, plus the rehearsal from spec §11 item 5 and the new-server path.
- [ ] **Step 2: Update `AGENTS.md`.**
  - Add **Deployment** to the sub-project list.
  - Add rows to the commands table: deploy (automatic on green `main`), rollback (Actions → Deploy → Run workflow with a SHA), and script tests (`uv run pytest deploy/tests`). Add `deploy/tests` to the "Tests without Docker" command.
  - Add a Code rules bullet: every migration must work with the previous release's code (rollback never downgrades the schema).
  - Under Boundaries, count `deploy/server/*` and `deploy.yml` as CI/Docker/Compose.
  - Add a link to `docs/deployment.md`.
- [ ] **Step 3: Update `architecture.md` §9 and the README.** Add one paragraph per spec §12 to architecture.md §9, and a runbook link to the README's link list.
- [ ] **Step 4: Verify.** Run `make lint && make test`. Expected: green. Every command quoted in the runbook names a script, flag or path that exists in Tasks 1–7.
- [ ] **Step 5: Commit.** Message: `docs: deployment runbook`.

---

### Task 9: Rollout and acceptance (owner, after merge)

The owner does this by following `docs/deployment.md`. It is not an agent task. It is listed here so that "done" matches spec §1.

- [ ] Do spec §10 "First-time order", steps 1–7.
- [ ] Do spec §11 acceptance checks 1–6, and note the results in the PR or an issue.
