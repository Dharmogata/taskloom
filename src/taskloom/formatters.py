"""ASCII DAG execution plan, cache-miss provenance, JSON, and Markdown formatters for TaskLoom."""

from __future__ import annotations

import json
from typing import List, Sequence

from taskloom.models import (
    DAGCheckReport,
    DAGRuleDefinition,
    PipelineReport,
)


def _render_ascii_layers(layers: List[List[str]]) -> List[str]:
    """Render topological execution layers as a concise ASCII DAG summary."""
    if not layers:
        return ["  (empty graph)"]
    lines: List[str] = []
    for idx, names in enumerate(layers):
        nodes_str = "  ||  ".join(f"[{n}]" for n in names)
        lines.append(f"  Layer {idx}: {nodes_str}")
        if idx < len(layers) - 1:
            lines.append("             │")
            lines.append("             ▼")
    return lines


def format_pipeline_report(
    report: PipelineReport,
    output_format: str = "text",
) -> str:
    """Format a PipelineReport (`run` or `explain`) into text, json, or markdown."""
    if output_format == "json":
        return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)

    if output_format == "markdown":
        lines: List[str] = [
            f"# TaskLoom Pipeline Report (`{report.mode}` · `{report.taskfile_path}`)",
            "",
            f"- **Total Tasks:** {report.total_tasks}",
            f"- **Cache Hits:** {report.cache_hits}",
            f"- **Cache Misses:** {report.cache_misses}",
            f"- **Failed Tasks:** {report.failed_count}",
            "",
            "## Topological Execution Plan",
            "",
        ]
        for idx, names in enumerate(report.layers):
            joined = ", ".join(f"`{n}`" for n in names)
            lines.append(f"- **Layer {idx}:** {joined}")
        lines.extend(
            [
                "",
                "## Task Cache Provenance & Execution Results",
                "",
                "| Layer | Task | Status | Cache | Duration (ms) | Miss Provenance |",
                "| :--- | :--- | :--- | :--- | :--- | :--- |",
            ]
        )
        for r in report.results:
            cache_badge = "HIT" if r.cache_hit else "MISS"
            reasons_str = (
                "—"
                if not r.miss_reasons
                else "; ".join(f"`{m.reason_code}`: {m.detail}" for m in r.miss_reasons)
            )
            lines.append(
                f"| L{r.layer_index} | `{r.name}` | **{r.status}** | {cache_badge} "
                f"| {r.duration_ms:.1f} | {reasons_str} |"
            )
        return "\n".join(lines)

    # Default: human-readable text with ASCII DAG plan
    header_title = (
        "TaskLoom Cache-Miss Provenance Explain"
        if report.mode == "explain"
        else f"TaskLoom Execution Report ({report.mode})"
    )
    lines = [
        f"{header_title} · {report.taskfile_path}",
        "─" * 78,
        "Topological Execution Plan:",
    ]
    lines.extend(_render_ascii_layers(report.layers))
    lines.append("─" * 78)

    for r in report.results:
        badge = "⚡ CACHED" if r.cache_hit else f"🔨 {r.status}"
        digest_short = r.digest[:12] if r.digest else "n/a"
        lines.append(
            f"  [L{r.layer_index}] {r.name:<20} {badge:<16} "
            f"(sha256:{digest_short} · {r.duration_ms:.1f}ms)"
        )
        for m in r.miss_reasons:
            lines.append(f"       └ [{m.reason_code}] {m.detail}")

    lines.append("─" * 78)
    lines.append(
        f"Summary: {report.total_tasks} task(s) "
        f"(Cache Hits: {report.cache_hits}, Cache Misses: {report.cache_misses}, "
        f"Failed: {report.failed_count})"
    )
    return "\n".join(lines)


def format_check_report(
    report: DAGCheckReport,
    output_format: str = "text",
) -> str:
    """Format a DAGCheckReport (`taskloom check`) into text, json, or markdown."""
    if output_format == "json":
        return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)

    if output_format == "markdown":
        lines: List[str] = [
            f"# TaskLoom Static DAG Check (`{report.taskfile_path}`)",
            "",
            f"- **Tasks Analyzed:** {report.task_count}",
            f"- **Total Violations:** {len(report.violations)}",
            "",
        ]
        if not report.violations:
            lines.append("No static DAG violations detected.")
            return "\n".join(lines)

        lines.extend(
            [
                "| Rule | Severity | Task | Finding & Remediation |",
                "| :--- | :--- | :--- | :--- |",
            ]
        )
        for v in report.violations:
            lines.append(
                f"| `{v.rule_id}` ({v.rule_name}) | **{v.severity.value.upper()}** "
                f"| `{v.task_name}` | {v.message} — *{v.remediation}* |"
            )
        return "\n".join(lines)

    if not report.violations:
        lines = [
            f"TaskLoom DAG Check ({report.taskfile_path}): "
            f"OK — 0 violations across {report.task_count} task(s).",
            "─" * 78,
            "Topological Execution Plan:",
        ]
        lines.extend(_render_ascii_layers(report.layers))
        return "\n".join(lines)

    lines = [
        f"TaskLoom DAG Check Report ({report.taskfile_path} · {report.task_count} task(s))",
        "─" * 78,
    ]
    for v in report.violations:
        lines.append(
            f"  ✗ [{v.rule_id}/{v.severity.value.upper()}] {v.rule_name} (task: {v.task_name})"
        )
        lines.append(f"    └ {v.message}")
        lines.append(f"    └ Fix: {v.remediation}")
    lines.append("─" * 78)
    lines.append(
        f"Summary: {len(report.violations)} violation(s) "
        f"(Critical: {sum(1 for v in report.violations if v.severity.value == 'critical')})"
    )
    return "\n".join(lines)


def format_rules_catalog(
    rules: Sequence[DAGRuleDefinition],
    output_format: str = "text",
) -> str:
    """Format the built-in static DAG lint rules (`TL001`–`TL005`) into text, json, or markdown."""
    if output_format == "json":
        return json.dumps([r.to_dict() for r in rules], indent=2, ensure_ascii=False)

    if output_format == "markdown":
        lines = [
            "# TaskLoom Built-in Static Pipeline Lint Rules",
            "",
            "| Rule ID | Name | Severity | Description | Remediation |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
        for r in rules:
            lines.append(
                f"| `{r.rule_id}` | `{r.name}` | **{r.severity.value.upper()}** "
                f"| {r.description} | {r.remediation} |"
            )
        return "\n".join(lines)

    lines = [
        "TaskLoom Built-in Static Pipeline Lint Rules:",
        "─" * 76,
    ]
    for r in rules:
        lines.append(
            f"  {r.rule_id} [{r.severity.value.upper():<8}] {r.name}"
        )
        lines.append(f"        {r.description}")
    return "\n".join(lines)
