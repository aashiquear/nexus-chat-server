"""
Graph Plotter tool.

Uses matplotlib/seaborn to generate publication-quality plots from CSV data
or LLM-provided numeric data.

Supported chart types
─────────────────────
  line, scatter, scatter_line, bar, grouped_bar, stacked_bar, count,
  pie, histogram, box, violin, area, heatmap, parity, regression

Advanced formatting
───────────────────
  - Per-series line color, marker style, line style
  - Figure size, DPI, font size
  - Annotation, legend placement, axis limits
  - Seaborn theming (when available)

Saves the generated plot as a PNG in the data directory.
"""

import json
import uuid
import csv
import io
import os
from pathlib import Path

from . import BaseTool, register_tool


@register_tool("graph_plotter")
class GraphPlotterTool(BaseTool):
    name = "graph_plotter"
    description = (
        "Plot publication-quality graphs from CSV files or numeric data. "
        "Supported chart types: line, scatter, scatter_line, bar, grouped_bar, "
        "stacked_bar, count, pie, histogram, box, violin, area, heatmap, "
        "parity (predicted vs actual), regression (with trend line). "
        "Provide either a csv_filename (from uploads) and column names, or "
        "direct x/y data arrays. Supports advanced formatting: per-series "
        "colors, markers, line styles, annotations, figsize, and more. "
        "Returns the saved PNG path for display."
    )
    parameters = {
        "type": "object",
        "properties": {
            "chart_type": {
                "type": "string",
                "enum": [
                    "line", "scatter", "scatter_line", "bar", "grouped_bar",
                    "stacked_bar", "count", "pie", "histogram", "box",
                    "violin", "area", "heatmap", "parity", "regression",
                ],
                "description": "Type of chart to plot.",
            },
            "title": {
                "type": "string",
                "description": "Title for the chart.",
            },
            "x_label": {
                "type": "string",
                "description": "Label for the x-axis.",
            },
            "y_label": {
                "type": "string",
                "description": "Label for the y-axis.",
            },
            "csv_filename": {
                "type": "string",
                "description": "Name of an uploaded CSV file to read data from.",
            },
            "x_column": {
                "type": "string",
                "description": "Column name in CSV to use for x-axis data.",
            },
            "y_column": {
                "type": "string",
                "description": "Column name in CSV to use for y-axis data.",
            },
            "y_columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Multiple column names for multi-series plots.",
            },
            "group_column": {
                "type": "string",
                "description": "Column name used for grouping / hue (count, box, violin, grouped_bar).",
            },
            "x_data": {
                "type": "array",
                "items": {},
                "description": "Direct x-axis data array (numbers or labels).",
            },
            "y_data": {
                "type": "array",
                "items": {},
                "description": "Direct y-axis data array (numbers).",
            },
            "y_data_series": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "data": {"type": "array", "items": {}},
                        "color": {"type": "string"},
                        "marker": {"type": "string"},
                        "linestyle": {"type": "string"},
                    },
                },
                "description": (
                    "Multiple y-data series for multi-line/bar plots. "
                    "Each series can have its own color, marker, and linestyle."
                ),
            },
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Labels for pie chart slices or legend entries.",
            },
            "color": {
                "type": "string",
                "description": "Primary color (e.g. '#5a8a7a', 'steelblue').",
            },
            "colors": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Color palette for multi-series or pie charts.",
            },
            "marker": {
                "type": "string",
                "description": "Marker style for scatter/line (e.g. 'o', 's', '^', 'D').",
            },
            "linestyle": {
                "type": "string",
                "description": "Line style (e.g. '-', '--', '-.', ':').",
            },
            "linewidth": {
                "type": "number",
                "description": "Line width in points (default 2).",
            },
            "alpha": {
                "type": "number",
                "description": "Transparency 0-1 (default varies by chart type).",
            },
            "grid": {
                "type": "boolean",
                "description": "Show grid lines (default true).",
            },
            "legend_position": {
                "type": "string",
                "description": "Legend position (e.g. 'upper right', 'lower left', 'best').",
            },
            "figsize": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Figure size as [width, height] in inches (default [10, 6]).",
            },
            "x_limit": {
                "type": "array",
                "items": {"type": "number"},
                "description": "X-axis limits as [min, max].",
            },
            "y_limit": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Y-axis limits as [min, max].",
            },
            "annotate": {
                "type": "boolean",
                "description": "Annotate data points with values (heatmap cells, bar tops, etc.).",
            },
            "bins": {
                "type": "integer",
                "description": "Number of bins for histogram (default: auto).",
            },
            "regression_degree": {
                "type": "integer",
                "description": "Polynomial degree for regression/trend line (default 1 = linear).",
            },
            "log_x": {
                "type": "boolean",
                "description": "Use logarithmic scale for x-axis.",
            },
            "log_y": {
                "type": "boolean",
                "description": "Use logarithmic scale for y-axis.",
            },
            "colormap": {
                "type": "string",
                "description": "Matplotlib colormap name for heatmap (default 'YlGnBu').",
            },
        },
        "required": ["chart_type"],
    }

    async def execute(self, **kwargs) -> str:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import numpy as np
        except ImportError:
            return json.dumps({"error": "matplotlib or numpy not installed."})

        # Try to use seaborn for nicer defaults
        try:
            import seaborn as sns
            sns.set_theme(style="whitegrid", palette="muted")
            _has_seaborn = True
        except ImportError:
            _has_seaborn = False

        chart_type = kwargs.get("chart_type", "line")
        title = kwargs.get("title", "Chart")
        x_label = kwargs.get("x_label", "")
        y_label = kwargs.get("y_label", "")
        grid = kwargs.get("grid", True)
        annotate = kwargs.get("annotate", False)

        # Resolve data from CSV or direct input
        x_data = kwargs.get("x_data")
        y_data = kwargs.get("y_data")
        y_data_series = kwargs.get("y_data_series")
        labels = kwargs.get("labels")
        csv_filename = kwargs.get("csv_filename")
        group_column = kwargs.get("group_column")

        upload_dir = Path(self.config.get("upload_dir", "./data/uploads"))

        # Dataframe for seaborn-based charts
        _df = None

        # Load from CSV if specified
        if csv_filename:
            csv_path = upload_dir / csv_filename
            if not csv_path.exists():
                return json.dumps({"error": f"CSV file not found: {csv_filename}"})
            try:
                _df = self._read_csv_to_df(csv_path)
                if _df is None:
                    x_data, y_data, y_data_series, labels = self._read_csv(
                        csv_path, kwargs
                    )
            except Exception as e:
                return json.dumps({"error": f"Failed to read CSV: {e}"})

        # Validate data availability
        err = self._validate_data(chart_type, x_data, y_data, y_data_series, _df, kwargs)
        if err:
            return json.dumps({"error": err})

        # Theme colors
        default_colors = [
            "#5a8a7a", "#c4993c", "#5a7a8a", "#b85450", "#6b9d8c",
            "#8a6b9d", "#9d8a6b", "#4a7a6a", "#7a5a8a", "#8a9d6b",
        ]
        color = kwargs.get("color", default_colors[0])
        colors = kwargs.get("colors", default_colors)
        marker = kwargs.get("marker", "o")
        linestyle = kwargs.get("linestyle", "-")
        linewidth = kwargs.get("linewidth", 2)
        alpha_default = 0.85 if chart_type in ("bar", "grouped_bar", "stacked_bar", "histogram") else 0.7
        alpha = kwargs.get("alpha", alpha_default)
        legend_pos = kwargs.get("legend_position", "best")
        figsize = kwargs.get("figsize", [10, 6])

        # Create figure
        fig, ax = plt.subplots(figsize=tuple(figsize), dpi=120)
        fig.patch.set_facecolor("#f7f6f3")
        ax.set_facecolor("#ffffff")

        try:
            # -- Dispatch to chart-specific plotters --
            if chart_type == "line":
                self._plot_line(ax, x_data, y_data, y_data_series, colors,
                                labels, marker, linestyle, linewidth, alpha, kwargs)

            elif chart_type == "scatter":
                self._plot_scatter(ax, x_data, y_data, y_data_series, colors,
                                   labels, marker, alpha, kwargs)

            elif chart_type == "scatter_line":
                self._plot_scatter_line(ax, x_data, y_data, y_data_series,
                                        colors, labels, marker, linestyle,
                                        linewidth, alpha, kwargs)

            elif chart_type == "regression":
                self._plot_regression(ax, x_data, y_data, y_data_series,
                                      colors, labels, marker, alpha, kwargs, np)

            elif chart_type == "bar":
                self._plot_bar(ax, x_data, y_data, y_data_series, colors,
                               labels, alpha, annotate, np)

            elif chart_type == "grouped_bar":
                self._plot_grouped_bar(ax, x_data, y_data_series, colors,
                                       labels, alpha, annotate, np, _df, kwargs)

            elif chart_type == "stacked_bar":
                self._plot_stacked_bar(ax, x_data, y_data_series, colors,
                                       labels, alpha, annotate, np, _df, kwargs)

            elif chart_type == "count":
                self._plot_count(ax, _df, kwargs, colors, alpha, _has_seaborn, annotate)

            elif chart_type == "pie":
                self._plot_pie(ax, y_data, labels, colors)

            elif chart_type == "histogram":
                self._plot_histogram(ax, y_data, y_data_series, colors,
                                     labels, alpha, kwargs)

            elif chart_type == "box":
                self._plot_box(ax, y_data, y_data_series, colors, _df,
                               kwargs, _has_seaborn)

            elif chart_type == "violin":
                self._plot_violin(ax, y_data, y_data_series, colors, _df,
                                  kwargs, _has_seaborn)

            elif chart_type == "area":
                self._plot_area(ax, x_data, y_data, y_data_series, colors,
                                labels, linewidth)

            elif chart_type == "heatmap":
                self._plot_heatmap(ax, y_data, y_data_series, labels, kwargs,
                                   plt, _df, annotate)

            elif chart_type == "parity":
                self._plot_parity(ax, x_data, y_data, color, marker, alpha, np)

            # -- Common formatting --
            ax.set_title(title, fontsize=14, fontweight="600", pad=12, color="#2c2c2c")
            if x_label and chart_type != "pie":
                ax.set_xlabel(x_label, fontsize=11, color="#6b6b6b")
            if y_label and chart_type != "pie":
                ax.set_ylabel(y_label, fontsize=11, color="#6b6b6b")
            if grid and chart_type not in ("pie", "heatmap"):
                ax.grid(True, alpha=0.3, linestyle="--")
            ax.tick_params(colors="#6b6b6b", labelsize=10)
            for spine in ax.spines.values():
                spine.set_color("#e2e0d8")

            # Axis limits
            if kwargs.get("x_limit"):
                ax.set_xlim(kwargs["x_limit"])
            if kwargs.get("y_limit"):
                ax.set_ylim(kwargs["y_limit"])

            # Log scale
            if kwargs.get("log_x"):
                ax.set_xscale("log")
            if kwargs.get("log_y"):
                ax.set_yscale("log")

            plt.tight_layout()

            # Save
            output_dir = Path(self.config.get("output_dir", "./data"))
            output_dir.mkdir(parents=True, exist_ok=True)
            filename = f"plot-{uuid.uuid4().hex[:8]}.png"
            filepath = output_dir / filename
            fig.savefig(filepath, bbox_inches="tight", facecolor=fig.get_facecolor())
            plt.close(fig)

            # Build a parallel Plotly figure for interactive in-chat
            # preview. The PNG remains as a static fallback and is also
            # what the canvas exports for download.
            figure_json = self._build_plotly_figure(
                chart_type, title, x_label, y_label,
                x_data, y_data, y_data_series, labels,
                colors, _df, kwargs,
            )

            payload = {
                "title": title,
                "chart_type": chart_type,
                "filename": filename,
                "path": str(filepath),
                "plot_image": filename,
            }
            if figure_json is not None:
                payload["figure_json"] = figure_json
            return json.dumps(payload)

        except Exception as e:
            plt.close(fig)
            return json.dumps({"error": f"Plot failed: {e}"})

    # ──────────────────────────── Plotly figure builder ────────────────────────────

    def _build_plotly_figure(
        self, chart_type, title, x_label, y_label,
        x_data, y_data, y_data_series, labels,
        colors, df, kwargs,
    ):
        """Construct a Plotly figure_json dict mirroring the matplotlib
        chart. Returns None if Plotly is unavailable or the chart type
        is unsupported — callers fall back to the PNG-only payload.
        """
        try:
            import plotly.graph_objects as go
            import plotly.io as pio
        except ImportError:
            return None

        try:
            data = []

            def _series_color(i, override=None):
                return override or colors[i % len(colors)]

            def _coerce_y(seq):
                out = []
                for v in seq:
                    try:
                        out.append(float(v))
                    except (TypeError, ValueError):
                        out.append(None)
                return out

            x_axis = x_data if x_data is not None else None

            if chart_type in ("line", "scatter_line"):
                mode = "lines+markers"
                if y_data_series:
                    for i, s in enumerate(y_data_series):
                        ys = _coerce_y(s["data"])
                        xs = x_axis if x_axis else list(range(len(ys)))
                        data.append(go.Scatter(
                            x=xs, y=ys, mode=mode, name=s.get("label", f"Series {i+1}"),
                            line=dict(color=_series_color(i, s.get("color"))),
                        ))
                elif y_data is not None:
                    ys = _coerce_y(y_data)
                    xs = x_axis if x_axis else list(range(len(ys)))
                    data.append(go.Scatter(x=xs, y=ys, mode=mode,
                                           name=(labels[0] if labels else "Series"),
                                           line=dict(color=_series_color(0))))

            elif chart_type == "scatter":
                if y_data_series:
                    for i, s in enumerate(y_data_series):
                        ys = _coerce_y(s["data"])
                        xs = x_axis if x_axis else list(range(len(ys)))
                        data.append(go.Scatter(
                            x=xs, y=ys, mode="markers", name=s.get("label", f"Series {i+1}"),
                            marker=dict(color=_series_color(i, s.get("color")), size=8),
                        ))
                elif y_data is not None:
                    ys = _coerce_y(y_data)
                    xs = x_axis if x_axis else list(range(len(ys)))
                    data.append(go.Scatter(x=xs, y=ys, mode="markers",
                                           marker=dict(color=_series_color(0), size=8)))

            elif chart_type == "bar":
                if y_data_series:
                    for i, s in enumerate(y_data_series):
                        ys = _coerce_y(s["data"])
                        xs = x_axis if x_axis else list(range(len(ys)))
                        data.append(go.Bar(x=xs, y=ys, name=s.get("label", f"Series {i+1}"),
                                           marker=dict(color=_series_color(i, s.get("color")))))
                elif y_data is not None:
                    ys = _coerce_y(y_data)
                    xs = x_axis if x_axis else list(range(len(ys)))
                    data.append(go.Bar(x=xs, y=ys, marker=dict(color=_series_color(0))))

            elif chart_type in ("grouped_bar", "stacked_bar"):
                series = y_data_series
                xs = x_axis
                if df is not None and kwargs.get("x_column") and kwargs.get("y_columns"):
                    xs = df[kwargs["x_column"]].astype(str).tolist()
                    series = [{"label": col, "data": df[col].tolist()} for col in kwargs["y_columns"]]
                if series:
                    for i, s in enumerate(series):
                        ys = _coerce_y(s["data"])
                        xx = xs if xs else list(range(len(ys)))
                        data.append(go.Bar(x=xx, y=ys, name=s.get("label", f"Series {i+1}"),
                                           marker=dict(color=_series_color(i, s.get("color")))))

            elif chart_type == "pie":
                if y_data is not None:
                    pie_values = _coerce_y(y_data)
                    pie_labels = labels if labels else [f"Slice {i+1}" for i in range(len(pie_values))]
                    data.append(go.Pie(values=pie_values, labels=pie_labels,
                                       marker=dict(colors=colors[:len(pie_values)])))

            elif chart_type == "histogram":
                if y_data_series:
                    for i, s in enumerate(y_data_series):
                        data.append(go.Histogram(x=_coerce_y(s["data"]),
                                                 name=s.get("label", f"Series {i+1}"),
                                                 marker=dict(color=_series_color(i, s.get("color"))),
                                                 opacity=0.6))
                elif y_data is not None:
                    data.append(go.Histogram(x=_coerce_y(y_data),
                                             marker=dict(color=_series_color(0))))

            elif chart_type == "box":
                if y_data_series:
                    for i, s in enumerate(y_data_series):
                        data.append(go.Box(y=_coerce_y(s["data"]),
                                           name=s.get("label", f"Series {i+1}"),
                                           marker=dict(color=_series_color(i, s.get("color")))))
                elif y_data is not None:
                    data.append(go.Box(y=_coerce_y(y_data),
                                       marker=dict(color=_series_color(0))))

            elif chart_type == "violin":
                if y_data_series:
                    for i, s in enumerate(y_data_series):
                        data.append(go.Violin(y=_coerce_y(s["data"]),
                                              name=s.get("label", f"Series {i+1}"),
                                              box_visible=True, meanline_visible=True,
                                              marker=dict(color=_series_color(i, s.get("color")))))
                elif y_data is not None:
                    data.append(go.Violin(y=_coerce_y(y_data),
                                          box_visible=True, meanline_visible=True))

            elif chart_type == "area":
                if y_data_series:
                    for i, s in enumerate(y_data_series):
                        ys = _coerce_y(s["data"])
                        xs = x_axis if x_axis else list(range(len(ys)))
                        data.append(go.Scatter(x=xs, y=ys, mode="lines",
                                               fill="tozeroy",
                                               name=s.get("label", f"Series {i+1}"),
                                               line=dict(color=_series_color(i, s.get("color")))))
                elif y_data is not None:
                    ys = _coerce_y(y_data)
                    xs = x_axis if x_axis else list(range(len(ys)))
                    data.append(go.Scatter(x=xs, y=ys, mode="lines", fill="tozeroy",
                                           line=dict(color=_series_color(0))))

            elif chart_type == "heatmap":
                z = None
                row_labels = None
                col_labels = labels
                if df is not None:
                    y_cols = kwargs.get("y_columns")
                    sub = df[y_cols] if y_cols else df.select_dtypes(include=[float, int])
                    if sub.shape[1] > 1:
                        corr = sub.corr()
                        z = corr.values.tolist()
                        row_labels = list(corr.columns)
                        col_labels = list(corr.columns)
                    else:
                        z = sub.values.tolist()
                elif y_data_series:
                    z = [_coerce_y(s["data"]) for s in y_data_series]
                    row_labels = [s.get("label", f"Row {i}") for i, s in enumerate(y_data_series)]
                elif y_data is not None:
                    z = y_data if isinstance(y_data[0], list) else [y_data]
                if z is not None:
                    data.append(go.Heatmap(z=z, x=col_labels, y=row_labels,
                                           colorscale=kwargs.get("colormap", "YlGnBu")))

            elif chart_type == "parity":
                if x_data is not None and y_data is not None:
                    xs = _coerce_y(x_data)
                    ys = _coerce_y(y_data)
                    data.append(go.Scatter(x=xs, y=ys, mode="markers",
                                           marker=dict(color=_series_color(0), size=8),
                                           name="Data"))
                    vmin = min([v for v in xs + ys if v is not None] or [0])
                    vmax = max([v for v in xs + ys if v is not None] or [1])
                    data.append(go.Scatter(x=[vmin, vmax], y=[vmin, vmax],
                                           mode="lines", line=dict(dash="dash", color="#b85450"),
                                           name="y = x"))

            elif chart_type == "regression":
                import numpy as _np
                degree = kwargs.get("regression_degree", 1)
                def _add_regression(xs_raw, ys_raw, label, color):
                    xs_arr = _np.array([float(v) for v in xs_raw])
                    ys_arr = _np.array([float(v) for v in ys_raw])
                    data.append(go.Scatter(x=xs_arr.tolist(), y=ys_arr.tolist(),
                                           mode="markers",
                                           marker=dict(color=color, size=8),
                                           name=f"{label} data" if label else "Data"))
                    coeffs = _np.polyfit(xs_arr, ys_arr, degree)
                    poly = _np.poly1d(coeffs)
                    xs_smooth = _np.linspace(xs_arr.min(), xs_arr.max(), 200)
                    data.append(go.Scatter(x=xs_smooth.tolist(), y=poly(xs_smooth).tolist(),
                                           mode="lines",
                                           line=dict(dash="dash", color=color),
                                           name=f"{label} fit" if label else "Fit"))
                if y_data_series:
                    for i, s in enumerate(y_data_series):
                        xs = x_axis if x_axis else list(range(len(s["data"])))
                        _add_regression(xs, s["data"], s.get("label", f"Series {i+1}"),
                                        _series_color(i, s.get("color")))
                elif y_data is not None:
                    xs = x_axis if x_axis else list(range(len(y_data)))
                    _add_regression(xs, y_data, labels[0] if labels else None,
                                    _series_color(0))

            elif chart_type == "count":
                # Build a frequency table from x_data or df[x_column]
                from collections import Counter
                if df is not None and kwargs.get("x_column"):
                    values = df[kwargs["x_column"]].astype(str).tolist()
                elif kwargs.get("x_data"):
                    values = [str(v) for v in kwargs["x_data"]]
                else:
                    values = []
                if values:
                    counts = Counter(values)
                    cats = list(counts.keys())
                    vals = [counts[c] for c in cats]
                    data.append(go.Bar(x=cats, y=vals,
                                       marker=dict(color=_series_color(0))))

            if not data:
                return None

            layout = dict(
                title=dict(text=title) if title else None,
                xaxis=dict(title=x_label) if x_label else dict(),
                yaxis=dict(title=y_label) if y_label else dict(),
                barmode="stack" if chart_type == "stacked_bar" else (
                    "group" if chart_type == "grouped_bar" else None
                ),
                margin=dict(l=50, r=30, t=50 if title else 20, b=50),
            )
            # Drop None layout keys so Plotly doesn't choke
            layout = {k: v for k, v in layout.items() if v}

            fig = go.Figure(data=data, layout=layout)
            return json.loads(pio.to_json(fig))
        except Exception:
            return None

    # ──────────────────────────── Data loading ────────────────────────────

    def _read_csv_to_df(self, csv_path):
        """Try to load CSV into a pandas DataFrame (for seaborn)."""
        try:
            import pandas as pd
            return pd.read_csv(csv_path)
        except ImportError:
            return None

    def _read_csv(self, csv_path: Path, kwargs: dict):
        """Read CSV and extract data based on column names."""
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        if not rows:
            raise ValueError("CSV file is empty")

        x_col = kwargs.get("x_column")
        y_col = kwargs.get("y_column")
        y_cols = kwargs.get("y_columns")
        labels_col = kwargs.get("labels")

        x_data = None
        y_data = None
        y_data_series = None
        labels = labels_col

        if x_col:
            x_data = []
            for r in rows:
                val = r.get(x_col, "")
                try:
                    x_data.append(float(val))
                except (ValueError, TypeError):
                    x_data.append(val)

        if y_col:
            y_data = []
            for r in rows:
                try:
                    y_data.append(float(r.get(y_col, 0)))
                except (ValueError, TypeError):
                    y_data.append(0)

        if y_cols:
            y_data_series = []
            for col in y_cols:
                series_data = []
                for r in rows:
                    try:
                        series_data.append(float(r.get(col, 0)))
                    except (ValueError, TypeError):
                        series_data.append(0)
                y_data_series.append({"label": col, "data": series_data})

        if kwargs.get("chart_type") == "pie" and x_col and not labels:
            labels = [str(r.get(x_col, "")) for r in rows]

        return x_data, y_data, y_data_series, labels

    def _validate_data(self, chart_type, x_data, y_data, y_data_series, df, kwargs):
        """Return error message if data is insufficient, else None."""
        if chart_type in ("count",):
            if df is None and not kwargs.get("x_data"):
                return "count plot requires a csv_filename with columns, or x_data."
            return None
        if chart_type == "pie":
            if y_data is None:
                return "y_data is required for pie charts."
        elif chart_type in ("histogram", "box", "violin"):
            if y_data is None and y_data_series is None and df is None:
                return f"y_data (or y_columns with csv) is required for {chart_type}."
        elif chart_type in ("heatmap",):
            if y_data_series is None and y_data is None and df is None:
                return "Data is required for heatmap."
        elif chart_type in ("grouped_bar", "stacked_bar"):
            if y_data_series is None and df is None:
                return f"{chart_type} requires y_data_series or csv with y_columns."
        else:
            if x_data is None and y_data is None and df is None:
                return "Provide x_data/y_data or a csv_filename with column names."
        return None

    # ──────────────────────────── Chart plotters ────────────────────────────

    def _plot_line(self, ax, x_data, y_data, y_data_series, colors, labels,
                   marker, linestyle, linewidth, alpha, kwargs):
        if y_data_series:
            for i, series in enumerate(y_data_series):
                lbl = series.get("label", f"Series {i+1}")
                d = [float(v) for v in series["data"]]
                x = x_data if x_data else list(range(len(d)))
                s_color = series.get("color", colors[i % len(colors)])
                s_marker = series.get("marker", marker)
                s_ls = series.get("linestyle", linestyle)
                ax.plot(x, d, color=s_color, label=lbl, linewidth=linewidth,
                        marker=s_marker, markersize=5, linestyle=s_ls, alpha=alpha)
            ax.legend(loc=kwargs.get("legend_position", "best"), framealpha=0.9)
        else:
            x = x_data if x_data else list(range(len(y_data)))
            ax.plot(x, [float(v) for v in y_data], color=colors[0],
                    linewidth=linewidth, marker=marker, markersize=5,
                    linestyle=linestyle, alpha=alpha,
                    label=labels[0] if labels else None)
            if labels:
                ax.legend(loc=kwargs.get("legend_position", "best"), framealpha=0.9)

    def _plot_scatter(self, ax, x_data, y_data, y_data_series, colors,
                      labels, marker, alpha, kwargs):
        if y_data_series:
            for i, series in enumerate(y_data_series):
                lbl = series.get("label", f"Series {i+1}")
                d = [float(v) for v in series["data"]]
                x = x_data if x_data else list(range(len(d)))
                s_color = series.get("color", colors[i % len(colors)])
                s_marker = series.get("marker", marker)
                ax.scatter(x, d, color=s_color, label=lbl, alpha=alpha,
                           s=50, marker=s_marker, edgecolors='white', linewidth=0.5)
            ax.legend(loc=kwargs.get("legend_position", "best"), framealpha=0.9)
        else:
            x = x_data if x_data else list(range(len(y_data)))
            ax.scatter(x, [float(v) for v in y_data], color=colors[0],
                       alpha=alpha, s=50, marker=marker,
                       edgecolors='white', linewidth=0.5)

    def _plot_scatter_line(self, ax, x_data, y_data, y_data_series, colors,
                           labels, marker, linestyle, linewidth, alpha, kwargs):
        """Scatter plot with connecting lines."""
        if y_data_series:
            for i, series in enumerate(y_data_series):
                lbl = series.get("label", f"Series {i+1}")
                d = [float(v) for v in series["data"]]
                x = x_data if x_data else list(range(len(d)))
                s_color = series.get("color", colors[i % len(colors)])
                s_marker = series.get("marker", marker)
                s_ls = series.get("linestyle", linestyle)
                ax.plot(x, d, color=s_color, label=lbl, linewidth=linewidth,
                        marker=s_marker, markersize=7, linestyle=s_ls, alpha=alpha)
                ax.scatter(x, d, color=s_color, s=60, marker=s_marker,
                           edgecolors='white', linewidth=0.8, zorder=5)
            ax.legend(loc=kwargs.get("legend_position", "best"), framealpha=0.9)
        else:
            x = x_data if x_data else list(range(len(y_data)))
            y = [float(v) for v in y_data]
            ax.plot(x, y, color=colors[0], linewidth=linewidth,
                    marker=marker, markersize=7, linestyle=linestyle, alpha=alpha,
                    label=labels[0] if labels else None)
            ax.scatter(x, y, color=colors[0], s=60, marker=marker,
                       edgecolors='white', linewidth=0.8, zorder=5)
            if labels:
                ax.legend(loc=kwargs.get("legend_position", "best"), framealpha=0.9)

    def _plot_regression(self, ax, x_data, y_data, y_data_series, colors,
                         labels, marker, alpha, kwargs, np):
        """Scatter with polynomial regression / trend line."""
        degree = kwargs.get("regression_degree", 1)

        def _fit_and_plot(ax, x, y, color, label, marker, alpha, np, degree):
            xf = np.array([float(v) for v in x])
            yf = np.array([float(v) for v in y])
            ax.scatter(xf, yf, color=color, alpha=alpha, s=50, marker=marker,
                       edgecolors='white', linewidth=0.5,
                       label=f"{label} data" if label else "Data")
            # Fit
            coeffs = np.polyfit(xf, yf, degree)
            poly = np.poly1d(coeffs)
            x_smooth = np.linspace(xf.min(), xf.max(), 200)
            trend_label = f"{'Linear' if degree == 1 else f'Poly({degree})'} fit"
            if label:
                trend_label = f"{label} {trend_label.lower()}"
            ax.plot(x_smooth, poly(x_smooth), '--', color=color,
                    linewidth=2, alpha=0.8, label=trend_label)
            # R² calculation
            y_pred = poly(xf)
            ss_res = np.sum((yf - y_pred) ** 2)
            ss_tot = np.sum((yf - np.mean(yf)) ** 2)
            r2 = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
            return r2

        if y_data_series:
            for i, series in enumerate(y_data_series):
                lbl = series.get("label", f"Series {i+1}")
                d = series["data"]
                x = x_data if x_data else list(range(len(d)))
                s_color = series.get("color", colors[i % len(colors)])
                s_marker = series.get("marker", marker)
                r2 = _fit_and_plot(ax, x, d, s_color, lbl, s_marker, alpha, np, degree)
        else:
            x = x_data if x_data else list(range(len(y_data)))
            lbl = labels[0] if labels else None
            r2 = _fit_and_plot(ax, x, y_data, colors[0], lbl, marker, alpha, np, degree)
            if r2 is not None:
                ax.annotate(f"R² = {r2:.4f}", xy=(0.05, 0.95),
                            xycoords='axes fraction', fontsize=10,
                            color='#6b6b6b', va='top')
        ax.legend(loc=kwargs.get("legend_position", "best"), framealpha=0.9)

    def _plot_bar(self, ax, x_data, y_data, y_data_series, colors, labels,
                  alpha, annotate, np):
        if y_data_series:
            # Treat as grouped bar
            return self._plot_grouped_bar(ax, x_data, y_data_series, colors,
                                          labels, alpha, annotate, np, None, {})
        y = [float(v) for v in y_data]
        x = x_data if x_data else list(range(len(y)))
        bars = ax.bar(range(len(y)), y, color=colors[0], alpha=alpha, edgecolor='white')
        if x_data:
            ax.set_xticks(range(len(x)))
            ax.set_xticklabels([str(v) for v in x], rotation=45, ha='right')
        if annotate:
            for bar in bars:
                h = bar.get_height()
                ax.annotate(f'{h:.1f}', xy=(bar.get_x() + bar.get_width() / 2, h),
                            ha='center', va='bottom', fontsize=9, color='#6b6b6b')

    def _plot_grouped_bar(self, ax, x_data, y_data_series, colors, labels,
                          alpha, annotate, np, df, kwargs):
        if df is not None and kwargs.get("x_column") and kwargs.get("y_columns"):
            x_col = kwargs["x_column"]
            y_cols = kwargs["y_columns"]
            x_vals = df[x_col].astype(str).tolist()
            y_data_series = [{"label": col, "data": df[col].tolist()} for col in y_cols]
            x_data = x_vals

        if not y_data_series:
            return

        n = len(y_data_series)
        x_pos = np.arange(len(y_data_series[0]["data"]))
        width = 0.8 / n
        for i, series in enumerate(y_data_series):
            lbl = series.get("label", f"Series {i+1}")
            d = [float(v) for v in series["data"]]
            s_color = series.get("color", colors[i % len(colors)])
            bars = ax.bar(x_pos + i * width - (n - 1) * width / 2, d,
                          width=width, color=s_color,
                          label=lbl, alpha=alpha, edgecolor='white')
            if annotate:
                for bar in bars:
                    h = bar.get_height()
                    ax.annotate(f'{h:.1f}',
                                xy=(bar.get_x() + bar.get_width() / 2, h),
                                ha='center', va='bottom', fontsize=8, color='#6b6b6b')
        if x_data:
            ax.set_xticks(x_pos)
            ax.set_xticklabels([str(v) for v in x_data], rotation=45, ha='right')
        ax.legend(framealpha=0.9)

    def _plot_stacked_bar(self, ax, x_data, y_data_series, colors, labels,
                          alpha, annotate, np, df, kwargs):
        if df is not None and kwargs.get("x_column") and kwargs.get("y_columns"):
            x_col = kwargs["x_column"]
            y_cols = kwargs["y_columns"]
            x_data = df[x_col].astype(str).tolist()
            y_data_series = [{"label": col, "data": df[col].tolist()} for col in y_cols]

        if not y_data_series:
            return

        x_pos = np.arange(len(y_data_series[0]["data"]))
        bottom = np.zeros(len(y_data_series[0]["data"]))
        for i, series in enumerate(y_data_series):
            lbl = series.get("label", f"Series {i+1}")
            d = np.array([float(v) for v in series["data"]])
            s_color = series.get("color", colors[i % len(colors)])
            ax.bar(x_pos, d, bottom=bottom, color=s_color, label=lbl,
                   alpha=alpha, edgecolor='white')
            bottom += d
        if x_data:
            ax.set_xticks(x_pos)
            ax.set_xticklabels([str(v) for v in x_data], rotation=45, ha='right')
        ax.legend(framealpha=0.9)

    def _plot_count(self, ax, df, kwargs, colors, alpha, has_seaborn, annotate):
        """Count plot — frequency of categories."""
        x_col = kwargs.get("x_column") or kwargs.get("x_data")
        group_col = kwargs.get("group_column")

        if df is not None and x_col and has_seaborn:
            import seaborn as sns
            hue = group_col if group_col and group_col in df.columns else None
            sns.countplot(data=df, x=x_col, hue=hue, ax=ax, palette=colors, alpha=alpha)
            ax.set_xticklabels(ax.get_xticklabels(), rotation=45, ha='right')
        elif isinstance(x_col, str) and kwargs.get("x_data"):
            # Direct data
            from collections import Counter
            counts = Counter(kwargs["x_data"])
            cats = list(counts.keys())
            vals = [counts[c] for c in cats]
            ax.bar(range(len(cats)), vals, color=colors[0], alpha=alpha, edgecolor='white')
            ax.set_xticks(range(len(cats)))
            ax.set_xticklabels([str(c) for c in cats], rotation=45, ha='right')
        elif df is not None and x_col:
            from collections import Counter
            counts = Counter(df[x_col].astype(str).tolist())
            cats = list(counts.keys())
            vals = [counts[c] for c in cats]
            ax.bar(range(len(cats)), vals, color=colors[0], alpha=alpha, edgecolor='white')
            ax.set_xticks(range(len(cats)))
            ax.set_xticklabels(cats, rotation=45, ha='right')
        if annotate:
            for container in ax.containers:
                ax.bar_label(container, fontsize=8, color='#6b6b6b')

    def _plot_pie(self, ax, y_data, labels, colors):
        data = [float(v) for v in y_data]
        pie_labels = labels if labels else [f"Slice {i+1}" for i in range(len(data))]
        wedges, texts, autotexts = ax.pie(
            data, labels=pie_labels, colors=colors[:len(data)],
            autopct="%1.1f%%", startangle=90, pctdistance=0.85,
            wedgeprops=dict(edgecolor='white', linewidth=2),
        )
        for t in autotexts:
            t.set_fontsize(9)
            t.set_color("#2c2c2c")

    def _plot_histogram(self, ax, y_data, y_data_series, colors, labels,
                        alpha, kwargs):
        bins = kwargs.get("bins", "auto")
        if y_data_series:
            for i, series in enumerate(y_data_series):
                lbl = series.get("label", f"Series {i+1}")
                d = [float(v) for v in series["data"]]
                s_color = series.get("color", colors[i % len(colors)])
                ax.hist(d, bins=bins, alpha=0.6, color=s_color,
                        label=lbl, edgecolor='white')
            ax.legend(framealpha=0.9)
        else:
            ax.hist([float(v) for v in y_data], bins=bins,
                    color=colors[0], edgecolor='white', alpha=alpha)

    def _plot_box(self, ax, y_data, y_data_series, colors, df, kwargs, has_seaborn):
        # Seaborn box plot from DataFrame
        if df is not None and has_seaborn:
            import seaborn as sns
            y_col = kwargs.get("y_column")
            x_col = kwargs.get("x_column") or kwargs.get("group_column")
            if y_col:
                sns.boxplot(data=df, x=x_col, y=y_col, ax=ax,
                            palette=colors, flierprops=dict(marker='o', markersize=4))
                if x_col:
                    ax.set_xticklabels(ax.get_xticklabels(), rotation=45, ha='right')
                return

        if y_data_series:
            box_data = [[float(v) for v in s["data"]] for s in y_data_series]
            bp = ax.boxplot(box_data, patch_artist=True,
                            labels=[s.get("label", f"S{i+1}") for i, s in enumerate(y_data_series)])
            for i, patch in enumerate(bp['boxes']):
                patch.set_facecolor(colors[i % len(colors)])
                patch.set_alpha(0.7)
        elif y_data:
            bp = ax.boxplot([[float(v) for v in y_data]], patch_artist=True)
            bp['boxes'][0].set_facecolor(colors[0])
            bp['boxes'][0].set_alpha(0.7)

    def _plot_violin(self, ax, y_data, y_data_series, colors, df, kwargs, has_seaborn):
        # Seaborn violin plot from DataFrame
        if df is not None and has_seaborn:
            import seaborn as sns
            y_col = kwargs.get("y_column")
            x_col = kwargs.get("x_column") or kwargs.get("group_column")
            if y_col:
                sns.violinplot(data=df, x=x_col, y=y_col, ax=ax,
                               palette=colors, inner="box")
                if x_col:
                    ax.set_xticklabels(ax.get_xticklabels(), rotation=45, ha='right')
                return

        if y_data_series:
            data = [[float(v) for v in s["data"]] for s in y_data_series]
            labels_list = [s.get("label", f"S{i+1}") for i, s in enumerate(y_data_series)]
        elif y_data:
            data = [[float(v) for v in y_data]]
            labels_list = ["Data"]
        else:
            return

        parts = ax.violinplot(data, showmeans=True, showmedians=True)
        for i, pc in enumerate(parts.get('bodies', [])):
            pc.set_facecolor(colors[i % len(colors)])
            pc.set_alpha(0.7)
        ax.set_xticks(range(1, len(labels_list) + 1))
        ax.set_xticklabels(labels_list)

    def _plot_area(self, ax, x_data, y_data, y_data_series, colors, labels, linewidth):
        if y_data_series:
            for i, series in enumerate(y_data_series):
                lbl = series.get("label", f"Series {i+1}")
                d = [float(v) for v in series["data"]]
                x = x_data if x_data else list(range(len(d)))
                s_color = series.get("color", colors[i % len(colors)])
                ax.fill_between(x, d, alpha=0.3, color=s_color)
                ax.plot(x, d, color=s_color, label=lbl, linewidth=linewidth)
            ax.legend(framealpha=0.9)
        else:
            x = x_data if x_data else list(range(len(y_data)))
            d = [float(v) for v in y_data]
            ax.fill_between(x, d, alpha=0.3, color=colors[0])
            ax.plot(x, d, color=colors[0], linewidth=linewidth)

    def _plot_heatmap(self, ax, y_data, y_data_series, labels, kwargs, plt, df, annotate):
        import numpy as np

        colormap = kwargs.get("colormap", "YlGnBu")

        # From DataFrame — correlation matrix
        if df is not None:
            y_cols = kwargs.get("y_columns")
            if y_cols:
                sub = df[y_cols]
            else:
                sub = df.select_dtypes(include=[float, int])
            arr = sub.corr().values if sub.shape[1] > 1 else sub.values
            col_labels = list(sub.columns) if sub.shape[1] > 1 else labels
            row_labels = list(sub.columns) if sub.shape[1] > 1 else None
        elif y_data_series:
            arr = np.array([[float(v) for v in s["data"]] for s in y_data_series], dtype=float)
            row_labels = [s.get("label", f"Row {i}") for i, s in enumerate(y_data_series)]
            col_labels = labels
        else:
            data_2d = y_data if isinstance(y_data[0], list) else [y_data]
            arr = np.array(data_2d, dtype=float)
            row_labels = None
            col_labels = labels

        im = ax.imshow(arr, cmap=colormap, aspect="auto")
        plt.colorbar(im, ax=ax, shrink=0.8)

        if annotate:
            for i in range(arr.shape[0]):
                for j in range(arr.shape[1]):
                    val = arr[i, j]
                    text_color = "white" if abs(val) > (arr.max() + arr.min()) / 2 else "black"
                    ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                            fontsize=8, color=text_color)

        if col_labels:
            ax.set_xticks(range(len(col_labels)))
            ax.set_xticklabels(col_labels, rotation=45, ha='right')
        if row_labels:
            ax.set_yticks(range(len(row_labels)))
            ax.set_yticklabels(row_labels)

    def _plot_parity(self, ax, x_data, y_data, color, marker, alpha, np):
        """Parity plot: actual vs predicted with y=x reference line."""
        x = np.array([float(v) for v in x_data])
        y = np.array([float(v) for v in y_data])
        ax.scatter(x, y, color=color, alpha=alpha, s=50, marker=marker,
                   edgecolors='white', linewidth=0.5, label="Data")
        all_vals = np.concatenate([x, y])
        vmin, vmax = all_vals.min(), all_vals.max()
        pad = (vmax - vmin) * 0.05
        ref = np.linspace(vmin - pad, vmax + pad, 100)
        ax.plot(ref, ref, '--', color='#b85450', linewidth=1.5, alpha=0.7, label="y = x")
        ax.set_xlim(vmin - pad, vmax + pad)
        ax.set_ylim(vmin - pad, vmax + pad)
        ax.set_aspect('equal', adjustable='box')
        ax.legend(framealpha=0.9)
