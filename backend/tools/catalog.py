"""
Tool catalog — static metadata for every built-in tool.

The catalog is what makes *dynamic tool discovery* cheap: the backend can
rank and describe tools without importing their modules (some pull in
SymPy, DuckDB, pandas …). A tool's module is imported lazily the first
time the tool is instantiated, so a Nexus instance with 30 tools starts as
fast as one with 5, and the model only ever sees the handful of tool
schemas that are relevant to the current request.

Each entry:
    id          tool name (matches @register_tool and settings.yaml)
    module      dotted module path that registers the tool
    category    coarse grouping shown in the sidebar
    pack        optional capability pack (tools in a pack toggle together)
    summary     one line used for ranking + the sidebar
    tags        keywords used by the discovery ranker
    risk        low | medium | high  (high = modifies state / spends money)
    permission  auto    — may be auto-selected by the discovery router
                pinned  — only used when the user pins it (never auto)
    offline     works without internet
    core        always attached in auto mode (cheap, universally useful)
"""

from __future__ import annotations

CATALOG: list[dict] = [
    # ---------------- Core utilities ----------------
    {"id": "calculator", "module": "backend.tools.math_tools", "category": "Utilities", "summary": "Exact arithmetic, percentages, powers, roots, trig and log with a safe parser",
     "tags": ["math", "arithmetic", "calculate", "percent", "sum", "multiply", "divide", "sqrt", "number", "compute", "interest", "tax", "tip"], "risk": "low", "permission": "auto", "offline": True, "core": True},
    {"id": "math_solver", "module": "backend.tools.math_tools", "category": "Utilities", "summary": "Symbolic math with SymPy: solve equations, simplify, differentiate, integrate, limits, matrices, factor",
     "tags": ["algebra", "equation", "solve", "derivative", "integral", "calculus", "matrix", "simplify", "factor", "polynomial", "limit", "symbolic", "x"], "risk": "low", "permission": "auto", "offline": True},
    {"id": "unit_converter", "module": "backend.tools.math_tools", "category": "Utilities", "summary": "Convert physical units (length, mass, temperature, energy, speed, data size, pressure…)",
     "tags": ["convert", "units", "km", "miles", "kg", "pounds", "celsius", "fahrenheit", "liters", "gallons", "inches", "cm", "mph", "kwh", "bytes"], "risk": "low", "permission": "auto", "offline": True},
    {"id": "statistics_analyze", "module": "backend.tools.math_tools", "category": "Data", "summary": "Descriptive statistics, correlation, linear regression, t-test/chi-square on numbers or uploaded columns",
     "tags": ["statistics", "mean", "median", "std", "correlation", "regression", "t-test", "p-value", "distribution", "outliers", "significance", "variance"], "risk": "low", "permission": "auto", "offline": True},
    {"id": "datetime_tool", "module": "backend.tools.builtin", "category": "Utilities", "summary": "Current date, time and timezone",
     "tags": ["date", "time", "today", "now", "timezone", "clock"], "risk": "low", "permission": "auto", "offline": True, "core": True},
    {"id": "datetime_calculate", "module": "backend.tools.time_tools", "category": "Utilities", "summary": "Date math: differences, add/subtract periods, business days, week numbers, public holidays, timezone conversion",
     "tags": ["days between", "deadline", "business days", "working days", "holiday", "weeks", "age", "timezone", "convert time", "schedule", "countdown", "duration"], "risk": "low", "permission": "auto", "offline": True},
    {"id": "currency_convert", "module": "backend.tools.time_tools", "category": "Utilities", "summary": "Currency conversion with dated exchange rates (ECB via frankfurter.app)",
     "tags": ["currency", "exchange rate", "usd", "eur", "gbp", "jpy", "convert money", "forex", "dollars", "euros"], "risk": "low", "permission": "auto", "offline": False},
    {"id": "text_utilities", "module": "backend.tools.text_tools", "category": "Utilities", "summary": "Word/char counts, diff two texts, regex find/replace, hashes, base64/URL encode, case/slug, random values & UUIDs",
     "tags": ["word count", "diff", "compare text", "regex", "hash", "sha256", "md5", "base64", "encode", "decode", "uuid", "random", "password", "slug", "uppercase"], "risk": "low", "permission": "auto", "offline": True},
    {"id": "json_tools", "module": "backend.tools.text_tools", "category": "Utilities", "summary": "Validate, pretty-print, query (path) and transform JSON; validate against a JSON Schema; convert JSON↔YAML/CSV",
     "tags": ["json", "yaml", "schema", "validate", "parse", "jq", "path", "format", "csv"], "risk": "low", "permission": "auto", "offline": True},

    # ---------------- Research / web ----------------
    {"id": "web_search", "module": "backend.tools.builtin", "category": "Research", "summary": "Search the web for current information (news, weather, prices, sports, definitions)",
     "tags": ["search", "latest", "news", "current", "today", "weather", "price", "who", "what is", "recent", "2025", "2026", "lookup"], "risk": "low", "permission": "auto", "offline": False, "core": True},
    {"id": "web_browser", "module": "backend.tools.data_tools", "category": "Research", "summary": "Open a URL and read the page text (HTML, PDF, JSON), find passages, follow links",
     "tags": ["open", "url", "link", "website", "read page", "article", "documentation", "fetch", "http", "scrape", "find in page"], "risk": "low", "permission": "auto", "offline": False, "core": True},
    {"id": "academic_search", "module": "backend.tools.research_tools", "category": "Research", "summary": "Search scholarly literature (arXiv, Crossref, OpenAlex): papers, abstracts, DOIs, citations, BibTeX",
     "tags": ["paper", "arxiv", "doi", "citation", "journal", "research", "study", "publication", "bibtex", "scholar", "literature", "preprint", "author"], "risk": "low", "permission": "auto", "offline": False},
    {"id": "change_tracker", "module": "backend.tools.research_tools", "category": "Research", "summary": "Snapshot a web page or uploaded file and report what changed since the last snapshot",
     "tags": ["track changes", "monitor", "diff", "what changed", "snapshot", "watch", "price drop", "release notes", "updated"], "risk": "low", "permission": "auto", "offline": False},

    # ---------------- Files & documents ----------------
    {"id": "file_reader", "module": "backend.tools.builtin", "category": "Files", "summary": "Read the text of an uploaded file (txt, md, pdf, docx, xlsx, code…)",
     "tags": ["read file", "uploaded", "document", "pdf", "docx", "open file", "contents", "attachment"], "risk": "low", "permission": "auto", "offline": True},
    {"id": "document_search", "module": "backend.tools.document_tools", "category": "Files", "summary": "Keyword/regex search inside uploaded documents with page numbers and surrounding context; extract tables from docx/xlsx/html",
     "tags": ["find in document", "search pdf", "page", "quote", "where does it say", "table", "extract table", "section", "grep"], "risk": "low", "permission": "auto", "offline": True},
    {"id": "csv_viewer", "module": "backend.tools.data_tools", "category": "Data", "summary": "Inspect CSV/TSV/Excel uploads: columns, types, preview rows, statistics, interactive table",
     "tags": ["csv", "excel", "xlsx", "spreadsheet", "table", "columns", "rows", "dataset", "preview", "data"], "risk": "low", "permission": "auto", "offline": True},
    {"id": "data_sql", "module": "backend.tools.data_tools", "category": "Data", "summary": "Run SQL (DuckDB) over uploaded CSV/TSV/Parquet/JSON/Excel files — joins, group by, window functions; results shown as a table",
     "tags": ["sql", "query", "group by", "join", "aggregate", "filter rows", "duckdb", "select", "count", "average per", "top 10", "dataset"], "risk": "low", "permission": "auto", "offline": True},
    {"id": "graph_plotter", "module": "backend.tools.graph_plotter", "category": "Data", "summary": "Plot charts (line, scatter, bar, pie, histogram, box, heatmap, regression…) from data or CSV columns",
     "tags": ["plot", "chart", "graph", "visualize", "histogram", "bar chart", "line chart", "scatter", "pie", "heatmap", "trend"], "risk": "low", "permission": "auto", "offline": True},
    {"id": "decision_compare", "module": "backend.tools.decision_tools", "category": "Data", "summary": "Structured decision analysis: weighted criteria matrix, hard constraints, ranking and sensitivity (which option wins if weights change)",
     "tags": ["compare", "decide", "which should i", "pros cons", "options", "ranking", "criteria", "best choice", "trade-off", "weighted", "vs", "versus", "alternatives"], "risk": "low", "permission": "auto", "offline": True},

    # ---------------- Create ----------------
    {"id": "code_executor", "module": "backend.tools.code_executor", "category": "Create", "summary": "Run Python/JS/TS/Bash/Java/C/C++/Go/Rust/R/HTML in the sandbox; installs packages; returns produced files",
     "tags": ["run code", "python", "script", "execute", "program", "simulate", "compute", "javascript", "java", "c++", "bash", "html page", "generate file", "test"], "risk": "medium", "permission": "auto", "offline": True, "core": True},
    {"id": "file_generator", "module": "backend.tools.file_generator", "category": "Create", "summary": "Generate downloadable PDF, DOCX, PPTX or PNG documents from content",
     "tags": ["pdf", "docx", "word document", "powerpoint", "pptx", "slides", "report", "export", "generate document", "image generate", "png"], "risk": "low", "permission": "auto", "offline": True},
    {"id": "svg_diagram", "module": "backend.tools.svg_diagram", "category": "Create", "summary": "Draw architecture / flow diagrams as SVG",
     "tags": ["diagram", "architecture", "flowchart", "svg", "draw", "boxes", "arrows", "schematic"], "risk": "low", "permission": "auto", "offline": True},
    {"id": "image_synthesizer", "module": "backend.tools.image_synthesizer", "category": "Create", "summary": "Describe an uploaded image with a local vision model (for non-vision chat models)",
     "tags": ["describe image", "photo", "picture", "what is in this image", "ocr", "vision", "caption"], "risk": "low", "permission": "auto", "offline": True},

    # ---------------- Personal ----------------
    {"id": "memory", "module": "backend.tools.memory_tools", "category": "Personal", "summary": "Long-term memory: save facts/preferences/decisions and recall them in later conversations",
     "tags": ["remember", "memory", "recall", "preference", "earlier", "last time", "what did i", "note that", "save for later", "my name", "forget"], "risk": "medium", "permission": "auto", "offline": True, "core": True},
    {"id": "tasks_manage", "module": "backend.tools.memory_tools", "category": "Personal", "summary": "Personal to-do list: add, list, complete, delete tasks with due dates and priorities",
     "tags": ["todo", "task", "reminder", "due", "deadline", "checklist", "done", "to-do list", "plan my week"], "risk": "medium", "permission": "auto", "offline": True},

    # ---------------- Meta ----------------
    {"id": "discover_tools", "module": "backend.tools.discovery", "category": "Meta", "summary": "Find and attach additional tools for the current task (when the attached set lacks a capability)",
     "tags": ["tool", "capability", "can you", "is there a way"], "risk": "low", "permission": "auto", "offline": True, "core": True, "hidden": True},
]

CATALOG_BY_ID: dict[str, dict] = {t["id"]: t for t in CATALOG}

CATEGORY_ORDER = ["Utilities", "Research", "Files", "Data", "Create", "Personal", "Meta", "Other"]
