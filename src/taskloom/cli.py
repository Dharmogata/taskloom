"""Command-line interface for TaskLoom content-addressed DAG task runner."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import List, Optional

from taskloom import __version__
from taskloom.dag import check_dag, get_builtin_rules, load_taskfile
from taskloom.engine import TaskLoomEngine
from taskloom.formatters import (
    format_check_report,
    format_pipeline_report,
    format_rules_catalog,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taskloom",
        description="Content-addressed SHA-256 DAG task runner and cache-miss provenance debugger.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"taskloom {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")

    # taskloom run
    run_p = subparsers.add_parser(
        "run",
        help="Execute DAG tasks in topological order with content-addressed SHA-256 caching.",
    )
    run_p.add_argument(
        "taskfile",
        nargs="?",
        default="taskloom.json",
        help="Path to JSON taskfile (default: taskloom.json).",
    )
    run_p.add_argument(
        "--target",
        default=None,
        help="Execute only the specified target task and its transitive dependencies.",
    )
    run_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute topological plan and cache-miss provenance without executing commands.",
    )
    run_p.add_argument(
        "--force",
        action="store_true",
        help="Force rebuild of all tasks in scope regardless of cached fingerprints.",
    )
    run_p.add_argument(
        "--format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output report format (default: text).",
    )

    # taskloom explain
    explain_p = subparsers.add_parser(
        "explain",
        help="Explain why each task in the DAG would hit or miss the cache without running commands.",
    )
    explain_p.add_argument(
        "taskfile",
        nargs="?",
        default="taskloom.json",
        help="Path to JSON taskfile (default: taskloom.json).",
    )
    explain_p.add_argument(
        "--target",
        default=None,
        help="Inspect only the specified target task and its transitive dependencies.",
    )
    explain_p.add_argument(
        "--format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output report format (default: text).",
    )

    # taskloom check
    check_p = subparsers.add_parser(
        "check",
        help="Run static DAG pipeline lint checks (TL001–TL005) on a taskfile.",
    )
    check_p.add_argument(
        "taskfile",
        nargs="?",
        default="taskloom.json",
        help="Path to JSON taskfile (default: taskloom.json).",
    )
    check_p.add_argument(
        "--format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output report format (default: text).",
    )

    # taskloom rules
    rules_p = subparsers.add_parser(
        "rules",
        help="List all built-in static DAG pipeline lint rules (TL001–TL005).",
    )
    rules_p.add_argument(
        "--format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output report format (default: text).",
    )

    return parser


def _resolve_taskfile_path(raw_arg: str) -> Path:
    candidate = Path(raw_arg)
    if candidate.is_dir():
        return candidate / "taskloom.json"
    return candidate


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "rules":
        print(format_rules_catalog(get_builtin_rules(), output_format=args.format))
        return 0

    taskfile_path = _resolve_taskfile_path(args.taskfile)
    if not taskfile_path.exists():
        print(f"Error: Taskfile not found: {taskfile_path}", file=sys.stderr)
        return 2

    try:
        tasks = load_taskfile(taskfile_path)
    except Exception as exc:
        print(f"Error: Failed to parse taskfile '{taskfile_path}': {exc}", file=sys.stderr)
        return 2

    if args.command == "check":
        report = check_dag(tasks, taskfile_path=str(taskfile_path))
        print(format_check_report(report, output_format=args.format))
        return 1 if report.violations else 0

    if args.command in ("run", "explain"):
        engine = TaskLoomEngine(base_dir=taskfile_path.parent)
        try:
            report = engine.run_pipeline(
                tasks=tasks,
                taskfile_path=str(taskfile_path),
                target=args.target,
                dry_run=(args.command == "run" and bool(args.dry_run)),
                explain_only=(args.command == "explain"),
                force=(args.command == "run" and bool(args.force)),
            )
        except (KeyError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

        print(format_pipeline_report(report, output_format=args.format))
        return 1 if report.failed_count > 0 else 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
