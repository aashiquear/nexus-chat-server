"""Built-in tools for Nexus Chat."""

import ast
import asyncio
import math
import operator
import datetime as dt
import json
import logging
import subprocess
import tempfile
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from . import BaseTool, register_tool

logger = logging.getLogger(__name__)


@register_tool("calculator")
class CalculatorTool(BaseTool):
    name = "calculator"
    description = "Evaluate mathematical expressions safely. Supports basic arithmetic, powers, sqrt, trig, and common math functions."
    parameters = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Mathematical expression to evaluate, e.g. '2 + 3 * 4' or 'sqrt(16)'"
            }
        },
        "required": ["expression"]
    }

    SAFE_FUNCTIONS = {
        "sqrt": math.sqrt, "abs": abs, "round": round,
        "sin": math.sin, "cos": math.cos, "tan": math.tan,
        "log": math.log, "log10": math.log10, "log2": math.log2,
        "pi": math.pi, "e": math.e, "pow": pow,
        "floor": math.floor, "ceil": math.ceil,
    }

    async def execute(self, **kwargs) -> str:
        expr = kwargs.get("expression", "")
        try:
            # Replace common function names for eval safety
            for name, func in self.SAFE_FUNCTIONS.items():
                if callable(func):
                    pass  # handled in namespace
            result = eval(expr, {"__builtins__": {}}, self.SAFE_FUNCTIONS)
            return json.dumps({"result": result, "expression": expr})
        except Exception as e:
            return json.dumps({"error": str(e), "expression": expr})


@register_tool("datetime_tool")
class DateTimeTool(BaseTool):
    name = "datetime_tool"
    description = "Get current date, time, and timezone information."
    parameters = {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "Timezone name (e.g. 'US/Mountain', 'UTC'). Defaults to UTC."
            },
            "format": {
                "type": "string",
                "description": "Output format string (strftime). Default: '%Y-%m-%d %H:%M:%S %Z'"
            }
        },
        "required": []
    }

    async def execute(self, **kwargs) -> str:
        fmt = kwargs.get("format", "%Y-%m-%d %H:%M:%S %Z")
        now = dt.datetime.now(dt.timezone.utc)
        return json.dumps({
            "datetime": now.strftime(fmt),
            "timestamp": now.timestamp(),
            "iso": now.isoformat(),
        })


@register_tool("code_executor")
class CodeExecutorTool(BaseTool):
    name = "code_executor"
    description = (
        "Execute Python code in a sandboxed Docker environment and return the output. "
        "Live output is streamed to the canvas terminal during execution."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute"
            }
        },
        "required": ["code"]
    }

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self._last_output = ""
        self._last_return_code = 0

    async def execute(self, **kwargs) -> str:
        """Non-streaming fallback: collect all chunks and return as JSON."""
        chunks = []
        async for chunk in self.stream_execute(**kwargs):
            chunks.append(chunk)
        return json.dumps({
            "output": "\n".join(chunks).strip() or "(no output)",
            "return_code": self._last_return_code,
        })

    async def stream_execute(self, **kwargs) -> AsyncIterator[str]:
        """Run Python code inside the nexus-sandbox Docker container
        and yield stdout/stderr lines in real time.
        """
        code = kwargs.get("code", "")
        timeout = self.config.get("timeout", 30)

        session_id = uuid.uuid4().hex[:12]
        sandbox_dir = Path("./data/sandbox")
        sandbox_dir.mkdir(parents=True, exist_ok=True)
        code_file = sandbox_dir / f"{session_id}.py"
        code_file.write_text(code, encoding="utf-8")

        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-i", "nexus-sandbox",
            "python3", f"/sandbox/{session_id}.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def pump(stream: asyncio.StreamReader, prefix: str) -> None:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                await queue.put(prefix + text)

        stdout_task = asyncio.create_task(pump(proc.stdout, ""))
        stderr_task = asyncio.create_task(pump(proc.stderr, "[stderr]: "))

        async def drain() -> None:
            await asyncio.gather(stdout_task, stderr_task)
            await queue.put(None)

        drain_task = asyncio.create_task(drain())

        output_lines: list[str] = []
        try:
            while True:
                try:
                    line = await asyncio.wait_for(queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    proc.kill()
                    yield "Execution timed out"
                    output_lines.append("Execution timed out")
                    break

                if line is None:
                    break

                output_lines.append(line)
                yield line
        finally:
            drain_task.cancel()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()

            self._last_return_code = proc.returncode or 0
            self._last_output = "\n".join(output_lines)

            # Cleanup sandbox file
            try:
                code_file.unlink()
            except OSError:
                pass


@register_tool("file_reader")
class FileReaderTool(BaseTool):
    name = "file_reader"
    description = "Read and extract text content from uploaded files (txt, md, csv, json, py, etc.)."
    parameters = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Name of the uploaded file to read"
            }
        },
        "required": ["filename"]
    }

    async def execute(self, **kwargs) -> str:
        filename = kwargs.get("filename", "")
        upload_dir = Path(self.config.get("upload_dir", "./data/uploads"))
        filepath = upload_dir / filename

        if not filepath.exists():
            return json.dumps({"error": f"File not found: {filename}"})

        try:
            ext = filepath.suffix.lower()
            if ext == ".pdf":
                return await self._read_pdf(filepath)
            elif ext == ".docx":
                return await self._read_docx(filepath)
            else:
                content = filepath.read_text(errors="replace")
                # Truncate if very long
                if len(content) > 50000:
                    content = content[:50000] + "\n... (truncated)"
                return json.dumps({
                    "filename": filename,
                    "content": content,
                    "size": filepath.stat().st_size,
                })
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _read_pdf(self, path: Path) -> str:
        try:
            from PyPDF2 import PdfReader
            reader = PdfReader(str(path))
            text = "\n".join(
                page.extract_text() or "" for page in reader.pages
            )
            return json.dumps({
                "filename": path.name, "content": text,
                "pages": len(reader.pages),
            })
        except ImportError:
            return json.dumps({"error": "PyPDF2 not installed"})

    async def _read_docx(self, path: Path) -> str:
        try:
            from docx import Document
            doc = Document(str(path))
            text = "\n".join(p.text for p in doc.paragraphs)
            return json.dumps({"filename": path.name, "content": text})
        except ImportError:
            return json.dumps({"error": "python-docx not installed"})


@register_tool("web_search")
class WebSearchTool(BaseTool):
    name = "web_search"
    description = (
        "Search the web for real-time, current, or factual information that is outside "
        "the model's training knowledge. Use this tool whenever the user asks about events, "
        "data, or conditions that change over time or require up-to-the-minute accuracy.\n\n"
        "**When to call this tool (trigger conditions):**\n"
        "- Weather forecasts or current conditions (e.g., 'How is the weather today near me?')\n"
        "- Latest news, breaking stories, or recent developments\n"
        "- Sports scores, fixtures, standings, or athlete statistics\n"
        "- Stock prices, cryptocurrency values, market indices, or financial data\n"
        "- Currency exchange rates or conversion\n"
        "- Current date-sensitive facts (e.g., 'Who is the current president?')\n"
        "- Dictionary definitions, word meanings, or spellings\n"
        "- Any query that explicitly asks for 'latest', 'current', 'today', 'now', 'recent', or 'live'\n\n"
        "**Few-shot examples:**\n"
        "User: 'How is the weather today near me?' → search_type='weather', query='current weather forecast', location='near me'\n"
        "User: 'What is the latest news?' → search_type='news', query='latest news headlines today'\n"
        "User: 'Who won the match yesterday?' → search_type='sports', query='yesterday match results scores'\n"
        "User: 'What is the price of Bitcoin?' → search_type='finance', query='Bitcoin price today USD'\n"
        "User: 'What does serendipity mean?' → search_type='dictionary', query='serendipity definition meaning'\n"
        "User: 'What happened in the tech world this week?' → search_type='news', query='tech news this week'\n"
        "User: 'Euro to Dollar exchange rate' → search_type='finance', query='EUR to USD exchange rate today'\n"
        "User: 'What movies are playing in theaters?' → search_type='general', query='movies playing in theaters now'\n\n"
        "Always set search_type to the most appropriate category so results can be formatted optimally."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query string. Be specific and include temporal keywords (today, now, latest, current) when relevant."
            },
            "search_type": {
                "type": "string",
                "enum": ["general", "news", "weather", "sports", "finance", "dictionary", "current_events"],
                "description": (
                    "Category of search to optimize result formatting. "
                    "general = broad factual queries; "
                    "news = breaking stories and headlines; "
                    "weather = forecasts and conditions; "
                    "sports = scores, fixtures, standings; "
                    "finance = stocks, crypto, rates, markets; "
                    "dictionary = word definitions and meanings; "
                    "current_events = recent political or social happenings."
                )
            },
            "location": {
                "type": "string",
                "description": "Optional geographic hint for weather, sports, or local news queries (e.g., 'London', 'New York', 'near me')."
            },
            "num_results": {
                "type": "integer",
                "description": "Number of search results to retrieve (default: 5, max: 10).",
                "default": 5,
                "minimum": 1,
                "maximum": 10
            }
        },
        "required": ["query", "search_type"]
    }

    async def execute(self, **kwargs) -> str:
        query = kwargs.get("query", "")
        search_type = kwargs.get("search_type", "general")
        location = kwargs.get("location", "")
        num = min(int(kwargs.get("num_results", 5)), 10)

        # Augment query with location if provided
        effective_query = query
        if location and location.lower() not in query.lower():
            effective_query = f"{query} {location}"

        raw_results: list[dict] = []
        extra: dict = {}
        engines_tried: list[str] = []

        # --- Weather: use Open-Meteo API (free, structured, no auth) ---
        if search_type == "weather":
            try:
                raw_results, extra = await self._openmeteo_weather(query, location)
                if raw_results:
                    engines_tried.append("open_meteo")
            except Exception as e:
                logger.debug("Open-Meteo failed: %s", e)
                engines_tried.append("open_meteo(failed)")

        # --- News / current_events: use news-specific engines first ---
        if search_type in ("news", "current_events") and not raw_results:
            # 1. DuckDuckGo news API (usually not rate-limited)
            try:
                from duckduckgo_search import DDGS
                with DDGS() as ddgs:
                    raw = list(ddgs.news(effective_query, max_results=num))
                if raw:
                    raw_results = [
                        {
                            "title": r.get("title", ""),
                            "text": r.get("body", ""),
                            "url": r.get("url", ""),
                            "date": r.get("date", ""),
                            "source": r.get("source", ""),
                        }
                        for r in raw
                    ]
                # Staleness guard: if all results are >30 days old, treat as failure
                if raw_results and self._all_stale(raw_results, days=30):
                    raw_results = []
                engines_tried.append("ddg_news")
            except Exception as e:
                logger.debug("DDG news failed: %s", e)
                engines_tried.append("ddg_news(failed)")

            # 2. Google News RSS (reliable, no auth, real headlines)
            if not raw_results:
                try:
                    raw_results, extra = await self._google_news_rss(effective_query, num)
                    engines_tried.append("google_news_rss")
                except Exception as e:
                    logger.debug("Google News RSS failed: %s", e)
                    engines_tried.append("google_news_rss(failed)")

        # --- General / other: multi-engine fallback chain ---
        if not raw_results:
            # 1. DuckDuckGo text API
            try:
                from duckduckgo_search import DDGS
                with DDGS() as ddgs:
                    raw = list(ddgs.text(effective_query, max_results=num))
                if raw:
                    raw_results = [
                        {
                            "title": r.get("title", ""),
                            "text": r.get("body", ""),
                            "url": r.get("href", ""),
                        }
                        for r in raw
                    ]
                engines_tried.append("ddg_text")
            except Exception as e:
                logger.debug("DDG text failed: %s", e)
                engines_tried.append("ddg_text(failed)")

        # 2. Bing mobile HTML (good fallback when DDG is blocked)
        if not raw_results:
            try:
                raw_results, extra = await self._bing_mobile_search(effective_query, num)
                engines_tried.append("bing_mobile")
            except Exception as e:
                logger.debug("Bing mobile failed: %s", e)
                engines_tried.append("bing_mobile(failed)")

        # 2b. Relevance guard: for weather queries, if Bing results don't contain
        # weather-related keywords, discard them (Bing often returns irrelevant forums).
        if raw_results and search_type == "weather":
            weather_keywords = {"weather", "forecast", "temperature", "rain", "snow", "sunny", "cloud", "wind", "humidity", "storm", "drizzle", "showers", "degrees", "°f", "°c", "high", "low", "nws", "noaa", "accuweather", "weather.com", "met office", "bbc weather"}
            relevant = any(
                any(kw in (r.get("title", "") + " " + r.get("text", "")).lower() for kw in weather_keywords)
                for r in raw_results
            )
            if not relevant:
                raw_results = []
                extra = {}

        # 2c. For dictionary queries, if Bing mobile returned irrelevant results
        # (titles don't contain the target word), discard and continue.
        if raw_results and search_type == "dictionary":
            import re as _re
            word = _re.sub(
                r'\b(definition|meaning|define|of|what is|what does|mean|dictionary)\b',
                '', query, flags=_re.IGNORECASE,
            ).strip().split()[0]
            relevant = any(word.lower() in r.get("title", "").lower() for r in raw_results)
            if not relevant:
                raw_results = []
                extra = {}

        # 2d. Sports relevance guard — discard results from non-sports domains or
        # results that lack any sports keywords. Prevents LLM hallucination on
        # garbage results (e.g. Stack Overflow for "New Zealand vs Bangladesh").
        if raw_results and search_type == "sports":
            sports_keywords = {
                "cricket", "football", "soccer", "rugby", "tennis", "basketball",
                "baseball", "hockey", "golf", "match", "score", "result", "game",
                "team", "player", "won", "win", "loss", "draw", "played", "vs",
                "versus", "scorecard", "highlights", "over", "innings", "wicket",
                "goal", "tournament", "championship", "league", "fixture", "live",
                "espncricinfo", "espn", "bbc sport", "skysports", "cricbuzz",
            }
            spam_domains = {"stackoverflow.com", "thumpertalk.com", "quora.com"}
            def _is_sports_relevant(r: dict) -> bool:
                title_text = (r.get("title", "") + " " + r.get("text", "")).lower()
                has_keyword = any(kw in title_text for kw in sports_keywords)
                url = r.get("url", "").lower()
                from_spam = any(d in url for d in spam_domains)
                return has_keyword and not from_spam

            if not any(_is_sports_relevant(r) for r in raw_results):
                raw_results = []
                extra = {}

        # 3. DuckDuckGo HTML scrape (last resort)
        if not raw_results:
            try:
                raw_results, extra = await self._ddg_html_search(effective_query, num)
                engines_tried.append("ddg_html")
            except Exception as e:
                logger.debug("DDG HTML failed: %s", e)
                engines_tried.append("ddg_html(failed)")

        # 4. DuckDuckGo Instant Answer (factual snippets)
        if not raw_results:
            try:
                raw_results, extra = await self._ddg_instant_answer(effective_query, num)
                engines_tried.append("ddg_instant_answer")
            except Exception as e:
                logger.debug("DDG instant answer failed: %s", e)
                engines_tried.append("ddg_instant_answer(failed)")

        # 5. Free Dictionary API (dictionary queries only)
        if not raw_results and search_type == "dictionary":
            try:
                raw_results, extra = await self._dictionary_api(query, num)
                engines_tried.append("dictionaryapi")
            except Exception as e:
                logger.debug("Dictionary API failed: %s", e)
                engines_tried.append("dictionaryapi(failed)")

        # 6. Query reformulation retry — strip location/temporal words and try Bing again
        if not raw_results:
            simplified = self._simplify_query(effective_query)
            if simplified != effective_query:
                try:
                    raw_results, extra = await self._bing_mobile_search(simplified, num)
                    if raw_results:
                        effective_query = simplified
                    engines_tried.append("bing_mobile_reformulated")
                except Exception as e:
                    logger.debug("Bing mobile reformulated failed: %s", e)
                    engines_tried.append("bing_mobile_reformulated(failed)")

        if not raw_results:
            return json.dumps({
                "query": query,
                "effective_query": effective_query,
                "search_type": search_type,
                "location": location or None,
                "engines_tried": engines_tried,
                "results": [{"text": "No results found for this query. Try rephrasing or using different keywords."}]
            })

        # --- Post-process / categorize results ---
        categorized = self._categorize_results(raw_results, search_type, query)
        return json.dumps({
            "query": query,
            "effective_query": effective_query,
            "search_type": search_type,
            "location": location or None,
            "engines_tried": engines_tried,
            **extra,
            **categorized,
        })

    # --- Engine helpers ---

    async def _bing_mobile_search(self, query: str, num: int) -> tuple[list[dict], dict]:
        """Bing mobile HTML search — works when DDG is rate-limited."""
        import httpx
        import re as _re
        from html import unescape
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        async with httpx.AsyncClient(timeout=15, headers=headers, follow_redirects=True) as client:
            resp = await client.get("https://www.bing.com/search", params={"q": query})
            html = resp.text
            results = []
            # Bing mobile uses li.b_algo blocks. Each block has two <a> tags with the same
            # href: the first is a breadcrumb URL, the second is the actual title.
            # We split by b_algo markers and parse each block individually.
            split = html.split('<li class="b_algo"')
            for block in split[1:]:  # skip preamble before first b_algo
                # Truncate at the start of the next major result block to avoid over-parsing
                end = block.find('<li class="b_algo"')
                if end == -1:
                    end = len(block)
                chunk = block[:end]
                # Find all external links in this chunk
                links = _re.findall(
                    r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
                    chunk,
                    _re.DOTALL,
                )
                ext = []
                for href, text in links:
                    if "bing.com" in href or "microsoft.com" in href:
                        continue
                    clean_text = _re.sub(r'<[^>]+>', '', unescape(text)).strip()
                    if clean_text:
                        ext.append((href, clean_text))
                if len(ext) < 2:
                    continue
                # Second link text is the real title; first is breadcrumb
                url = ext[0][0]
                title = ext[1][1]
                # Skip if title is just a URL fragment
                if len(title) < 5 or title.startswith("http") or " › " in title:
                    continue
                # Extract the best snippet from divs/spans/ps in the chunk
                snippets = _re.findall(
                    r'<(?:div|span|p)[^>]*>(.*?)</(?:div|span|p)>',
                    chunk,
                    _re.DOTALL,
                )
                clean_snippets = [_re.sub(r'<[^>]+>', '', s).strip() for s in snippets]
                snippet = ""
                for s in clean_snippets:
                    if 20 < len(s) < 300 and not s.startswith("http") and " › " not in s and s != title:
                        snippet = s
                        break
                results.append({"title": title, "text": snippet, "url": url})
                if len(results) >= num:
                    break

            # Extract related searches (filter out Bing internal params)
            related = []
            related_matches = _re.findall(
                r'<a[^>]+href="/search\?q=([^"]+)&[^"]*"[^>]*>(.*?)</a>',
                html,
                _re.DOTALL,
            )
            from urllib.parse import unquote as _unquote
            for q_raw, _ in related_matches[:8]:
                clean = _unquote(q_raw.replace("+", " "))
                # Discard Bing internal tracking params that leak through
                if "FORM=" in clean or "filters=" in clean or "&amp;" in clean:
                    continue
                if clean and clean not in related:
                    related.append(clean)
            return results, {"related_queries": related[:5]}

    async def _ddg_html_search(self, query: str, num: int) -> tuple[list[dict], dict]:
        """DuckDuckGo HTML scrape fallback."""
        import asyncio
        await asyncio.sleep(1)
        import httpx
        from html import unescape
        from urllib.parse import unquote as url_unquote
        import re as _re
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        async with httpx.AsyncClient(timeout=15, headers=headers, follow_redirects=True) as client:
            resp = await client.get("https://html.duckduckgo.com/html/", params={"q": query})
            html_text = resp.text
            results = []
            result_blocks = _re.findall(
                r'<a[^>]+class="result__a"[^>]+href="([^"]*)"[^>]*>(.*?)</a>.*?'
                r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
                html_text,
                _re.DOTALL,
            )
            for url, title, snippet in result_blocks[:num]:
                clean_title = _re.sub(r'<[^>]+>', '', unescape(title)).strip()
                clean_snippet = _re.sub(r'<[^>]+>', '', unescape(snippet)).strip()
                url_match = _re.search(r'uddg=([^&]+)', url)
                actual_url = url_unquote(url_match.group(1)) if url_match else url
                if clean_title or clean_snippet:
                    results.append({"title": clean_title, "text": clean_snippet, "url": actual_url})
            # Related queries
            related = _re.findall(
                r'<a[^>]+class="result__a"[^>]+href="/l/\?kh=-\d+&q=([^"]+)"[^>]*>(.*?)</a>',
                html_text,
                _re.DOTALL,
            )
            extra = {"related_queries": [url_unquote(q) for q, _ in related[:5]]}
            return results, extra

    async def _ddg_instant_answer(self, query: str, num: int) -> tuple[list[dict], dict]:
        """DuckDuckGo Instant Answer API for factual snippets."""
        import httpx
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1},
            )
            data = resp.json()
            results = []
            for topic in data.get("RelatedTopics", [])[:num]:
                if "Text" in topic:
                    results.append({"text": topic["Text"], "url": topic.get("FirstURL", "")})
            abstract = data.get("Abstract", "")
            if abstract and not results:
                results.append({"title": "Instant Answer", "text": abstract, "url": ""})
            return results, {}

    async def _google_news_rss(self, query: str, num: int) -> tuple[list[dict], dict]:
        """Google News RSS — reliable, no auth, returns real headlines with dates."""
        import httpx
        import re as _re
        async with httpx.AsyncClient(timeout=15) as client:
            params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
            resp = await client.get("https://news.google.com/rss/search", params=params)
            text = resp.text
            items = _re.findall(r'<item>(.*?)</item>', text, _re.DOTALL)
            results = []
            for item in items[:num]:
                title_m = _re.search(r'<title>\s*(.*?)\s*</title>', item, _re.DOTALL)
                link_m = _re.search(r'<link>\s*(.*?)\s*</link>', item, _re.DOTALL)
                pub_m = _re.search(r'<pubDate>\s*(.*?)\s*</pubDate>', item, _re.DOTALL)
                source_m = _re.search(r'<source[^>]*>\s*(.*?)\s*</source>', item, _re.DOTALL)

                def _strip_cdata(s: str) -> str:
                    return _re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', s, flags=_re.DOTALL).strip()

                raw_title = _strip_cdata(title_m.group(1)) if title_m else ""
                raw_source = _strip_cdata(source_m.group(1)) if source_m else ""
                raw_source = _re.sub(r'&amp;', '&', raw_source)
                url = link_m.group(1).strip() if link_m else ""
                pub_date = pub_m.group(1).strip() if pub_m else ""

                if raw_title:
                    results.append({
                        "title": raw_title,
                        "text": f"{raw_title} — via {raw_source}" + (f" ({pub_date})" if pub_date else ""),
                        "url": url,
                        "date": pub_date,
                        "source": raw_source,
                    })
            return results, {}

    async def _dictionary_api(self, query: str, num: int) -> tuple[list[dict], dict]:
        """Free Dictionary API (dictionaryapi.dev) — no auth, works for English words."""
        import httpx
        import re as _re
        # Extract the word from the query (remove "definition", "meaning", etc.)
        word = _re.sub(r'\b(definition|meaning|define|of|what is|what does|mean|dictionary)\b', '', query, flags=_re.IGNORECASE).strip()
        word = word.split()[0] if word else query.split()[0]
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}")
            if resp.status_code != 200:
                return [], {}
            data = resp.json()
            results = []
            for entry in data[:1]:  # Usually one entry per word
                word_text = entry.get("word", word)
                phonetic = entry.get("phonetic", "")
                parts = []
                for meaning in entry.get("meanings", [])[:3]:
                    pos = meaning.get("partOfSpeech", "")
                    defs = [d.get("definition", "") for d in meaning.get("definitions", [])[:2]]
                    if defs:
                        parts.append(f"{pos}: " + "; ".join(defs))
                text = f"{word_text}" + (f" {phonetic}" if phonetic else "") + " — " + " | ".join(parts)
                results.append({
                    "title": f"Definition of {word_text}",
                    "text": text,
                    "url": f"https://en.wiktionary.org/wiki/{word_text}",
                })
            return results, {}

    def _all_stale(self, results: list[dict], days: int = 30) -> bool:
        """Return True if every result is older than `days`."""
        import re as _re
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        for r in results:
            date_str = r.get("date", "")
            if not date_str:
                return False  # can't tell → not stale
            # Try ISO 8601
            for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %Z"):
                try:
                    dt = datetime.strptime(date_str, fmt)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt >= cutoff:
                        return False
                    break
                except ValueError:
                    continue
            else:
                return False  # unparseable → not stale
        return True

    async def _openmeteo_weather(self, query: str, location: str) -> tuple[list[dict], dict]:
        """Open-Meteo free weather API — structured 7-day forecast, no auth."""
        import httpx
        import re as _re
        from datetime import datetime, timedelta

        # Extract location from query + location parameter
        loc = location.strip() if location else ""
        if not loc:
            # Try to extract a place name from the query
            loc_match = _re.search(r'\b(?:in|for|near|at)\s+([A-Za-z\s,]+?)(?:\s+(?:weather|forecast|today|tomorrow|now|\d{4}))?\b', query, _re.IGNORECASE)
            if loc_match:
                loc = loc_match.group(1).strip()
        if not loc:
            loc = "New York"  # ultimate fallback

        # 1. Geocode (try raw, then stripped variants)
        async with httpx.AsyncClient(timeout=15) as client:
            geo_results = []
            for attempt_loc in [loc, _re.sub(r',\s*[A-Za-z\.\s]+$', '', loc).strip(), loc.split(',')[0].strip()]:
                if not attempt_loc:
                    continue
                geo_resp = await client.get(
                    "https://geocoding-api.open-meteo.com/v1/search",
                    params={"name": attempt_loc, "count": 1, "language": "en", "format": "json"},
                )
                geo_data = geo_resp.json()
                geo_results = geo_data.get("results", [])
                if geo_results:
                    break
            if not geo_results:
                return [], {}
            place = geo_results[0]
            lat = place["latitude"]
            lon = place["longitude"]
            place_name = place.get("name", loc)
            country = place.get("country", "")
            admin1 = place.get("admin1", "")
            full_name = f"{place_name}, {admin1}" if admin1 else place_name
            if country:
                full_name += f", {country}"

        # 2. Forecast
            forecast_resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max,weathercode",
                    "timezone": "auto",
                    "forecast_days": 7,
                },
            )
            forecast = forecast_resp.json()
            daily = forecast.get("daily", {})
            dates = daily.get("time", [])
            max_temps = daily.get("temperature_2m_max", [])
            min_temps = daily.get("temperature_2m_min", [])
            precips = daily.get("precipitation_sum", [])
            winds = daily.get("windspeed_10m_max", [])
            codes = daily.get("weathercode", [])

        # WMO Weather interpretation codes
        WMO_CODES = {
            0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
            45: "Fog", 48: "Depositing rime fog",
            51: "Light drizzle", 53: "Moderate drizzle", 55: "Dense drizzle",
            56: "Light freezing drizzle", 57: "Dense freezing drizzle",
            61: "Slight rain", 63: "Moderate rain", 65: "Heavy rain",
            66: "Light freezing rain", 67: "Heavy freezing rain",
            71: "Slight snow fall", 73: "Moderate snow fall", 75: "Heavy snow fall",
            77: "Snow grains",
            80: "Slight rain showers", 81: "Moderate rain showers", 82: "Violent rain showers",
            85: "Slight snow showers", 86: "Heavy snow showers",
            95: "Thunderstorm", 96: "Thunderstorm with slight hail", 99: "Thunderstorm with heavy hail",
        }

        # Build structured daily entries
        daily_entries = []
        for i, date in enumerate(dates):
            code = codes[i] if i < len(codes) else None
            condition = WMO_CODES.get(code, "Unknown") if code is not None else "Unknown"
            entry = {
                "date": date,
                "condition": condition,
                "temp_high_c": max_temps[i] if i < len(max_temps) else None,
                "temp_low_c": min_temps[i] if i < len(min_temps) else None,
                "precipitation_mm": precips[i] if i < len(precips) else None,
                "wind_max_kmh": winds[i] if i < len(winds) else None,
            }
            daily_entries.append(entry)

        # Build human-readable summary text
        today = datetime.now().strftime("%Y-%m-%d")
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        summary_parts = [f"Weather forecast for {full_name}."]
        for entry in daily_entries[:5]:
            day_label = "Today" if entry["date"] == today else ("Tomorrow" if entry["date"] == tomorrow else entry["date"])
            summary_parts.append(
                f"{day_label}: {entry['condition']}, high {entry['temp_high_c']}C, low {entry['temp_low_c']}C, "
                f"precipitation {entry['precipitation_mm']}mm, wind {entry['wind_max_kmh']}km/h."
            )
        summary_text = " ".join(summary_parts)

        results = [{
            "title": f"7-Day Weather Forecast for {full_name}",
            "text": summary_text,
            "url": f"https://open-meteo.com/en/docs#latitude={lat}&longitude={lon}",
        }]
        extra = {"daily_forecast": daily_entries, "location": full_name}
        return results, extra

    def _simplify_query(self, query: str) -> str:
        """Strip temporal/local qualifiers for a broader retry search."""
        import re as _re
        # Remove common temporal words
        simplified = _re.sub(
            r'\b(today|now|latest|current|recent|live|this week|this month|yesterday|tomorrow)\b',
            '',
            query,
            flags=_re.IGNORECASE,
        )
        # Remove extra whitespace
        simplified = ' '.join(simplified.split())
        return simplified.strip() or query

    def _categorize_results(self, raw: list[dict], search_type: str, query: str) -> dict:
        """Categorize and structure raw search results for easier LLM consumption."""
        import re as _re

        # Common helpers
        def extract_table_candidates(texts: list[str]) -> list[dict]:
            """Heuristically detect score/rate tables from snippets."""
            rows = []
            for t in texts:
                # Look for patterns like "Team A 3 - 1 Team B" or "BTC $67,000"
                score_match = _re.search(r'([A-Za-z\s]+)\s+(\d+)[\s\-:]+(\d+)\s+([A-Za-z\s]+)', t)
                if score_match:
                    rows.append({
                        "home": score_match.group(1).strip(),
                        "home_score": score_match.group(2),
                        "away_score": score_match.group(3),
                        "away": score_match.group(4).strip(),
                        "context": t,
                    })
                price_match = _re.search(r'(BTC|ETH|Bitcoin|Ether(?:eum)?|EUR|USD|GBP|JPY|Gold|Silver)[\s\-:]?\$?([\d,\.]+)', t, _re.IGNORECASE)
                if price_match:
                    rows.append({
                        "asset": price_match.group(1).strip(),
                        "value": price_match.group(2),
                        "context": t,
                    })
            return rows

        def maybe_extract_weather(texts: list[str]) -> list[dict]:
            """Heuristically extract temperature and condition info."""
            entries = []
            for t in texts:
                temps = _re.findall(r'(-?\d+)\s*[°°]\s*([CF])', t)
                conditions = _re.findall(r'(sunny|cloudy|rain|snow|clear|overcast|storm|windy|fog|humid|drizzle|showers|thunder)', t, _re.IGNORECASE)
                if temps or conditions:
                    entries.append({
                        "snippet": t,
                        "temperatures": [f"{v} {u.upper()}" for v, u in temps],
                        "conditions": conditions,
                    })
            return entries

        # Default structure
        result = {
            "summary": "",
            "results": raw,
        }

        if search_type == "weather":
            weather_entries = maybe_extract_weather([r["text"] for r in raw])
            result["weather_extracts"] = weather_entries
            result["summary"] = (
                "Weather-related search results. Use the snippets and extracted temperatures "
                "to answer the user's forecast or condition question."
            )

        elif search_type == "sports":
            table_rows = extract_table_candidates([r["text"] for r in raw])
            result["sports_extracts"] = table_rows
            result["summary"] = (
                "Sports search results. Use the snippets and any extracted scores/tables to "
                "answer the user's question about matches, fixtures, or standings."
            )

        elif search_type == "finance":
            table_rows = extract_table_candidates([r["text"] for r in raw])
            result["finance_extracts"] = table_rows
            result["summary"] = (
                "Financial search results. Use the snippets and any extracted rates/prices to "
                "answer the user's question about markets, stocks, crypto, or exchange rates."
            )

        elif search_type == "dictionary":
            defs = []
            for r in raw:
                text = r["text"]
                # Try to extract "word: definition" or "word - definition" patterns
                match = _re.search(r'^(?:Definition of\s+)?([^:]+)[:\-]\s*(.+)', text)
                if match:
                    defs.append({"term": match.group(1).strip(), "definition": match.group(2).strip(), "source": r.get("url", "")})
                else:
                    defs.append({"term": query, "definition": text, "source": r.get("url", "")})
            result["definitions"] = defs
            result["summary"] = (
                "Dictionary search results. Use the definitions below to answer the user's "
                "question about word meaning, spelling, or usage."
            )

        elif search_type in ("news", "current_events"):
            # Heuristically extract date mentions from snippets
            date_patterns = [
                r'\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4})\b',
                r'\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})\b',
                r'\b(\d{4}-\d{2}-\d{2})\b',
                r'\b(\d{1,2}/\d{1,2}/\d{4})\b',
                r'\b(yesterday|today|last\s+(?:night|week|month|year|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday))\b',
                r'\b(\d+\s+(?:hours?|minutes?)\s+ago)\b',
            ]
            date_extracts = []
            for r in raw:
                text = r.get("text", "")
                found = []
                for pat in date_patterns:
                    for m in _re.findall(pat, text, _re.IGNORECASE):
                        found.append(m)
                if found:
                    date_extracts.append({"dates": found, "snippet": text, "source": r.get("url", "")})
            result["date_extracts"] = date_extracts
            if search_type == "news":
                result["summary"] = (
                    "News search results. These are recent articles and headlines. Summarize the key "
                    "developments for the user and cite sources where possible. Pay attention to extracted dates."
                )
            else:
                result["summary"] = (
                    "Current-events search results. These reflect recent political, social, or "
                    "cultural happenings. Provide a concise summary with attribution. Pay attention to extracted dates."
                )

        else:
            result["summary"] = (
                "General web search results. Synthesize the information below to answer the "
                "user's query accurately. Cite sources when stating specific facts."
            )

        return result
