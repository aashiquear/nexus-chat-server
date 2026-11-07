# Nexus Chat tool strategy (November 2026)

This note records what mainstream assistants ship as tools, which of those Nexus
covers, which gaps a *local* chatbot can fill that hosted products mostly do not,
and how Nexus keeps a large catalog cheap to use through **dynamic tool discovery**.

## 1. What modern assistants expose as tools

| Capability | ChatGPT | Claude | Gemini | Open WebUI / local stacks | Nexus |
|---|---|---|---|---|---|
| Web search with citations | yes | yes (web search) | yes (Google grounding) | yes (SearXNG/APIs) | `web_search` |
| Fetch / read a URL or PDF | browsing | web fetch | URL context | tools | `web_browser` |
| Sandboxed code (Python/bash) | Code Interpreter / data analysis | code execution | code execution | code interpreter | `code_executor` (10 languages, package install, artifacts) |
| File & document analysis | file uploads, file search | files API | files | knowledge / RAG | RAG + `file_reader`, `csv_viewer`, `document_search`, `data_sql` |
| Image understanding | yes | yes | yes | vision models | image attachments + `image_synthesizer` |
| Image generation | yes | — | yes | ComfyUI/diffusers | `file_generator` (png via diffusion) |
| Memory across chats | yes | memory tool (client-side) | yes | memory | `memory` |
| Connectors / apps / MCP | apps & connectors, desktop MCP | MCP connectors, skills | Workspace extensions | MCP, OpenAPI tool servers | MCP marketplace (registry, import, stdio/HTTP) |
| Scheduled tasks | yes | yes | — | — | not yet (see roadmap) |
| Computer / browser use | agent mode | computer use | — | — | not yet (sandbox + web_browser cover reading) |
| Canvas / artifacts | canvas | artifacts | canvas | — | canvas panel (plots, CSV, PDF, HTML, code) |

Sources consulted: OpenAI help/changelog summaries for 2026 (tools menu, agent mode,
apps & connectors, scheduled tasks), Anthropic platform docs (web search, web fetch,
code execution, memory, computer use, skills), Open WebUI docs (native function
calling default, built-in tools, community tool catalog, MCP/OpenAPI servers).

## 2. Gaps worth filling locally (and now implemented)

Hosted assistants lean on the model for arithmetic, dates and units, and keep
"personal state" inside their cloud. A local, open-weight chatbot benefits from
deterministic, offline tools and from owning that state:

| Tool | Why it matters | Status |
|---|---|---|
| `calculator` (safe AST parser) | exact arithmetic without `eval` | replaced |
| `math_solver` (SymPy) | algebra/calculus/matrices that 7–30B models get wrong | new |
| `unit_converter` (pint) | dimensionally checked conversions | new |
| `statistics_analyze` | describe/correlation/regression/t-test on numbers or columns | new |
| `datetime_calculate` | business days, holidays, timezone math | new |
| `currency_convert` | dated ECB rates, citable | new |
| `text_utilities`, `json_tools` | diffs, regex, hashes, schema validation | new |
| `document_search` | quote *where* a document says something (page numbers), extract tables | new |
| `data_sql` (DuckDB) | SQL across every uploaded CSV/Parquet/JSON/Excel | new |
| `academic_search` | arXiv + Crossref + OpenAlex, DOI → BibTeX | new |
| `change_tracker` | snapshot a page/file and report what changed | new |
| `memory` | long-term facts/preferences, searchable, per user on the server | new |
| `tasks_manage` | persistent to-do list with due dates | new |
| `decision_compare` | weighted criteria matrix + sensitivity analysis, rendered as table + chart | new |
| `discover_tools` | lets the model pull in tools on demand | new |

Not yet built (candidates): scheduled workflows (`workflow.compose`), answer
verification pass (`answer.verify`), audio transcription (whisper.cpp), browser
automation, local filesystem browsing outside uploads. MCP servers already cover
several of these (filesystem, GitHub, browser, Home Assistant …).

## 3. Dynamic tool discovery

Sending 25+ tool schemas to a local model on every request wastes context and
hurts tool selection. Nexus therefore:

1. Keeps a **catalog** (`backend/tools/catalog.py`) with metadata only — module
   path, category, tags, risk, permission, core flag. Tool modules are imported
   lazily on first use, so enabling every tool costs nothing at startup
   (`import backend.main` loads neither SymPy nor DuckDB nor pandas).
2. Ranks the catalog plus connected MCP tools against the latest user message
   with a **hybrid scorer** (keyword/tag overlap + ONNX MiniLM embedding
   similarity when available) and attaches: pinned tools, the small *core* set
   (calculator, date/time, web search, web browser, code executor, memory,
   discover_tools) and the top matches (default 8).
3. Exposes **`discover_tools`** so the model can request more tools mid-turn; the
   orchestrator attaches them for the next round and the UI shows what was added.
4. Lets users switch to **Manual** mode (classic tick-boxes) and pin tools in
   Auto mode. Policies live under `tool_selection` in `settings.yaml`.

Measured on the 15-query routing check in `scratchpad/smoke_tools.py`: 15/15
queries ranked the intended tool in the top 3 with keyword scoring alone.

## 4. Adding a tool

1. Create `backend/tools/<module>.py`, subclass `BaseTool`, decorate with
   `@register_tool("id")`, return JSON strings (`csv_preview`, `figure_json`,
   `downloadable`, `plot_image` payloads render automatically in the UI).
2. Add a catalog entry (module, category, tags, risk, `core` if universally useful).
3. Add a `tools.<id>` entry in `config/settings.yaml`.
4. Use `self.upload_dir()`, `self.ctx.files_dir`, `self.ctx.state_dir` for paths so
   the multi-user server edition scopes them per user automatically.
