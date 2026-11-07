"""Search inside uploaded documents and extract tables."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from . import BaseTool, register_tool


def _pdf_pages(path: Path) -> list[str]:
    from PyPDF2 import PdfReader

    return [(p.extract_text() or "") for p in PdfReader(str(path)).pages]


def _docx_text(path: Path) -> list[str]:
    from docx import Document

    doc = Document(str(path))
    return ["\n".join(p.text for p in doc.paragraphs)]


def _pptx_slides(path: Path) -> list[str]:
    from pptx import Presentation

    out = []
    for slide in Presentation(str(path)).slides:
        out.append("\n".join(s.text_frame.text for s in slide.shapes if getattr(s, "has_text_frame", False)))
    return out


def load_document_units(path: Path) -> tuple[list[str], str]:
    """Return (units, unit_label) where units are pages/slides/the whole text."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _pdf_pages(path), "page"
    if ext == ".docx":
        return _docx_text(path), "document"
    if ext == ".pptx":
        return _pptx_slides(path), "slide"
    text = path.read_text(encoding="utf-8", errors="replace")
    # Split markdown/text on headings so hits have a section label.
    return [text], "document"


@register_tool("document_search")
class DocumentSearchTool(BaseTool):
    name = "document_search"
    description = (
        "Search inside one or all uploaded documents (PDF, DOCX, PPTX, TXT, MD, code) for a keyword, phrase or regex "
        "and return the matching passages with page/slide numbers and line context — use it to quote exactly where a "
        "document says something. Also: operation=outline lists headings/sections; operation=tables extracts tables "
        "from DOCX/XLSX/HTML/Markdown files as JSON rows."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["search", "outline", "tables"], "default": "search"},
            "query": {"type": "string", "description": "Keyword, phrase or regex (search)."},
            "filename": {"type": "string", "description": "Uploaded file name; omit to search all uploaded documents."},
            "regex": {"type": "boolean", "description": "Treat query as a regular expression.", "default": False},
            "context_chars": {"type": "integer", "description": "Characters of context around each hit (default 240).", "default": 240},
            "max_hits": {"type": "integer", "description": "Maximum hits to return (default 20).", "default": 20},
        },
        "required": [],
    }

    SEARCHABLE = {".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown", ".csv", ".json", ".py", ".js", ".ts", ".html", ".htm", ".xml", ".yaml", ".yml", ".log", ".rst", ".tex"}

    async def execute(self, **kwargs) -> str:
        try:
            return await asyncio.to_thread(self._run, kwargs)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _files(self, filename: str | None) -> list[Path]:
        base = self.upload_dir()
        if filename:
            p = base / Path(filename).name
            if not p.exists():
                raise FileNotFoundError(f"{filename} is not uploaded")
            return [p]
        return sorted(p for p in base.iterdir() if p.is_file() and p.suffix.lower() in self.SEARCHABLE)

    def _run(self, k: dict) -> str:
        op = k.get("operation") or "search"
        files = self._files(k.get("filename"))
        if op == "tables":
            return json.dumps({"files": [self._tables(p) for p in files]}, default=str)
        if op == "outline":
            return json.dumps({"files": [self._outline(p) for p in files]}, default=str)
        query = (k.get("query") or "").strip()
        if not query:
            return json.dumps({"error": "query is required for search"})
        pattern = re.compile(query if k.get("regex") else re.escape(query), re.I)
        ctx = max(40, int(k.get("context_chars") or 240))
        max_hits = max(1, int(k.get("max_hits") or 20))
        hits, scanned = [], 0
        for p in files:
            try:
                units, label = load_document_units(p)
            except Exception as e:
                hits.append({"file": p.name, "error": f"could not read: {e}"})
                continue
            scanned += 1
            for idx, text in enumerate(units, 1):
                for m in pattern.finditer(text):
                    start, end = max(0, m.start() - ctx), min(len(text), m.end() + ctx)
                    line_no = text.count("\n", 0, m.start()) + 1
                    hits.append({"file": p.name, label: idx if label != "document" else None, "line": line_no,
                                 "match": m.group(0), "context": re.sub(r"\s+", " ", text[start:end]).strip()})
                    if len(hits) >= max_hits:
                        break
                if len(hits) >= max_hits:
                    break
            if len(hits) >= max_hits:
                break
        return json.dumps({"query": query, "files_searched": scanned, "hits": hits, "truncated": len(hits) >= max_hits}, default=str)

    def _outline(self, p: Path) -> dict:
        units, label = load_document_units(p)
        headings = []
        for idx, text in enumerate(units, 1):
            for line in text.splitlines():
                s = line.strip()
                if not s or len(s) > 120:
                    continue
                if re.match(r"^(#{1,6}\s|\d+(\.\d+)*\s+[A-Z]|[A-Z][A-Z \-]{4,}$|(Chapter|Section|Part)\s+\d+)", s):
                    headings.append({label: idx, "heading": s.lstrip("# ")})
        return {"file": p.name, "units": len(units), "unit": label, "headings": headings[:200]}

    def _tables(self, p: Path) -> dict:
        ext = p.suffix.lower()
        tables = []
        if ext == ".docx":
            from docx import Document

            for i, t in enumerate(Document(str(p)).tables, 1):
                rows = [[c.text.strip() for c in r.cells] for r in t.rows]
                tables.append({"index": i, "rows": rows[:200], "n_rows": len(rows)})
        elif ext in (".xlsx", ".xlsm", ".xls"):
            import pandas as pd

            for name, df in pd.read_excel(p, sheet_name=None, nrows=200).items():
                tables.append({"sheet": name, "columns": [str(c) for c in df.columns], "rows": json.loads(df.head(200).to_json(orient="values")), "n_rows": len(df)})
        elif ext in (".html", ".htm", ".md", ".markdown"):
            import pandas as pd

            text = p.read_text(encoding="utf-8", errors="replace")
            if ext in (".md", ".markdown"):
                for block in re.findall(r"((?:^\|.*\|\s*$\n?){2,})", text, re.M):
                    lines = [l for l in block.strip().splitlines() if not re.match(r"^\|?\s*:?-{2,}", l)]
                    rows = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines]
                    tables.append({"columns": rows[0], "rows": rows[1:200], "n_rows": len(rows) - 1})
            else:
                try:
                    for i, df in enumerate(pd.read_html(text), 1):
                        tables.append({"index": i, "columns": [str(c) for c in df.columns], "rows": json.loads(df.head(200).to_json(orient="values")), "n_rows": len(df)})
                except ValueError:
                    pass
        elif ext == ".csv":
            import pandas as pd

            df = pd.read_csv(p, nrows=200)
            tables.append({"columns": [str(c) for c in df.columns], "rows": json.loads(df.to_json(orient="values")), "n_rows": len(df)})
        else:
            return {"file": p.name, "tables": [], "note": f"table extraction not supported for {ext}; use file_reader or code_executor"}
        return {"file": p.name, "tables": tables}
