# Railway Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A merge to `main` deploys a migrated, healthy `api` and a running `bot` on Railway, next to a Railway-managed Postgres, with the whole environment described in `.railway/railway.ts`.

**Architecture:** Railway builds our two Dockerfiles from GitHub after CI is green. A TypeScript IaC file (`railway@3.16.0`) declares `postgres`, `api` and `bot`. Two workflows run `railwayapp/config`: they plan on PRs that touch `.railway/**` and apply the pinned plan on merge. Secrets live only in Railway (`preserve()`). No application code changes.

**Tech Stack:** Railway IaC (`railway/iac` 3.16.0, evaluated by `@railway/cli` 5.71.0 on Node 24), GitHub Actions, Docker/BuildKit, uv 0.13.6.

**Spec:** [docs/superpowers/specs/2026-11-12-railway-deployment-design.md](../specs/2026-11-12-railway-deployment-design.md)

## Global Constraints

- The spec is the owner's approval for the Dockerfile and workflow changes that AGENTS.md lists under "Ask before". `ci.yml`, `compose.yaml`, `compose.prod.yaml` and all Python code stay unchanged.
- Railway names: project `engleks`, environment `production`, resources `postgres`, `api`, `bot`. Repo `topol-3000/engleks`, branch `main`, `checkSuites: true`.
- Region: `europe-west4-drams3a` (Railway's id for the spec's `europe-west4`), set with `{ region }` on `postgres(...)` and `regions: { [REGION]: 1 }` on each service. Both compile to `multiRegionConfig` with `numReplicas: 1`.
- Pins: npm `railway` `3.16.0` (exact, no `^`); `cli-version: "5.71.0"` (the action defaults to `latest`; IaC needs ≥ 5.42.1; deliberately not the newest release, 5.74.0, which is only hours old: the action installs the CLI by version without an integrity check, so raise the pin in a later PR once a release has been out a week or two); `railwayapp/config@9e17d413dbe219f19d62538d9c06b5dbb5e9abf8 # v1.2.0`; `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1` with `persist-credentials: false`.
- Railway variable templates (`${{postgres.PGUSER}}`, `${{api.RAILWAY_PRIVATE_DOMAIN}}`) go in **double-quoted** TS strings, never backticks. In a template literal, `${{...}}` is a JS interpolation.
- Secrets are `preserve()` and never get a value in git, CI or a commit message: `API_SERVICE_TOKEN`, `API_LLM_API_KEY`, `API_LLM_BASE_URL`, `BOT_TELEGRAM_TOKEN`.
- Tools missing on the host (node, npm, actionlint) run in Docker, never through `sudo`/apt.
- Commit messages: conventional style as in `git log`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work on branch `feat/railway-deployment`. Everything ships in one PR (spec §6 step 4). Pushing and opening the PR wait for the owner (Task 5).

## Review Focus

- **The plan job cannot resolve `railway/iac`.** The action runs `npm ci` only at the repo root, but our `package.json` is in `.railway/`. Without our own `npm ci --prefix .railway` step, the first plan fails on the import. Pinned in Task 3 (the step itself) and Task 5 (the first plan comment).
- **Backticks around a Railway template.** `` `...${{postgres.PGUSER}}...` `` evaluates to `[object Object]` or fails to parse. Expected: the literal `${{postgres.PGUSER}}` reaches Railway. Pinned in Task 2's evaluation check (asserts the exact connection-string literal).
- **A docs-only or `.railway/`-only commit redeploys the app**, or a bot-only commit redeploys the api. Expected: only matching services rebuild. Pinned in Task 2's check (exact watch-pattern arrays) and Task 5's acceptance item.
- **Two bot pollers on one token.** A second replica or an overlapping deploy gives repeated 409s from Telegram. Expected: one bot at a time. Pinned in Task 2's check (`numReplicas: 1`, `overlapSeconds: 0`, `restartPolicyType: "ALWAYS"`).
- **A source-only change reinstalls every dependency**, because the dependency layer depends on the sources. Expected: the first `uv sync` layer is `CACHED`. Pinned in Task 1 Step 3.

---

### Task 1: Dockerfiles without the uv cache mount, dependencies in their own layer

**Files:**
- Modify: `apps/api/Dockerfile:12-22`, `apps/bot/Dockerfile:12-22`

**Interfaces:**
- Produces: images whose runtime contents match today's, built from the repo root exactly as CI's `images` job does (`docker build -f apps/<app>/Dockerfile .`). Railway builds the same files (Task 2's `dockerfilePath`).

- [ ] **Step 1: Rewrite the install section of `apps/api/Dockerfile`**

Replace everything from `COPY pyproject.toml uv.lock ./` through the `uv sync` `RUN` with the block in spec §5 "Changed: Dockerfiles". Copy the `uv` image reference with its full digest from the current file (`ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f…d0`). The first sync adds `--no-install-workspace`. The second keeps `--no-editable`. Neither has `--mount=type=cache`. Keep the comment `# uv is mounted for this step only, so it does not stay in the image.` above the first `RUN`. Add one comment line above it: `# Dependencies first, so a source-only change reuses this layer (Railway rejects cache mounts without a service id).` Everything from `COPY apps/api/alembic.ini` down stays as it is.

- [ ] **Step 2: Do the same in `apps/bot/Dockerfile`**

Use `apps/bot/src` and `--package engleks-bot`. The bot's `apt-get` line keeps its `--only-upgrade`.

- [ ] **Step 3: Build twice and check the cache**

```bash
for app in api bot; do docker build -f apps/$app/Dockerfile -t engleks-$app:ci . ; done
# BuildKit compares contents, not mtimes: change a source file for real.
echo "# cache probe" >> apps/api/src/engleks_api/__init__.py
echo "# cache probe" >> apps/bot/src/engleks_bot/__init__.py
for app in api bot; do docker build --progress=plain -f apps/$app/Dockerfile -t engleks-$app:ci . 2>&1 | grep -E "uv sync|CACHED" ; done
git checkout apps/api/src/engleks_api/__init__.py apps/bot/src/engleks_bot/__init__.py
```

Expected: for each app, the step with `--no-install-workspace` shows `CACHED`, and the `--no-editable` step runs again.

- [ ] **Step 4: Smoke-test the images as CI does**

```bash
for app in api bot; do docker run --rm --entrypoint python engleks-$app:ci -c "import engleks_$app.main"; done
docker run --rm --entrypoint alembic engleks-api:ci --help >/dev/null && echo ok
```

Expected: no output from the imports, then `ok` (the api image still has alembic for the pre-deploy command).

- [ ] **Step 5: Commit**

```bash
git add apps/api/Dockerfile apps/bot/Dockerfile
git commit -m "build(docker): install dependencies in their own layer, drop the uv cache mount"
```

### Task 2: `.railway/` IaC: Postgres, api, bot and their variables

**Files:**
- Create: `.railway/railway.ts`, `.railway/package.json`, `.railway/package-lock.json`
- Modify: `.gitignore` (add `node_modules/`)

**Interfaces:**
- Consumes: `apps/api/Dockerfile`, `apps/bot/Dockerfile` (Task 1).
- Produces: `.railway/` with a lockfile that `npm ci --prefix .railway` installs (Task 3). The default export is a `RailwayProgram` returning `project("engleks", { resources: [db, api, bot] })`.

- [ ] **Step 1: Create `.railway/package.json` and the lockfile**

`package.json`: `{"name": "engleks-railway", "private": true, "type": "module", "dependencies": {"railway": "3.16.0"}}`. Generate the lockfile in Docker:

```bash
docker run --rm -u "$(id -u):$(id -g)" -e npm_config_cache=/tmp/npm -v "$PWD/.railway":/w -w /w node:24-slim \
  npm install --no-audit --no-fund --save-exact
```

Expected: `.railway/package-lock.json` exists, its `packages[""].dependencies.railway` is `"3.16.0"`, and `.railway/node_modules/` exists. Add `node_modules/` to `.gitignore`. `git status --short` must not list `node_modules`.

- [ ] **Step 2: Write `.railway/railway.ts`**

Import `defineRailway, github, postgres, preserve, project, service` from `"railway/iac"`. Use the imported `project` and a no-argument callback; the spec sketch's `(ctx, project)` also works, but `ctx` is unused. Constants: `REGION = "europe-west4-drams3a"`, `source = github("topol-3000/engleks", { branch: "main", checkSuites: true })`, `shared = ["pyproject.toml", "uv.lock", "packages/contracts/**"]`. A short header comment says what the file is, that plan/apply run in CI (link `docs/deployment/railway.md`), and that `${{…}}` strings are Railway templates resolved at deploy time.

| | `postgres` | `api` | `bot` |
| --- | --- | --- | --- |
| Call | `postgres("postgres", { region: REGION })` | `service("api", …)` | `service("bot", …)` |
| `build` | | `{ builder: "DOCKERFILE", dockerfilePath: "apps/api/Dockerfile", watchPatterns: ["apps/api/**", "apps/bot/pyproject.toml", ...shared] }` | same with `apps/bot/Dockerfile`, `["apps/bot/**", "apps/api/pyproject.toml", ...shared]` |
| `regions` | | `{ [REGION]: 1 }` | `{ [REGION]: 1 }` |
| top-level | | `preDeploy: "alembic upgrade head"`, `healthcheck: "/readyz"`, `healthcheckTimeout: 120` | |
| `deploy` | | `{ restartPolicyType: "ON_FAILURE" }` | `{ restartPolicyType: "ALWAYS", overlapSeconds: 0 }` |

`env` values are exactly spec §4. Literals: `API_LLM_MODEL: "engleks-rewrite"`, `API_RATE_LIMIT_PER_MINUTE: "10"`, `API_DAILY_TOKEN_BUDGET: "200000"`, `API_LOG_FORMAT: "json"`, `API_LOG_LEVEL: "INFO"`, `PORT: "8000"`, `BOT_LOG_FORMAT: "json"`, `BOT_LOG_LEVEL: "INFO"`. Also these:
- `API_DB_CONNECTION_STRING`: `"postgresql+asyncpg://${{postgres.PGUSER}}:${{postgres.PGPASSWORD}}@${{postgres.PGHOST}}:${{postgres.PGPORT}}/${{postgres.PGDATABASE}}"` (double quotes; splitting it with `+` is fine).
- `API_SERVICE_TOKEN`, `API_LLM_API_KEY`, `API_LLM_BASE_URL`, `BOT_TELEGRAM_TOKEN`: `preserve()`.
- `API_BOT_USERNAME`: `preserve()`. Not a secret, but its value (the production bot's username without `@`) is set in the Railway UI next to `BOT_TELEGRAM_TOKEN`, since both come from BotFather. This departs from the spec's literal (owner's decision). It is optional in `Settings`, so an unset value only means `engleks-admin` prints no t.me link.
- `BOT_SERVICE_TOKEN`: `api.env.API_SERVICE_TOKEN`.
- `BOT_API_BASE_URL`: `"http://${{api.RAILWAY_PRIVATE_DOMAIN}}:8000"`.

- [ ] **Step 3: Evaluate the program and check the result**

Save this as `$SCRATCH/check-railway.mjs`, where `$SCRATCH` is your scratchpad directory. It is not committed.

```js
import assert from "node:assert/strict";
import { createRailwayContext, project } from "railway/iac";
const program = (await import("/w/railway.ts")).default;
const def = await program(createRailwayContext({}), project);
const by = Object.fromEntries(def.resources.map((r) => [r.name, r]));
const shared = ["pyproject.toml", "uv.lock", "packages/contracts/**"];
const one = { "europe-west4-drams3a": { numReplicas: 1 } };
assert.equal(def.name, "engleks");
assert.deepEqual(Object.keys(by).sort(), ["api", "bot", "postgres"]);
assert.equal(by.postgres.image, "ghcr.io/railwayapp-templates/postgres-ssl:18");
assert.deepEqual(by.postgres.deploy.multiRegionConfig, one);
for (const name of ["api", "bot"]) {
  assert.deepEqual(by[name].source, { type: "github", repo: "topol-3000/engleks", branch: "main", checkSuites: true });
  assert.equal(by[name].build.dockerfilePath, `apps/${name}/Dockerfile`);
  assert.deepEqual(by[name].deploy.multiRegionConfig, one);
}
assert.deepEqual(new Set(by.api.build.watchPatterns), new Set(["apps/api/**", "apps/bot/pyproject.toml", ...shared]));
assert.deepEqual(new Set(by.bot.build.watchPatterns), new Set(["apps/bot/**", "apps/api/pyproject.toml", ...shared]));
assert.deepEqual(by.api.deploy.preDeployCommand, ["alembic upgrade head"]);
assert.equal(by.api.deploy.healthcheckPath, "/readyz");
assert.equal(by.api.deploy.healthcheckTimeout, 120);
assert.equal(by.api.deploy.restartPolicyType, "ON_FAILURE");
assert.equal(by.bot.deploy.restartPolicyType, "ALWAYS");
assert.equal(by.bot.deploy.overlapSeconds, 0);
assert.equal(by.bot.deploy.healthcheckPath, undefined);
const v = (svc, key) => by[svc].variables[key];
assert.deepEqual(v("api", "API_DB_CONNECTION_STRING"), { type: "literal", value:
  "postgresql+asyncpg://${{postgres.PGUSER}}:${{postgres.PGPASSWORD}}@${{postgres.PGHOST}}:${{postgres.PGPORT}}/${{postgres.PGDATABASE}}" });
for (const k of ["API_SERVICE_TOKEN", "API_LLM_API_KEY", "API_LLM_BASE_URL", "API_BOT_USERNAME"]) assert.deepEqual(v("api", k), { type: "preserve" });
assert.deepEqual(v("bot", "BOT_TELEGRAM_TOKEN"), { type: "preserve" });
assert.deepEqual(v("bot", "BOT_SERVICE_TOKEN"), { type: "reference", resource: "service.api", output: "API_SERVICE_TOKEN" });
assert.deepEqual(v("bot", "BOT_API_BASE_URL"), { type: "literal", value: "http://${{api.RAILWAY_PRIVATE_DOMAIN}}:8000" });
assert.equal(v("api", "PORT").value, "8000");
console.log("railway.ts OK");
```

Run it:

```bash
docker run --rm -v "$PWD/.railway":/w -v "$SCRATCH/check-railway.mjs":/w/check.mjs:ro -w /w node:24-slim node check.mjs
```

Expected: `railway.ts OK`. A failure here is a mistake in `railway.ts`. Fix the file, not the check.

- [ ] **Step 4: Scan the lockfile as CI will**

```bash
docker run --rm -v "$PWD":/src:ro -v trivy-cache:/root/.cache/ -w /src \
  aquasec/trivy:0.76.1@sha256:790fd66d1697395d8693d1acaa1ea702406a20f109b6fc7bac8db623eca6c06c \
  fs --no-progress --scanners vuln,misconfig,secret --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 --skip-dirs .venv --skip-dirs .railway/node_modules .
```

Expected: exit 0. If a finding in `railway`'s dependency tree has a fix, stop and report it to the owner. Do not ignore it (AGENTS.md).

- [ ] **Step 5: Commit**

```bash
git add .railway/railway.ts .railway/package.json .railway/package-lock.json .gitignore
git commit -m "feat(deploy): describe the Railway environment as code"
```

### Task 3: Plan and apply workflows

**Files:**
- Create: `.github/workflows/railway-plan.yml`, `.github/workflows/railway-apply.yml`

**Interfaces:**
- Consumes: `.railway/package-lock.json` (Task 2); the repo secret `RAILWAY_TOKEN` (Task 5).
- Produces: the plan artifact `railway-plan` (the action's default name), uploaded on the PR's head SHA. The apply job finds it through the merged PR.

- [ ] **Step 1: Write `railway-plan.yml`**

- `name: Railway plan`. Trigger: `on: pull_request` with `paths: [".railway/**"]`. Top-level `permissions: { contents: read, pull-requests: write }`. `concurrency: { group: railway-plan-${{ github.ref }}, cancel-in-progress: true }`.
- One job, `plan`: `runs-on: ubuntu-24.04`, with `if: github.event.pull_request.head.repo.full_name == github.repository` (skips forks).
- Steps:
  1. checkout (pinned, `persist-credentials: false`).
  2. `run: npm ci --prefix .railway --ignore-scripts --no-audit --no-fund`. Comment above it: `# The action runs npm ci only at the repo root; railway.ts imports railway/iac from .railway/node_modules.`
  3. `railwayapp/config` (pinned) with `command: plan`, `railway-token: ${{ secrets.RAILWAY_TOKEN }}`, `cli-version: "5.71.0"`.

- [ ] **Step 2: Write `railway-apply.yml`**

- `name: Railway apply`. Trigger: `on: push` with `branches: [main]` and `paths: [".railway/**"]`. `permissions: { contents: read, actions: read, pull-requests: read }`. `concurrency: { group: railway-apply, cancel-in-progress: false }`, so two applies never run at once.
- One job, `apply`, on `ubuntu-24.04`. Steps: checkout (pinned, `persist-credentials: false`), then `railwayapp/config` with `command: apply`, the same token and `cli-version`. No `npm ci`: apply does not evaluate `railway.ts`.
- Comment at the top: a direct push without a PR has no pinned plan and fails by design. If the environment changed since the plan, the apply fails too; re-run the plan on a new PR.

- [ ] **Step 3: Lint the workflows**

```bash
docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:1.7.7 -color
```

Expected: no output, exit 0. `ci.yml` must also pass. If it reports something in `ci.yml` that this change did not introduce, mention it and leave it alone.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/railway-plan.yml .github/workflows/railway-apply.yml
git commit -m "ci(deploy): plan Railway changes on PRs and apply them on merge"
```

### Task 4: Runbook and docs

**Files:**
- Create: `docs/deployment/railway.md`
- Modify: `AGENTS.md` (Layout, the "a config value" row, Boundaries, one link), `docs/architecture.md:131-133` (§9 Runtime)

**Interfaces:**
- Consumes: names and values from Tasks 1–3 (resource names, variable names, workflow names, `cli-version`).

- [ ] **Step 1: Write `docs/deployment/railway.md`**

The audience is whoever operates production: they know the repo but not Railway. Sections, in this order:
1. **What runs where**: the topology diagram from spec §3 and the cost table from spec §2.
2. **Bootstrap**: spec §6, steps 1–7, verbatim in substance.
3. **Secrets and Railway-held values**: the five `preserve()` variables and where each value comes from (`openssl rand -hex 32`, the LiteLLM virtual key, the proxy URL, BotFather for the token and `API_BOT_USERNAME`). Set them in the Railway UI, seal `BOT_TELEGRAM_TOKEN` and `API_LLM_API_KEY`, never seal `API_SERVICE_TOKEN`, then redeploy. **Rotation**: change the value in the UI, then redeploy `api` *and* `bot` (the bot references the api's token).
4. **How a deploy happens**: spec §3 "Rollout of a push to `main`", plus how an infra change flows (PR → plan comment → merge → apply). Apply fails on drift or on a direct push, and the fix is a new PR.
5. **Rollback**: redeploy a previous deployment in Railway. Expand-only migrations make this safe, and no down-migration runs.
6. **Logs**: Railway's service logs, or `railway logs --service api`.
7. **Admin CLI in production**: `railway ssh --service api`, then `engleks-admin invite create` (and other `engleks-admin` commands).
8. **Known gaps**: spec §8, backups first.

- [ ] **Step 2: Update `AGENTS.md`**

- Layout block: add `.railway/          Railway IaC (railway.ts); plan/apply run in CI` and `docs/deployment/  runbooks`.
- "a config value" row: append `; for production, an entry in .railway/railway.ts (a literal, or preserve() for a secret)`.
- Boundaries "Ask before": add `changing .railway/` next to the CI and Dockerfile items.
- Under Commands, after the table: `Production runs on Railway: see [docs/deployment/railway.md](docs/deployment/railway.md).`
- Keep the "Migrations run in the `migrate` Compose service" rule true by adding `, or Railway's pre-deploy command in production`.

- [ ] **Step 3: Update `docs/architecture.md` §9**

Keep the Compose sentence for local runs. Add: production runs on Railway (`api`, `bot`, managed Postgres 18, private networking, no public domain), migrations run as the api's pre-deploy command, the LiteLLM proxy runs outside Railway and is reached through `API_LLM_BASE_URL`, and a link to the runbook.

- [ ] **Step 4: Verify**

```bash
make lint && make test
grep -o '](\([^)#]*\)' docs/deployment/railway.md AGENTS.md | sed 's/.*](//' | grep -v '^http' | sort -u
```

Expected: lint and tests green (nothing in Python changed). Every relative link printed exists, relative to the file that contains it.

- [ ] **Step 5: Commit**

```bash
git add docs/deployment/railway.md AGENTS.md docs/architecture.md
git commit -m "docs(deploy): add the Railway runbook and point AGENTS.md at it"
```

### Task 5: Bootstrap, first plan and acceptance (with the owner)

The owner does the steps that need Railway or GitHub admin access. The agent prepares the commands and records the results. Nothing here is pushed or merged without the owner's go-ahead.

**Interfaces:**
- Consumes: the branch from Tasks 1–4.

- [ ] **Step 1: Owner: bootstrap steps 1–3 from spec §6**

Railway project `engleks`; the GitHub App installed and a member's GitHub account connected; a project token scoped to `production` saved as the repo secret `RAILWAY_TOKEN`; nothing else polling the production bot token. Confirm each one with the owner before going on.

- [ ] **Step 2: Push the branch and open the PR (after the owner says so)**

The PR body links the spec, this plan and the runbook, and contains the acceptance checklist from spec §7 as unchecked boxes. It ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

- [ ] **Step 3: Check the plan comment**

Expected: three resources created (`postgres`, `api`, `bot`), variable values `«hidden»`, no destructive marker, and CI green, including the `images` job on the new Dockerfiles. If the plan rejects something, apply the matching fallback from spec §7 "Assumptions" and push. Two cases are not in that table. If the region id is rejected, use the id the Railway UI shows for Europe West. If the import fails, check the `npm ci --prefix .railway` step.

- [ ] **Step 4: Owner merges; then bootstrap steps 5–7**

The first deploy is expected to fail in pre-deploy because the secrets are empty. The owner sets the four secrets and `API_BOT_USERNAME`, seals two, redeploys `api` and `bot`, and creates the first invite through `railway ssh --service api`.

- [ ] **Step 5: Run the acceptance checklist (spec §7) and record the results in the PR**

Each item with its evidence (a log line, a deployment id, a screenshot). For the watch-pattern item, use two small follow-up PRs: one docs-only, one that touches only `apps/bot/`. The failure-safety and rollback items are run once, with the owner watching. Any item that fails becomes an issue or a fix PR. Do not tick it.
