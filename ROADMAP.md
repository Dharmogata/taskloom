# TaskLoom Engineering Roadmap (200+ Hours)

This roadmap details the planned multi-phase evolution of **TaskLoom** from a deterministic SHA-256 DAG task runner and cache-miss provenance debugger into a full-featured content-addressed build orchestrator with parallel layer execution, local/remote artifact blob restoration, dynamic file-access verification, and Chrome trace timeline exports.

---

## Phase 1: Core SHA-256 Fingerprinting, DAG Planner & Static Lint Rules (Completed - v0.1.0)
- [x] Core data models for `TaskSpec`, `TaskFingerprint`, `CacheMissReason`, `TaskExecutionResult`, and `PipelineReport` (`models.py`).
- [x] Topological layer computation and target subgraph extraction (`dag.py`).
- [x] Built-in static pipeline lint rules `TL001` (`dependency-cycle`), `TL002` (`unknown-task-dependency`), `TL003` (`overlapping-task-outputs`), `TL004` (`missing-declared-inputs-or-outputs`), and `TL005` (`undeclared-producer-dependency`).
- [x] SHA-256 content-addressed cache engine (`.taskloom/cache.json`) with exact cache-miss provenance attribution (`FIRST_RUN`, `COMMAND_CHANGED`, `INPUT_FILE_MODIFIED`, `ENV_VAR_CHANGED`, `OUTPUT_FILE_MISSING_OR_TAMPERED`, `UPSTREAM_DEP_REBUILT`).
- [x] CLI (`run`, `explain`, `check`, `rules`) with `text`, `json`, and `markdown` formatters.

---

## Phase 2: Parallel Layer Execution & Resource Concurrency Semaphores (~35 Hours)
- [ ] **Multi-Worker Layer Executor (`--jobs N`):** Execute independent tasks within the same topological layer concurrently using `concurrent.futures.ThreadPoolExecutor` and non-blocking subprocess pipe readers.
- [ ] **Named Resource Semaphores (`resources: {"gpu": 1, "db_port": 1}`):** Allow tasks to declare exclusive or bounded resource locks so parallel tasks do not collide on local ports or hardware devices.
- [ ] **Fail-Fast vs Keep-Going Modes (`--keep-going`):** Configurable cancellation behavior when one task in a parallel layer fails while sibling branches can still complete.

---

## Phase 3: Content-Addressed Artifact Blob Cache & Branch Switching (~40 Hours)
- [ ] **Local Object Store (`.taskloom/cas/sha256/<ab>/<digest>`):** Store compressed snapshots of declared task output files indexed by `TaskFingerprint.combined_digest`.
- [ ] **Instant Artifact Restoration on Cache Hit:** When switching Git branches or after `git clean`, restore matching output files directly from `.taskloom/cas/` without re-executing the command.
- [ ] **Pluggable Remote HTTP/S3 Read-Write Cache Backend:** Share content-addressed build artifacts across CI runners and developer machines with cryptographic SHA-256 verification on download.

---

## Phase 4: Extended Static DAG & Shell Portability Lint Rules (`TL006`–`TL015`) (~35 Hours)
- [ ] **Rule `TL006` (Glob Output Overlap):** Statically detect overlapping wildcard patterns in task outputs (e.g., `dist/*.js` vs `dist/app.js`).
- [ ] **Rule `TL007` (Untracked `$ENV_VAR` Reference in Command):** Parse task `command` strings for `$VAR` / `${VAR}` expansions that are not listed in the task's `env` array.
- [ ] **Rule `TL008` (Shadowed Producer Glob):** Detect when a consumer task's input glob (`build/*.o`) matches outputs from multiple upstream tasks where only a subset is declared in `deps`.
- [ ] **Rule `TL009` – `TL015`:** Cross-platform shell portability warnings, redundant transitive dependency edges, and missing output directory creation checks.

---

## Phase 5: Dynamic File-Access Tracer & Hermeticity Auditor (`taskloom trace`) (~40 Hours)
- [ ] **Undeclared Input Read Detector:** Monitor file access timestamps/probes or platform audit hooks during task execution to flag files read by a command that were omitted from `inputs`.
- [ ] **Unexpected Output Mutation Detector:** Snapshot workspace tree hashes before and after task execution in strict mode (`--strict-sandbox`) to catch tasks mutating files outside their declared `outputs`.

---

## Phase 6: Chrome Trace Timeline (`trace_event`), Graphviz DOT & GitHub Action (~35 Hours)
- [ ] **Chrome Tracing Exporter (`taskloom run --trace build-trace.json`):** Export critical-path timing and cache-hit/miss metadata in `chrome://tracing` / Perfetto JSON format.
- [ ] **Graphviz DOT & Mermaid Exporter (`taskloom graph --format mermaid`)** for interactive architecture documentation.
