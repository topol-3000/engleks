# Secrets Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Production config lives in `deploy/prod.env`, secrets live in Infisical, and every deploy builds `.env` itself, rotating database passwords and the API's LiteLLM key from the values in Infisical.

**Architecture:** A new server script, `engleks-render-env`, logs in to Infisical with a read-only machine identity (`curl` + `jq`) and merges `deploy/prod.env`, the secrets named in `deploy/secrets.txt`, `ENGLEKS_TAG` and `ENGLEKS_SECRETS_HASH` into `.env.next`. `engleks-deploy` renders, keeps the running file as `.env.previous`, checks restic, swaps the files, runs `ALTER ROLE` for changed DB passwords, then pulls, starts and ensures the LiteLLM key with `generate_key.py --ensure-stdin`. A rollback deploys `.env.previous` offline. A `engleks.secrets` label recreates the four app services when a secret changes.

**Tech Stack:** bash, `curl`, `jq`, `sha256sum`, `flock`; Infisical REST API (Universal Auth); Docker Compose; LiteLLM key API (stdlib `urllib`); pytest with the logging stub in `deploy/tests/conftest.py`; PyYAML (dev only) for the consistency test.

**Spec:** [docs/superpowers/specs/2026-11-06-secrets-management-design.md](../specs/2026-11-06-secrets-management-design.md)

## Global Constraints

- The spec is the owner's approval for the changes to `compose.yaml`, `.github/workflows/deploy.yml`, `deploy/server/*` and the shellcheck file pattern listed here (AGENTS.md "Ask before"). Nothing else in CI, Dockerfiles or Compose changes.
- No secret value in any process's argv, in script output or in `.deploy.log`. Values travel through stdin, process substitution (`--data @<(...)`, `-H @<(...)`) or a child's environment. Error messages name the key, never the value.
- `.env` and every copy of it (`.env.next`, `.env.previous`, temp files) are 0600, created under `umask 077`. `.env` is never sourced. Scripts read it with an `env_get`-style `sed` helper (last `^KEY=` line, verbatim).
- Value rule (today's `check_value`): no whitespace, `"`, `'`, `` ` ``, `$`, `#` or `\`. It applies to every value written to `.env`.
- Paths: credentials `~deploy/.config/engleks/infisical` (0600, dir 0700, owned by `deploy`); scripts `/usr/local/bin/engleks-render-env` and `/usr/local/bin/engleks-deploy`; checkout `/opt/engleks`. Test seams from env only: `ENGLEKS_DIR`, `BACKUP_DIR`, `ENGLEKS_LOCK_TIMEOUT`, `ENGLEKS_INFISICAL_CREDENTIALS`.
- Infisical: EU Cloud, `INFISICAL_API_URL` default `https://eu.infisical.com`, project `engleks`, environment `prod`, identity `engleks-prod-server`.
- LiteLLM key: `--max-budget 10 --budget-duration 30d`; models are `MODELS` in `generate_key.py`.
- New scripts: `#!/usr/bin/env bash`, `set -euo pipefail`, shellcheck-clean (`make lint`).
- `jq` must be installed wherever `deploy/tests` run. GitHub's `ubuntu-24.04` runner has it. This dev machine does not: `sudo apt-get install -y jq` before Task 2.
- Commits: conventional style as in `git log`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

### Where this plan settles points the spec leaves open or gets wrong

1. **Deploy step order** (spec §6 steps 3–5). The order is: render, copy `.env` to `.env.previous`, check restic, move `.env.next` over `.env`, then `ALTER ROLE`. In the spec's order, a restic failure after `ALTER ROLE` leaves Postgres on the new password, and the rollback cannot undo it because `.env` and `.env.previous` are still identical. With this order a failure before the move changes nothing, and a failure after it is undone by the rollback, which compares `.env` with `.env.previous`.
2. **The rollback skips the restic check.** `.env.previous`'s restic values were already running, and the Storage Box may be the reason the deploy failed. The online check also runs when `RESTIC_REPOSITORY` changed, not only `RESTIC_PASSWORD`, so a moved repository gets initialised.
3. **A failed redeploy of the running SHA now rolls back.** Today it does not. With secrets in play, a same-SHA redeploy can rotate a password, so it restores `.env.previous` like any other failure.
4. **`--ensure-stdin` never puts the key in a URL** (spec §11 wins over §7's `GET /key/info?key=`, which would write the key to the proxy's access log). It calls `POST /key/update` with `{key, models}`. If that returns 404, it calls `POST /key/generate`. Task 1 checks the missing-key status against the real proxy.
5. **`ALTER ROLE` input:** stdin to `psql -v ON_ERROR_STOP=1` is `\set pw '<value>'` followed by `ALTER ROLE <role> PASSWORD :'pw';`. The value rule forbids quotes and backslashes, so single-quoting is safe.
6. **Render also rejects:** an empty secret value, and `ENGLEKS_TAG` or `ENGLEKS_SECRETS_HASH` in either source file.
7. **`ENGLEKS_SECRETS_HASH`** is the lowercase hex SHA-256 of the exact bytes of the secret lines as written to `.env`: `KEY=value\n` for each name in `secrets.txt`, in order.

## Review Focus

- **A secret-only redeploy that fails.** Someone rotates `API_DB_PASSWORD`, clicks **Run workflow** with no input, and `up` fails. They expect the old `.env` and the old role password back, even though the SHA did not change. Pinned in Task 3 (`test_failed_same_sha_redeploy_restores_env_and_password`).
- **A restic failure while a DB password also changed.** No `ALTER ROLE` may run before the restic check passes. Pinned in Task 3 (`test_restic_failure_alters_no_role`).
- **A value pasted into Infisical with a trailing newline or space** (common when copying keys). Render fails, names the key and never prints the value. Pinned in Task 2 (`test_rejects_value_compose_would_misread`, with `"tok\n"` and `"tok "` cases).
- **A secret created in Infisical with an empty value.** Render fails, naming the key, instead of starting the bot with an empty token. Pinned in Task 2 (`test_empty_secret_fails`).
- **A manual rollback, via Run workflow, to a commit from before this change** (no `deploy/prod.env`). Render fails with a message that names `deploy/prod.env`, and the server stays on the running release. Pinned in Task 2 (`test_missing_prod_env_fails_clearly`) and Task 3 (`test_infisical_failure_leaves_server_untouched`).

---

### Task 1: `generate_key.py --ensure-stdin` and `--delete-stdin`

**Files:**
- Modify: `deploy/litellm/generate_key.py`
- Test: `deploy/tests/test_generate_key.py`

**Interfaces:**
- Produces, run in the litellm container:
  - `python /deploy/generate_key.py --ensure-stdin --max-budget 10 --budget-duration 30d` reads the key on stdin and prints `created` or `updated`.
  - `python /deploy/generate_key.py --delete-stdin` reads a key on stdin and prints `deleted` or `missing`.
  - Both exit non-zero on any other HTTP status, with a message that does not contain the key.
- In the module:
  - `ensure_key(post: Callable[[str, dict[str, object]], int], key: str, max_budget: float | None, budget_duration: str | None) -> str`.
  - `delete_key(post: Callable[[str, dict[str, object]], int], key: str) -> str`.
  - `post(path, payload)` POSTs JSON to `http://localhost:4000<path>` with the master key and returns the HTTP status, for 4xx too. `main()` builds it with `urllib`. Use no PEP 695 `type` statement: the container's Python may be older than 3.12.
  - The existing no-flag mode (`make llm-key`) is unchanged.

- [ ] **Step 1: Write the failing tests.** Use a fake `post` that records `(path, payload)` and returns statuses from a dict:

```python
def fake(statuses: dict[str, int]):
    calls: list[tuple[str, dict[str, object]]] = []
    def post(path: str, payload: dict[str, object]) -> int:
        calls.append((path, payload))
        return statuses[path]
    return post, calls

def test_ensure_updates_existing_key_models_only():
    post, calls = fake({"/key/update": 200})
    assert gk.ensure_key(post, "sk-x", 10.0, "30d") == "updated"
    assert calls == [("/key/update", {"key": "sk-x", "models": MODELS})]

def test_ensure_creates_missing_key_with_value_and_budget():
    post, calls = fake({"/key/update": 404, "/key/generate": 200})
    assert gk.ensure_key(post, "sk-x", 10.0, "30d") == "created"
    assert calls[1] == ("/key/generate", {"key": "sk-x", "models": MODELS,
        "metadata": {"service": "engleks-api"}, "max_budget": 10.0, "budget_duration": "30d"})

@pytest.mark.parametrize("statuses", [{"/key/update": 500}, {"/key/update": 404, "/key/generate": 400}])
def test_ensure_fails_on_other_status_without_printing_key(statuses, capsys): ...
    # pytest.raises(SystemExit); "sk-x" not in str(exc) nor capsys output

@pytest.mark.parametrize(("status", "result"), [(200, "deleted"), (404, "missing")])
def test_delete(status, result): ...  # calls == [("/key/delete", {"keys": ["sk-x"]})]

def test_delete_fails_on_other_status(): ...

def test_key_never_in_path():  # over every call recorded by the tests above: "sk-x" not in path

def test_ensure_and_delete_are_exclusive():  # parse_args(["--ensure-stdin", "--delete-stdin"]) exits 2
```

- [ ] **Step 2: Run them to make sure they fail.** Run `uv run pytest deploy/tests/test_generate_key.py -q`. Expected: FAIL (`ensure_key` not defined).
- [ ] **Step 3: Implement.** Add a mutually exclusive `--ensure-stdin` / `--delete-stdin` group. Keep the budget pairing rule. Read the key from `sys.stdin.read().strip()`; an empty key exits with an error. Keep the set of "missing key" statuses in one constant, `MISSING = {404}`, used by both functions. Update the docstring.
- [ ] **Step 4: Check against the real proxy.** With `make up` running in dev:
  1. `printf sk-plan-test | docker compose exec -T litellm python /deploy/generate_key.py --ensure-stdin` prints `created`. Run again: it prints `updated`.
  2. `--delete-stdin` prints `deleted`, then `missing`.
  3. If the proxy answers a missing key with a status other than 404, put that status in `MISSING`, change the tests to match and say so in the commit message.
  4. Change `LITELLM_MASTER_KEY` in your dev `.env` and `docker compose up -d --force-recreate litellm`. Note whether the `sk-plan-test` key (re-created) still answers `GET /key/info` called with its own bearer. Task 6 records the result in the runbook. Restore your `.env` afterwards.
- [ ] **Step 5: Verify.** Run `make lint && uv run pytest deploy/tests -q`. Expected: clean, all pass.
- [ ] **Step 6: Commit.** Message: `feat(litellm): ensure and delete a key with a given value`.

---

### Task 2: `engleks-render-env`, `deploy/prod.env`, `deploy/secrets.txt`

**Files:**
- Create: `deploy/server/engleks-render-env` (mode 755), `deploy/prod.env`, `deploy/secrets.txt`
- Modify: `deploy/tests/conftest.py`, `pyproject.toml` (`uv add --dev pyyaml`)
- Test: `deploy/tests/test_engleks_render_env.py`, `deploy/tests/test_prod_config.py`

**Interfaces:**
- Produces:
  - `engleks-render-env <sha> <output>`, run in `$ENGLEKS_DIR` with the checkout at `<sha>`. Exit 0: written. Exit 1: any failure, with `<output>` untouched.
  - The script reads `$ENGLEKS_DIR/deploy/prod.env`, `$ENGLEKS_DIR/deploy/secrets.txt` and the credential file `${ENGLEKS_INFISICAL_CREDENTIALS:-$HOME/.config/engleks/infisical}` (`KEY=value` lines, never sourced). `INFISICAL_API_URL` is optional (default `https://eu.infisical.com`). `INFISICAL_PROJECT_ID`, `INFISICAL_ENVIRONMENT`, `INFISICAL_CLIENT_ID` and `INFISICAL_CLIENT_SECRET` are required.
  - Output format, exactly:

    ```
    # Generated by engleks-render-env from deploy/prod.env and Infisical. Do not edit; changes are lost on the next deploy.
    <prod.env lines, comments and blank lines dropped, in file order>
    <NAME=value for each name in secrets.txt, in file order>
    ENGLEKS_TAG=<sha>
    ENGLEKS_SECRETS_HASH=<hash, see "settles" item 7>
    ```

  - Its output names only steps, key names and counts.
- Produces in `conftest.py`, for Tasks 3–5:
  - Constants: `TEST_PROD_ENV: str`, `TEST_SECRETS_TXT: str`, `SECRET_NAMES: list[str]` and `DEFAULT_SECRETS: dict[str, str]`. Their values are below.
  - `rendered_env(tag: str, secrets: dict[str, str] = DEFAULT_SECRETS) -> str`: the exact text the script writes. It computes the hash with `hashlib`.
  - `Server.set_secrets(secrets: dict[str, str]) -> None`: the Infisical response.
  - `Server.inputs() -> str`: the input log, described under the stub changes below.
  - Stub env `STUB_FAIL_CODE` (default 1).
  - `server.run` passes `stdin=subprocess.DEVNULL` when `input` is None.
  - `DEFAULT_ENV` stays as it is until Task 3.

- [ ] **Step 1: Extend the harness.**
  1. **Fake checkout.** The fixture writes `root/deploy/prod.env` = `TEST_PROD_ENV` and `root/deploy/secrets.txt` = `TEST_SECRETS_TXT`.

     ```python
     TEST_PROD_ENV = "# test config\nCOMPOSE_FILE=compose.yaml:compose.prod.yaml\n\nRESTIC_REPOSITORY=sftp:storagebox:engleks\n"
     SECRET_NAMES = ["API_DB_PASSWORD", "LITELLM_DB_PASSWORD", "API_LLM_API_KEY", "RESTIC_PASSWORD", "HEALTHCHECKS_BACKUP_URL"]
     TEST_SECRETS_TXT = "# test names\n" + "".join(f"{n}\n" for n in SECRET_NAMES)
     DEFAULT_SECRETS = {"API_DB_PASSWORD": "db-pw-1", "LITELLM_DB_PASSWORD": "llm-db-pw-1",
                        "API_LLM_API_KEY": "sk-api-1", "RESTIC_PASSWORD": "s3cret-restic",
                        "HEALTHCHECKS_BACKUP_URL": HC}
     ```

  2. **Credentials.** The fixture writes a credential file and points `ENGLEKS_INFISICAL_CREDENTIALS` at it. It holds all five fields: project `proj-test`, environment `prod`, client ID `client-id-test`, client secret `client-secret-test`.
  3. **Fake Infisical.** It writes `tmp/infisical/login.json` = `{"accessToken": "tok-test", "expiresIn": 60, "tokenType": "Bearer"}`, and `secrets.json` from `set_secrets(DEFAULT_SECRETS)`. The secrets file has the shape `{"secrets": [{"secretKey": k, "secretValue": v}, ...], "imports": []}`.
  4. **Stub output for curl.** A URL containing `universal-auth/login` prints `login.json`. A URL containing `/secrets` prints `secrets.json`.
  5. **Stub input log.** The stub appends to `$STUB_INPUT_LOG` (`Server.inputs()` reads it):
     - for curl, the contents of every `@file` argument it can read;
     - for `docker compose exec -T ...`, its stdin.

     Each record is `>>> <call line>\n<content>\n`.
  6. **Stub exit code.** A matched `STUB_FAIL` now exits `${STUB_FAIL_CODE:-1}`.
  7. **Bin directory.**
     - `bin/engleks-render-env` is a symlink to `SCRIPTS / "engleks-render-env"`, so Task 3 runs the real script.
     - `bin/jq` is a wrapper that appends `jq <args>` to `$STUB_LOG` and `exec`s the real `jq` (found with `shutil.which` when the fixture is built). This lets the leak test see jq's argv.
- [ ] **Step 2: Write the failing tests** in `test_engleks_render_env.py`. Run `render(server, out)` = `server.run("engleks-render-env", NEW, str(out))`.
  - `test_writes_exact_file`: exit 0; text == `rendered_env(NEW)`; mode 0600.
  - `test_hash_changes_only_with_secrets`: render twice with the same secrets and get the same hash. Change `RESTIC_PASSWORD` and the hash changes. Render with a different sha and the hash stays the same.
  - `test_login_and_fetch_requests`: in `inputs()`, the login body contains `client-secret-test` and the secrets call's header contains `Bearer tok-test`. The secrets call's logged URL contains `proj-test` and `prod`.
  - `test_missing_secret_fails`: drop `API_LLM_API_KEY` from Infisical. Exit 1, `"API_LLM_API_KEY"` in stderr.
  - `test_empty_secret_fails`: same, with value `""`.
  - `test_name_in_both_sources_fails`: append `API_DB_PASSWORD=x` to `prod.env`.
  - `test_reserved_name_fails`: parametrize `ENGLEKS_TAG=x` in `prod.env` and `ENGLEKS_TAG` in `secrets.txt`.
  - `test_rejects_value_compose_would_misread`: parametrize the value of `API_DB_PASSWORD` over `"a$b"`, `"a'b"`, `"a b"`, `"tok\n"`, `"tok "`, `"a#b"`, `"a\\b"`. Exit 1, the key in stderr, the value not in stdout or stderr.
  - `test_rejects_bad_prod_env_value`: `prod.env` line `API_BOT_USERNAME=a b`.
  - `test_unknown_infisical_keys_ignored_with_warning`: add `EXTRA_ONE` and `EXTRA_TWO`. Exit 0, one stderr line names both, and they are not in the output.
  - `test_bad_sha_fails`: parametrize `"abc"`, `"B" * 40`.
  - `test_missing_prod_env_fails_clearly`: delete `prod.env`. Exit 1, `"deploy/prod.env"` in stderr.
  - `test_missing_credential_field_fails`: parametrize over the four required fields, removing each from the file.
  - `test_login_or_fetch_failure`: parametrize `STUB_FAIL` over `"universal-auth"` and `"/secrets"`. Exit 1.
  - `test_failure_leaves_output_untouched`: pre-write `out` with `"old\n"` and run each failure above (a parametrized helper is fine). `out` still reads `"old\n"` and no `out.*` temp file remains.
  - `test_no_secret_in_output_or_argv`: on success, no value of `DEFAULT_SECRETS` (except `HC`) and neither `client-secret-test` nor `tok-test` appears in `server.calls()`, stdout or stderr.
- [ ] **Step 3: Run them to make sure they fail.** Run `uv run pytest deploy/tests/test_engleks_render_env.py -q`. Expected: FAIL (script missing).
- [ ] **Step 4: Implement `deploy/server/engleks-render-env`** following spec §5 and the settles items 6 and 7.
  - **Endpoints.** Check Infisical's API reference for the current Universal Auth login and raw-secrets endpoints and use them. At the time of the spec they were v1 login and v3 `secrets/raw` with `workspaceId`. The stub matches only `universal-auth/login` and `/secrets`.
  - **Requests.** Use `curl -fsS --max-time 30`. Build the login body with `jq -n '{clientId: env.ID, clientSecret: env.SECRET}'`, with the two variables set only in jq's environment. Never use `--arg`.
  - **Parsing.** Parse the secrets with `jq -r '.secrets[] | [.secretKey, .secretValue] | @tsv'`. `@tsv` turns a newline into the two characters `\n`, which the value rule then rejects because of the backslash.
  - **Writing.** Build in memory, validate everything, then `mktemp "$output.XXXXXX"`, write, `mv -f`. A trap removes the temp file on failure.
- [ ] **Step 5: Run them to make sure they pass.** Run `uv run pytest deploy/tests/test_engleks_render_env.py -q`. Expected: PASS.
- [ ] **Step 6: Write the real config files.**
  - `deploy/prod.env`, with a short header comment saying what belongs here and that secrets go to Infisical and `secrets.txt`:
    - `COMPOSE_FILE=compose.yaml:compose.prod.yaml`
    - `LLM_UPSTREAM_MODEL=openai/gpt-6.1-terra`
    - `LLM_UPSTREAM_API_BASE=https://api.openai.com/v1`
    - `API_LLM_MODEL=gpt-6.1-terra`
    - `API_BOT_USERNAME=<ask the owner: it is in the server's .env; do not guess>`
    - `API_RATE_LIMIT_PER_MINUTE=10`
    - `API_DAILY_TOKEN_BUDGET=200000`
    - `LITELLM_UI_USERNAME=admin`
    - `RESTIC_REPOSITORY=sftp:storagebox:engleks`
  - `deploy/secrets.txt`: the eleven names of spec §4, in that order, with a header comment.
- [ ] **Step 7: Write `test_prod_config.py`** (the spec §11 consistency test), using `yaml.safe_load` on `compose.yaml`:
  - `test_every_compose_secret_has_a_source`: every `secrets.*.environment` is in `secrets.txt` or `prod.env`.
  - `test_sources_disjoint_and_unique`: no name in both files, no duplicate in either.
  - `test_real_files_pass_the_value_rule`: every `prod.env` value passes the value rule.

  Run `uv run pytest deploy/tests/test_prod_config.py -q`. Expected: PASS.
- [ ] **Step 8: Verify.** Run `make lint && uv run pytest deploy/tests -q`. Expected: clean, all pass. `DEFAULT_ENV` is unchanged, so the deploy and backup tests still pass.
- [ ] **Step 9: Commit.** Message: `feat(deploy): render .env from deploy/prod.env and Infisical`.

---

### Task 3: `engleks-deploy` renders, reconciles and rolls back `.env`

**Files:**
- Modify: `deploy/server/engleks-deploy`, `deploy/tests/conftest.py` (`DEFAULT_ENV = rendered_env(PREV)`)
- Test: `deploy/tests/test_engleks_deploy.py`

**Interfaces:**
- Consumes:
  - `engleks-render-env <sha> <output>` (Task 2), called by name from `PATH`.
  - `generate_key.py --ensure-stdin` and `--delete-stdin` (Task 1).
  - `rendered_env`, `set_secrets`, `inputs()` and `STUB_FAIL_CODE` (Task 2).
- Produces: the same CLI and exit codes as today, plus two behaviours:
  - A missing `.env` means a first deploy.
  - `.env.next` never survives the script (the EXIT trap removes it), and `.env.previous` survives only a run that was killed.

- [ ] **Step 1: Switch the fixture** to `DEFAULT_ENV = rendered_env(PREV)`. Run `uv run pytest deploy/tests/test_engleks_backup.py -q` and expect PASS: backup reads the same keys.
- [ ] **Step 2: Rewrite the tests.**
  - **Helpers.**
    - `ops(server)`: the `git`, `docker` and `restic` calls, without curl or jq.
    - `UP` as today.
    - `ENSURE = "docker compose exec -T litellm python /deploy/generate_key.py --ensure-stdin --max-budget 10 --budget-duration 30d"`.
    - `ALTER(role)`: the input-log fragment `"\\set pw '<v>'\nALTER ROLE <role> PASSWORD :'pw';"`.
  - **Keep, adjusted:**
    - malformed SHA;
    - SHA from argument;
    - pull failure skips up;
    - unknown SHA rolls back;
    - image cleanup (3 tests);
    - lock;
    - lost client (2 tests);
    - `test_rollback_works_offline`, which now also asserts no `curl` call after the failed pull.
  - **Remove:** `test_deploy_keeps_rest_of_env_and_mode` and `test_redeploy_same_sha_failure_no_rollback`.
  - **New:**
    - `test_deploys_sha`: `ops` begins `git fetch origin`, `git checkout --detach NEW`, `docker compose pull @NEW`, `UP… @NEW`, `ENSURE… @NEW` (image cleanup follows). The login and secrets curls come between the checkout and the pull. No `restic`. `inputs()` has `sk-api-1` under the ensure call.
    - `test_env_is_rendered`: `.env` == `rendered_env(NEW)`, mode 0600, and no `.env.next` or `.env.previous` left.
    - `test_redeploy_of_running_sha`: `.env` = `rendered_env(NEW)`, deploy NEW. Exit 0.
    - `test_first_deploy`: no `.env`, `STUB_FAIL="^restic cat config"`, `STUB_FAIL_CODE="10"`. Exit 0. `restic cat config`, then `restic init`, both before the move (their `@tag` is empty). No `docker compose up -d --wait postgres`, no ALTER. The restic password is not in `calls()`.
    - `test_restic_checked_when_password_or_repository_changes`: parametrize `RESTIC_PASSWORD` in secrets, and `RESTIC_REPOSITORY` in `prod.env`. `restic cat config` is called and no `restic init`.
    - `test_restic_failure_fails_deploy`: change `RESTIC_PASSWORD`, `STUB_FAIL="^restic cat config"`. Exit 1, no `restic init`, the output names the runbook, `.env` == `DEFAULT_ENV`.
    - `test_restic_failure_alters_no_role`: like the previous test, with `API_DB_PASSWORD` changed too. `inputs()` has no `ALTER ROLE`.
    - `test_changed_db_password_alters_role_before_up`: parametrize `("API_DB_PASSWORD", "CURRENT_USER")` and `("LITELLM_DB_PASSWORD", "litellm")`.
      - `docker compose up -d --wait postgres` comes before a `docker compose exec -T postgres` psql call, which comes before `UP`.
      - The input has `ALTER(role)` with the new value.
      - The new value is not in `calls()`.
    - `test_unchanged_passwords_alter_nothing`.
    - `test_failed_deploy_restores_env_and_password`: change `API_DB_PASSWORD`, `STUB_FAIL="^docker compose up -d --wait --wait-timeout"`, `STUB_FAIL_TAG=NEW`.
      - Exit 1, `.env` == `DEFAULT_ENV`.
      - `inputs()` has the ALTER to `db-pw-2`, then the ALTER back to `db-pw-1`.
      - An ensure with `sk-api-1` runs after the rollback's `UP @PREV`.
    - `test_failed_same_sha_redeploy_restores_env_and_password`: `.env` = `rendered_env(NEW)`, secrets with `API_DB_PASSWORD="db-pw-2"`, `up` fails. Exit 1, `.env` == `rendered_env(NEW)` (the old secrets), and the ALTER back is present.
    - `test_infisical_failure_leaves_server_untouched`: `STUB_FAIL="universal-auth"`. Exit 1, `.env` == `DEFAULT_ENV`, no `pull` or `up` with `@NEW`, last checkout `PREV`.
    - `test_key_rotation_deletes_old_key_after_ensure`: secrets with `API_LLM_API_KEY="sk-api-2"`. In `inputs()`, the ensure gets `sk-api-2`, then a `--delete-stdin` call gets `sk-api-1`.
    - `test_failed_old_key_delete_is_a_warning`: the same, plus `STUB_FAIL="delete-stdin"`. Exit 0.
    - `test_failed_ensure_rolls_back`: `STUB_FAIL="ensure-stdin"`, `STUB_FAIL_TAG=NEW`. Exit 1, `.env` == `DEFAULT_ENV`.
    - `test_failure_without_previous`: no `.env`, `up` fails. Exit 1, one checkout, `.env` == `rendered_env(NEW)`.
    - `test_no_secret_in_calls_or_output`: rotate every secret but `HEALTHCHECKS_BACKUP_URL`. Neither the old nor the new values appear in `calls()`, stdout, stderr or `.deploy.log`.
- [ ] **Step 3: Run them to make sure they fail.** Run `uv run pytest deploy/tests/test_engleks_deploy.py -q`. Expected: the new tests FAIL.
- [ ] **Step 4: Implement.** Remove `set_tag` and the missing-`.env` error. Read `previous` with an `env_get` that tolerates a missing file. `deploy TARGET [offline]` does, each step `|| return 1`:
  1. `git fetch origin` (online), then `git checkout --detach TARGET`.
  2. Pick the file to install:
     - online: `engleks-render-env TARGET .env.next`, then, if `.env` exists, copy it to `.env.previous` with mode 0600; the new file is `.env.next`;
     - offline: `.env.previous` if it exists; otherwise none, and skip to step 6.
  3. Online only: if there is no `.env`, or `RESTIC_REPOSITORY` or `RESTIC_PASSWORD` differs between `.env` and the new file, run `restic cat config >/dev/null` with both values in restic's environment only. Exit 10 means `restic init`. Any other failure prints `error: restic cannot open the repository with the new values; see "Changing a secret" in docs/deployment.md` and returns 1.
  4. Read the old `API_DB_PASSWORD`, `LITELLM_DB_PASSWORD` and `API_LLM_API_KEY` from `.env` into local variables (left empty if there is no `.env`). Then `mv -f` the new file over `.env`.
  5. For each DB password that was non-empty before and differs now: `docker compose up -d --wait postgres` once, then pipe the `\set` and `ALTER ROLE` lines from settles item 5 into `docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'`. The role is `CURRENT_USER` for the API and `litellm` for the proxy.
  6. `docker compose pull` (online), then `docker compose up -d --wait --wait-timeout 180`.
  7. `printf '%s' "$key" | ENSURE`, with the key from the new `.env`. If the old key was non-empty and differs, pipe it to `--delete-stdin`. A failed delete prints a warning only.

  The main flow:
  - **Success:** `rm -f .env.previous` and clean up the old images.
  - **Failure:** roll back with `deploy "$previous" offline` whenever `previous` is non-empty, even when it equals `sha` (settles item 3).
  - **On exit:** the EXIT trap also runs `rm -f .env.next`.

  Update the header comment to describe the new steps.
- [ ] **Step 5: Verify.** Run `make lint && uv run pytest deploy/tests -q`. Expected: clean, all pass.
- [ ] **Step 6: Commit.** Message: `feat(deploy): render .env on every deploy and reconcile Postgres, restic and the LiteLLM key`.

---

### Task 4: Provisioning: `generate-secrets.sh`, `setup.sh`, backups, no bootstrap

**Files:**
- Create: `deploy/infisical/generate-secrets.sh` (mode 755)
- Modify: `deploy/server/setup.sh`, `deploy/server/engleks-backup`, `.pre-commit-config.yaml` (shellcheck `files: ^deploy/(server|infisical)/`)
- Delete: `deploy/server/bootstrap-env.sh`, `deploy/tests/test_bootstrap_env.py`
- Test: `deploy/tests/test_generate_secrets.py`, `deploy/tests/test_engleks_backup.py`

**Interfaces:**
- Produces:
  - `generate-secrets.sh` prints exactly seven `KEY=value` lines on stdout and nothing else there. Any guidance goes to stderr.
  - `setup.sh` takes the same arguments as today. It also installs `jq` and `/usr/local/bin/engleks-render-env`, and writes the credential file if it is missing.

- [ ] **Step 1: Write the failing tests.**
  - `test_generate_secrets.py` runs the script with `subprocess.run(["bash", path])`.
    - Parse its stdout lines. The names are exactly `API_DB_PASSWORD`, `LITELLM_DB_PASSWORD`, `BOT_SERVICE_TOKEN`, `LITELLM_MASTER_KEY`, `LITELLM_UI_PASSWORD`, `API_LLM_API_KEY` and `RESTIC_PASSWORD`, each once.
    - The two `sk-` names match `sk-[0-9a-f]{64}` and the rest `[0-9a-f]{64}`. All seven values are distinct.
    - A second run gives different values.
  - In `test_engleks_backup.py`, change `test_success` to expect `restic backup {server.backups} --tag nightly` (no `.env`).
- [ ] **Step 2: Run them to make sure they fail.** Run `uv run pytest deploy/tests/test_generate_secrets.py deploy/tests/test_engleks_backup.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - **`generate-secrets.sh`:** each value from `openssl rand -hex 32`.
  - **`engleks-backup`:** drop `"$ENV_FILE"` from `restic backup`, and update the header comment ("dumps only").
  - **`setup.sh`:**
    - add `jq` to the apt line and install `engleks-render-env` next to `engleks-deploy`;
    - add a step `infisical credentials`: if `$deploy_home/.config/engleks/infisical` is missing, ask for the project ID and client ID with `read -rp` and the client secret with `read -rsp`. Each must be non-empty and match `^[A-Za-z0-9._-]+$`.
    - write the file as `INFISICAL_API_URL=https://eu.infisical.com`, `INFISICAL_PROJECT_ID`, `INFISICAL_ENVIRONMENT=prod`, `INFISICAL_CLIENT_ID`, `INFISICAL_CLIENT_SECRET`. The directory is `install -d -m 0700 -o deploy -g deploy`; the file is mode 0600, owned by `deploy`, written through a temp file with `install`;
    - leave an existing file untouched;
    - replace the final message's step 3 (`bootstrap-env.sh`) with "Create the GitHub environment if it does not exist, then Actions → Deploy → Run workflow".
  - Delete `bootstrap-env.sh` and its test.
- [ ] **Step 4: Verify.** Run `make lint && uv run pytest deploy/tests -q`. Expected: clean (shellcheck now also checks `deploy/infisical/`), all pass. Also run `grep -rn bootstrap-env deploy .github Makefile`. Expected: no output.
- [ ] **Step 5: Commit.** Message: `feat(deploy): provision from Infisical, drop bootstrap-env.sh and .env from backups`.

---

### Task 5: Secrets-hash label and the optional SHA input

**Files:**
- Modify: `compose.yaml`, `.github/workflows/deploy.yml`
- Test: `deploy/tests/test_prod_config.py`

**Interfaces:**
- Consumes: `ENGLEKS_SECRETS_HASH` in `.env` (Task 2).
- Produces:
  - the label `engleks.secrets: ${ENGLEKS_SECRETS_HASH:-dev}` on `migrate`, `api`, `litellm` and `bot`;
  - **Run workflow** with an empty `sha` deploys the head of the selected branch.

- [ ] **Step 1: Write the failing test.** `test_secret_label_on_app_services_only`, on `yaml.safe_load(compose.yaml)["services"]`:
  - `migrate`, `api`, `litellm` and `bot` have `labels == {"engleks.secrets": "${ENGLEKS_SECRETS_HASH:-dev}"}`;
  - `postgres` and `ollama` have no `engleks.secrets` label.
- [ ] **Step 2: Run it to make sure it fails.** Run `uv run pytest deploy/tests/test_prod_config.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
  - Add the four `labels:` blocks, with a one-line comment at the first one on why: Compose ignores changed secret values, and Postgres reads its passwords only at init.
  - In `deploy.yml`:
    - set `sha` to `required: false` with the description `Empty: deploy the head of the branch picked above. A full SHA: roll back to that commit`;
    - set the env line to `SHA: ${{ github.event.workflow_run.head_sha || inputs.sha || github.sha }}`;
    - update the header comment's "Run workflow" sentence.
- [ ] **Step 4: Verify.**
  - Run `uv run pytest deploy/tests -q`. Expected: PASS.
  - Run `ENGLEKS_TAG=$(printf '0%.0s' {1..40}) docker compose --env-file .env.example -f compose.yaml -f compose.prod.yaml config --quiet`. Expected: exit 0.
  - Run `docker compose --env-file .env.example config | grep -c 'engleks.secrets: dev'`. Expected: `4`.
- [ ] **Step 5: Commit.** Message: `feat(deploy): recreate app services on a secret change; Run workflow without a SHA`.

---

### Task 6: Documentation

**Files:**
- Modify: `docs/deployment.md`, `AGENTS.md`, `.env.example`, `docs/superpowers/specs/2026-11-04-deployment-design.md` (§6)

**Interfaces:**
- Consumes: Tasks 1–5, and the master-key finding from Task 1 Step 4.

- [ ] **Step 1: `docs/deployment.md`**, per spec §12.
  - **Intro and the layout block.**
    - `.env` is generated, and the backups hold dumps only.
    - Add `/usr/local/bin/engleks-render-env` and `~deploy/.config/engleks/infisical`.
  - **First-time setup.**
    - A new step before `setup.sh`: the Infisical EU project `engleks`, environment `prod`, and `generate-secrets.sh` on your machine pasted into ".env import". Add the four third-party secrets by hand. Save `RESTIC_PASSWORD` in the password manager.
    - A new step for the machine identity `engleks-prod-server`: Universal Auth, a read-only role on `prod` and a short access-token TTL. Its **Trusted IPs** are the server's IPv4 address **and its IPv6 /64**: Hetzner servers have IPv6 and `curl` prefers it.
    - The `setup.sh` prompts.
    - Remove the "Create .env" step.
    - The first deploy is **Run workflow** with no input. It runs `restic init`. On Ubuntu 24.04's restic 0.16, a missing repository is not exit 10, so the deploy fails there: run `restic init` by hand once, as the error says.
  - **How a deploy works.** The new steps from Task 3, and **Run workflow** with no input.
  - **Rollback.**
    - Unchanged, plus: a rollback restores the previous `.env` without calling Infisical.
    - Commits from before the Infisical move cannot be deployed.
    - Replace the old `API_LLM_MODEL` note with the fact that `prod.env` rolls back with the code.
  - **Switching the model.** A PR to `deploy/prod.env` (and to `MODELS` in `generate_key.py` for a new alias). The deploy syncs the key's models. Keep the "Claude Haiku → GPT-6.1 Terra" subsection only if it still applies, otherwise remove it.
  - **Changing a secret.**
    - Edit in Infisical, then **Run workflow** with no input. The deploy recreates the app services, runs `ALTER ROLE` and creates the new LiteLLM key with its cap restarted.
    - Write down the master-key result from Task 1.
    - Keep the manual restic re-key: run `restic key passwd` with the old values first, then change the value in Infisical, then Run workflow. The deploy refuses a password the repository does not accept.
    - The `env_get` snippets for the spending cap and the UI login still work against the generated `.env`.
  - **Updating the server scripts.** Add `engleks-render-env`.
  - **Backups and restore.** Dumps only. On a new server: `setup.sh` (a new identity, or the new IP added to the old one), install the Storage Box key, deploy, restore both dumps. Never restore `.env`. `RESTIC_PASSWORD` comes from Infisical or the password manager.
  - **A one-time section "Moving to Infisical",** spec §10 steps 1–5, including that rollbacks to earlier commits are unsupported afterwards.
- [ ] **Step 2: `AGENTS.md`.**
  - **Layout.** Add `deploy/prod.env`, `deploy/secrets.txt` and `deploy/infisical/`. The `deploy/server/` line reads "(setup, render-env, deploy, backup)".
  - **Commands.** The Deploy row mentions Run workflow with no input.
  - **"Where does new code go?"**
    - The config-value row adds "a production value goes in `deploy/prod.env`".
    - A new row: "a production secret | a Compose secret, its name in `deploy/secrets.txt`, its value in Infisical before merging".
  - **Testing.** The server script tests need `jq`, and the `server` fixture runs the real `engleks-render-env` against a fake Infisical (`set_secrets`, `inputs()`).
- [ ] **Step 3: `.env.example`.** Rewrite the closing production comment: production config is in `deploy/prod.env` and secrets in Infisical, and `engleks-render-env` generates the server's `.env`, adding `ENGLEKS_TAG` and `ENGLEKS_SECRETS_HASH`.
- [ ] **Step 4: Deployment spec §6.** Add one line at the top: "Replaced by the [Secrets Management design](2026-11-06-secrets-management-design.md): `.env` is generated on every deploy and `bootstrap-env.sh` is gone."
- [ ] **Step 5: Verify.**
  - Run `grep -rn "bootstrap-env\|password manager" docs/deployment.md AGENTS.md .env.example`. Expected: only the `RESTIC_PASSWORD` break-glass mentions remain.
  - Run `make lint && make test`. Expected: clean, all pass.
- [ ] **Step 6: Commit.** Message: `docs: runbook and agent guide for Infisical secrets`.

The manual check from spec §11 (Deploy run green, `docker compose ps` shows the label, the bot answers, a backup turns healthchecks.io green) happens during the owner's move (runbook "Moving to Infisical", step 4), after merge. It is not part of this plan's tasks.
