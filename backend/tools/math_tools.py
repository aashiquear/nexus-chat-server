"""Math & numbers: safe calculator, symbolic solver, unit conversion, statistics."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import math
import operator
import re

from . import BaseTool, register_tool

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Safe calculator (AST walker — no eval)
# ----------------------------------------------------------------------
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {
    "sqrt": math.sqrt, "abs": abs, "round": round, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2, "sinh": math.sinh, "cosh": math.cosh,
    "tanh": math.tanh, "log": math.log, "ln": math.log, "log10": math.log10, "log2": math.log2, "exp": math.exp,
    "pow": pow, "floor": math.floor, "ceil": math.ceil, "factorial": math.factorial, "gcd": math.gcd, "lcm": math.lcm,
    "min": min, "max": max, "sum": lambda *a: sum(a[0] if len(a) == 1 and isinstance(a[0], (list, tuple)) else a),
    "avg": lambda *a: (lambda xs: sum(xs) / len(xs))(a[0] if len(a) == 1 and isinstance(a[0], (list, tuple)) else a),
    "mean": lambda *a: (lambda xs: sum(xs) / len(xs))(a[0] if len(a) == 1 and isinstance(a[0], (list, tuple)) else a),
    "degrees": math.degrees, "radians": math.radians, "hypot": math.hypot, "comb": math.comb, "perm": math.perm,
}
_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}
_MAX_POW = 10_000


def safe_eval(expr: str):
    expr = expr.strip().replace("^", "**").replace("×", "*").replace("÷", "/")
    # Thousands separators (1,234,567) are dropped only when the comma sits
    # between digit groups of exactly three; argument/list separators stay.
    expr = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", expr)
    # Postfix percentage (15%, 15% * 80, (15%)) vs binary modulo (10 % 3):
    # '%' is a percentage only when it is followed by the end of the
    # expression, a closing bracket/comma, or a * / ^ operator; when a
    # number, '(' or a sign follows it is Python's modulo operator.
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%(?=\s*(?:$|[)\],*/^]|\*\*))", r"(\1/100)", expr)
    tree = ast.parse(expr, mode="eval")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            l, r = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(r) > _MAX_POW:
                raise ValueError("exponent too large")
            return _BIN[type(node.op)](l, r)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
            return _UN[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Name):
            if node.id in _CONSTS:
                return _CONSTS[node.id]
            raise ValueError(f"unknown name '{node.id}'")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        if isinstance(node, (ast.List, ast.Tuple)):
            return [ev(e) for e in node.elts]
        raise ValueError(f"unsupported expression: {ast.dump(node)[:60]}")

    return ev(tree)


@register_tool("calculator")
class CalculatorTool(BaseTool):
    name = "calculator"
    description = (
        "Evaluate a mathematical expression exactly (safe parser, no code execution). Supports + - * / // ** ^, modulo "
        "(10 % 3), parentheses, postfix percentages (15% * 80, 80 * 15%), constants pi/e, and functions sqrt, log, ln, exp, sin/cos/tan, "
        "round, floor, ceil, factorial, gcd, min/max/sum/avg. Use it for any arithmetic where accuracy matters."
    )
    parameters = {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "Expression to evaluate, e.g. '(1200*1.07**5)' or 'sqrt(16)+15%*80'."},
            "precision": {"type": "integer", "description": "Decimal places to round the result to (default: full precision).", "default": None},
        },
        "required": ["expression"],
    }

    async def execute(self, **kwargs) -> str:
        expr = kwargs.get("expression", "")
        try:
            result = safe_eval(expr)
            precision = kwargs.get("precision")
            shown = round(result, int(precision)) if precision is not None and isinstance(result, float) else result
            return json.dumps({"expression": expr, "result": shown}, default=str)
        except ZeroDivisionError:
            return json.dumps({"error": "division by zero", "expression": expr})
        except Exception as e:
            return json.dumps({"error": f"{e}", "expression": expr})


# ----------------------------------------------------------------------
# Symbolic math (SymPy)
# ----------------------------------------------------------------------
@register_tool("math_solver")
class MathSolverTool(BaseTool):
    name = "math_solver"
    description = (
        "Symbolic mathematics with SymPy. Operations: solve (equations/systems), simplify, expand, factor, "
        "differentiate, integrate (indefinite or definite), limit, series, matrix (det/inverse/eigenvalues/rank), "
        "evaluate (numeric value), latex. Write expressions in Python/SymPy syntax: x**2 + 3*x - 4, sin(x)/x, "
        "Eq(2*x + 1, 7) or '2*x + 1 = 7'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["solve", "simplify", "expand", "factor", "differentiate", "integrate", "limit", "series", "matrix", "evaluate", "latex"]},
            "expression": {"type": "string", "description": "Expression or equation(s). For systems separate equations with ';'. For matrix use [[1,2],[3,4]]."},
            "variable": {"type": "string", "description": "Variable to solve for / differentiate / integrate with respect to (default x)."},
            "lower": {"type": "string", "description": "Lower bound for definite integral / limit point / series point."},
            "upper": {"type": "string", "description": "Upper bound for definite integral."},
            "order": {"type": "integer", "description": "Derivative order or series order (default 1 / 6)."},
            "matrix_op": {"type": "string", "enum": ["det", "inverse", "eigenvalues", "eigenvectors", "rank", "transpose", "rref"], "description": "For operation=matrix."},
            "substitutions": {"type": "object", "description": "Values to substitute for evaluate, e.g. {\"x\": 2}."},
        },
        "required": ["operation", "expression"],
    }

    async def execute(self, **kwargs) -> str:
        try:
            return await asyncio.wait_for(asyncio.to_thread(self._run, kwargs), timeout=30)
        except asyncio.TimeoutError:
            return json.dumps({"error": "computation timed out (30s)"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    # SymPy's parse_expr ultimately eval()s Python, so untrusted input is
    # validated against a restricted grammar first: only numbers, operators,
    # brackets, commas, '=' and identifiers that are either allow-listed
    # SymPy functions/constants or short symbol names. No attribute access,
    # strings, keywords or dunder names ever reach the parser.
    ALLOWED_NAMES = {
        "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sinh", "cosh", "tanh", "asinh", "acosh", "atanh",
        "exp", "log", "ln", "sqrt", "cbrt", "root", "Abs", "abs", "sign", "floor", "ceiling", "ceil", "factorial",
        "gamma", "binomial", "Rational", "Integer", "Float", "pi", "E", "I", "oo", "zoo", "nan", "Eq", "Ne",
        "Lt", "Le", "Gt", "Ge", "And", "Or", "Not", "Max", "Min", "Sum", "Product", "Piecewise", "Derivative",
        "Integral", "Limit", "Symbol", "symbols", "diff", "integrate", "limit", "simplify", "expand", "factor",
        "sec", "csc", "cot", "erf", "re", "im", "conjugate", "Matrix", "det", "inf", "infinity",
    }
    _TOKEN_RE = re.compile(r"\s*(?:(\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+)|([A-Za-z_][A-Za-z0-9_]*)|(\*\*|<=|>=|==|!=|[-+*/^(),=<>\[\]{}'!])|(.))")
    _MAX_EXPR_LEN = 2000

    @classmethod
    def validate_expression(cls, text: str) -> None:
        if len(text) > cls._MAX_EXPR_LEN:
            raise ValueError("expression too long")
        if "__" in text or re.search(r"[A-Za-z_)\]]\s*\.", text):
            raise ValueError("attribute access is not allowed in expressions")
        pos = 0
        for m in cls._TOKEN_RE.finditer(text):
            num, ident, op, bad = m.groups()
            if bad is not None and bad.strip():
                raise ValueError(f"character {bad!r} is not allowed in expressions")
            if ident is not None:
                if ident in ("lambda", "import", "exec", "eval", "open", "def", "class", "global", "nonlocal", "yield", "return", "del"):
                    raise ValueError(f"'{ident}' is not allowed in expressions")
                if ident not in cls.ALLOWED_NAMES and len(ident) > 3:
                    raise ValueError(
                        f"unknown name '{ident}'. Use single-letter symbols (x, y, t, a1…) and SymPy functions "
                        f"like sin, cos, exp, log, sqrt, pi, E."
                    )
            pos = m.end()
        if pos < len(text.rstrip()):
            raise ValueError("could not tokenise expression")

    def _run(self, k: dict) -> str:
        import sympy as sp
        from sympy.parsing.sympy_parser import (convert_xor, implicit_multiplication_application, parse_expr,
                                                standard_transformations)

        tr = standard_transformations + (implicit_multiplication_application, convert_xor)
        # Explicit, minimal namespace: only the allow-listed SymPy names.
        global_dict = {name: getattr(sp, name) for name in self.ALLOWED_NAMES if hasattr(sp, name)}
        global_dict.update({"ln": sp.log, "ceil": sp.ceiling, "abs": sp.Abs, "inf": sp.oo, "infinity": sp.oo})
        local = {}

        def parse(text: str):
            text = text.strip()
            self.validate_expression(text)
            if "=" in text and "==" not in text and "<=" not in text and ">=" not in text and not text.startswith("Eq("):
                lhs, rhs = text.split("=", 1)
                return sp.Eq(parse_expr(lhs, transformations=tr, local_dict=local, global_dict=dict(global_dict)),
                             parse_expr(rhs, transformations=tr, local_dict=local, global_dict=dict(global_dict)))
            return parse_expr(text, transformations=tr, local_dict=local, global_dict=dict(global_dict))

        op = k["operation"]
        expr_text = k["expression"]
        var_name = k.get("variable") or "x"
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,2}", var_name):
            raise ValueError("variable must be a short symbol name like x, y, t")
        var = sp.Symbol(var_name)
        out: dict = {"operation": op, "input": expr_text}

        if op == "matrix":
            raw = expr_text.strip()
            if not raw.startswith("["):
                raise ValueError("matrix must be given as a JSON-style list of rows, e.g. [[1,2],[3,4]]")
            rows = json.loads(raw)
            if not isinstance(rows, list) or not all(isinstance(r, list) for r in rows):
                raise ValueError("matrix must be a list of rows")
            M = sp.Matrix([[parse(str(c)) if isinstance(c, str) else sp.nsimplify(c) for c in r] for r in rows])
            mop = k.get("matrix_op") or "det"
            res = {
                "det": lambda: M.det(), "inverse": lambda: M.inv(), "eigenvalues": lambda: M.eigenvals(),
                "eigenvectors": lambda: [(val, mult, [list(v) for v in vecs]) for val, mult, vecs in M.eigenvects()],
                "rank": lambda: M.rank(), "transpose": lambda: M.T, "rref": lambda: M.rref()[0],
            }[mop]()
            out.update({"matrix_op": mop, "result": str(res), "latex": sp.latex(res) if hasattr(res, "free_symbols") or isinstance(res, sp.Matrix) else None})
            return json.dumps(out, default=str)

        if op == "solve":
            parts = [p for p in re.split(r"[;\n]", expr_text) if p.strip()]
            eqs = [parse(p) for p in parts]
            symbols = sorted({s for e in eqs for s in e.free_symbols}, key=lambda s: s.name)
            if k.get("variable"):
                target = [var]
            else:
                target = symbols if len(eqs) > 1 else symbols[:1] or [var]
            sol = sp.solve(eqs if len(eqs) > 1 else eqs[0], target, dict=True)
            out["solutions"] = [{str(s): str(v) for s, v in d.items()} for d in sol]
            out["numeric"] = [{str(s): (float(v) if v.is_real else str(sp.N(v))) for s, v in d.items() if v.is_number} for d in sol]
            out["latex"] = [sp.latex(d) for d in sol]
            return json.dumps(out, default=str)

        e = parse(expr_text)
        if isinstance(e, sp.Eq):
            e = e.lhs - e.rhs
        if op == "simplify":
            res = sp.simplify(e)
        elif op == "expand":
            res = sp.expand(e)
        elif op == "factor":
            res = sp.factor(e)
        elif op == "differentiate":
            res = sp.diff(e, var, int(k.get("order") or 1))
        elif op == "integrate":
            if k.get("lower") is not None and k.get("upper") is not None:
                res = sp.integrate(e, (var, parse(str(k["lower"])), parse(str(k["upper"]))))
            else:
                res = sp.integrate(e, var)
        elif op == "limit":
            point = parse(str(k.get("lower") or "oo"))
            res = sp.limit(e, var, point)
        elif op == "series":
            point = parse(str(k.get("lower") or "0"))
            res = sp.series(e, var, point, int(k.get("order") or 6))
        elif op == "evaluate":
            # Substitution keys must be plain symbols and values go through the
            # same validated parser as expressions (sympify() would eval
            # arbitrary Python).
            subs = {}
            for name, value in (k.get("substitutions") or {}).items():
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,2}", str(name)):
                    raise ValueError(f"invalid substitution variable '{name}'")
                if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                    raise ValueError(f"substitution for '{name}' must be a number or expression string")
                subs[sp.Symbol(str(name))] = sp.nsimplify(value) if isinstance(value, (int, float)) else parse(str(value))
            res = sp.N(e.subs(subs), 15)
        elif op == "latex":
            res = e
        else:
            return json.dumps({"error": f"unknown operation {op}"})
        out["result"] = str(res)
        out["latex"] = sp.latex(res)
        try:
            if res.is_number:
                out["numeric"] = float(res)
        except Exception:
            pass
        return json.dumps(out, default=str)


# ----------------------------------------------------------------------
# Unit conversion (pint)
# ----------------------------------------------------------------------
@register_tool("unit_converter")
class UnitConverterTool(BaseTool):
    name = "unit_converter"
    description = (
        "Convert between physical units with dimensional checking: length, area, volume, mass, temperature, "
        "time, speed, energy, power, pressure, data size (bytes/MB/GiB), angles, fuel economy, etc. "
        "Examples: 5 miles → km, 72 fahrenheit → celsius, 3 kWh → joule, 1.5 GiB → MB."
    )
    parameters = {
        "type": "object",
        "properties": {
            "value": {"type": "number", "description": "Numeric quantity."},
            "from_unit": {"type": "string", "description": "Source unit, e.g. 'mile', 'degF', 'kilowatt_hour', 'GiB', 'mph', 'psi'."},
            "to_unit": {"type": "string", "description": "Target unit, e.g. 'km', 'degC', 'joule', 'MB', 'km/h', 'bar'."},
        },
        "required": ["value", "from_unit", "to_unit"],
    }
    _ALIASES = {"f": "degF", "fahrenheit": "degF", "°f": "degF", "c": "degC", "celsius": "degC", "°c": "degC", "k": "kelvin",
                "mph": "mile/hour", "kph": "km/hour", "kmh": "km/hour", "mpg": "mile/gallon", "psi": "psi", "sqft": "foot**2",
                "sqm": "meter**2", "cc": "cm**3", "lbs": "pound", "lb": "pound", "oz": "ounce", "tbsp": "tablespoon", "tsp": "teaspoon",
                "mb": "megabyte", "gb": "gigabyte", "tb": "terabyte", "kb": "kilobyte", "mib": "mebibyte", "gib": "gibibyte", "kib": "kibibyte"}

    async def execute(self, **kwargs) -> str:
        try:
            import pint
        except ImportError:
            return json.dumps({"error": "pint is not installed"})
        ureg = pint.UnitRegistry()
        ureg.default_format = "~P"

        def norm(u: str) -> str:
            u = (u or "").strip()
            return self._ALIASES.get(u.lower(), u)

        try:
            value = float(kwargs["value"])
            src, dst = norm(kwargs["from_unit"]), norm(kwargs["to_unit"])
            q = ureg.Quantity(value, src)
            res = q.to(dst)
            return json.dumps({
                "input": f"{value:g} {src}", "result": float(res.magnitude), "unit": str(res.units),
                "formatted": f"{value:g} {src} = {float(res.magnitude):.6g} {res.units}",
            })
        except pint.errors.DimensionalityError as e:
            return json.dumps({"error": f"incompatible units: {e}"})
        except Exception as e:
            return json.dumps({"error": f"{e}. Use pint unit names like meter, mile, degC, kilogram, liter, joule, byte."})


# ----------------------------------------------------------------------
# Statistics
# ----------------------------------------------------------------------
@register_tool("statistics_analyze")
class StatisticsTool(BaseTool):
    name = "statistics_analyze"
    description = (
        "Statistical analysis of numeric data given inline or as columns of an uploaded CSV/Excel file. "
        "Analyses: describe (mean, median, std, quartiles, skew, outliers), correlation (Pearson/Spearman between two "
        "series or all numeric columns), regression (linear y~x with slope, intercept, R², p-value), ttest (one- or "
        "two-sample Welch), chi_square (contingency table), normality (Shapiro-Wilk), percentile."
    )
    parameters = {
        "type": "object",
        "properties": {
            "analysis": {"type": "string", "enum": ["describe", "correlation", "regression", "ttest", "chi_square", "normality", "percentile"]},
            "data": {"type": "array", "items": {"type": "number"}, "description": "Primary numeric series (inline)."},
            "data2": {"type": "array", "items": {"type": "number"}, "description": "Second series (correlation, regression y, two-sample ttest)."},
            "filename": {"type": "string", "description": "Uploaded CSV/XLSX to read columns from instead of inline data."},
            "column": {"type": "string", "description": "Column name for the primary series (with filename)."},
            "column2": {"type": "string", "description": "Second column name."},
            "table": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}, "description": "Contingency table for chi_square."},
            "mu": {"type": "number", "description": "Population mean for one-sample ttest."},
            "percentiles": {"type": "array", "items": {"type": "number"}, "description": "Percentiles to compute (0-100)."},
        },
        "required": ["analysis"],
    }

    async def execute(self, **kwargs) -> str:
        try:
            return await asyncio.to_thread(self._run, kwargs)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _load(self, k: dict):
        import numpy as np

        if k.get("filename"):
            import pandas as pd
            from pathlib import Path

            p = self.upload_dir() / Path(k["filename"]).name
            if not p.exists():
                raise FileNotFoundError(f"{k['filename']} not uploaded")
            df = pd.read_excel(p) if p.suffix.lower() in (".xlsx", ".xls") else pd.read_csv(p, sep=None, engine="python")
            if k.get("column") and k.get("column2") and k.get("analysis") in ("correlation", "regression"):
                # Paired analyses: drop a row when *either* value is missing
                # so observations stay aligned. Independent-sample tests
                # (ttest) keep each column's own non-missing values.
                pair = pd.DataFrame({
                    "a": pd.to_numeric(df[k["column"]], errors="coerce"),
                    "b": pd.to_numeric(df[k["column2"]], errors="coerce"),
                }).dropna()
                return pair["a"].to_numpy(), pair["b"].to_numpy(), df
            a = pd.to_numeric(df[k["column"]], errors="coerce").dropna().to_numpy() if k.get("column") else None
            b = pd.to_numeric(df[k["column2"]], errors="coerce").dropna().to_numpy() if k.get("column2") else None
            return a, b, df
        a = np.asarray(k.get("data") or [], dtype=float) if k.get("data") is not None else None
        b = np.asarray(k.get("data2") or [], dtype=float) if k.get("data2") is not None else None
        return a, b, None

    def _run(self, k: dict) -> str:
        import numpy as np

        try:
            from scipy import stats
        except ImportError:
            stats = None
        analysis = k["analysis"]
        a, b, df = self._load(k)
        out: dict = {"analysis": analysis}

        if analysis == "describe":
            if a is None or len(a) == 0:
                raise ValueError("data (or filename+column) is required")
            q1, med, q3 = np.percentile(a, [25, 50, 75])
            iqr = q3 - q1
            outliers = a[(a < q1 - 1.5 * iqr) | (a > q3 + 1.5 * iqr)]
            out.update({
                "n": int(len(a)), "mean": float(a.mean()), "median": float(med), "std": float(a.std(ddof=1)) if len(a) > 1 else 0.0,
                "min": float(a.min()), "max": float(a.max()), "q1": float(q1), "q3": float(q3), "iqr": float(iqr),
                "sum": float(a.sum()), "outliers": [float(x) for x in outliers[:20]],
            })
            if stats is not None and len(a) > 2:
                out["skew"] = float(stats.skew(a))
                out["kurtosis"] = float(stats.kurtosis(a))
        elif analysis == "percentile":
            ps = k.get("percentiles") or [5, 25, 50, 75, 95]
            out["percentiles"] = {str(p): float(v) for p, v in zip(ps, np.percentile(a, ps))}
        elif analysis == "correlation":
            if df is not None and not k.get("column"):
                num = df.select_dtypes("number")
                out["pearson_matrix"] = json.loads(num.corr().round(4).to_json())
                out["columns"] = list(num.columns)
            else:
                if a is None or b is None or len(a) != len(b):
                    raise ValueError("two series of equal length are required")
                pear = float(np.corrcoef(a, b)[0, 1])
                out["pearson_r"] = pear
                out["r_squared"] = pear ** 2
                if stats is not None:
                    out["pearson_p"] = float(stats.pearsonr(a, b)[1])
                    sr = stats.spearmanr(a, b)
                    out["spearman_rho"] = float(sr.correlation)
                    out["spearman_p"] = float(sr.pvalue)
        elif analysis == "regression":
            if a is None or b is None or len(a) != len(b):
                raise ValueError("x (data) and y (data2) of equal length are required")
            slope, intercept = np.polyfit(a, b, 1)
            pred = slope * a + intercept
            ss_res = float(((b - pred) ** 2).sum())
            ss_tot = float(((b - b.mean()) ** 2).sum()) or 1.0
            out.update({"slope": float(slope), "intercept": float(intercept), "r_squared": 1 - ss_res / ss_tot,
                        "equation": f"y = {slope:.6g}·x + {intercept:.6g}", "n": int(len(a))})
            if stats is not None:
                lr = stats.linregress(a, b)
                out["p_value"] = float(lr.pvalue)
                out["std_err"] = float(lr.stderr)
        elif analysis == "ttest":
            if stats is None:
                raise ValueError("scipy is required for t-tests")
            if b is not None and len(b):
                t = stats.ttest_ind(a, b, equal_var=False)
                out.update({"test": "Welch two-sample t-test", "t": float(t.statistic), "p_value": float(t.pvalue), "mean1": float(a.mean()), "mean2": float(b.mean())})
            else:
                mu = float(k.get("mu") or 0.0)
                t = stats.ttest_1samp(a, mu)
                out.update({"test": f"one-sample t-test vs {mu}", "t": float(t.statistic), "p_value": float(t.pvalue), "mean": float(a.mean())})
            out["significant_at_0.05"] = out["p_value"] < 0.05
        elif analysis == "chi_square":
            if stats is None:
                raise ValueError("scipy is required for chi-square")
            table = np.asarray(k.get("table") or [], dtype=float)
            chi2, p, dof, expected = stats.chi2_contingency(table)
            out.update({"chi2": float(chi2), "p_value": float(p), "dof": int(dof), "expected": expected.round(3).tolist(), "significant_at_0.05": p < 0.05})
        elif analysis == "normality":
            if stats is None:
                raise ValueError("scipy is required for normality tests")
            w, p = stats.shapiro(a[:5000])
            out.update({"test": "Shapiro-Wilk", "W": float(w), "p_value": float(p), "normal_at_0.05": p >= 0.05})
        return json.dumps(out, default=str)
