#!/usr/bin/env python3
"""Plot generation-to-generation failure attribution comparisons."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ROOT = REPO_ROOT / "experiments" / "LexBench-Browser" / "All" / "browser-use"
REPORT_DIR = REPO_ROOT / "reports" / "assets"

SUMMARY_FILE = "task_gpt-4.1_per_task_threshold_stepwise_failure_taxonomy_gpt-5.5-judge_summary.json"

CODES = ["M1.1", "M1.2", "M1.3", "M2.1", "M2.2", "M2.3", "M3.1", "M3.2", "M3.3"]
GROUPS = {
    "Task Reasoning": ["M1.1", "M1.2", "M1.3"],
    "Action Execution": ["M2.1", "M2.2", "M2.3"],
    "Web Constraints": ["M3.1", "M3.2", "M3.3"],
}
GROUP_COLORS = {
    "Task Reasoning": "#355F9F",
    "Action Execution": "#C6922E",
    "Web Constraints": "#3E8582",
}
SUB_COLORS = {
    "M1.1": "#254B8D",
    "M1.2": "#6F8FC7",
    "M1.3": "#B8C7E5",
    "M2.1": "#B57E19",
    "M2.2": "#D4A246",
    "M2.3": "#E9C981",
    "M3.1": "#2F7774",
    "M3.2": "#72AAA7",
    "M3.3": "#B5D6D3",
}

MODEL_DIRS = {
    "doubao20": "doubao-seed-2-0-pro/20260604_100016",
    "doubao21": "doubao-seed-2-1-pro-260628/20260623_164627",
    "glm51": "glm-5.1/20260605_103731",
    "glm52": "glm-5.2/20260624_104300",
}


def load_counts(key: str) -> tuple[Counter[str], int]:
    path = EXPERIMENT_ROOT / MODEL_DIRS[key] / "tasks_eval_result" / SUMMARY_FILE
    summary = json.loads(path.read_text(encoding="utf-8"))
    counts = Counter(summary["primary_code_counts"])
    counts.pop("OTHER", None)
    return counts, sum(counts.values())


def plot_panel(ax: plt.Axes, title: str, series: list[tuple[str, Counter[str], int, str | None, float]]) -> None:
    y = np.arange(len(CODES))
    height = 0.28
    offsets = np.linspace(-height / 1.7, height / 1.7, len(series))
    colors = [SUB_COLORS[code] for code in CODES]

    for offset, (label, counts, total, hatch, alpha) in zip(offsets, series):
        values = np.array([counts.get(code, 0) for code in CODES])
        ax.barh(
            y + offset,
            values,
            height=height,
            color=colors,
            alpha=alpha,
            edgecolor="white",
            linewidth=0.9,
            hatch=hatch,
            label=f"{label} ({total}/210 attributed)",
        )
        for i, value in enumerate(values):
            if value:
                ax.text(
                    value + 0.45,
                    i + offset,
                    f"{int(value)}",
                    va="center",
                    ha="left",
                    fontsize=8.8,
                    color="#222222" if alpha > 0.85 else "#555555",
                )

    ax.set_yticks(y)
    ax.set_yticklabels(CODES, fontsize=10.5, fontweight="bold")
    ax.invert_yaxis()
    ax.set_xlim(0, 43)
    ax.set_xlabel("Primary attribution count", fontsize=10.5, color="#333333")
    ax.set_title(title, fontsize=14, fontweight="bold", color="#1C2B33", pad=14)
    ax.grid(axis="x", linestyle="--", linewidth=0.7, color="#d7dbe2", alpha=0.9)
    ax.set_axisbelow(True)

    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#d0d5dd")
    ax.tick_params(axis="x", colors="#333333", labelsize=9)
    ax.tick_params(axis="y", length=0)

    group_spans = {
        "Task\nReasoning": (0, 2),
        "Action\nExecution": (3, 5),
        "Web\nConstraints": (6, 8),
    }
    for text, (lo, hi) in group_spans.items():
        mid = (lo + hi) / 2
        if "Task" in text:
            color = GROUP_COLORS["Task Reasoning"]
        elif "Action" in text:
            color = GROUP_COLORS["Action Execution"]
        else:
            color = GROUP_COLORS["Web Constraints"]
        ax.text(
            -0.14,
            mid,
            text,
            transform=ax.get_yaxis_transform(),
            ha="right",
            va="center",
            rotation=90,
            fontsize=11.5,
            fontweight="bold",
            color=color,
            linespacing=0.9,
            clip_on=False,
        )

    handles = [
        Patch(facecolor="#777777", edgecolor="white", hatch=hatch, label=f"{label} ({total}/210 attributed)")
        for label, _, total, hatch, _ in series
    ]
    ax.legend(
        handles=handles,
        loc="lower right",
        frameon=True,
        framealpha=0.95,
        facecolor="white",
        edgecolor="#dddddd",
        fontsize=9.0,
    )


def main() -> None:
    mpl.rcParams.update(
        {
            "font.family": "DejaVu Serif",
            "axes.titlesize": 14,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    doubao20, doubao20_total = load_counts("doubao20")
    doubao21, doubao21_total = load_counts("doubao21")
    glm51, glm51_total = load_counts("glm51")
    glm52, glm52_total = load_counts("glm52")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(14.0, 5.9), dpi=220, sharey=False)
    plot_panel(
        axes[0],
        "Doubao 2.0 Pro vs. Doubao 2.1 Pro",
        [
            ("Doubao 2.0 Pro", doubao20, doubao20_total, None, 0.96),
            ("Doubao 2.1 Pro", doubao21, doubao21_total, "////", 0.62),
        ],
    )
    plot_panel(
        axes[1],
        "GLM-5.1 vs. GLM-5.2",
        [
            ("GLM-5.1", glm51, glm51_total, None, 0.96),
            ("GLM-5.2", glm52, glm52_total, "////", 0.62),
        ],
    )
    fig.subplots_adjust(left=0.075, right=0.99, top=0.90, bottom=0.12, wspace=0.18)

    pdf_path = REPORT_DIR / "generation_failure_comparison.pdf"
    png_path = REPORT_DIR / "generation_failure_comparison.png"
    fig.savefig(pdf_path, bbox_inches="tight")
    fig.savefig(png_path, bbox_inches="tight")
    print(pdf_path)
    print(png_path)


if __name__ == "__main__":
    main()
