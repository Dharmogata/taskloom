"""Core data models for TaskLoom DAG tasks, content fingerprints, cache-miss provenance, and static lint rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ViolationSeverity(str, Enum):
    """Severity levels for static DAG lint violations."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"

    @property
    def rank(self) -> int:
        return {
            ViolationSeverity.MEDIUM: 1,
            ViolationSeverity.HIGH: 2,
            ViolationSeverity.CRITICAL: 3,
        }[self]


class CacheMissCode(str, Enum):
    """Canonical reason codes for task cache misses."""

    FIRST_RUN = "FIRST_RUN"
    FORCED_REBUILD = "FORCED_REBUILD"
    COMMAND_CHANGED = "COMMAND_CHANGED"
    INPUT_FILE_MODIFIED = "INPUT_FILE_MODIFIED"
    ENV_VAR_CHANGED = "ENV_VAR_CHANGED"
    OUTPUT_FILE_MISSING_OR_TAMPERED = "OUTPUT_FILE_MISSING_OR_TAMPERED"
    UPSTREAM_DEP_REBUILT = "UPSTREAM_DEP_REBUILT"


@dataclass(frozen=True)
class TaskSpec:
    """Declarative specification for a single DAG task."""

    name: str
    command: str
    deps: List[str] = field(default_factory=list)
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    env_keys: List[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "deps": list(self.deps),
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "env_keys": list(self.env_keys),
            "description": self.description,
        }


@dataclass(frozen=True)
class TaskFingerprint:
    """Content-addressed SHA-256 fingerprint of a task's command, inputs, env vars, and outputs."""

    task_name: str
    command_sha256: str
    inputs_hashes: Dict[str, str] = field(default_factory=dict)
    env_hashes: Dict[str, str] = field(default_factory=dict)
    outputs_hashes: Dict[str, str] = field(default_factory=dict)
    combined_digest: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_name": self.task_name,
            "command_sha256": self.command_sha256,
            "inputs_hashes": dict(sorted(self.inputs_hashes.items())),
            "env_hashes": dict(sorted(self.env_hashes.items())),
            "outputs_hashes": dict(sorted(self.outputs_hashes.items())),
            "combined_digest": self.combined_digest,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskFingerprint:
        return cls(
            task_name=str(data["task_name"]),
            command_sha256=str(data["command_sha256"]),
            inputs_hashes=dict(data.get("inputs_hashes", {})),
            env_hashes=dict(data.get("env_hashes", {})),
            outputs_hashes=dict(data.get("outputs_hashes", {})),
            combined_digest=str(data.get("combined_digest", "")),
        )


@dataclass(frozen=True)
class CacheMissReason:
    """Detailed provenance record explaining why a task missed the content cache."""

    reason_code: str
    detail: str
    changed_paths: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reason_code": self.reason_code,
            "detail": self.detail,
            "changed_paths": list(self.changed_paths),
        }


@dataclass
class TaskExecutionResult:
    """Execution or dry-run outcome for a single task in the DAG."""

    name: str
    status: str
    cache_hit: bool
    miss_reasons: List[CacheMissReason] = field(default_factory=list)
    duration_ms: float = 0.0
    return_code: int = 0
    layer_index: int = 0
    digest: str = ""
    stdout: str = ""
    stderr: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "cache_hit": self.cache_hit,
            "miss_reasons": [m.to_dict() for m in self.miss_reasons],
            "duration_ms": round(self.duration_ms, 2),
            "return_code": self.return_code,
            "layer_index": self.layer_index,
            "digest": self.digest,
        }


@dataclass
class PipelineReport:
    """Aggregated report for a DAG execution or cache-miss provenance explain pass."""

    taskfile_path: str
    mode: str
    target_task: Optional[str] = None
    layers: List[List[str]] = field(default_factory=list)
    results: List[TaskExecutionResult] = field(default_factory=list)

    @property
    def total_tasks(self) -> int:
        return len(self.results)

    @property
    def cache_hits(self) -> int:
        return sum(1 for r in self.results if r.cache_hit)

    @property
    def cache_misses(self) -> int:
        return sum(1 for r in self.results if not r.cache_hit)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.status == "FAILED")

    @property
    def total_duration_ms(self) -> float:
        return round(sum(r.duration_ms for r in self.results), 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "taskfile_path": self.taskfile_path,
            "mode": self.mode,
            "target_task": self.target_task,
            "layers": self.layers,
            "total_tasks": self.total_tasks,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "failed_count": self.failed_count,
            "total_duration_ms": self.total_duration_ms,
            "results": [r.to_dict() for r in self.results],
        }


@dataclass(frozen=True)
class DAGRuleDefinition:
    """Metadata describing a built-in static pipeline lint rule."""

    rule_id: str
    name: str
    severity: ViolationSeverity
    description: str
    remediation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "severity": self.severity.value,
            "description": self.description,
            "remediation": self.remediation,
        }


@dataclass(frozen=True)
class DAGViolation:
    """A single static DAG lint violation discovered by `taskloom check`."""

    rule_id: str
    rule_name: str
    severity: ViolationSeverity
    task_name: str
    message: str
    remediation: str
    related_items: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "severity": self.severity.value,
            "task_name": self.task_name,
            "message": self.message,
            "remediation": self.remediation,
            "related_items": list(self.related_items),
        }


@dataclass
class DAGCheckReport:
    """Report returned by static DAG lint analysis (`TL001`–`TL005`)."""

    taskfile_path: str
    task_count: int
    violations: List[DAGViolation] = field(default_factory=list)
    layers: List[List[str]] = field(default_factory=list)

    @property
    def has_critical(self) -> bool:
        return any(v.severity == ViolationSeverity.CRITICAL for v in self.violations)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "taskfile_path": self.taskfile_path,
            "task_count": self.task_count,
            "total_violations": len(self.violations),
            "has_critical": self.has_critical,
            "layers": self.layers,
            "violations": [v.to_dict() for v in self.violations],
        }
