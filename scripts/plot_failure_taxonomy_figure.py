#!/usr/bin/env python3
"""Plot LexBench-Browser failure taxonomy figure."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch


REPO_ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ROOT = REPO_ROOT / "experiments" / "LexBench-Browser" / "All" / "browser-use"
PAPER_FIG_DIR = Path("/Users/abc/Desktop/lexmount/lexbench_arxiv_paper/lexmount_tech_report/fig")

TAXONOMY_FILE = (
    "task_gpt-4.1_per_task_threshold_stepwise_failure_taxonomy_gpt-5.5-judge.jsonl"
)

CODES = ["M1.1", "M1.2", "M1.3", "M2.1", "M2.2", "M2.3", "M3.1", "M3.2", "M3.3"]
LABELS = {
    "M1.1": "Requirement Following",
    "M1.2": "Target Selection",
    "M1.3": "Evidence Grounding",
    "M2.1": "UI Misoperation",
    "M2.2": "Infinite Loop",
    "M2.3": "Format Breakdown",
    "M3.1": "Bot Defense",
    "M3.2": "Access Barrier",
    "M3.3": "Site Limitation",
}
GROUPS = {
    "Task Reasoning": ["M1.1", "M1.2", "M1.3"],
    "Action Execution": ["M2.1", "M2.2", "M2.3"],
    "Web Constraints": ["M3.1", "M3.2", "M3.3"],
}
GROUP_OF = {code: group for group, codes in GROUPS.items() for code in codes}

# Low-saturation Lexmount-compatible palette: royal blue, amber, teal.
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

DONUT_MODELS = {
    "bu-2-0",
    "MiniMax-M3",
    "dmx-claude-opus-4-8-thinking",
    "qwen3.7-max",
    "gemini-3.1-pro-preview",
    "gemini-3.5-flash",
    "kimi-k2.6",
    "glm-5.1",
    "doubao-seed-2-0-pro",
    "gpt-5.5",
}
COMPARE_MODELS = [
    ("gpt-5.5", "GPT-5.5", 1.0, None, "#777777"),
    ("doubao-seed-2-0-pro", "Doubao 2.0 Pro", 0.62, "////", "#aaaaaa"),
    ("doubao-seed-2-1-pro-260628", "Doubao 2.1 Pro", 0.38, "\\\\\\\\", "#c8c8c8"),
]


def load_primary_counts() -> tuple[dict[str, Counter], Counter]:
    by_model: dict[str, Counter] = {}
    total = Counter()
    for path in sorted(EXPERIMENT_ROOT.glob(f"*/*/tasks_eval_result/{TAXONOMY_FILE}")):
        model = path.relative_to(EXPERIMENT_ROOT).parts[0]
        counts = Counter()
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                code = row["taxonomy"]["primary_code"]
                if code == "OTHER":
                    continue
                counts[code] += 1
                if model in DONUT_MODELS:
                    total[code] += 1
        by_model[model] = counts
    return by_model, total


def autopct_values(values: list[int]) -> list[str]:
    total = sum(values)
    return [f"{value / total * 100:.1f}%" if value else "" for value in values]


def add_outer_labels(ax: plt.Axes, wedges, values: list[int], total: int) -> None:
    for wedge, code, value in zip(wedges, CODES, values):
        theta = math.radians((wedge.theta1 + wedge.theta2) / 2.0)
        x, y = math.cos(theta), math.sin(theta)
        pct = value / total * 100
        label = f"{code} {LABELS[code]}\n{pct:.1f}%"
        ha = "left" if x >= 0 else "right"
        xy = (0.98 * x, 0.98 * y)
        xytext = (1.35 * x, 1.35 * y)
        ax.annotate(
            label,
            xy=xy,
            xytext=xytext,
            ha=ha,
            va="center",
            fontsize=8.7,
            color="#222222",
            arrowprops=dict(
                arrowstyle="-|>",
                lw=0.75,
                color="#222222",
                shrinkA=0,
                shrinkB=4,
                connectionstyle="arc3,rad=0.08",
            ),
        )


def plot_donut(ax: plt.Axes, total_counts: Counter) -> None:
    sub_values = [total_counts[code] for code in CODES]
    group_names = list(GROUPS)
    group_values = [sum(total_counts[code] for code in GROUPS[group]) for group in group_names]
    total = sum(sub_values)

    wedges, _ = ax.pie(
        sub_values,
        radius=1.0,
        startangle=90,
        counterclock=False,
        colors=[SUB_COLORS[code] for code in CODES],
        wedgeprops=dict(width=0.27, edgecolor="white", linewidth=1.0),
    )
    ax.pie(
        group_values,
        radius=0.72,
        startangle=90,
        counterclock=False,
        colors=[GROUP_COLORS[group] for group in group_names],
        wedgeprops=dict(width=0.34, edgecolor="white", linewidth=1.0),
    )
    ax.add_artist(plt.Circle((0, 0), 0.34, color="white", zorder=10))

    # Inner labels.
    cumulative = 0
    for group, value in zip(group_names, group_values):
        angle = 90 - (cumulative + value / 2) / total * 360
        cumulative += value
        theta = math.radians(angle)
        ax.text(
            0.58 * math.cos(theta),
            0.58 * math.sin(theta),
            f"{group}\n{value / total * 100:.1f}%",
            ha="center",
            va="center",
            fontsize=8.8,
            color="white",
            fontweight="bold",
            linespacing=0.95,
        )

    add_outer_labels(ax, wedges, sub_values, total)
    ax.set(aspect="equal")
    ax.set_title(
        "All Models",
        fontsize=14,
        fontweight="bold",
        color="#1C2B33",
        pad=18,
    )
    ax.text(
        0,
        -1.28,
        f"{total} failed trajectories",
        ha="center",
        va="center",
        fontsize=9.5,
        color="#555555",
    )


def plot_model_bars(ax: plt.Axes, by_model: dict[str, Counter]) -> None:
    series = []
    for model_key, label, alpha, hatch, legend_color in COMPARE_MODELS:
        counts = by_model[model_key]
        total = sum(counts.values())
        values = np.array([counts[code] for code in CODES])
        series.append((label, total, values, alpha, hatch, legend_color))
    y = np.arange(len(CODES))
    height = 0.22
    offsets = np.linspace(-height, height, len(series))
    colors = [SUB_COLORS[code] for code in CODES]

    for offset, (label, total, values, alpha, hatch, _) in zip(offsets, series):
        ax.barh(
            y + offset,
            values,
            height=height,
            color=colors,
            alpha=alpha,
            edgecolor="white",
            linewidth=0.9,
            hatch=hatch,
            label=f"{label} ({total}/210 failed)",
        )

    for offset, (_, _, values, alpha, _, _) in zip(offsets, series):
        text_color = "#222222" if alpha > 0.9 else "#555555"
        for i, value in enumerate(values):
            if value:
                ax.text(value + 0.45, i + offset, f"{int(value)}", va="center", ha="left", fontsize=8.1, color=text_color)

    ax.set_yticks(y)
    ax.set_yticklabels([f"{code}" for code in CODES], fontsize=10, fontweight="bold")
    ax.invert_yaxis()
    ax.set_xlim(0, 43)
    ax.set_xlabel("Primary attribution count", fontsize=10.5, color="#333333")
    ax.set_title("GPT-5.5 vs. Doubao 2.0/2.1", fontsize=14, fontweight="bold", color="#1C2B33", pad=18)
    ax.grid(axis="x", linestyle="--", linewidth=0.7, color="#d7dbe2", alpha=0.9)
    ax.set_axisbelow(True)

    for spine in ["top", "right", "left"]:
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color("#d0d5dd")
    ax.tick_params(axis="x", colors="#333333", labelsize=9)
    ax.tick_params(axis="y", length=0)

    # Group labels on the left, similar to CocoaBench.
    group_spans = {
        "Task\nReasoning": (0, 2),
        "Action\nExecution": (3, 5),
        "Web\nConstraints": (6, 8),
    }
    for text, (lo, hi) in group_spans.items():
        mid = (lo + hi) / 2
        group_name = text.replace("\n", " ")
        if "Task" in group_name:
            color = GROUP_COLORS["Task Reasoning"]
        elif "Action" in group_name:
            color = GROUP_COLORS["Action Execution"]
        else:
            color = GROUP_COLORS["Web Constraints"]
        ax.text(
            -0.205,
            mid,
            text,
            transform=ax.get_yaxis_transform(),
            ha="right",
            va="center",
            rotation=90,
            fontsize=12,
            fontweight="bold",
            color=color,
            linespacing=0.9,
            clip_on=False,
        )

    legend_handles = [
        Patch(
            facecolor=legend_color,
            edgecolor="white",
            hatch=hatch,
            label=f"{label} ({total}/210 failed)",
        )
        for label, total, _, _, hatch, legend_color in series
    ]
    ax.legend(
        handles=legend_handles,
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

    by_model, total_counts = load_primary_counts()
    PAPER_FIG_DIR.mkdir(parents=True, exist_ok=True)

    fig = plt.figure(figsize=(14.5, 6.0), dpi=220)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.08, 1.28], wspace=0.40)
    ax0 = fig.add_subplot(gs[0, 0])
    ax1 = fig.add_subplot(gs[0, 1])

    plot_donut(ax0, total_counts)
    plot_model_bars(ax1, by_model)

    fig.subplots_adjust(left=0.035, right=0.99, top=0.92, bottom=0.10)

    pdf_path = PAPER_FIG_DIR / "failure_taxonomy_doubao_comparison.pdf"
    png_path = PAPER_FIG_DIR / "failure_taxonomy_doubao_comparison.png"
    fig.savefig(pdf_path, bbox_inches="tight")
    fig.savefig(png_path, bbox_inches="tight")
    print(pdf_path)
    print(png_path)


if __name__ == "__main__":
    main()
