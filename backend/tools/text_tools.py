"""Text and JSON utilities (deterministic, offline)."""

from __future__ import annotations

import base64
import difflib
import hashlib
import json
import re
import secrets
import string
import unicodedata
import urllib.parse
import uuid

from . import BaseTool, register_tool


@register_tool("text_utilities")
class TextUtilitiesTool(BaseTool):
    name = "text_utilities"
    description = (
        "Deterministic text operations. Operations: count (words/chars/lines/sentences, reading time), diff (unified "
        "diff of text vs text2), regex_find (all matches with groups), regex_replace, hash (md5/sha1/sha256/sha512), "
        "base64_encode/base64_decode, url_encode/url_decode, case (upper/lower/title/snake/camel/kebab), slug, "
        "random (secure random integer/float/choice/sample/password), uuid, sort_lines, dedupe_lines, extract "
        "(emails/urls/numbers/dates from text)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["count", "diff", "regex_find", "regex_replace", "hash", "base64_encode", "base64_decode", "url_encode", "url_decode", "case", "slug", "random", "uuid", "sort_lines", "dedupe_lines", "extract"]},
            "text": {"type": "string"},
            "text2": {"type": "string", "description": "Second text for diff."},
            "pattern": {"type": "string", "description": "Regex pattern."},
            "replacement": {"type": "string"},
            "flags": {"type": "string", "description": "Regex flags: i (ignore case), m (multiline), s (dotall)."},
            "algorithm": {"type": "string", "description": "Hash algorithm (sha256 default) or case style (upper/lower/title/snake/camel/kebab) or extract kind (emails/urls/numbers/dates)."},
            "min": {"type": "number"}, "max": {"type": "number"},
            "count_n": {"type": "integer", "description": "How many random values / UUIDs / password length."},
            "choices": {"type": "array", "items": {"type": "string"}, "description": "Items for random choice/sample."},
        },
        "required": ["operation"],
    }

    async def execute(self, **kwargs) -> str:
        try:
            return json.dumps(self._run(kwargs), default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _run(self, k: dict) -> dict:
        op = k["operation"]
        text = k.get("text") or ""
        if op == "count":
            words = re.findall(r"\b\w[\w'-]*\b", text)
            sentences = [s for s in re.split(r"[.!?]+\s", text.strip()) if s.strip()]
            return {"characters": len(text), "characters_no_spaces": len(text.replace(" ", "")), "words": len(words),
                    "lines": text.count("\n") + (1 if text else 0), "sentences": len(sentences), "paragraphs": len([p for p in text.split("\n\n") if p.strip()]),
                    "reading_time_minutes": round(len(words) / 220, 1), "unique_words": len({w.lower() for w in words})}
        if op == "diff":
            a, b = text.splitlines(), (k.get("text2") or "").splitlines()
            diff = list(difflib.unified_diff(a, b, "text", "text2", lineterm="", n=2))
            ratio = difflib.SequenceMatcher(None, text, k.get("text2") or "").ratio()
            return {"similarity": round(ratio, 4), "added": sum(1 for l in diff if l.startswith("+") and not l.startswith("+++")),
                    "removed": sum(1 for l in diff if l.startswith("-") and not l.startswith("---")), "diff": "\n".join(diff[:400])}
        flags = 0
        for ch in (k.get("flags") or ""):
            flags |= {"i": re.I, "m": re.M, "s": re.S}.get(ch, 0)
        if op == "regex_find":
            matches = [{"match": m.group(0), "start": m.start(), "groups": m.groups()} for m in re.finditer(k.get("pattern") or "", text, flags)]
            return {"count": len(matches), "matches": matches[:200]}
        if op == "regex_replace":
            out, n = re.subn(k.get("pattern") or "", k.get("replacement") or "", text, flags=flags)
            return {"replacements": n, "result": out}
        if op == "hash":
            algo = (k.get("algorithm") or "sha256").lower()
            if algo not in hashlib.algorithms_available:
                raise ValueError(f"unknown algorithm {algo}")
            return {"algorithm": algo, "hex": hashlib.new(algo, text.encode()).hexdigest()}
        if op == "base64_encode":
            return {"result": base64.b64encode(text.encode()).decode()}
        if op == "base64_decode":
            return {"result": base64.b64decode(text.encode()).decode("utf-8", errors="replace")}
        if op == "url_encode":
            return {"result": urllib.parse.quote(text, safe="")}
        if op == "url_decode":
            return {"result": urllib.parse.unquote(text)}
        if op == "case":
            style = (k.get("algorithm") or "lower").lower()
            words = re.findall(r"[A-Za-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", text))
            res = {"upper": text.upper(), "lower": text.lower(), "title": text.title(), "snake": "_".join(w.lower() for w in words),
                   "kebab": "-".join(w.lower() for w in words), "camel": (words[0].lower() + "".join(w.title() for w in words[1:])) if words else ""}.get(style)
            if res is None:
                raise ValueError("style must be upper/lower/title/snake/camel/kebab")
            return {"result": res}
        if op == "slug":
            s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
            return {"result": re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")}
        if op == "random":
            n = int(k.get("count_n") or 1)
            if k.get("choices"):
                ch = list(k["choices"])
                return {"result": [secrets.choice(ch) for _ in range(n)] if n > len(ch) else secrets.SystemRandom().sample(ch, n)}
            if k.get("min") is not None or k.get("max") is not None:
                lo, hi = float(k.get("min") or 0), float(k.get("max") if k.get("max") is not None else 100)
                if float(lo).is_integer() and float(hi).is_integer():
                    return {"result": [secrets.randbelow(int(hi) - int(lo) + 1) + int(lo) for _ in range(n)]}
                rnd = secrets.SystemRandom()
                return {"result": [round(rnd.uniform(lo, hi), 6) for _ in range(n)]}
            alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_"
            length = int(k.get("count_n") or 16)
            return {"result": "".join(secrets.choice(alphabet) for _ in range(length)), "kind": "password"}
        if op == "uuid":
            return {"result": [str(uuid.uuid4()) for _ in range(int(k.get("count_n") or 1))]}
        if op == "sort_lines":
            return {"result": "\n".join(sorted(text.splitlines(), key=str.lower))}
        if op == "dedupe_lines":
            seen, out = set(), []
            for line in text.splitlines():
                if line not in seen:
                    seen.add(line)
                    out.append(line)
            return {"result": "\n".join(out), "removed": len(text.splitlines()) - len(out)}
        if op == "extract":
            kind = (k.get("algorithm") or "emails").lower()
            pat = {"emails": r"[\w.+-]+@[\w-]+\.[\w.-]+", "urls": r"https?://[^\s)\]>\"']+", "numbers": r"[-+]?\d[\d,]*(?:\.\d+)?",
                   "dates": r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? \d{4})\b"}.get(kind)
            if not pat:
                raise ValueError("kind must be emails/urls/numbers/dates")
            found = re.findall(pat, text)
            return {"kind": kind, "count": len(found), "items": list(dict.fromkeys(found))[:300]}
        raise ValueError(f"unknown operation {op}")


@register_tool("json_tools")
class JsonTools(BaseTool):
    name = "json_tools"
    description = (
        "Work with JSON/YAML data. Operations: validate (syntax errors with position), format (pretty/minify), "
        "query (dotted path with [index] and wildcards, e.g. items[*].price), schema_validate (against a JSON Schema), "
        "to_yaml, from_yaml, to_csv (array of objects → CSV), keys (structure summary), diff (structural diff of two documents)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["validate", "format", "query", "schema_validate", "to_yaml", "from_yaml", "to_csv", "keys", "diff"]},
            "data": {"type": "string", "description": "JSON (or YAML for from_yaml) text."},
            "data2": {"type": "string", "description": "Second document for diff."},
            "path": {"type": "string", "description": "Query path, e.g. users[0].email or items[*].price"},
            "schema": {"type": "string", "description": "JSON Schema text for schema_validate."},
            "minify": {"type": "boolean"},
        },
        "required": ["operation", "data"],
    }

    async def execute(self, **kwargs) -> str:
        try:
            return json.dumps(self._run(kwargs), default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    @staticmethod
    def _query(obj, path: str):
        tokens = re.findall(r"[^.\[\]]+|\[\*\]|\[-?\d+\]", path or "")
        cur = [obj]
        for tok in tokens:
            nxt = []
            for c in cur:
                if tok == "[*]":
                    nxt.extend(c if isinstance(c, list) else list(c.values()) if isinstance(c, dict) else [])
                elif tok.startswith("["):
                    i = int(tok[1:-1])
                    if isinstance(c, list) and -len(c) <= i < len(c):
                        nxt.append(c[i])
                elif tok == "*":
                    nxt.extend(c if isinstance(c, list) else list(c.values()) if isinstance(c, dict) else [])
                elif isinstance(c, dict) and tok in c:
                    nxt.append(c[tok])
            cur = nxt
        return cur

    def _run(self, k: dict) -> dict:
        op = k["operation"]
        raw = k.get("data") or ""
        if op == "validate":
            try:
                json.loads(raw)
                return {"valid": True}
            except json.JSONDecodeError as e:
                return {"valid": False, "error": e.msg, "line": e.lineno, "column": e.colno, "position": e.pos}
        if op == "from_yaml":
            import yaml

            return {"result": yaml.safe_load(raw)}
        obj = json.loads(raw)
        if op == "format":
            return {"result": json.dumps(obj, separators=(",", ":")) if k.get("minify") else json.dumps(obj, indent=2, ensure_ascii=False)}
        if op == "query":
            res = self._query(obj, k.get("path") or "")
            return {"path": k.get("path"), "count": len(res), "result": res[0] if len(res) == 1 else res}
        if op == "schema_validate":
            import jsonschema

            schema = json.loads(k.get("schema") or "{}")
            validator = jsonschema.Draft202012Validator(schema)
            errors = [{"path": "/".join(str(p) for p in e.absolute_path), "message": e.message} for e in validator.iter_errors(obj)]
            return {"valid": not errors, "errors": errors[:50]}
        if op == "to_yaml":
            import yaml

            return {"result": yaml.safe_dump(obj, sort_keys=False, allow_unicode=True)}
        if op == "to_csv":
            import csv
            import io

            rows = obj if isinstance(obj, list) else [obj]
            if not rows or not all(isinstance(r, dict) for r in rows):
                raise ValueError("to_csv needs an array of objects")
            cols = list(dict.fromkeys(c for r in rows for c in r))
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
            return {"result": buf.getvalue(), "rows": len(rows), "columns": cols}
        if op == "keys":
            def summarize(o, depth=0):
                if depth > 4:
                    return "…"
                if isinstance(o, dict):
                    return {kk: summarize(v, depth + 1) for kk, v in list(o.items())[:50]}
                if isinstance(o, list):
                    return [summarize(o[0], depth + 1)] if o else []
                return type(o).__name__
            return {"structure": summarize(obj)}
        if op == "diff":
            other = json.loads(k.get("data2") or "null")
            changes = []

            def walk(a, b, path=""):
                if isinstance(a, dict) and isinstance(b, dict):
                    for key in set(a) | set(b):
                        p = f"{path}.{key}" if path else key
                        if key not in a:
                            changes.append({"path": p, "change": "added", "value": b[key]})
                        elif key not in b:
                            changes.append({"path": p, "change": "removed", "value": a[key]})
                        else:
                            walk(a[key], b[key], p)
                elif isinstance(a, list) and isinstance(b, list):
                    for i in range(max(len(a), len(b))):
                        p = f"{path}[{i}]"
                        if i >= len(a):
                            changes.append({"path": p, "change": "added", "value": b[i]})
                        elif i >= len(b):
                            changes.append({"path": p, "change": "removed", "value": a[i]})
                        else:
                            walk(a[i], b[i], p)
                elif a != b:
                    changes.append({"path": path, "change": "modified", "from": a, "to": b})
            walk(obj, other)
            return {"changes": changes[:300], "count": len(changes)}
        raise ValueError(f"unknown operation {op}")
