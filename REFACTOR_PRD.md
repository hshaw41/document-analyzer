# PRD — Module-split refactor: a print-free core for Document Analyzer

Status: ready to implement. Derived from the locked design record (`research/module-split-design.md`, design locked 2026-06-15) via the `to-prd` skill.
Dependency diagram: `docs/module_dependency_map.svg` (referenced throughout; not reproduced here).
Authority: the research record is the source of truth. This PRD restates its decisions as an implementation spec; it does not introduce or overturn any decision. Where the record left an access path implicit, this PRD flags it as an **Open sub-decision** rather than inventing one silently.

> Note on this file's location: `docs/` and `research/` are both gitignored in this repo, so a PRD that ought to be version-tracked cannot live there. This file sits at the repo root so it is committed alongside the code it specifies. Flagging the choice for confirmation.

---

## Problem Statement

I want to add a Streamlit frontend that imports the existing summarisation logic directly. I can't: today `document_analyzer.py` braids the business logic with terminal I/O — `print`, `input`, and Rich progress bars are woven straight through the engine. The logic can't speak without a terminal attached. A Streamlit app has no `print`, no `input`, and no Rich console, so it cannot reuse the core as-is. Step 1 (PR #1) made the module importable without running on import, but the logic is still terminal-bound.

## Solution

Carve `document_analyzer.py` into separate modules with one job: produce a **print/input/Rich-free core** that both the existing CLI and a future Streamlit app import as peers. The CLI is not removed — it stops *being* the program and becomes one consumer of the core, sitting beside a future `app.py`. Every kind of I/O is pushed to the edge of the system; the pure core (engine + services) speaks only in data. A skin (CLI today, Streamlit later) turns that data into words and widgets.

The single driver is the Streamlit frontend (see `docs/week8_frontend_prd.md`). No end-user behaviour changes in this refactor; the CLI must behave exactly as it does today after every step.

---

## The five seams and where each lands

These seams were identified in teaching lesson 1 and are the spine of the refactor:

1. **Program runs at import** — DONE (PR #1: `main()` + `if __name__ == "__main__"` guard, `DEBUG` global replaced by a `debug` parameter).
2. **Logic braided with terminal I/O** — THE driver. Becomes the module boundary: the pure core gets no `print`/`input`/Rich, and the import block enforces it (a module that never imports `rich` provably cannot render a progress bar).
3. **The 9-tuple return** — becomes the `SummaryResult` dataclass (see Implementation Decisions).
4. **Cache filepath rebuilt four times** — internal fix in persistence: one `_summary_path` helper.
5. **`tldr`/`key_terms` stored once-per-document, not per-depth** — a real bug, but **out of scope here** and deferred to a separate lowest-priority follow-up. This split preserves today's once-per-document behaviour exactly. See Out of Scope.

---

## Module map

Seven flat files living together in a plain `src/` directory (established in step 0a, #17) — **not** a package: no `__init__.py`. `src/` keeps the repo root clean while preserving no-ceremony sibling imports (because Python and Streamlit put the run script's own directory on `sys.path`, `import engine` / `from services import ...` resolve plainly via `python src/cli.py` / `streamlit run src/app.py`). A future `app.py` (Streamlit) sits beside them in `src/`. Colours below match `docs/module_dependency_map.svg`; arrows in that diagram mean *imports* and always point downward.

| Module | Role | Contents | Imports |
|---|---|---|---|
| `config.py` | config / knobs (gray) | `MODEL`, `MODEL_PRICING`, `CHUNK_SIZE`, `CHARS_PER_TOKEN`, `THINKING_BUDGET` — tunable knobs only | nothing |
| `extraction.py` | edge — file I/O (blue) | `get_document` (path → text) | nothing app-side (uses `pymupdf`, `python-docx`, `striprtf`) |
| `claude_client.py` | edge — network I/O (blue) | `get_claude_response`, `calculate_response_cost`, `API_ERRORS` | `config` |
| `engine.py` | pure core (teal) | `summarise_document`, `chunk_document`, `SummaryResult` + nested stats dataclass, `SUMMARY_PROMPTS`, the reduce / structured-output instruction strings, `SUMMARY_OUTPUT_CONFIG` | `claude_client`, `config` |
| `persistence.py` | edge — disk cache (blue) | `save_summary`, `load_summary`, `save_partial_summaries`, `clear_partial_summaries`, `_summary_path` (new), `list_saved_summaries` (new) | nothing app-side (uses `os`, `json`) |
| `services.py` | pure core (teal) | `get_or_generate_summary` (summary service), `QA` class, `QANDA_PROMPTS` | `engine`, `persistence` |
| `cli.py` | edge — terminal skin (blue) | display fns, `get_prompt_type`, the flows, `qa_mode`, `post_summary_menu`, `main` + guard | `services`, `extraction`, `config` |
| `app.py` (future, not this PRD) | edge — Streamlit skin (blue) | Streamlit UI | `services`, `extraction` |

**Import direction is the enforcement mechanism.** The boundary only becomes real when crossing it requires a visible `import`. If `engine.py` never imports `rich`, it cannot print a progress bar. Skins (`cli.py`, future `app.py`) are the only modules permitted to `print`, `input`, or touch Rich.

There is deliberately **no `utils.py`** (the old "Utilities" section held two unrelated roommates — `get_document` and `chunk_document` — which go to different homes) and **no `io.py`** (I/O is a rim around the core, not a band between skin and service — see Implementation Decisions).

---

## User Stories

Actors: **Frontend Developer** (me, building Streamlit next), **CLI User** (behaviour must not change), **Skin** (any consumer of the core — CLI today, Streamlit later), **Test Author**, **Maintainer**.

1. As a Frontend Developer, I want to import the summarisation core without a terminal attached, so that Streamlit can call it directly.
2. As a Frontend Developer, I want the core to contain no `print`, `input`, or Rich calls, so that importing it never writes to a server console or blocks on stdin.
3. As a Frontend Developer, I want to hand the core already-extracted text plus a filename, so that an in-memory upload (bytes, no path) works just as a CLI file path does.
4. As a Frontend Developer, I want every summarisation call to return one consistent shape, so that I write one rendering path whether the result cost \$3 or came off disk in 3ms.
5. As a Frontend Developer, I want a cache hit to return the same shape as a fresh generation, so that I never special-case "loaded from cache" in the UI.
6. As a Frontend Developer, I want per-chunk progress reported as data (a stage plus current/total), so that I can render a Streamlit progress bar from numbers the engine emits without it knowing a browser exists.
7. As a Frontend Developer, I want errors returned as data on a status field, so that I can show an error banner instead of catching exceptions across a UI boundary.
8. As a Frontend Developer, I want Q&A conversation history owned by an object I construct, so that a session's history lives somewhere between calls and a new session starts clean.
9. As a CLI User, I want the tool to behave exactly as it does today — same menus, same prompts, same progress bars, same cached output — so that nothing regresses.
10. As a CLI User, I want resume-after-failure to keep working, so that a long document that failed mid-run picks up from the last completed chunk.
11. As a CLI User, I want the partial-summary caching behaviour preserved exactly, so that nothing about today's save/restore changes.
12. As a Skin, I want to check a single `status` field first and have a loud default branch, so that an unrecognised or non-complete status can never silently render nothing.
13. As a Skin, I want the engine's `error` field to be display-only text, so that I show it but never branch on its contents.
14. As a Skin, I want the run diagnostics (tokens, costs, chunk count) grouped together, so that the debug display takes one object instead of eight arguments.
15. As a Skin, I want to measure the document's character/token estimate myself, so that the engine isn't burdened with presentation arithmetic (the skin holds the document; it can call `len()`).
16. As a Skin, I want one call to list saved summaries that returns loaded results, so that I render a menu without knowing the cache is a folder of JSON files.
17. As a Skin, I want the available summary depth levels surfaced to me, so that I can present the depth menu without importing the engine where the prompt prose lives.
18. As a Test Author, I want the engine callable with no progress callback, so that tests exercise the logic without any progress machinery.
19. As a Test Author, I want each module testable behind its public interface, so that I assert on returned data, not on captured stdout.
20. As a Test Author, I want the cache path derived in one place, so that a test about storage layout has a single function to target.
21. As a Maintainer, I want each constant to live with its only consumer, so that prompt text sits beside the code that uses it and only tunable knobs live in `config.py`.
22. As a Maintainer, I want the cache filepath built in exactly one helper, so that changing the storage scheme touches one line, not four.
23. As a Maintainer, I want all knowledge that "the cache is JSON files in `summaries/`" confined to `persistence.py`, so that moving to SQLite or one combined file changes only that module.
24. As a Maintainer, I want the refactor delivered bottom-up, one module per branch/PR, so that the CLI still runs after every single step.
25. As a Maintainer, I want the seam-5 per-depth fix kept out of this refactor, so that I never fix two things at once and can review the split on its own.
26. As a Maintainer, I want annotated public signatures on module boundaries and dataclass fields, so that the contracts between modules are written down.
27. As a Frontend Developer, I want network retries and warnings inside the API client to not print, so that importing the client transitively (via the engine) never dumps to a console.

---

## Implementation Decisions

Every decision below is carried verbatim from the locked design record, with its reasoning.

### Architecture & layout

- **Separate files, not one labelled file.** "Module" in Python means file. One file with banner comments is a labelled monolith, not a split — the boundary is only real when crossing it requires a visible `import`. The import block becomes the enforcement.
- **Flat files in a plain `src/` directory, no package.** The modules live together under `src/` (step 0a, #17) to keep the repo root clean, but `src/` is a plain directory — no `__init__.py`. Because Python (`python src/cli.py`) and Streamlit (`streamlit run src/app.py`) put the run script's own directory on `sys.path`, siblings still import with no ceremony (`import engine`). A true package (imported as `document_analyzer.engine`, run via `python -m`) would only pay off if this became a library other projects install — not today (YAGNI). Tests live at the repo root under `tests/` (step 0b, #18), with a pytest path hint pointing at `src/`. (This refines the original "flat in the repo root" wording in the design record: the no-package decision stands; only the location moves to `src/`.)
- **No `utils.py`.** The old "Utilities" section paired two unrelated functions; `get_document` goes to `extraction.py`, `chunk_document` goes to `engine.py` (it feeds the engine).
- **No `io.py`. I/O is a category, not a layer.** I/O is a *rim* around the pure core ("functional core, imperative shell"), not a band slotted between skin and service. Network I/O must sit *below* the engine (the engine calls it); file/terminal I/O sit at the *top* (the skin). One module holding both would be imported by both the bottom and the top of the stack, dragging high-level code down next to raw plumbing — grouping by category when layering demands grouping by distance from the core. The four kinds get four homes, each next to its consumer: network → `claude_client.py` (below the engine); disk cache → `persistence.py` (beside services); file reading → `extraction.py` (at the skin's edge); terminal/widgets → the skin itself (the top).
- **Naming.** `claude_client.py` over `api.py` (says what it talks to); `cli.py` over `main.py` (pairs with future `app.py`); `services.py` over `workflows.py`/`analyzer.py` (it holds a summary service + a Q&A service — "services" is standard vocabulary; the repo name already says "document analyzer", so the file needn't repeat it).

### Constants placement

- **A constant earns a `config.py` slot only if a human might tune it** (the knobs: model, pricing, chunk size, chars-per-token, thinking budget). **Prompt text is behaviour-code that happens to be prose** and lives with its only consumer: `SUMMARY_PROMPTS` + the reduce/structured-output instruction strings + `SUMMARY_OUTPUT_CONFIG` in the engine; `QANDA_PROMPTS` in services (with the `QA` class); `API_ERRORS` in `claude_client`.

### Progress without printing — callback

- The engine reports per-chunk progress but cannot know whether a terminal, a browser, or nothing is listening. Decision: an optional **callback** `on_progress(stage, current, total)`. Default does nothing (tests pass nothing).
- **`stage` is data, not prose.** Two stages total: a counting stage (the map step) and a separate "final stretch" reduce stage (a distinct summary step, not part of the count). The engine emits data; the skin turns data into words. Passing a ready-made English string was rejected — it drags presentation back into the core.
- The engine **always** fires the callback — the single-chunk path reports as `1/1`, no silent special-case.
- **Resume needs no new machinery:** the first event simply arrives as e.g. `current=6, total=12`; the skin renders "resuming from 6" if it cares.
- On a cache hit the engine never runs, so the callback never fires — correct, not a gap.
- Generator/yield was rejected because it inverts control (the caller would drive the engine's loop). The engine stays a plain function you call and get an answer from; the callback is one optional interface the caller opts into.

### Errors and return shape — errors-as-data + one `SummaryResult` dataclass

- The engine ends three ways: **complete**, **partial** (some chunks summarised, reduce failed — carries resume state), **failed** (nothing produced).
- **Errors as data, not exceptions.** The partial case is not an error — it's a *degraded result carrying state* (the chunk summaries that must be saved for resume), so a data channel already has to exist; routing total failure through the same channel means skins learn ONE protocol. API failure on a 40-chunk document is "weather", not exceptional — reserve real exceptions for programmer errors (e.g. an unknown `prompt_type`).
- **Named weakness + standing mitigation:** errors-as-data fails *silently* if a skin ignores the status field. Mitigation is a standing duty on every skin: **check `status` first, with a loud default branch** — anything not `complete` shows the carried error; an unrecognised status must complain, never silently render nothing.
- **`error` is display-only.** It equals `str(e)` — diagnostic text the engine *forwards*, not prose it *composes*. Skins may display it but must never branch on its contents. (The day a skin needs to branch — e.g. "retry button only for rate limits" — is the day `error` grows a machine-readable kind field. Not before.)
- **Return shape = one `SummaryResult` dataclass** (defined in `engine.py`). Dict was rejected (stringly-typed, silent KeyErrors, no written contract); namedtuple was rejected (still positionally unpackable → lets seam 3 relapse). Dataclass gives named fields, dot access, defaults, is not positionally unpackable, and is the written contract seam 3 asked for.
- **One class for all three statuses** (status as discriminator; unused fields default to `None`), NOT three classes per status. Three classes is type-system theatre in Python — callers would `isinstance`-branch instead of reading a field.

`SummaryResult` fields (annotated; lives in `engine.py`):

```python
@dataclass
class SummaryStats:
    input_tokens: int = 0
    output_tokens: int = 0
    input_cost: float = 0.0
    output_cost: float = 0.0
    chunks: int = 0

@dataclass
class SummaryResult:
    status: str                              # "complete" | "partial" | "failed" — checked first by every caller
    error: str | None = None                 # str(e); None on success; display-only
    summary: str | None = None
    tldr: str | None = None
    key_terms: list[str] | None = None
    chunk_summaries: list[str] | None = None # None unless partial; the resume payload, consumed only by services
    stats: SummaryStats = field(default_factory=SummaryStats)
    from_cache: bool = False                 # set True when services builds the result from a cache hit
```

- `summary`/`tldr`/`key_terms` are the payload, used individually everywhere; kept **flat at top level**, not nested.
- `chunk_summaries` shares a name-prefix with `chunks` but has zero functional overlap; it is consumed only by services, to save for resume.
- The nested **stats** group travels as a convoy (today `display_debug_info` takes ~8 args, six of which are this group). `chunks` (= `len(chunked_document)`) is pure diagnostics and joins the group.
- **What leaves the engine and moves to the skin:** `char_count` / `estimated_tokens`. The skin holds the document and can measure `len()` itself.
- The exact spelling of the dataclass shape above is the *decision*; field names/defaults are the implementer's to finalise as long as the contract holds.

### The middle layer (`services.py`)

- `get_or_generate_summary` is **orchestration, not summarisation**: check cache → else resume partials → else generate → persist → return. It **always returns a `SummaryResult`, including cache hits** — on a hit it builds one from the saved file (`status="complete"`, empty `stats`, `from_cache=True`). One currency for every code path.
- **Input boundary: services takes already-extracted text + the filename (as cache identity), NOT a file path.** File reading is I/O like print/input — push it to the edge. The CLI has a path; Streamlit has an in-memory upload. Each skin extracts however it can; services downstream just receives text.
- **`QA` becomes a stateful class** — the project's first class. Conversation history has no home but memory; an object must guard it between calls. Constructed once with `summary` + `prompt_type`; owns `messages` as an attribute; exposes one `ask` method (errors-as-data; a failed `ask` leaves history untouched, like today's `messages.pop()`). **No `clear` method** — "quit" means the skin drops the object; the next session constructs a fresh one. The death of the object is the clearing. (Contrast persistence, whose source of truth is the file on disk — a class there would create a second truth to sync, so persistence stays plain functions.)

### Persistence specifics

- **Seam 4:** extract the cache filepath (`summaries/{name}_summary.json`, currently rebuilt in all four functions) into one `_summary_path(filename)` helper.
- **`browse_flow` leak fix:** today the CLI does its own `os.listdir("summaries")` + `json.load` + filter — the skin knows the cache is "a folder of JSON files". Move that into persistence as `list_saved_summaries()`, returning the loaded results (the completed-summary records, skipping partial-only files); the CLI just renders a menu from them. Proof it's right: the day storage moves to SQLite or one combined file, only `persistence.py` changes.
- Persistence stays **plain functions**, not a class — its source of truth is the file, so there is no in-memory state needing an owning object.

### Public contracts (annotated signatures the issues reference)

Names keep the existing snake_case convention — this refactor **moves** code, it does not rename it. New names follow the same convention.

```python
# config.py — constants only, no functions

# extraction.py
def get_document(filename: str) -> str: ...
#   unchanged behaviour; raises ValueError for unsupported type;
#   FileNotFoundError / pymupdf.FileNotFoundError / PackageNotFoundError propagate to the skin (current behaviour)

# claude_client.py
def get_claude_response(client, messages, system_prompt, temperature=1.0,
                        model=MODEL, max_tokens=4096,
                        output_config=None, thinking_budget=None): ...   # returns the raw response object
def calculate_response_cost(response, model=MODEL) -> tuple[float, float]: ...  # (input_cost, output_cost)
API_ERRORS: tuple  # the caught anthropic exception classes

# engine.py
def chunk_document(document: str, chunk_size: int) -> list[str]: ...
def summarise_document(client, document: str, prompt_type: str, extended_thinking: bool,
                       saved_chunk_summaries: list[str] | None = None,
                       on_progress=None) -> SummaryResult: ...
#   on_progress(stage, current, total), optional, default no-op; always fired (single chunk = 1/1)

# persistence.py
def _summary_path(filename: str) -> str: ...                  # new — seam 4
def save_summary(filename, summary, prompt_type, tldr=None, key_terms=None) -> None: ...
def load_summary(filename) -> dict | None: ...
def save_partial_summaries(filename, chunk_summaries, prompt_type) -> None: ...
def clear_partial_summaries(filename) -> None: ...
def list_saved_summaries() -> list[dict]: ...                 # new — returns completed-summary records

# services.py
def get_or_generate_summary(client, filename: str, document: str, prompt_type: str,
                            extended_thinking: bool, on_progress=None) -> SummaryResult: ...
#   the `debug` parameter is dropped — debug display is a skin concern now

class QA:
    def __init__(self, client, summary: str, prompt_type: str): ...   # owns self.messages
    def ask(self, question: str, extended_thinking: bool = False): ...  # errors-as-data; failed ask leaves history untouched
QANDA_PROMPTS: dict
```

### Open sub-decisions (design record was silent; recommended resolutions, flagged for confirmation)

These do not overturn any locked decision — they resolve access paths the record left implicit. Each is called out again in the relevant issue.

- **A. Print-free `claude_client`.** `get_claude_response` currently prints two things: a temperature warning (when `thinking` is enabled with `temperature != 1.0` — effectively a dead branch, since every current caller leaves temperature at 1.0) and rate-limit retry messages. To meet the "no module prints except `cli.py`" criterion, both must leave the client. **Recommended:** drop the temperature warning (set silently); make retries silent (the retry behaviour itself is unchanged). The client gains no progress callback — it sits below the engine. Confirm.
- **B. Skin's doorway to depth levels.** `cli.get_prompt_type` needs the ordered depth-level identifiers (`simple`/`in_depth`/`expert`), but the prompt prose (`SUMMARY_PROMPTS`) lives in `engine` and `cli` does not import `engine` (its imports are `services`, `extraction`, `config`). **Recommended:** `services` surfaces the available depth identifiers (a small accessor or constant) so the skin stays off the engine. Confirm.
- **C. Skin's doorway to the saved-summaries list.** `list_saved_summaries` lives in `persistence`, but `cli`'s import list (`services`, `extraction`, `config`) does not include `persistence`, and the diagram draws no `cli → persistence` arrow. **Recommended:** the skin reaches the saved-summaries list through `services` (the facade over engine + persistence), keeping `cli`'s imports as the record lists them. Alternative: allow `cli` to import `persistence` directly as a sibling edge. Confirm which.

---

## Testing Decisions

- **What makes a good test here:** assert on *external behaviour through the public interface*, not implementation details. Because the core becomes print-free and returns data, tests assert on returned `SummaryResult` fields and on persistence side-effects — not on captured stdout. This is the payoff of the split: the logic is now testable without a terminal.
- **Modules to test (highest value first):**
  - `engine.summarise_document` — single-chunk → `status="complete"` with payload + stats; multi-chunk happy path; map fails with zero chunks done → `status="failed"`; reduce fails after chunks done → `status="partial"` carrying `chunk_summaries`; `on_progress` fires with correct `(stage, current, total)` including `1/1` on the single-chunk path; resume from `saved_chunk_summaries` starts at the right index. The Anthropic client is the seam to fake/stub.
  - `persistence` — `_summary_path` builds the expected path; `save_summary` creates vs updates without clobbering other depths; `save`/`clear` partials round-trip; `list_saved_summaries` returns completed records and skips partial-only files. Use a temp `summaries/` dir.
  - `services.get_or_generate_summary` — cache hit returns `from_cache=True` with `status="complete"` and the same shape as a fresh run; miss generates and persists; partial result is saved for resume and not cached as complete (preserve the quirk — see below); `QA.ask` appends to history on success and leaves history untouched on a failed ask.
  - `extraction.get_document` — each supported extension returns text; unsupported raises `ValueError`. Fixtures: the existing `test_doc.*` files in the repo root.
- **Prior art:** there is no existing test suite in the repo today; these are the first tests. Keep them plain and fixture-light, matching the project's simple style. (Per project convention, tests ship with the code — each module's issue includes its tests in the same PR.)

---

## Out of Scope

- **The partial-map / reduce-success caching quirk is permanent and preserved untouched.** An incomplete summary can be cached as complete; this is a deliberate, standing decision. The refactor must not "fix" it, and no test should assert it away — tests should pin today's behaviour.
- **Seam 5 — per-depth `tldr`/`key_terms` — is a separate, lowest-priority follow-up.** `tldr`/`key_terms` are genuinely a property of the document-at-a-given-depth, so storing them once-per-document is wrong and *will* be fixed — but only after the entire split lands, as its own issue. This refactor **preserves today's once-per-document behaviour exactly**. Do not fix two things at once.
- **The Streamlit `app.py` itself.** This PRD delivers the print-free core that makes `app.py` possible; building the frontend is the Week-8 work (`docs/week8_frontend_prd.md`), not this refactor.
- **Renaming.** Functions keep their existing names; this is a move, not a rename.
- **Any change to summarisation quality, prompts, pricing, or model selection.** Behaviour is held constant.
- **Finished implementation code.** This PRD and its issues are specs. All application code is written by the developer (deliberate skills exercise); Claude reviews and critiques.

---

## Build order

Bottom-up — leaves first, so the code still runs after each step. Delivered as a **staircase of small, night-sized PRs**: each conceptual change gets its own step, and mechanical *moves* are kept separate from *contract changes*. Every step is blocked only by the one before it (they all edit the shrinking `document_analyzer.py`, so they merge in sequence). The engine's print-free goal lands at step 4d, not each step — the intermediate engine steps deliberately keep their prints/Rich while the CLI keeps running.

Two layout-prep steps establish the structure first; then the bottom-up extraction staircase. Tracked as GitHub issues on `hshaw41/document-analyzer` (the two prep steps are #17–#18 by creation order, but sit first in the dependency chain):

| Step | Work | Issue |
|---|---|---|
| 0a | `src/` — move `document_analyzer.py` into a plain `src/` directory; set the run command + pytest path hint | #17 |
| 0b | `tests/` — move the `test_doc.*` fixtures into `tests/fixtures/`; commit the tiny one | #18 |
| 1 | `config.py` — extract the knobs (born in `src/`) | #2 |
| 2 | `extraction.py` — move `get_document` | #3 |
| 3 | `claude_client.py` — move `get_claude_response`, `calculate_response_cost`, `API_ERRORS`; make print-free (sub-decision A) | #4 |
| 4a | `engine.py` — move `chunk_document` | #5 |
| 4b | `engine.py` — move `summarise_document` + its prompt/output constants (pure move; still 9-tuple, still prints/Rich) | #6 |
| 4c | `engine.py` — introduce `SummaryResult` + `SummaryStats`; convert to errors-as-data; remove engine failure prints; caller goes status-first | #7 |
| 4d | `engine.py` — replace Rich progress with the `on_progress` callback → engine fully print/input/Rich-free | #8 |
| 5a | `persistence.py` — move the four cache functions; extract `_summary_path` (seam 4) | #9 |
| 5b | `persistence.py` — add `list_saved_summaries` | #10 |
| 6a | `services.py` — move `get_or_generate_summary` (returns `SummaryResult` incl. cache hits, takes text not path, drops `debug`, accepts `on_progress`) | #11 |
| 6b | `services.py` — surface depth identifiers (B) + saved-summaries via the facade (C) | #12 |
| 6c | `services.py` — build the `QA` class; move `QANDA_PROMPTS` in | #13 |
| 7a | `cli.py` — wire `browse_flow` to `list_saved_summaries` via services | #14 |
| 7b | `cli.py` — relocate all skin code into `cli.py`; make it the entry point; remove/shim `document_analyzer.py` | #15 |

Then, separately and lowest priority: the seam-5 per-depth `tldr`/`key_terms` fix → **#16** (label `bug`).

Each step's acceptance: the CLI still runs end-to-end and behaves as before; nothing in the newly-created module prints or takes input except `cli.py` (the engine reaches that state at 4d).

---

## Further Notes

- **PRD location:** committed at repo root as `REFACTOR_PRD.md` because `docs/` and `research/` are gitignored. Flagged for confirmation (top of this file).
- **Issues are specs for a human, not AFK agents.** The developer reviews each issue and opens a PR against it as he implements. The `to-issues` skill's default `ready-for-agent` label is therefore *not* applied; issues are tracked by build order, not agent-readiness. The 2 layout-prep steps + 14 split steps + the seam-5 follow-up are live as **#2–#18** on `hshaw41/document-analyzer` (label `refactor`; #16 is `bug`). Dependency order: #17 → #18 → #2 → #3 → … → #15 → #16.
- **Annotations are welcome** in this repo where they pay rent — dataclass fields and public signatures on module boundaries (as used above). The global "no annotations" default does not apply here.
- **Posture:** the old week-7 deadline is dead; polish (e.g. seam 4, the browse-flow fix) is in-bounds, but keep total duration bounded.
- **Deferred:** an `/improve-codebase-architecture` depth audit is queued for *after* the split lands in code — explicitly not part of this work.
