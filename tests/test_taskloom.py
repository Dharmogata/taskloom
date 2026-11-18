"""Comprehensive unit test suite for TaskLoom DAG planner, lint rules, SHA-256 caching, and CLI."""

from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from taskloom.cli import main
from taskloom.dag import (
    check_dag,
    compute_topological_layers,
    get_builtin_rules,
    load_taskfile,
    parse_taskfile_dict,
)
from taskloom.engine import (
    CacheMissCode,
    TaskLoomEngine,
    compute_task_fingerprint,
)
from taskloom.models import TaskSpec


class TestTaskLoomDAGAndRules(unittest.TestCase):
    """Tests for topological layering and static lint rules TL001–TL005."""

    def test_topological_sort_layers_and_target_subgraph(self) -> None:
        raw = {
            "tasks": {
                "compile_core": {
                    "command": "echo core",
                    "inputs": ["src/core.c"],
                    "outputs": ["build/core.o"],
                },
                "compile_util": {
                    "command": "echo util",
                    "inputs": ["src/util.c"],
                    "outputs": ["build/util.o"],
                },
                "link_app": {
                    "command": "echo link",
                    "deps": ["compile_core", "compile_util"],
                    "inputs": ["build/core.o", "build/util.o"],
                    "outputs": ["dist/app"],
                },
                "docs": {
                    "command": "echo docs",
                    "inputs": ["README.md"],
                    "outputs": ["dist/docs.html"],
                },
            }
        }
        tasks = parse_taskfile_dict(raw)
        layers = compute_topological_layers(tasks)
        self.assertEqual(len(layers), 2)
        self.assertEqual(layers[0], ["compile_core", "compile_util", "docs"])
        self.assertEqual(layers[1], ["link_app"])

        sub_layers = compute_topological_layers(tasks, target="link_app")
        self.assertEqual(sub_layers, [["compile_core", "compile_util"], ["link_app"]])

    def test_tl001_dependency_cycle_detected(self) -> None:
        raw = {
            "tasks": {
                "a": {"command": "echo a", "deps": ["c"], "inputs": ["a.txt"], "outputs": ["a.out"]},
                "b": {"command": "echo b", "deps": ["a"], "inputs": ["b.txt"], "outputs": ["b.out"]},
                "c": {"command": "echo c", "deps": ["b"], "inputs": ["c.txt"], "outputs": ["c.out"]},
            }
        }
        tasks = parse_taskfile_dict(raw)
        report = check_dag(tasks)
        rule_ids = [v.rule_id for v in report.violations]
        self.assertIn("TL001", rule_ids)
        self.assertTrue(report.has_critical)
        with self.assertRaises(ValueError):
            compute_topological_layers(tasks)

    def test_tl002_unknown_task_dependency_detected(self) -> None:
        raw = {
            "tasks": {
                "bundle": {
                    "command": "echo bundle",
                    "deps": ["nonexistent_step"],
                    "inputs": ["src/index.js"],
                    "outputs": ["dist/bundle.js"],
                }
            }
        }
        tasks = parse_taskfile_dict(raw)
        report = check_dag(tasks)
        rule_ids = [v.rule_id for v in report.violations]
        self.assertIn("TL002", rule_ids)
        with self.assertRaises(ValueError):
            compute_topological_layers(tasks)

    def test_tl003_overlapping_task_outputs_detected(self) -> None:
        raw = {
            "tasks": {
                "gen_a": {
                    "command": "echo a",
                    "inputs": ["a.in"],
                    "outputs": ["dist/shared.out"],
                },
                "gen_b": {
                    "command": "echo b",
                    "inputs": ["b.in"],
                    "outputs": ["./dist/shared.out"],
                },
            }
        }
        tasks = parse_taskfile_dict(raw)
        report = check_dag(tasks)
        rule_ids = [v.rule_id for v in report.violations]
        self.assertIn("TL003", rule_ids)

    def test_tl004_missing_declared_inputs_or_outputs_detected(self) -> None:
        raw = {
            "tasks": {
                "side_effect_only": {
                    "command": "echo hello",
                }
            }
        }
        tasks = parse_taskfile_dict(raw)
        report = check_dag(tasks)
        rule_ids = [v.rule_id for v in report.violations]
        self.assertIn("TL004", rule_ids)

    def test_tl005_undeclared_producer_dependency_detected(self) -> None:
        raw = {
            "tasks": {
                "produce_schema": {
                    "command": "echo schema",
                    "inputs": ["schema.idl"],
                    "outputs": ["gen/schema.json"],
                },
                "consume_schema": {
                    "command": "echo client",
                    "deps": [],
                    "inputs": ["gen/schema.json"],
                    "outputs": ["dist/client.py"],
                },
            }
        }
        tasks = parse_taskfile_dict(raw)
        report = check_dag(tasks)
        rule_ids = [v.rule_id for v in report.violations]
        self.assertIn("TL005", rule_ids)


class TestTaskLoomEngineAndProvenance(unittest.TestCase):
    """Tests for SHA-256 fingerprinting, cache hits, and cache-miss provenance attribution."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "src").mkdir(parents=True, exist_ok=True)
        (self.root / "dist").mkdir(parents=True, exist_ok=True)
        (self.root / "src" / "data.txt").write_text("alpha-v1\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _build_sample_tasks(self, build_cmd_override: str | None = None) -> dict[str, TaskSpec]:
        py = sys.executable
        build_cmd = build_cmd_override or (
            f'"{py}" -c "from pathlib import Path; '
            f"txt = Path('src/data.txt').read_text(encoding='utf-8'); "
            f"Path('dist/stage1.txt').write_text(txt.upper(), encoding='utf-8')\""
        )
        package_cmd = (
            f'"{py}" -c "from pathlib import Path; '
            f"txt = Path('dist/stage1.txt').read_text(encoding='utf-8'); "
            f"Path('dist/final.txt').write_text('PKG:' + txt, encoding='utf-8')\""
        )
        return parse_taskfile_dict(
            {
                "tasks": {
                    "build_stage1": {
                        "command": build_cmd,
                        "inputs": ["src/*.txt"],
                        "outputs": ["dist/stage1.txt"],
                        "env": ["BUILD_PROFILE"],
                    },
                    "package_final": {
                        "command": package_cmd,
                        "deps": ["build_stage1"],
                        "inputs": ["dist/stage1.txt"],
                        "outputs": ["dist/final.txt"],
                    },
                }
            }
        )

    def test_first_run_then_cache_hit_then_input_modified(self) -> None:
        tasks = self._build_sample_tasks()
        env = {"BUILD_PROFILE": "release"}
        engine = TaskLoomEngine(base_dir=self.root, environ=env)

        # 1. First run: both tasks should execute with FIRST_RUN provenance
        r1 = engine.run_pipeline(tasks)
        self.assertEqual(r1.cache_hits, 0)
        self.assertEqual(r1.cache_misses, 2)
        self.assertEqual(r1.results[0].miss_reasons[0].reason_code, CacheMissCode.FIRST_RUN.value)
        self.assertTrue((self.root / "dist" / "final.txt").exists())

        # 2. Second run without changes: 100% cache hit
        r2 = engine.run_pipeline(tasks)
        self.assertEqual(r2.cache_hits, 2)
        self.assertEqual(r2.cache_misses, 0)
        self.assertEqual(r2.results[0].status, "CACHED")
        self.assertEqual(r2.results[1].status, "CACHED")

        # 3. Modify input file `src/data.txt`: explain should attribute INPUT_FILE_MODIFIED and UPSTREAM_DEP_REBUILT
        (self.root / "src" / "data.txt").write_text("alpha-v2-modified\n", encoding="utf-8")
        explain_report = engine.run_pipeline(tasks, explain_only=True)
        self.assertEqual(explain_report.cache_misses, 2)
        stage1_reasons = [m.reason_code for m in explain_report.results[0].miss_reasons]
        final_reasons = [m.reason_code for m in explain_report.results[1].miss_reasons]
        self.assertIn(CacheMissCode.INPUT_FILE_MODIFIED.value, stage1_reasons)
        self.assertIn("src/data.txt", explain_report.results[0].miss_reasons[0].changed_paths)
        self.assertIn(CacheMissCode.UPSTREAM_DEP_REBUILT.value, final_reasons)

    def test_command_env_and_output_tamper_provenance(self) -> None:
        tasks = self._build_sample_tasks()
        engine = TaskLoomEngine(base_dir=self.root, environ={"BUILD_PROFILE": "release"})
        engine.run_pipeline(tasks)

        # Change tracked environment variable
        engine_debug = TaskLoomEngine(base_dir=self.root, environ={"BUILD_PROFILE": "debug"})
        exp_env = engine_debug.run_pipeline(tasks, explain_only=True)
        stage1_codes = [m.reason_code for m in exp_env.results[0].miss_reasons]
        self.assertIn(CacheMissCode.ENV_VAR_CHANGED.value, stage1_codes)

        # Change command string
        py = sys.executable
        modified_tasks = self._build_sample_tasks(
            build_cmd_override=(
                f'"{py}" -c "from pathlib import Path; '
                f"Path('dist/stage1.txt').write_text('CUSTOM', encoding='utf-8')\""
            )
        )
        exp_cmd = engine.run_pipeline(modified_tasks, explain_only=True)
        cmd_codes = [m.reason_code for m in exp_cmd.results[0].miss_reasons]
        self.assertIn(CacheMissCode.COMMAND_CHANGED.value, cmd_codes)

        # Tamper with output file on disk
        (self.root / "dist" / "final.txt").unlink()
        exp_out = engine.run_pipeline(tasks, explain_only=True)
        final_codes = [m.reason_code for m in exp_out.results[1].miss_reasons]
        self.assertIn(CacheMissCode.OUTPUT_FILE_MISSING_OR_TAMPERED.value, final_codes)


class TestTaskLoomCLI(unittest.TestCase):
    """Tests for CLI subcommands (`run`, `explain`, `check`, `rules`)."""

    def test_cli_rules_and_check_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input.txt").write_text("hello taskloom\n", encoding="utf-8")
            py = sys.executable
            taskfile = root / "taskloom.json"
            taskfile.write_text(
                json.dumps(
                    {
                        "tasks": {
                            "copy_step": {
                                "command": (
                                    f'"{py}" -c "from pathlib import Path; '
                                    f"Path('out.txt').write_text(Path('input.txt').read_text(encoding='utf-8'), encoding='utf-8')\""
                                ),
                                "inputs": ["input.txt"],
                                "outputs": ["out.txt"],
                            }
                        }
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            # 1. `taskloom rules --format json`
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = main(["rules", "--format", "json"])
            self.assertEqual(rc, 0)
            rules_data = json.loads(buf.getvalue())
            self.assertEqual(len(rules_data), len(get_builtin_rules()))

            # 2. `taskloom check`
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = main(["check", str(taskfile)])
            self.assertEqual(rc, 0)
            self.assertIn("0 violations", buf.getvalue())

            # 3. `taskloom run` (first run -> miss)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = main(["run", str(taskfile), "--format", "json"])
            self.assertEqual(rc, 0)
            run_data = json.loads(buf.getvalue())
            self.assertEqual(run_data["cache_misses"], 1)

            # 4. `taskloom explain` (second pass -> cache hit)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                rc = main(["explain", str(taskfile), "--format", "markdown"])
            self.assertEqual(rc, 0)
            self.assertIn("HIT", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
