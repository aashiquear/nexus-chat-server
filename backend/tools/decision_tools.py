"""Structured decision analysis (weighted criteria + sensitivity)."""

from __future__ import annotations

import json

from . import BaseTool, register_tool


@register_tool("decision_compare")
class DecisionCompareTool(BaseTool):
    name = "decision_compare"
    description = (
        "Compare options (products, jobs, apartments, tools, plans…) with a weighted decision matrix. Give options, "
        "criteria with weights (importance) and a direction (higher or lower is better), and a score per option per "
        "criterion (raw numbers like price/€ or ratings 1-10). Optional hard constraints eliminate options. Returns "
        "normalised scores, a ranking, the winner's margin, and a sensitivity analysis showing how much each weight "
        "must change to flip the top choice. Renders an interactive table and chart."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}, "description": "Option names."},
            "criteria": {
                "type": "array",
                "items": {"type": "object", "properties": {
                    "name": {"type": "string"},
                    "weight": {"type": "number", "description": "Importance (any positive scale)."},
                    "direction": {"type": "string", "enum": ["higher", "lower"], "description": "Whether higher or lower raw values are better (default higher)."},
                    "min": {"type": "number", "description": "Hard constraint: eliminate options below this."},
                    "max": {"type": "number", "description": "Hard constraint: eliminate options above this."},
                }, "required": ["name", "weight"]},
            },
            "scores": {"type": "object", "description": "{option: {criterion: raw value}}", "additionalProperties": {"type": "object"}},
        },
        "required": ["options", "criteria", "scores"],
    }

    async def execute(self, **kwargs) -> str:
        try:
            return json.dumps(self._run(kwargs), default=str)
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _compute(self, options, criteria, scores, weights):
        total_w = sum(weights.values()) or 1.0
        norm = {}
        for c in criteria:
            name = c["name"]
            vals = [float(scores[o][name]) for o in options]
            lo, hi = min(vals), max(vals)
            for o, v in zip(options, vals):
                n = 1.0 if hi == lo else (v - lo) / (hi - lo)
                if (c.get("direction") or "higher") == "lower":
                    n = 1.0 - n
                norm.setdefault(o, {})[name] = n
        totals = {o: sum(norm[o][c["name"]] * weights[c["name"]] for c in criteria) / total_w for o in options}
        return norm, totals

    def _run(self, k: dict) -> dict:
        options = list(k["options"])
        criteria = list(k["criteria"])
        scores = k["scores"]
        for o in options:
            if o not in scores:
                raise ValueError(f"missing scores for option '{o}'")
            for c in criteria:
                if c["name"] not in scores[o]:
                    raise ValueError(f"missing score for '{o}' / '{c['name']}'")
        # constraints
        eliminated = {}
        for o in options:
            for c in criteria:
                v = float(scores[o][c["name"]])
                if c.get("min") is not None and v < float(c["min"]):
                    eliminated[o] = f"{c['name']} {v} < min {c['min']}"
                if c.get("max") is not None and v > float(c["max"]):
                    eliminated[o] = f"{c['name']} {v} > max {c['max']}"
        alive = [o for o in options if o not in eliminated]
        if len(alive) < 1:
            return {"error": "all options eliminated by constraints", "eliminated": eliminated}
        weights = {c["name"]: float(c["weight"]) for c in criteria}
        norm, totals = self._compute(alive, criteria, scores, weights)
        ranking = sorted(alive, key=lambda o: -totals[o])
        winner = ranking[0]
        runner = ranking[1] if len(ranking) > 1 else None

        # sensitivity: for each criterion, find the weight multiplier at which the winner changes
        sensitivity = []
        for c in criteria:
            name = c["name"]
            flip_at = None
            for mult in [0, 0.25, 0.5, 0.75, 1.25, 1.5, 2, 3, 5, 10]:
                w2 = dict(weights)
                w2[name] = weights[name] * mult
                _, t2 = self._compute(alive, criteria, scores, w2)
                if max(t2, key=t2.get) != winner:
                    flip_at = mult
                    break
            sensitivity.append({"criterion": name, "weight": weights[name], "weight_share": round(weights[name] / (sum(weights.values()) or 1), 3),
                                "winner_changes_if_weight_x": flip_at, "robust": flip_at is None or flip_at in (0, 10)})

        cols = [{"name": "option", "kind": "text"}] + [{"name": c["name"], "kind": "number"} for c in criteria] + [{"name": "score", "kind": "number"}, {"name": "rank", "kind": "number"}]
        rows = []
        for o in ranking:
            rows.append([o] + [scores[o][c["name"]] for c in criteria] + [round(totals[o] * 100, 1), ranking.index(o) + 1])
        for o, why in eliminated.items():
            rows.append([f"{o} (eliminated: {why})"] + [scores[o][c["name"]] for c in criteria] + [None, None])

        fig = {
            "data": [{"type": "bar", "name": c["name"], "x": ranking,
                      "y": [round(norm[o][c["name"]] * weights[c["name"]] / (sum(weights.values()) or 1) * 100, 2) for o in ranking]} for c in criteria],
            "layout": {"barmode": "stack", "title": {"text": k.get("title") or "Weighted decision matrix"}, "yaxis": {"title": "weighted score (0-100)"}, "legend": {"orientation": "h"}},
        }
        return {
            "title": k.get("title") or "Decision matrix",
            "ranking": [{"option": o, "score": round(totals[o] * 100, 1)} for o in ranking],
            "winner": winner, "margin": round((totals[winner] - totals[runner]) * 100, 1) if runner else None,
            "eliminated": eliminated or None, "sensitivity": sensitivity,
            "csv_preview": {"filename": "decision_matrix", "total_rows": len(rows), "filtered_rows": len(rows), "offset": 0, "returned_rows": len(rows), "columns": cols, "rows": rows, "stats": None},
            "figure_json": fig,
            "explanation": "Scores are min-max normalised per criterion (direction-aware), weighted and summed. Sensitivity shows the weight multiplier at which another option would take first place.",
        }
