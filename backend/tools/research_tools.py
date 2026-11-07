"""Research tools: academic search and change tracking."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from . import BaseTool, register_tool


@register_tool("academic_search")
class AcademicSearchTool(BaseTool):
    name = "academic_search"
    description = (
        "Search scholarly literature across arXiv, Crossref and OpenAlex (free APIs, no key). Returns titles, authors, "
        "year, venue, DOI/arXiv id, abstract snippet, citation counts and links; operation=bibtex formats a DOI or "
        "arXiv id as BibTeX; operation=doi resolves a DOI to its metadata. Use for papers, citations and prior work."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["search", "doi", "bibtex"], "default": "search"},
            "query": {"type": "string", "description": "Search terms, DOI (10.xxxx/…) or arXiv id (2401.01234)."},
            "source": {"type": "string", "enum": ["all", "arxiv", "crossref", "openalex"], "default": "all"},
            "max_results": {"type": "integer", "description": "Results per source (default 5, max 20).", "default": 5},
            "year_from": {"type": "integer"}, "year_to": {"type": "integer"},
            "sort": {"type": "string", "enum": ["relevance", "date", "citations"], "default": "relevance"},
        },
        "required": ["query"],
    }
    UA = {"User-Agent": "NexusChat/1.0 (mailto:nexus-chat@example.com)"}

    async def execute(self, **kwargs) -> str:
        import httpx

        op = kwargs.get("operation") or "search"
        q = (kwargs.get("query") or "").strip()
        n = max(1, min(int(kwargs.get("max_results") or 5), 20))
        async with httpx.AsyncClient(timeout=25, headers=self.UA, follow_redirects=True) as client:
            try:
                if op in ("doi", "bibtex") or re.match(r"^10\.\d{4,9}/", q):
                    doi = q.replace("https://doi.org/", "")
                    if op == "bibtex":
                        r = await client.get(f"https://doi.org/{doi}", headers={"Accept": "application/x-bibtex", **self.UA})
                        if r.status_code == 200 and r.text.strip().startswith("@"):
                            return json.dumps({"doi": doi, "bibtex": r.text.strip()})
                    r = await client.get(f"https://api.crossref.org/works/{doi}")
                    if r.status_code != 200:
                        return json.dumps({"error": f"DOI lookup failed ({r.status_code})"})
                    item = self._crossref_item(r.json()["message"])
                    if op == "bibtex":
                        item["bibtex"] = self._bibtex(item)
                    return json.dumps(item)
                if re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", q):
                    res = await self._arxiv(client, f"id:{q}", 1, "relevance")
                    if res and op == "bibtex":
                        res[0]["bibtex"] = self._bibtex(res[0])
                    return json.dumps({"results": res})

                source = kwargs.get("source") or "all"
                sort = kwargs.get("sort") or "relevance"
                tasks = []
                if source in ("all", "arxiv"):
                    tasks.append(self._arxiv(client, q, n, sort))
                if source in ("all", "crossref"):
                    tasks.append(self._crossref(client, q, n, sort, kwargs.get("year_from"), kwargs.get("year_to")))
                if source in ("all", "openalex"):
                    tasks.append(self._openalex(client, q, n, sort, kwargs.get("year_from"), kwargs.get("year_to")))
                groups = await asyncio.gather(*tasks, return_exceptions=True)
                results, errors, seen = [], [], set()
                for g in groups:
                    if isinstance(g, Exception):
                        errors.append(str(g))
                        continue
                    for it in g:
                        key = (it.get("doi") or it.get("arxiv_id") or it.get("title", "")).lower()
                        if key in seen:
                            continue
                        seen.add(key)
                        results.append(it)
                return json.dumps({"query": q, "count": len(results), "results": results, "errors": errors or None}, default=str)
            except Exception as e:
                return json.dumps({"error": str(e)})

    async def _arxiv(self, client, q: str, n: int, sort: str) -> list[dict]:
        import xml.etree.ElementTree as ET

        params = {"search_query": q if q.startswith("id:") else f"all:{q}", "max_results": n,
                  "sortBy": "submittedDate" if sort == "date" else "relevance", "sortOrder": "descending"}
        r = await client.get("https://export.arxiv.org/api/query", params=params)
        r.raise_for_status()
        ns = {"a": "http://www.w3.org/2005/Atom"}
        out = []
        for e in ET.fromstring(r.text).findall("a:entry", ns):
            aid = (e.findtext("a:id", "", ns) or "").rsplit("/abs/", 1)[-1]
            out.append({
                "source": "arxiv", "title": re.sub(r"\s+", " ", e.findtext("a:title", "", ns)).strip(),
                "authors": [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)][:12],
                "year": (e.findtext("a:published", "", ns) or "")[:4], "arxiv_id": aid,
                "doi": (e.findtext("{http://arxiv.org/schemas/atom}doi") or None),
                "url": f"https://arxiv.org/abs/{aid}", "pdf": f"https://arxiv.org/pdf/{aid}",
                "abstract": re.sub(r"\s+", " ", e.findtext("a:summary", "", ns)).strip()[:700],
            })
        return out

    def _crossref_item(self, m: dict) -> dict:
        year = None
        for key in ("published-print", "published-online", "issued", "created"):
            parts = (m.get(key) or {}).get("date-parts") or [[None]]
            if parts and parts[0] and parts[0][0]:
                year = parts[0][0]
                break
        return {
            "source": "crossref", "title": (m.get("title") or [""])[0], "authors": [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in m.get("author", [])][:12],
            "year": year, "venue": (m.get("container-title") or [""])[0], "doi": m.get("DOI"), "url": m.get("URL"),
            "citations": m.get("is-referenced-by-count"), "type": m.get("type"),
            "abstract": re.sub(r"<[^>]+>", "", m.get("abstract", ""))[:700] or None,
            "volume": m.get("volume"), "issue": m.get("issue"), "pages": m.get("page"), "publisher": m.get("publisher"),
        }

    async def _crossref(self, client, q, n, sort, y0, y1) -> list[dict]:
        params = {"query": q, "rows": n, "select": "DOI,title,author,issued,container-title,URL,is-referenced-by-count,type,abstract,volume,issue,page,publisher,published-print,published-online"}
        if sort == "citations":
            params.update({"sort": "is-referenced-by-count", "order": "desc"})
        elif sort == "date":
            params.update({"sort": "published", "order": "desc"})
        filt = []
        if y0:
            filt.append(f"from-pub-date:{y0}")
        if y1:
            filt.append(f"until-pub-date:{y1}")
        if filt:
            params["filter"] = ",".join(filt)
        r = await client.get("https://api.crossref.org/works", params=params)
        r.raise_for_status()
        return [self._crossref_item(m) for m in r.json().get("message", {}).get("items", [])]

    async def _openalex(self, client, q, n, sort, y0, y1) -> list[dict]:
        params = {"search": q, "per-page": n}
        filt = []
        if y0:
            filt.append(f"from_publication_date:{y0}-01-01")
        if y1:
            filt.append(f"to_publication_date:{y1}-12-31")
        if filt:
            params["filter"] = ",".join(filt)
        if sort == "citations":
            params["sort"] = "cited_by_count:desc"
        elif sort == "date":
            params["sort"] = "publication_date:desc"
        r = await client.get("https://api.openalex.org/works", params=params)
        r.raise_for_status()
        out = []
        for w in r.json().get("results", []):
            inv = w.get("abstract_inverted_index") or {}
            abstract = None
            if inv:
                words = sorted(((pos, word) for word, positions in inv.items() for pos in positions))
                abstract = " ".join(wd for _, wd in words)[:700]
            out.append({
                "source": "openalex", "title": w.get("title"), "authors": [a.get("author", {}).get("display_name") for a in w.get("authorships", [])][:12],
                "year": w.get("publication_year"), "venue": ((w.get("primary_location") or {}).get("source") or {}).get("display_name"),
                "doi": (w.get("doi") or "").replace("https://doi.org/", "") or None, "url": w.get("id"),
                "open_access_pdf": (w.get("open_access") or {}).get("oa_url"), "citations": w.get("cited_by_count"), "abstract": abstract,
            })
        return out

    @staticmethod
    def _bibtex(item: dict) -> str:
        first = (item.get("authors") or ["unknown"])[0].split()[-1].lower()
        key = re.sub(r"[^a-z0-9]", "", f"{first}{item.get('year') or ''}")
        fields = {"title": item.get("title"), "author": " and ".join(item.get("authors") or []), "year": item.get("year"),
                  "journal": item.get("venue"), "doi": item.get("doi"), "url": item.get("url"), "volume": item.get("volume"), "pages": item.get("pages")}
        if item.get("arxiv_id"):
            fields["eprint"] = item["arxiv_id"]
            fields["archivePrefix"] = "arXiv"
        body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields.items() if v)
        return f"@article{{{key},\n{body}\n}}"


@register_tool("change_tracker")
class ChangeTrackerTool(BaseTool):
    name = "change_tracker"
    description = (
        "Track changes over time. operation=snapshot stores the current text of a web page (URL) or uploaded file under "
        "a name; operation=check fetches it again and reports exactly what changed since the last snapshot (added/removed "
        "lines, similarity, numbers that changed — useful for prices, release notes, docs, status pages); "
        "operation=list / delete manage tracked items. Snapshots persist across conversations."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["snapshot", "check", "list", "delete"]},
            "name": {"type": "string", "description": "Short label for the tracked item (default: derived from the URL/file)."},
            "url": {"type": "string", "description": "Web page to track."},
            "filename": {"type": "string", "description": "Uploaded file to track instead of a URL."},
            "selector_regex": {"type": "string", "description": "Optional regex; only the matching part of the page is tracked (e.g. a price)."},
        },
        "required": ["operation"],
    }

    def _store(self) -> Path:
        d = Path(self.ctx.state_dir) / "snapshots"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def _fetch_text(self, url: str | None, filename: str | None, selector: str | None) -> tuple[str, str]:
        if filename:
            p = self.upload_dir() / Path(filename).name
            if not p.exists():
                raise FileNotFoundError(f"{filename} not uploaded")
            from .document_tools import load_document_units

            units, _ = load_document_units(p)
            text, source = "\n".join(units), p.name
        else:
            from .data_tools import WebBrowserTool

            browser = WebBrowserTool(self.config)
            res = json.loads(await browser.execute(url=url, max_chars=40000, include_links=False))
            if "error" in res:
                raise RuntimeError(res["error"])
            text, source = res.get("text", ""), res.get("url", url)
        if selector:
            m = re.search(selector, text, re.I | re.S)
            text = m.group(0) if m else ""
        return text, source

    async def execute(self, **kwargs) -> str:
        op = kwargs.get("operation")
        store = self._store()
        try:
            if op == "list":
                items = []
                for f in sorted(store.glob("*.json")):
                    meta = json.loads(f.read_text())
                    items.append({k: meta.get(k) for k in ("name", "source", "taken_at", "checks", "last_changed")})
                return json.dumps({"tracked": items})
            name = kwargs.get("name") or (urlparse(kwargs.get("url") or "").netloc + urlparse(kwargs.get("url") or "").path) or kwargs.get("filename") or "item"
            key = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80] or hashlib.md5(name.encode()).hexdigest()[:8]
            path = store / f"{key}.json"
            if op == "delete":
                if path.exists():
                    path.unlink()
                    return json.dumps({"deleted": name})
                return json.dumps({"error": f"no tracked item '{name}'"})
            if op == "snapshot":
                text, source = await self._fetch_text(kwargs.get("url"), kwargs.get("filename"), kwargs.get("selector_regex"))
                path.write_text(json.dumps({"name": name, "source": source, "url": kwargs.get("url"), "filename": kwargs.get("filename"),
                                            "selector": kwargs.get("selector_regex"), "text": text[:200000], "taken_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                            "checks": 0, "last_changed": None}))
                return json.dumps({"snapshot": name, "source": source, "characters": len(text), "preview": text[:400]})
            if op == "check":
                if not path.exists():
                    return json.dumps({"error": f"no snapshot named '{name}' — take one first with operation=snapshot"})
                meta = json.loads(path.read_text())
                text, source = await self._fetch_text(meta.get("url") or kwargs.get("url"), meta.get("filename") or kwargs.get("filename"), meta.get("selector"))
                old = meta.get("text", "")
                ratio = difflib.SequenceMatcher(None, old, text).ratio()
                diff = list(difflib.unified_diff(old.splitlines(), text.splitlines(), "previous", "current", lineterm="", n=1))
                added = [l[1:] for l in diff if l.startswith("+") and not l.startswith("+++")]
                removed = [l[1:] for l in diff if l.startswith("-") and not l.startswith("---")]
                num_re = r"[-+]?\$?\d[\d,]*(?:\.\d+)?%?"
                old_nums, new_nums = set(re.findall(num_re, old)), set(re.findall(num_re, text))
                meta.update({"text": text[:200000], "checks": int(meta.get("checks", 0)) + 1, "taken_at": time.strftime("%Y-%m-%d %H:%M:%S")})
                changed = ratio < 0.999
                if changed:
                    meta["last_changed"] = meta["taken_at"]
                path.write_text(json.dumps(meta))
                return json.dumps({
                    "name": name, "source": source, "changed": changed, "similarity": round(ratio, 4),
                    "added_lines": added[:60], "removed_lines": removed[:60],
                    "numbers_added": sorted(new_nums - old_nums)[:30], "numbers_removed": sorted(old_nums - new_nums)[:30],
                    "previous_snapshot": meta.get("taken_at"),
                })
            return json.dumps({"error": f"unknown operation {op}"})
        except Exception as e:
            return json.dumps({"error": str(e)})
