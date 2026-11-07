"""Data & web tools: ``csv_viewer`` and ``web_browser``.

csv_viewer   — inspect CSV/TSV/Excel uploads: schema, preview rows and
               summary statistics. Returns a ``csv_preview`` payload the
               frontend renders as an interactive table (and can open in
               the canvas CSV viewer).
web_browser  — fetch a web page and return its readable text so the model
               can read sources that post-date its training data. Pairs
               with ``web_search`` (search → open result → answer).
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from . import BaseTool, register_tool

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# CSV viewer
# ----------------------------------------------------------------------
@register_tool("csv_viewer")
class CSVViewerTool(BaseTool):
    name = "csv_viewer"
    description = (
        "Inspect a tabular data file the user uploaded (CSV, TSV, TXT with "
        "delimiters, XLSX/XLS). Returns the column names and inferred types, a "
        "preview of rows, per-column statistics, and renders an interactive "
        "table for the user. Call this before analysing or plotting a dataset "
        "so you know the exact column names. Use `rows` to page through data, "
        "`columns` to restrict output, and `query` (pandas query syntax, e.g. "
        "\"price > 100 and region == 'EU'\") to filter."
    )
    parameters = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Name of the uploaded CSV/TSV/Excel file to inspect.",
            },
            "rows": {
                "type": "integer",
                "description": "How many rows to include in the preview (default 50, max 500).",
                "default": 50,
            },
            "offset": {
                "type": "integer",
                "description": "Row offset for paging through large files (default 0).",
                "default": 0,
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional subset of columns to return.",
            },
            "query": {
                "type": "string",
                "description": "Optional pandas DataFrame.query() filter expression.",
            },
            "sheet": {
                "type": "string",
                "description": "Sheet name for Excel workbooks (default: first sheet).",
            },
            "describe": {
                "type": "boolean",
                "description": "Include summary statistics (default true).",
                "default": True,
            },
        },
        "required": ["filename"],
    }

    MAX_ROWS = 500

    def _resolve(self, filename: str) -> Path | None:
        name = Path(filename).name
        candidates = [
            self.upload_dir() / name,
            Path(self.ctx.files_dir) / name,
            Path(self.ctx.downloads_dir) / name,
        ]
        for c in candidates:
            if c.exists() and c.is_file():
                return c
        return None

    async def execute(self, **kwargs) -> str:
        filename = kwargs.get("filename", "")
        path = self._resolve(filename)
        if path is None:
            return json.dumps({"error": f"File not found: {filename}. Ask the user to upload it first."})
        try:
            return await asyncio.to_thread(self._inspect, path, kwargs)
        except Exception as e:
            logger.exception("csv_viewer failed")
            return json.dumps({"error": f"Failed to read {filename}: {e}"})

    def _inspect(self, path: Path, kwargs: dict) -> str:
        import pandas as pd

        ext = path.suffix.lower()
        sheet = kwargs.get("sheet")
        sheets: list[str] = []
        if ext in (".xlsx", ".xlsm", ".xls"):
            xls = pd.ExcelFile(path)
            sheets = list(xls.sheet_names)
            df = xls.parse(sheet or sheets[0])
        elif ext == ".json":
            df = pd.read_json(path)
        elif ext == ".parquet":
            df = pd.read_parquet(path)
        else:
            sep = "\t" if ext in (".tsv", ".tab") else None
            df = pd.read_csv(path, sep=sep, engine="python", on_bad_lines="skip")

        total_rows = int(len(df))
        query = kwargs.get("query")
        if query:
            try:
                df = df.query(query)
            except Exception as e:
                return json.dumps({"error": f"Invalid query '{query}': {e}"})
        filtered_rows = int(len(df))

        columns = kwargs.get("columns")
        if columns:
            missing = [c for c in columns if c not in df.columns]
            if missing:
                return json.dumps({
                    "error": f"Unknown column(s): {missing}",
                    "available_columns": [str(c) for c in df.columns],
                })
            df = df[columns]

        n = max(1, min(int(kwargs.get("rows") or 50), self.MAX_ROWS))
        offset = max(0, int(kwargs.get("offset") or 0))
        page = df.iloc[offset: offset + n]

        def _cell(v):
            if v is None:
                return None
            try:
                if pd.isna(v):
                    return None
            except (TypeError, ValueError):
                pass
            if hasattr(v, "item"):
                try:
                    v = v.item()
                except Exception:
                    pass
            if isinstance(v, (int, float, bool, str)):
                return v
            return str(v)

        schema = []
        for col in df.columns:
            series = df[col]
            dtype = str(series.dtype)
            kind = (
                "number" if pd.api.types.is_numeric_dtype(series)
                else "datetime" if pd.api.types.is_datetime64_any_dtype(series)
                else "boolean" if pd.api.types.is_bool_dtype(series)
                else "text"
            )
            schema.append({
                "name": str(col),
                "dtype": dtype,
                "kind": kind,
                "nulls": int(series.isna().sum()),
                "unique": int(series.nunique(dropna=True)),
            })

        stats = None
        if kwargs.get("describe", True):
            try:
                desc = df.describe(include="all").transpose()
                stats = {}
                for col, row in desc.iterrows():
                    stats[str(col)] = {
                        k: _cell(v) for k, v in row.items()
                        if _cell(v) is not None
                    }
            except Exception as e:  # pragma: no cover - defensive
                stats = {"error": str(e)}

        preview_rows = [[_cell(v) for v in row] for row in page.itertuples(index=False, name=None)]
        payload = {
            "filename": path.name,
            "total_rows": total_rows,
            "filtered_rows": filtered_rows,
            "offset": offset,
            "returned_rows": len(preview_rows),
            "columns": schema,
            "rows": preview_rows,
            "sheets": sheets or None,
            "stats": stats,
        }
        # Marker for the frontend renderer.
        return json.dumps({
            "title": f"{path.name} ({filtered_rows:,} rows × {len(df.columns)} cols)",
            "csv_preview": payload,
            "summary": (
                f"{path.name}: {total_rows:,} rows, {len(df.columns)} columns. "
                f"Columns: {', '.join(c['name'] for c in schema)}"
            ),
        }, default=str)


# ----------------------------------------------------------------------
# Web browser (URL → readable text)
# ----------------------------------------------------------------------
_BLOCK_TAGS = re.compile(
    r"</?(p|div|br|li|ul|ol|h[1-6]|tr|td|th|table|section|article|header|footer|"
    r"blockquote|pre|hr|dd|dt|nav|aside|figure|figcaption|main)[^>]*>",
    re.IGNORECASE,
)
_STRIP_TAGS = re.compile(
    r"<(script|style|noscript|svg|canvas|iframe|template|head)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_COMMENTS = re.compile(r"<!--.*?-->", re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_LINKS = re.compile(r"<a\s+[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def html_to_text(raw: str, base_url: str = "", max_links: int = 40) -> tuple[str, str, list[dict]]:
    """Very small readability pass: strip scripts/styles, keep block
    structure as newlines, decode entities. Returns (title, text, links)."""
    title_m = _TITLE.search(raw)
    title = html.unescape(title_m.group(1)).strip() if title_m else ""
    body = _STRIP_TAGS.sub(" ", raw)
    body = _COMMENTS.sub(" ", body)

    links: list[dict] = []
    seen = set()
    for m in _LINKS.finditer(body):
        href = m.group(1).strip()
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        text = html.unescape(_TAGS.sub("", m.group(2))).strip()
        if not text:
            continue
        absolute = urljoin(base_url, href) if base_url else href
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append({"text": text[:120], "url": absolute})
        if len(links) >= max_links:
            break

    body = _BLOCK_TAGS.sub("\n", body)
    body = _TAGS.sub(" ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n\n", body)
    return title, body.strip(), links


@register_tool("web_browser")
class WebBrowserTool(BaseTool):
    name = "web_browser"
    description = (
        "Open a web page (URL) and return its readable text content plus the "
        "main links on the page. Use this to read articles, documentation, "
        "release notes, API docs, or any source returned by web_search — "
        "especially for anything that may have changed after your training "
        "data cutoff. Supports HTML, plain text, JSON and PDF pages. Long pages "
        "are paginated: pass `offset` to continue reading."
    )
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute http(s) URL to open."},
            "offset": {
                "type": "integer",
                "description": "Character offset to start from when a page was truncated (default 0).",
                "default": 0,
            },
            "max_chars": {
                "type": "integer",
                "description": "Maximum characters of text to return (default 12000, max 40000).",
                "default": 12000,
            },
            "include_links": {
                "type": "boolean",
                "description": "Include the list of links found on the page (default true).",
                "default": True,
            },
        },
        "required": ["url"],
    }

    USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36 NexusChat/1.0"
    )
    MAX_BYTES = 6 * 1024 * 1024

    async def execute(self, **kwargs) -> str:
        import httpx

        url = (kwargs.get("url") or "").strip()
        if not url:
            return json.dumps({"error": "url is required"})
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        parsed = urlparse(url)
        if not parsed.netloc:
            return json.dumps({"error": f"Invalid URL: {url}"})
        if self.config.get("block_private_hosts", True) and self._is_private_host(parsed.hostname or ""):
            return json.dumps({"error": "Refusing to open private/internal network addresses."})

        offset = max(0, int(kwargs.get("offset") or 0))
        max_chars = max(500, min(int(kwargs.get("max_chars") or 12000), 40000))
        timeout = float(self.config.get("timeout", 25))

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=timeout,
                headers={"User-Agent": self.USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/json,text/plain,application/pdf;q=0.9,*/*;q=0.8"},
            ) as client:
                resp = await client.get(url)
        except httpx.HTTPError as e:
            return json.dumps({"error": f"Request failed: {e}", "url": url})

        if resp.status_code >= 400:
            return json.dumps({"error": f"HTTP {resp.status_code}", "url": str(resp.url)})

        content_type = resp.headers.get("content-type", "").lower()
        raw = resp.content[: self.MAX_BYTES]
        final_url = str(resp.url)
        title = ""
        links: list[dict] = []

        if "pdf" in content_type or final_url.lower().endswith(".pdf"):
            text = await asyncio.to_thread(self._pdf_text, raw)
        elif "json" in content_type:
            try:
                text = json.dumps(json.loads(raw.decode("utf-8", errors="replace")), indent=2)
            except Exception:
                text = raw.decode("utf-8", errors="replace")
        elif "html" in content_type or raw[:512].lstrip().lower().startswith((b"<!doctype", b"<html")):
            title, text, links = html_to_text(raw.decode(resp.encoding or "utf-8", errors="replace"), final_url)
        else:
            text = raw.decode(resp.encoding or "utf-8", errors="replace")

        total = len(text)
        snippet = text[offset: offset + max_chars]
        result = {
            "url": final_url,
            "title": title,
            "content_type": content_type.split(";")[0],
            "total_chars": total,
            "offset": offset,
            "returned_chars": len(snippet),
            "truncated": offset + len(snippet) < total,
            "next_offset": offset + len(snippet) if offset + len(snippet) < total else None,
            "text": snippet,
        }
        if kwargs.get("include_links", True):
            result["links"] = links[:40]
        return json.dumps(result)

    @staticmethod
    def _pdf_text(raw: bytes) -> str:
        import io

        try:
            from PyPDF2 import PdfReader
        except ImportError:  # pragma: no cover
            return "(PDF support requires PyPDF2)"
        reader = PdfReader(io.BytesIO(raw))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)

    @staticmethod
    def _is_private_host(host: str) -> bool:
        import ipaddress

        host = host.lower()
        if host in ("localhost",) or host.endswith(".local") or host.endswith(".internal"):
            return True
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        except ValueError:
            return False
