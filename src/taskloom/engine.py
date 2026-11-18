"""Content-addressed SHA-256 fingerprint calculator, cache-miss provenance analyzer, and DAG execution engine."""

from __future__ import annotations

import glob
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Dict, List, Mapping, Optional, Set, Tuple

from taskloom.dag import compute_topological_layers, resolve_subgraph
from taskloom.models import (
    CacheMissCode,
    CacheMissReason,
    PipelineReport,
    TaskExecutionResult,
    TaskFingerprint,
    TaskSpec,
)


MISSING_FILE_SENTINEL = "__MISSING__"
UNSET_ENV_SENTINEL = "__UNSET__"


def sha256_text(text: str) -> str:
    """Compute hex SHA-256 digest of a UTF-8 string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    """Compute hex SHA-256 digest of a file's contents, or return MISSING_FILE_SENTINEL."""
    if not path.exists() or not path.is_file():
        return MISSING_FILE_SENTINEL
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _normalize_rel(base_dir: Path, target: Path) -> str:
    try:
        rel = target.resolve().relative_to(base_dir.resolve())
        return str(rel).replace("\\", "/")
    except ValueError:
        return str(target).replace("\\", "/")


def expand_file_patterns(base_dir: Path, patterns: List[str]) -> Dict[str, str]:
    """Expand file paths or glob patterns relative to `base_dir` and return {rel_path: sha256}."""
    hashes: Dict[str, str] = {}
    for pattern in patterns:
        norm_pat = pattern.strip().replace("\\", "/")
        while norm_pat.startswith("./"):
            norm_pat = norm_pat[2:]
        abs_pattern = str(base_dir / norm_pat)
        has_magic = glob.has_magic(norm_pat)
        if has_magic:
            matched = sorted(glob.glob(abs_pattern, recursive=True))
            if not matched:
                hashes[norm_pat] = MISSING_FILE_SENTINEL
            else:
                for m in matched:
                    p = Path(m)
                    if p.is_file():
                        rel = _normalize_rel(base_dir, p)
                        hashes[rel] = sha256_file(p)
        else:
            p = base_dir / norm_pat
            hashes[norm_pat] = sha256_file(p)
    return dict(sorted(hashes.items()))


def compute_env_hashes(
    env_keys: List[str],
    environ: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Compute SHA-256 hashes of tracked environment variable values."""
    env_source = os.environ if environ is None else environ
    result: Dict[str, str] = {}
    for key in sorted(set(env_keys)):
        if key in env_source:
            result[key] = sha256_text(str(env_source[key]))
        else:
            result[key] = UNSET_ENV_SENTINEL
    return result


def compute_combined_digest(
    command_sha256: str,
    inputs_hashes: Dict[str, str],
    env_hashes: Dict[str, str],
    outputs_hashes: Dict[str, str],
) -> str:
    """Compute a single canonical SHA-256 digest over all task fingerprint components."""
    payload = {
        "command_sha256": command_sha256,
        "inputs_hashes": dict(sorted(inputs_hashes.items())),
        "env_hashes": dict(sorted(env_hashes.items())),
        "outputs_hashes": dict(sorted(outputs_hashes.items())),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256_text(serialized)


def compute_task_fingerprint(
    spec: TaskSpec,
    base_dir: Path,
    environ: Optional[Mapping[str, str]] = None,
) -> TaskFingerprint:
    """Compute the full content-addressed SHA-256 fingerprint for a task."""
    cmd_hash = sha256_text(spec.command.strip())
    inp_hashes = expand_file_patterns(base_dir, spec.inputs)
    env_hashes = compute_env_hashes(spec.env_keys, environ=environ)
    out_hashes = expand_file_patterns(base_dir, spec.outputs)
    combined = compute_combined_digest(cmd_hash, inp_hashes, env_hashes, out_hashes)
    return TaskFingerprint(
        task_name=spec.name,
        command_sha256=cmd_hash,
        inputs_hashes=inp_hashes,
        env_hashes=env_hashes,
        outputs_hashes=out_hashes,
        combined_digest=combined,
    )


def diff_fingerprints(
    current: TaskFingerprint,
    cached: Optional[TaskFingerprint],
    rebuilt_upstream_deps: List[str],
    force: bool = False,
) -> List[CacheMissReason]:
    """Compare current task state against cached fingerprint and return exact CacheMissReason items."""
    reasons: List[CacheMissReason] = []

    if force:
        reasons.append(
            CacheMissReason(
                reason_code=CacheMissCode.FORCED_REBUILD.value,
                detail="Rebuild forced via `--force` flag.",
                changed_paths=[],
            )
        )
        return reasons

    if cached is None:
        reasons.append(
            CacheMissReason(
                reason_code=CacheMissCode.FIRST_RUN.value,
                detail=f"No previous cache entry found for task '{current.task_name}'.",
                changed_paths=[],
            )
        )
        return reasons

    if current.command_sha256 != cached.command_sha256:
        reasons.append(
            CacheMissReason(
                reason_code=CacheMissCode.COMMAND_CHANGED.value,
                detail=(
                    f"Task command string changed "
                    f"({cached.command_sha256[:10]} -> {current.command_sha256[:10]})."
                ),
                changed_paths=[],
            )
        )

    all_input_paths = sorted(set(current.inputs_hashes.keys()) | set(cached.inputs_hashes.keys()))
    changed_inputs: List[str] = []
    for path in all_input_paths:
        cur_val = current.inputs_hashes.get(path, MISSING_FILE_SENTINEL)
        old_val = cached.inputs_hashes.get(path, MISSING_FILE_SENTINEL)
        if cur_val != old_val or cur_val == MISSING_FILE_SENTINEL:
            changed_inputs.append(path)

    if changed_inputs:
        paths_preview = ", ".join(changed_inputs)
        reasons.append(
            CacheMissReason(
                reason_code=CacheMissCode.INPUT_FILE_MODIFIED.value,
                detail=f"Input file(s) modified, added, or missing: {paths_preview}",
                changed_paths=changed_inputs,
            )
        )

    all_env_keys = sorted(set(current.env_hashes.keys()) | set(cached.env_hashes.keys()))
    changed_envs: List[str] = []
    for k in all_env_keys:
        if current.env_hashes.get(k) != cached.env_hashes.get(k):
            changed_envs.append(k)

    if changed_envs:
        env_preview = ", ".join(changed_envs)
        reasons.append(
            CacheMissReason(
                reason_code=CacheMissCode.ENV_VAR_CHANGED.value,
                detail=f"Tracked environment variable(s) changed: {env_preview}",
                changed_paths=changed_envs,
            )
        )

    all_output_paths = sorted(set(current.outputs_hashes.keys()) | set(cached.outputs_hashes.keys()))
    tampered_outputs: List[str] = []
    for path in all_output_paths:
        cur_val = current.outputs_hashes.get(path, MISSING_FILE_SENTINEL)
        old_val = cached.outputs_hashes.get(path, MISSING_FILE_SENTINEL)
        if cur_val == MISSING_FILE_SENTINEL or cur_val != old_val:
            tampered_outputs.append(path)

    if tampered_outputs:
        out_preview = ", ".join(tampered_outputs)
        reasons.append(
            CacheMissReason(
                reason_code=CacheMissCode.OUTPUT_FILE_MISSING_OR_TAMPERED.value,
                detail=f"Declared output file(s) missing or modified since last cached build: {out_preview}",
                changed_paths=tampered_outputs,
            )
        )

    if rebuilt_upstream_deps:
        deps_preview = ", ".join(rebuilt_upstream_deps)
        reasons.append(
            CacheMissReason(
                reason_code=CacheMissCode.UPSTREAM_DEP_REBUILT.value,
                detail=f"Upstream dependency task(s) rebuilt or invalidated: {deps_preview}",
                changed_paths=list(rebuilt_upstream_deps),
            )
        )

    return reasons


class CacheStore:
    """Manages reading and writing content-addressed task fingerprints in `.taskloom/cache.json`."""

    def __init__(self, cache_path: Path) -> None:
        self.cache_path = cache_path

    def load(self) -> Dict[str, TaskFingerprint]:
        if not self.cache_path.exists():
            return {}
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            tasks_map = raw.get("tasks", {})
            return {
                name: TaskFingerprint.from_dict(entry)
                for name, entry in tasks_map.items()
                if isinstance(entry, dict)
            }
        except (OSError, ValueError, KeyError):
            return {}

    def save(self, fingerprints: Dict[str, TaskFingerprint]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "tasks": {
                name: fp.to_dict()
                for name, fp in sorted(fingerprints.items())
            },
        }
        self.cache_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )


class TaskLoomEngine:
    """Executes or explains DAG tasks with SHA-256 content-addressed caching."""

    def __init__(
        self,
        base_dir: Path,
        cache_path: Optional[Path] = None,
        environ: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.base_dir = base_dir.resolve()
        self.cache_path = (
            cache_path.resolve()
            if cache_path is not None
            else (self.base_dir / ".taskloom" / "cache.json")
        )
        self.store = CacheStore(self.cache_path)
        self.environ = environ

    def run_pipeline(
        self,
        tasks: Dict[str, TaskSpec],
        taskfile_path: str = "taskloom.json",
        target: Optional[str] = None,
        dry_run: bool = False,
        explain_only: bool = False,
        force: bool = False,
    ) -> PipelineReport:
        """Execute or analyze the DAG in topological layer order."""
        layers = compute_topological_layers(tasks, target=target)
        sub_tasks = resolve_subgraph(tasks, target=target)
        cached_fps = self.store.load()

        mode = "explain" if explain_only else ("dry-run" if dry_run else "run")
        results: List[TaskExecutionResult] = []
        rebuilt_tasks: Set[str] = set()
        failed_tasks: Set[str] = set()

        for layer_idx, layer_names in enumerate(layers):
            for task_name in layer_names:
                spec = sub_tasks[task_name]

                # If an upstream dependency failed in this run, skip execution
                upstream_failed = [d for d in spec.deps if d in failed_tasks]
                if upstream_failed:
                    results.append(
                        TaskExecutionResult(
                            name=task_name,
                            status="SKIPPED_UPSTREAM_FAILURE",
                            cache_hit=False,
                            miss_reasons=[
                                CacheMissReason(
                                    reason_code=CacheMissCode.UPSTREAM_DEP_REBUILT.value,
                                    detail=f"Skipped because upstream dependency failed: {', '.join(upstream_failed)}",
                                    changed_paths=upstream_failed,
                                )
                            ],
                            duration_ms=0.0,
                            return_code=1,
                            layer_index=layer_idx,
                        )
                    )
                    failed_tasks.add(task_name)
                    continue

                pre_fp = compute_task_fingerprint(
                    spec, self.base_dir, environ=self.environ
                )
                rebuilt_upstream = sorted([d for d in spec.deps if d in rebuilt_tasks])
                miss_reasons = diff_fingerprints(
                    current=pre_fp,
                    cached=cached_fps.get(task_name),
                    rebuilt_upstream_deps=rebuilt_upstream,
                    force=force,
                )

                if not miss_reasons:
                    results.append(
                        TaskExecutionResult(
                            name=task_name,
                            status="CACHED",
                            cache_hit=True,
                            miss_reasons=[],
                            duration_ms=0.0,
                            return_code=0,
                            layer_index=layer_idx,
                            digest=pre_fp.combined_digest,
                        )
                    )
                    continue

                # Cache miss detected
                rebuilt_tasks.add(task_name)
                if explain_only or dry_run:
                    results.append(
                        TaskExecutionResult(
                            name=task_name,
                            status="WOULD_RUN",
                            cache_hit=False,
                            miss_reasons=miss_reasons,
                            duration_ms=0.0,
                            return_code=0,
                            layer_index=layer_idx,
                            digest=pre_fp.combined_digest,
                        )
                    )
                    continue

                # Execute command in base_dir
                start_ns = time.perf_counter_ns()
                exec_env = dict(os.environ)
                if self.environ is not None:
                    exec_env.update(self.environ)

                proc = subprocess.run(
                    spec.command,
                    shell=True,
                    cwd=str(self.base_dir),
                    env=exec_env,
                    capture_output=True,
                    text=True,
                )
                elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000.0

                if proc.returncode != 0:
                    failed_tasks.add(task_name)
                    results.append(
                        TaskExecutionResult(
                            name=task_name,
                            status="FAILED",
                            cache_hit=False,
                            miss_reasons=miss_reasons,
                            duration_ms=elapsed_ms,
                            return_code=proc.returncode,
                            layer_index=layer_idx,
                            digest=pre_fp.combined_digest,
                            stdout=proc.stdout,
                            stderr=proc.stderr,
                        )
                    )
                else:
                    # Re-compute fingerprint after execution so newly generated outputs are hashed
                    post_fp = compute_task_fingerprint(
                        spec, self.base_dir, environ=self.environ
                    )
                    cached_fps[task_name] = post_fp
                    results.append(
                        TaskExecutionResult(
                            name=task_name,
                            status="EXECUTED",
                            cache_hit=False,
                            miss_reasons=miss_reasons,
                            duration_ms=elapsed_ms,
                            return_code=0,
                            layer_index=layer_idx,
                            digest=post_fp.combined_digest,
                            stdout=proc.stdout,
                            stderr=proc.stderr,
                        )
                    )

        if not explain_only and not dry_run:
            self.store.save(cached_fps)

        return PipelineReport(
            taskfile_path=taskfile_path,
            mode=mode,
            target_task=target,
            layers=layers,
            results=results,
        )
