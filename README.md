# TaskLoom

**TaskLoom** is a content-addressed SHA-256 DAG task runner and cache-miss provenance debugger for local build pipelines and CI workflows.

Traditional task runners (`make`, shell scripts, or basic task aliases) either rely on fragile file modification timestamps (`mtime`) or re-run everything from scratch without explaining *why* a step rebuilt. **TaskLoom** fingerprints every task's command string, input file globs, tracked environment variables, and output artifacts using SHA-256 (`TaskFingerprint`), executes tasks in topological DAG layers, and attributes every cache miss to its exact root cause (`COMMAND_CHANGED`, `INPUT_FILE_MODIFIED`, `ENV_VAR_CHANGED`, `OUTPUT_FILE_MISSING_OR_TAMPERED`, or `UPSTREAM_DEP_REBUILT`).

---

## Who It's For

- **Platform and build engineers** debugging why incremental builds or codegen pipelines unexpectedly miss cache in CI.
- **Polyglot repository maintainers** looking for a zero-dependency, content-addressed task runner with built-in static DAG lint rules (`TL001`–`TL005`).

---

## What It's Built With

- **Language:** Python 3.10+
- **Core Architecture:** Pure Python standard library (`hashlib`, `subprocess`, `os`, `glob`, `json`, `argparse`, `pathlib`, `dataclasses`, `unittest`) with **zero external runtime dependencies**.
- **Test Suite:** Compatible with both `pytest` and `python -m unittest`.

---

## Features

- **SHA-256 Content-Addressed Caching (`.taskloom/cache.json`):** Computes deterministic digests across task command strings, expanded input file globs, tracked environment variables, and declared output files.
- **Exact Cache-Miss Provenance Debugger (`taskloom explain`):** Inspects why any task in the DAG would rebuild without executing a single command (`FIRST_RUN`, `COMMAND_CHANGED`, `INPUT_FILE_MODIFIED`, `ENV_VAR_CHANGED`, `OUTPUT_FILE_MISSING_OR_TAMPERED`, `UPSTREAM_DEP_REBUILT`).
- **Built-in Static Pipeline Lint Rules (`TL001` – `TL005`):**
  - `TL001` (`CRITICAL` / `dependency-cycle`): Detects circular dependencies in the task graph.
  - `TL002` (`CRITICAL` / `unknown-task-dependency`): Flags tasks referencing nonexistent dependency names in `deps`.
  - `TL003` (`HIGH` / `overlapping-task-outputs`): Detects two tasks declaring the same output file path, preventing race conditions and cache thrashing.
  - `TL004` (`MEDIUM` / `missing-declared-inputs-or-outputs`): Flags unanchored side-effect tasks that declare neither `inputs` nor `outputs`.
  - `TL005` (`MEDIUM` / `undeclared-producer-dependency`): Detects when Task B lists an input file produced as an output of Task A without listing Task A in `deps`.
- **Multi-Format Reporting (`text`, `json`, `markdown`):** Renders ASCII topological layer plans, provenance tables, and CI-ready JSON/Markdown reports.

```text
$ taskloom explain taskloom.json

TaskLoom Cache-Miss Provenance Explain · taskloom.json
──────────────────────────────────────────────────────────────────────────────
Topological Execution Plan:
  Layer 0: [compile_schema]  ||  [lint_configs]
             │
             ▼
  Layer 1: [bundle_app]
──────────────────────────────────────────────────────────────────────────────
  [L0] compile_schema       🔨 WOULD_RUN      (sha256:9f3a1c8e4d02 · 0.0ms)
       └ [INPUT_FILE_MODIFIED] Input file(s) modified, added, or missing: src/schema.idl
  [L0] lint_configs         ⚡ CACHED         (sha256:4b8e21a7c910 · 0.0ms)
  [L1] bundle_app           🔨 WOULD_RUN      (sha256:7d1e509b3a11 · 0.0ms)
       └ [UPSTREAM_DEP_REBUILT] Upstream dependency task(s) rebuilt or invalidated: compile_schema
──────────────────────────────────────────────────────────────────────────────
Summary: 3 task(s) (Cache Hits: 1, Cache Misses: 2, Failed: 0)
```

---

## How to Run It

### 1. Install

```bash
git clone https://github.com/Dharmogata/taskloom.git
cd taskloom
pip install -e .
```

### 2. Run, Explain, or Lint a Taskfile

```bash
# Lint the task DAG for cycles, overlapping outputs, and missing producer edges
taskloom check taskloom.json

# Execute the DAG with SHA-256 content caching
taskloom run taskloom.json

# Explain why each task would hit or miss cache without running commands
taskloom explain taskloom.json

# Run or explain only a specific target subgraph
taskloom run taskloom.json --target bundle_app --format markdown

# List all built-in static DAG lint rules (TL001–TL005)
taskloom rules
```

### 3. Run the Test Suite

```bash
# Using pytest
pytest -v

# Or using standard library unittest
PYTHONPATH=src python -m unittest discover -s tests -v
```

---

## Planned Work Ahead (TODO & Roadmap)

TaskLoom has **200+ hours of planned engineering work** detailed in [ROADMAP.md](ROADMAP.md):

- [ ] **Phase 2 (~35h): Parallel Layer Worker Pool & Resource Concurrency Semaphores** (concurrent execution of tasks within the same topological layer using configurable `--jobs N` and CPU/memory resource slots).
- [ ] **Phase 3 (~40h): Local & Remote Content-Addressed Artifact Blob Store (`.taskloom/cas/`)** (restore cached output files directly from content-addressed blobs when switching Git branches).
- [ ] **Phase 4 (~35h): Extended Static Pipeline Lint Rules (`TL006`–`TL015`)** (glob overlap detection, undeclared environment variable usage in shell commands, dead/unreachable subgraph rules, and shell portability checks).
- [ ] **Phase 5 (~40h): File-System Syscall / Access Tracer (`taskloom trace`)** (verify declared `inputs` and `outputs` against actual files read and written during command execution).
- [ ] **Phase 6 (~35h): OpenTelemetry Trace Exporter, Chrome `trace_event` Timeline & GitHub Action**.

See [ROADMAP.md](ROADMAP.md) for the complete specification.

---

## License

Released under the [MIT License](LICENSE).
