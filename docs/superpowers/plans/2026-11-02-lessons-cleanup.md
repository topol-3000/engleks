# Lessons Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove modes, explanation language, corrections and the bot's rerun feature everywhere, and replace the Alembic history with one fresh migration, so everything describes only the `process_text.v3` flow.

**Architecture:** Three tasks, each leaving `make lint && make test` green. (1) The bot drops the rerun feature and stops using `mode`, while the contract still has it. (2) Contract, API and DB drop `Mode`, `explanation_language` and `correction` together, and the migration is regenerated. (3) Docs describe only the current design.

**Tech Stack:** Python 3.14, uv workspace, FastAPI, SQLAlchemy + Alembic, aiogram 3, pydantic, pytest + testcontainers.

**Spec:** [docs/superpowers/specs/2026-11-02-lessons-cleanup-design.md](../specs/2026-11-02-lessons-cleanup-design.md)

## Global Constraints

- Keep `process_text.v3.j2` byte-for-byte and `PROMPT_VERSION = "process_text.v3"`.
- Wire request is exactly `{text}`; wire response is exactly `{interaction_id, source_language, output_text, explanations}`.
- No compatibility shims: no deprecated fields, unused enums, unused tables or dead routes.
- Follow AGENTS.md (layer rules, logging, no `print`, never log user text). `make lint && make test` before each commit.
- Commit messages: conventional style as in `git log`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- A user taps a Minimal/Polished button on a reply sent before this change (`rerun:minimal` callback): no API call, no error, nothing sent. Pinned in Task 1 (`test_stale_rerun_button_ignored`).
- A bot built before this change still sends `mode`/`explanation_language` during a rolling deploy: the API ignores the extra keys and answers 200. Pinned in Task 2 (`test_old_client_fields_are_ignored`, contracts `test_request_ignores_unknown_fields`).
- After migrating, the DB has exactly `alembic_version`, `app_user`, `identity`, `interaction`: no leftover `correction` table. Pinned in Task 2 (`test_schema_has_only_current_tables`).
- A local DB still at the old revision `0002` fails `make migrate` with "Can't locate revision". Expected: the recreate command in `docs/architecture.md` and the Task 2 commit message. Not testable; the reviewer checks the docs.
- The response no longer has `mode`: pinned in Task 2 (`"mode" not in r.json()`).

---

### Task 1: Bot without modes or rerun

**Files:**
- Delete: `apps/bot/src/engleks_bot/handlers/rerun.py`, `apps/bot/src/engleks_bot/rendering/keyboards.py`, `apps/bot/tests/test_rerun.py`, `apps/bot/tests/test_keyboards.py`
- Modify: `apps/bot/src/engleks_bot/main.py`, `handlers/texts.py`, `handlers/start.py`, `rendering/errors.py`, `rendering/reply.py`
- Test: `apps/bot/tests/test_texts.py`, `test_routing.py`, `test_reply.py`, `test_errors.py`, `test_start.py`

**Interfaces:**
- Consumes: the current contract (`ProcessTextResponse` still requires `mode`; test helpers keep passing `mode=Mode.NATURAL` until Task 2).
- Produces: `on_text(message: Message, event_from_user: User, api: ApiClient, bot: Bot) -> None` does the whole request/reply; `process_and_reply` no longer exists. `build_dispatcher(api)` sets only `dispatcher["api"]`. `rendering.errors` exports `MAX_INPUT_CHARS, UNAVAILABLE, NO_ACCESS, CANNOT_PROCESS, TOO_LONG, LLM_DOWN, GENERIC, SEND_TEXT, error_text`.

- [ ] **Step 1: Change the tests**

`test_texts.py` (drop the `keyboard_modes` import):

```python
# in test_request_and_reply, replacing the keyboard assertion
assert msg.reply_markup is None

async def test_long_reply_has_no_keyboard(make_message, make_api, bot, sent):
    # was test_long_reply_only_last_has_keyboard; same long output
    msgs = sent()
    assert len(msgs) > 1
    assert all(m.reply_markup is None for m in msgs)

# in test_logs_never_contain_text
assert "mode" not in entry
```

`test_routing.py`: delete `callback_update`, `test_rerun_callback_routed`, `test_group_callback_ignored`, `test_foreign_callback_ignored` and the keyboards import. Add:

```python
@pytest.mark.parametrize("data", ["rerun:minimal", "other:1"])
async def test_stale_rerun_button_ignored(data, feed, api_calls, session):
    callback = {"id": "cb", "from": USER, "chat_instance": "ci", "data": data,
                "message": {**message_dict("I went.", message_id=11), "from": BOT_USER}}
    await feed({"update_id": 1, "callback_query": callback})
    assert api_calls == []
    assert session.requests == []
```

`test_reply.py`: headers lose the mode, e.g. `"I went.\n\nLessons\n1. ..."` and `f"I went.\n\nTranslated from {name}\n1. A lesson."`; `("bold", 9, 7)` for `Lessons` after `I went.`; `("bold", 4, 7)` after `😀`; packing test expects `"a" * 4000 + "\n\nLessons"`; the oversized test expects `.startswith("Lessons")`. The `response()` helper takes no `mode` argument and passes `mode=Mode.NATURAL`.

`test_errors.py`: delete `test_rewriting` and its imports.

`test_start.py` / `HELP_TEXT` exact copy:

```python
HELP_TEXT = (
    "Hi! I'm Engleks, your English writing trainer.\n\n"
    "Send me a message in English and I'll correct it, or write in Ukrainian or Russian "
    "and I'll translate it into English. You'll also get short lessons on the patterns "
    "behind the edits."
)
```

- [ ] **Step 2: Run the bot tests and see them fail**

Run: `uv run pytest apps/bot/tests -q`
Expected: FAIL (reply headers, keyboard assertions, help text, `mode` in the log).

- [ ] **Step 3: Implement**

- Delete the four files listed above.
- `main.build_dispatcher`: remove `dispatcher["in_flight"]`, the `callback_query` filter and `rerun.build_router()`.
- `handlers/texts.py`: fold `process_and_reply` into `on_text`. It builds `ProcessTextRequest(text=text)`, replies with no `reply_markup`, and logs `api_call_failed` and `text_processed` without `mode`.
- `rendering/errors.py`: remove `ALREADY_WORKING`, `ORIGINAL_GONE`, `TOO_OLD`, `rewriting` and the `Mode` import.
- `rendering/reply.py`: `_header` returns `Bold("Translated from <Language>")`, `Bold("Lessons")` or `None`.
- `handlers/start.py`: the `HELP_TEXT` above.

- [ ] **Step 4: Verify**

Run: `make lint && make test`
Expected: lint clean; all tests pass.

- [ ] **Step 5: Commit**

```bash
git add -A apps/bot
git commit -m "refactor(bot): remove modes and the rerun buttons"
```

### Task 2: Contract, API and schema without modes, explanation language or corrections

**Files:**
- Delete: `apps/api/src/engleks_api/infra/llm/prompts/process_text.v1.j2`, `process_text.v2.j2`, `apps/api/migrations/versions/0001_initial.py`, `0002_core_slice.py`
- Create: `apps/api/migrations/versions/0001_initial.py` (autogenerated)
- Modify: `packages/contracts/src/engleks_contracts/texts.py`, `__init__.py`; `apps/api/src/engleks_api/domain/text_processing.py`, `domain/ports.py`, `application/process_text.py`, `infra/llm/proxy_rewriter.py`, `infra/db/models.py`, `infra/db/repositories.py`, `transport/texts.py`
- Test: `packages/contracts/tests/test_texts.py`; `apps/api/tests/unit/test_enum_parity.py`, `test_process_text.py`, `test_proxy_rewriter.py`; `apps/api/tests/integration/test_texts.py`, `test_repositories.py`, `test_migrations.py`, `test_llm.py`; bot test helpers in `apps/bot/tests/test_texts.py`, `test_reply.py`, `test_routing.py`, `test_api_client.py`

**Interfaces:**
- Consumes: Task 1's bot, which no longer reads `response.mode` or sends `mode`.
- Produces:
  - `ProcessTextRequest(text: str)`; `ProcessTextResponse(interaction_id: UUID, source_language: Language, output_text: str, explanations: list[str])`; `engleks_contracts.Mode` no longer exists.
  - Domain: `TextRewriter.rewrite(self, text: str) -> RewriteOutcome`; `Interaction(id: UUID, user_id: UUID, input_text: str, outcome: RewriteOutcome)`; `ProcessedText(interaction_id: UUID, source_language: Language, output_text: str, explanations: list[str])`; `Mode` and `RewriteRequest` no longer exist.
  - `process_text(auth: AuthContext, text: str, rewriter: TextRewriter, interactions: InteractionRepository) -> ProcessedText`.
  - `ProxyRewriter.rewrite(self, text: str) -> RewriteOutcome`.

- [ ] **Step 1: Change the tests**

Contracts:

```python
def test_request_is_only_text():
    assert ProcessTextRequest(text="hi").model_dump() == {"text": "hi"}

def test_request_ignores_unknown_fields():
    body = {"text": "hi", "mode": "minimal", "explanation_language": "uk"}
    assert ProcessTextRequest.model_validate(body).model_dump() == {"text": "hi"}

def test_mode_is_gone():
    assert "Mode" not in engleks_contracts.__all__
```

`test_request_defaults` is deleted. `test_response_round_trip` drops `mode` and asserts `set(body) == {"interaction_id", "source_language", "output_text", "explanations"}`.

API unit:
- `test_enum_parity`: parametrize only `(domain.Language, wire.Language)`.
- `test_process_text`: `FakeRewriter.texts: list[str]` records `rewrite(text)`. Calls are `process_text(AUTH, "He go home.", rewriter, repo)`. Assert `repo.saved == [Interaction(result.interaction_id, AUTH.user_id, "He go home.", out)]` and `(result.source_language, result.output_text, result.explanations)`.
- `test_proxy_rewriter`: `REQ` becomes `TEXT`, and calls become `.rewrite(TEXT)`. Replace `test_system_prompt_is_the_same_for_every_mode_and_explanation_language` with:

```python
async def test_system_prompt_is_v3_verbatim():
    seen: list[httpx2.Request] = []
    await make(recording([completion(VALID)], seen)).rewrite(TEXT)
    system = json.loads(seen[0].content)["messages"][0]["content"]
    assert system == V3_PROMPT.read_text().rstrip("\n")
```

`V3_PROMPT` is a module-level `Path` to `engleks_api/infra/llm/prompts/process_text.v3.j2`, resolved from `engleks_api.infra.llm.__file__`.

API integration:
- `test_texts.py`: `FakeRewriter.texts: list[str]`. The success test posts `{"text": "He go home."}` and asserts `"mode" not in r.json()`, `body.explanations == [...]` and `fake.texts == ["He go home."]`. Add:

```python
async def test_old_client_fields_are_ignored(api):
    client, fake = api
    body = {"text": "He go home.", "mode": "minimal", "explanation_language": "uk"}
    r = await client.post(URL, json=body, headers=headers("8181"))
    assert r.status_code == 200
    assert fake.texts == ["He go home."]
```

- `test_repositories.py`: `Interaction(uuid.uuid7(), user_id, "He go home, no?", outcome)`. Assert `row.source_language == "en"` (no `mode` or `explanation_language`). Remove the `CorrectionRow` count.
- `test_migrations.py`:

```python
async def test_schema_has_only_current_tables(engine):
    async with engine.connect() as c:
        names = await c.run_sync(lambda conn: sa.inspect(conn).get_table_names())
    assert sorted(names) == ["alembic_version", "app_user", "identity", "interaction"]
```

- `test_llm.py`: `rewriter.rewrite("...")` with plain strings; remove the `Mode` and `RewriteRequest` imports.

Bot helpers: remove `mode=` from every `ProcessTextResponse(...)` and the `Mode` imports. `test_texts.test_request_and_reply` expects the request JSON `{"text": "I has went."}`. `test_api_client` builds `ProcessTextRequest(text="I has went.")` and expects `{"text": "I has went."}`.

- [ ] **Step 2: Run and see them fail**

Run: `uv run pytest packages/contracts/tests apps/api/tests/unit apps/bot/tests -q`
Expected: FAIL (import errors for the new signatures; request JSON still has `mode`).

- [ ] **Step 3: Implement code**

Implement the Interfaces above. Also:
- Delete prompts v1/v2. `ProxyRewriter` renders v3 with `.render()` and drops the "one behaviour for every mode" comment.
- Models: remove `CorrectionRow`, and `InteractionRow.mode` and `.explanation_language`. The repository saves `input_text=interaction.input_text`.
- `ports.InteractionRepository.add` docstring: `"""Store the interaction."""`.
- Transport maps `body.text` in and the four response fields out. Remove the `Mode`/`WireMode` imports.

- [ ] **Step 4: Regenerate the migration**

```bash
git rm -q apps/api/migrations/versions/0001_initial.py apps/api/migrations/versions/0002_core_slice.py
uv run --package engleks-api python - <<'EOF'
import sys
from alembic import command
from alembic.config import Config
from testcontainers.postgres import PostgresContainer
sys.path.insert(0, "apps/api/tests/integration")
from conftest import POSTGRES_IMAGE
with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as pg:
    cfg = Config("apps/api/alembic.ini")
    cfg.set_main_option("sqlalchemy.url", pg.get_connection_url().replace("%", "%%"))
    command.revision(cfg, message="initial", autogenerate=True, rev_id="0001")
EOF
make fmt
```

Expected: `apps/api/migrations/versions/0001_initial.py` with `down_revision = None`. Review it by hand against the old `0002_core_slice.py` (`git show HEAD:apps/api/migrations/versions/0002_core_slice.py`). It should have the same `app_user`, `identity` and `interaction` tables, constraint names and `ix_interaction_user_id_created_at` (`created_at DESC`), without `mode`, `explanation_language` or `correction`. The downgrade drops them in reverse order. Fix any autogenerate gaps (e.g. the DESC index) by hand.

- [ ] **Step 5: Verify**

Run: `make lint && make test`
Expected: lint clean; all tests pass, including `test_upgrade_downgrade_and_check` and `test_schema_has_only_current_tables`.

- [ ] **Step 6: Commit**

```bash
git add -A packages apps
git commit -m "refactor!: drop modes, explanation language and corrections; reset migrations

Pre-release reset: the migration history is replaced by a single 0001.
Recreate the local API database (keeps the LiteLLM database and Ollama models):

  docker compose exec postgres psql -U engleks -d postgres \\
    -c 'DROP DATABASE engleks WITH (FORCE)' -c 'CREATE DATABASE engleks'
  make migrate"
```

### Task 3: Docs describe only the current design

**Files:**
- Modify: `AGENTS.md`, `docs/architecture.md`, `docs/superpowers/specs/2026-11-01-core-slice-design.md`, `docs/superpowers/plans/2026-11-01-core-slice.md`, `docs/superpowers/specs/2026-11-01-bot-design.md`, `docs/superpowers/plans/2026-11-01-bot.md`, `docs/superpowers/specs/2026-11-01-project-structure-design.md`, `docs/superpowers/plans/2026-11-01-project-structure.md`

**Interfaces:**
- Consumes: the final names from Tasks 1 and 2.

- [ ] **Step 1: List every stale reference**

```bash
git grep -n -i -E '\bmodes?\b|explanation_language|explanation language|ChangeItem|\bCategor|reconcil|correction|rerun|in_flight|in-flight|minimal|polished|diff engine|difflib|changes\[\]' -- docs AGENTS.md ':!docs/superpowers/specs/2026-11-02-lessons-cleanup-design.md' ':!docs/superpowers/plans/2026-11-02-lessons-cleanup.md'
```

Expected: a list of hits to fix in Step 2.

- [ ] **Step 2: Rewrite**

- `AGENTS.md`: intro without modes. Contract rule 5's enum example is `language` only. The "shared object in the bot" row is `ApiClient` only. The "bot command or button" row becomes "a bot command"; keep its `rendering/` note for layout.
- `docs/architecture.md`: the Improve and Translate bullets (one prompt, lessons in English); the `app_user` settings without explanation language or default mode; no `correction` row; the sequence diagram request has no `mode`; §8 API draft request and response match the contract. Under Persistence, add the recreate command from the spec (§5).
- Core-slice spec and plan: replace the diff-engine/reconciliation sections and tasks, categories, `correction` table, `RewriteRequest`, `ChangeItem` and the mode or explanation-language prompt variables with the v3 design (`explanations: list[str]`, `rewrite(text)`, the three tables, one migration `0001`). Remove tasks that only built removed code. Keep the remaining tasks' structure.
- Bot spec and plan: remove the rerun buttons, keyboards, in-flight set and their error texts; reply layout is output, header (`Lessons` / `Translated from …`) and numbered lessons; the scope and decision tables lose mode selection.
- Project-structure spec and plan: fix only the hits Step 1 lists, if they describe removed behaviour.

- [ ] **Step 3: Verify**

Run the Step 1 grep again.
Expected: only hits that are not about the old design (e.g. a "Minimal auth" heading, English words like "change" in unrelated sentences). Then run `make lint && make test` (docs don't affect them, but the branch must stay green).

Final repo-wide check:

```bash
git grep -n -i -E '\bMode\b|explanation_language|ChangeItem|\bCategory\b|reconcile|CorrectionRow|rerun|in_flight|\bminimal\b|\bpolished\b' -- apps packages
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add -A AGENTS.md docs
git commit -m "docs: describe only the lessons design"
```
