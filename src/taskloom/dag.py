"""Taskfile parser, topological layer planner, and static pipeline lint rules (TL001–TL005)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from taskloom.models import (
    DAGCheckReport,
    DAGRuleDefinition,
    DAGViolation,
    TaskSpec,
    ViolationSeverity,
)


BUILTIN_DAG_RULES: Tuple[DAGRuleDefinition, ...] = (
    DAGRuleDefinition(
        rule_id="TL001",
        name="dependency-cycle",
        severity=ViolationSeverity.CRITICAL,
        description="Circular dependency detected in task graph, making topological execution impossible.",
        remediation="Break the cycle by removing one of the circular edges in `deps`.",
    ),
    DAGRuleDefinition(
        rule_id="TL002",
        name="unknown-task-dependency",
        severity=ViolationSeverity.CRITICAL,
        description="Task references a dependency name in `deps` that does not exist in the taskfile.",
        remediation="Define the missing task or correct the misspelled name in `deps`.",
    ),
    DAGRuleDefinition(
        rule_id="TL003",
        name="overlapping-task-outputs",
        severity=ViolationSeverity.HIGH,
        description="Two tasks declare the same output file path, causing race conditions or cache thrashing.",
        remediation="Assign distinct output paths per task or merge the competing tasks.",
    ),
    DAGRuleDefinition(
        rule_id="TL004",
        name="missing-declared-inputs-or-outputs",
        severity=ViolationSeverity.MEDIUM,
        description="Task has neither `inputs` nor `outputs` declared, preventing deterministic content caching.",
        remediation="Declare `inputs` and/or `outputs` globs/paths so TaskLoom can fingerprint artifacts.",
    ),
    DAGRuleDefinition(
        rule_id="TL005",
        name="undeclared-producer-dependency",
        severity=ViolationSeverity.MEDIUM,
        description=(
            "Task B lists an input file that is declared as an output of Task A, "
            "but Task B does not list Task A in `deps`."
        ),
        remediation="Add the producer task to `deps` to guarantee deterministic execution order.",
    ),
)


def get_builtin_rules() -> Sequence[DAGRuleDefinition]:
    """Return all built-in static DAG lint rules (TL001–TL005)."""
    return BUILTIN_DAG_RULES


def _normalize_rel_path(raw_path: str) -> str:
    """Normalize a relative path or glob for cross-platform comparison."""
    cleaned = raw_path.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def parse_taskfile_dict(data: Dict[str, Any]) -> Dict[str, TaskSpec]:
    """Parse a dictionary representation of a taskloom JSON file into TaskSpec objects."""
    if not isinstance(data, dict):
        raise ValueError("Taskfile root must be a JSON object.")

    raw_tasks = data.get("tasks", data)
    if not isinstance(raw_tasks, dict):
        raise ValueError("`tasks` field in taskfile must be a JSON object mapping task names to specs.")

    specs: Dict[str, TaskSpec] = {}
    for name, cfg in raw_tasks.items():
        if not isinstance(cfg, dict):
            raise ValueError(f"Task '{name}' definition must be a JSON object.")
        command = cfg.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"Task '{name}' must define a non-empty string `command`.")

        deps = [str(d) for d in cfg.get("deps", [])]
        inputs = [str(i) for i in cfg.get("inputs", [])]
        outputs = [str(o) for o in cfg.get("outputs", [])]
        env_raw = cfg.get("env", cfg.get("env_keys", []))
        env_keys = [str(e) for e in env_raw]
        description = str(cfg.get("description", ""))

        specs[name] = TaskSpec(
            name=name,
            command=command,
            deps=deps,
            inputs=inputs,
            outputs=outputs,
            env_keys=env_keys,
            description=description,
        )

    return specs


def load_taskfile(path: Path) -> Dict[str, TaskSpec]:
    """Load and parse a JSON taskfile from disk."""
    raw_text = path.read_text(encoding="utf-8")
    data = json.loads(raw_text)
    return parse_taskfile_dict(data)


def find_cycles(tasks: Dict[str, TaskSpec]) -> List[List[str]]:
    """Detect all directed cycles in the task dependency graph using DFS."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {name: WHITE for name in tasks}
    stack: List[str] = []
    cycles: List[List[str]] = []
    seen_canonical: Set[Tuple[str, ...]] = set()

    def dfs(node: str) -> None:
        color[node] = GRAY
        stack.append(node)
        spec = tasks[node]
        for dep in spec.deps:
            if dep not in tasks:
                continue
            if color[dep] == WHITE:
                dfs(dep)
            elif color[dep] == GRAY:
                idx = stack.index(dep)
                cycle_nodes = stack[idx:]
                min_pos = min(range(len(cycle_nodes)), key=lambda i: cycle_nodes[i])
                canonical = tuple(cycle_nodes[min_pos:] + cycle_nodes[:min_pos])
                if canonical not in seen_canonical:
                    seen_canonical.add(canonical)
                    cycles.append(list(canonical) + [canonical[0]])
        stack.pop()
        color[node] = BLACK

    for name in sorted(tasks.keys()):
        if color[name] == WHITE:
            dfs(name)

    return cycles


def resolve_subgraph(tasks: Dict[str, TaskSpec], target: Optional[str] = None) -> Dict[str, TaskSpec]:
    """Return the subset of tasks required to build `target` (or all tasks if `target` is None)."""
    if target is None:
        return dict(tasks)
    if target not in tasks:
        raise KeyError(f"Unknown target task: '{target}'")

    needed: Set[str] = set()
    queue: List[str] = [target]
    while queue:
        curr = queue.pop(0)
        if curr in needed:
            continue
        needed.add(curr)
        if curr in tasks:
            for dep in tasks[curr].deps:
                if dep not in needed:
                    queue.append(dep)

    return {k: v for k, v in tasks.items() if k in needed}


def compute_topological_layers(
    tasks: Dict[str, TaskSpec],
    target: Optional[str] = None,
) -> List[List[str]]:
    """Compute topological execution layers where each layer's dependencies are satisfied by earlier layers.

    Raises ValueError if an unknown dependency or dependency cycle exists.
    """
    sub_tasks = resolve_subgraph(tasks, target=target)

    for name, spec in sorted(sub_tasks.items()):
        for dep in spec.deps:
            if dep not in tasks:
                raise ValueError(
                    f"[TL002] Task '{name}' references unknown dependency '{dep}'."
                )

    in_degree: Dict[str, int] = {name: 0 for name in sub_tasks}
    dependents: Dict[str, List[str]] = {name: [] for name in sub_tasks}

    for name, spec in sub_tasks.items():
        unique_deps = list(dict.fromkeys(spec.deps))
        in_degree[name] = len(unique_deps)
        for dep in unique_deps:
            dependents[dep].append(name)

    layers: List[List[str]] = []
    ready = sorted([name for name, deg in in_degree.items() if deg == 0])
    visited_count = 0

    while ready:
        layers.append(ready)
        visited_count += len(ready)
        next_ready: List[str] = []
        for node in ready:
            for child in dependents[node]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    next_ready.append(child)
        ready = sorted(next_ready)

    if visited_count != len(sub_tasks):
        cycles = find_cycles(sub_tasks)
        cycle_desc = " -> ".join(cycles[0]) if cycles else "circular dependency"
        raise ValueError(f"[TL001] Dependency cycle detected in task graph: {cycle_desc}")

    return layers


def _collect_transitive_deps(tasks: Dict[str, TaskSpec], start: str) -> Set[str]:
    """Return all direct and transitive dependencies of `start` (safe under cycles)."""
    visited: Set[str] = set()
    stack = list(tasks[start].deps) if start in tasks else []
    while stack:
        curr = stack.pop()
        if curr in visited:
            continue
        visited.add(curr)
        if curr in tasks:
            for d in tasks[curr].deps:
                if d not in visited:
                    stack.append(d)
    return visited


def check_dag(
    tasks: Dict[str, TaskSpec],
    taskfile_path: str = "taskloom.json",
) -> DAGCheckReport:
    """Run static pipeline lint rules TL001–TL005 against the parsed task graph."""
    rule_map = {r.rule_id: r for r in BUILTIN_DAG_RULES}
    violations: List[DAGViolation] = []

    # TL002: unknown-task-dependency
    for name in sorted(tasks.keys()):
        spec = tasks[name]
        for dep in spec.deps:
            if dep not in tasks:
                r = rule_map["TL002"]
                violations.append(
                    DAGViolation(
                        rule_id=r.rule_id,
                        rule_name=r.name,
                        severity=r.severity,
                        task_name=name,
                        message=f"Task '{name}' depends on unknown task '{dep}'.",
                        remediation=r.remediation,
                        related_items=[dep],
                    )
                )

    # TL001: dependency-cycle
    cycles = find_cycles(tasks)
    for cyc in cycles:
        r = rule_map["TL001"]
        chain = " -> ".join(cyc)
        violations.append(
            DAGViolation(
                rule_id=r.rule_id,
                rule_name=r.name,
                severity=r.severity,
                task_name=cyc[0],
                message=f"Circular dependency detected: {chain}",
                remediation=r.remediation,
                related_items=cyc,
            )
        )

    # TL003: overlapping-task-outputs
    output_producers: Dict[str, List[str]] = {}
    for name in sorted(tasks.keys()):
        for out in tasks[name].outputs:
            norm_out = _normalize_rel_path(out)
            output_producers.setdefault(norm_out, []).append(name)

    for norm_out, owners in sorted(output_producers.items()):
        unique_owners = list(dict.fromkeys(owners))
        if len(unique_owners) > 1:
            r = rule_map["TL003"]
            owners_str = ", ".join(f"'{o}'" for o in unique_owners)
            violations.append(
                DAGViolation(
                    rule_id=r.rule_id,
                    rule_name=r.name,
                    severity=r.severity,
                    task_name=unique_owners[0],
                    message=(
                        f"Output path '{norm_out}' is declared by multiple tasks ({owners_str}), "
                        f"causing race conditions or cache thrashing."
                    ),
                    remediation=r.remediation,
                    related_items=unique_owners + [norm_out],
                )
            )

    # TL004: missing-declared-inputs-or-outputs
    for name in sorted(tasks.keys()):
        spec = tasks[name]
        if not spec.inputs and not spec.outputs:
            r = rule_map["TL004"]
            violations.append(
                DAGViolation(
                    rule_id=r.rule_id,
                    rule_name=r.name,
                    severity=r.severity,
                    task_name=name,
                    message=(
                        f"Task '{name}' declares neither `inputs` nor `outputs`, "
                        f"preventing deterministic content caching."
                    ),
                    remediation=r.remediation,
                    related_items=[name],
                )
            )

    # TL005: undeclared-producer-dependency
    for name in sorted(tasks.keys()):
        spec = tasks[name]
        all_deps = _collect_transitive_deps(tasks, name)
        for inp in spec.inputs:
            norm_inp = _normalize_rel_path(inp)
            producers = output_producers.get(norm_inp, [])
            for producer in producers:
                if producer != name and producer not in all_deps:
                    r = rule_map["TL005"]
                    violations.append(
                        DAGViolation(
                            rule_id=r.rule_id,
                            rule_name=r.name,
                            severity=r.severity,
                            task_name=name,
                            message=(
                                f"Task '{name}' consumes input '{norm_inp}' produced by task '{producer}', "
                                f"but '{producer}' is not listed in `deps`."
                            ),
                            remediation=r.remediation,
                            related_items=[producer, norm_inp],
                        )
                    )

    layers: List[List[str]] = []
    if not any(v.rule_id in ("TL001", "TL002") for v in violations):
        try:
            layers = compute_topological_layers(tasks)
        except ValueError:
            layers = []

    return DAGCheckReport(
        taskfile_path=taskfile_path,
        task_count=len(tasks),
        violations=violations,
        layers=layers,
    )
