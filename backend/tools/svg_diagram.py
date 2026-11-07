"""
SVG Architecture Diagram Generator tool.

The LLM produces SVG markup describing an architecture diagram and this
tool validates, saves, and returns it so the frontend can render it
inline.
"""

import json
import re
import uuid
from pathlib import Path

from . import BaseTool, register_tool


@register_tool("svg_diagram")
class SVGDiagramTool(BaseTool):
    name = "svg_diagram"
    description = (
        "Generate an SVG architecture diagram. Provide the SVG markup for the "
        "diagram and an optional title. The tool validates the SVG and returns "
        "it for display. Use standard SVG elements (rect, text, line, path, "
        "circle, polygon, marker, defs) to draw boxes, arrows, and labels."
    )
    parameters = {
        "type": "object",
        "properties": {
            "svg": {
                "type": "string",
                "description": (
                    "Complete SVG markup string starting with <svg ...> and ending "
                    "with </svg>. Use a viewBox for responsive sizing, e.g. "
                    'viewBox="0 0 800 600". Include rects for boxes, text for '
                    "labels, and line/path with markers for arrows."
                ),
            },
            "title": {
                "type": "string",
                "description": "Short title for the diagram (shown above it).",
            },
        },
        "required": ["svg"],
    }

    async def execute(self, **kwargs) -> str:
        raw_svg: str = kwargs.get("svg", "")
        title: str = kwargs.get("title", "Architecture Diagram")

        # Basic validation
        if not raw_svg.strip():
            return json.dumps({"error": "Empty SVG markup provided."})

        # Ensure it contains an <svg> root
        if not re.search(r"<svg[\s>]", raw_svg, re.IGNORECASE):
            return json.dumps({"error": "Markup must contain an <svg> element."})

        if "</svg>" not in raw_svg.lower():
            return json.dumps({"error": "SVG markup is missing closing </svg> tag."})

        # Extract just the <svg>...</svg> portion (strip surrounding text)
        match = re.search(
            r"(<svg[\s\S]*?</svg>)", raw_svg, re.IGNORECASE
        )
        if not match:
            return json.dumps({"error": "Could not extract valid SVG element."})

        svg_clean = match.group(1)
        # Strip active content: scripts, event handlers, javascript: URLs,
        # foreignObject (can embed HTML). The UI renders SVG via <img>, but
        # the file is also served from /api/files so keep it inert on disk.
        svg_clean = re.sub(r"<script\b[\s\S]*?</script\s*>", "", svg_clean, flags=re.IGNORECASE)
        svg_clean = re.sub(r"<foreignObject\b[\s\S]*?</foreignObject\s*>", "", svg_clean, flags=re.IGNORECASE)
        svg_clean = re.sub(r"\s+on[a-zA-Z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", svg_clean)
        svg_clean = re.sub(r"(href|xlink:href)\s*=\s*([\"'])\s*javascript:[^\"']*\2", r"\1=\2#\2", svg_clean, flags=re.IGNORECASE)

        # Ensure xmlns is present for standalone rendering
        if "xmlns" not in svg_clean:
            svg_clean = svg_clean.replace(
                "<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1
            )

        # Save to data/uploads so it's accessible via file reader too
        output_dir = Path(self.ctx.files_dir) if self.ctx.username is not None else Path(
            self.config.get("output_dir", "./data/uploads")
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"diagram-{uuid.uuid4().hex[:8]}.svg"
        filepath = output_dir / filename
        filepath.write_text(svg_clean, encoding="utf-8")

        return json.dumps({
            "title": title,
            "svg": svg_clean,
            "filename": filename,
            "path": str(filepath),
        })
