#!/usr/bin/env -S pipx run

# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "json5==0.17.3",
#   "typer==0.27.2",
# ]
# ///

"""Unit tests for claude-switch. Run: make test-claude-switch

The pins mirror claude-switch.py's PEP 723 metadata, because importing the
tool module imports those packages.
"""

import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

TOOL_PATH = Path(__file__).resolve().parent / "claude-switch.py"


def load_tool() -> ModuleType:
    """Imports claude-switch.py; its dashed file name needs an explicit file load."""
    spec = importlib.util.spec_from_file_location("claude_switch", TOOL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = load_tool()


class MergeTest(unittest.TestCase):
    def test_merges_objects_and_replaces_arrays(self) -> None:
        source = {"nested": {"kept": True, "overridden": "old"}, "items": [1, 2]}
        target = {"nested": {"overridden": "new", "added": True}, "items": [3]}
        self.assertEqual(
            tool.merge_objects(source, target),
            {
                "nested": {"kept": True, "overridden": "new", "added": True},
                "items": [3],
            },
        )

    def test_null_targets_keep_source_objects_and_replace_scalars(self) -> None:
        source = {"nested": {"kept": True}, "replaced": "source"}
        target = {"nested": None, "replaced": None}

        merged = tool.merge_objects(source, target)

        self.assertEqual(merged, {"nested": {"kept": True}, "replaced": None})


class ResolvePresetTest(unittest.TestCase):
    def test_applies_parents_in_order_and_removes_metadata(self) -> None:
        presets = {
            "base": {"common": True, "value": "base", "base_only": True},
            "second": {"common": True, "value": "second"},
            "selected": {"extends": ["base", "second"], "value": "selected"},
        }
        self.assertEqual(
            tool.resolve_preset(presets, "selected", ()),
            {"value": "selected", "base_only": True},
        )

    def test_rejects_inheritance_cycles(self) -> None:
        presets = {"first": {"extends": ["second"]}, "second": {"extends": ["first"]}}
        with self.assertRaises(tool.ConfigError) as raised:
            tool.resolve_preset(presets, "first", ())
        self.assertEqual(
            str(raised.exception),
            "Circular preset inheritance detected: first -> second -> first",
        )


class ReadJsoncTest(unittest.TestCase):
    def test_reads_comments_and_trailing_commas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.jsonc"
            path.write_text('{\n  // fragment\n  "enabled": true,\n}', encoding="utf-8")
            self.assertEqual(tool.read_jsonc_object(path), {"enabled": True})

    def test_rejects_non_object_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.jsonc"
            path.write_text("[1]", encoding="utf-8")
            with self.assertRaises(tool.ConfigError):
                tool.read_jsonc_object(path)


class SummaryTest(unittest.TestCase):
    def test_lists_the_main_session_settings(self) -> None:
        config = {
            "agent": "Orchestrator",
            "model": "claude-opus-5-5",
            "effortLevel": "high",
            "permissions": {"defaultMode": "auto"},
        }
        self.assertEqual(
            tool.format_summary(config),
            "\n"
            "  agent       │ Orchestrator\n"
            "  model       │ claude-opus-5-5\n"
            "  effortLevel │ high\n"
            "\n",
        )

    def test_without_main_session_settings_there_is_no_summary(self) -> None:
        self.assertIsNone(tool.format_summary({"permissions": {}}))


class SwitchTest(unittest.TestCase):
    def test_merges_common_settings_with_the_resolved_preset(self) -> None:
        common = {
            "agent": "Orchestrator",
            "env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "16"},
            "permissions": {
                "defaultMode": "auto",
                "deny": ["Agent(Explore)"],
            },
        }
        presets = {
            "base": {"common": True, "model": "claude-sonnet-5-5"},
            "p-opus": {
                "extends": ["base"],
                "model": "claude-opus-5-5",
                "effortLevel": "high",
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / tool.COMMON_FILE).write_text(
                json.dumps(common), encoding="utf-8"
            )
            (config_dir / tool.PRESETS_FILE).write_text(
                json.dumps(presets), encoding="utf-8"
            )
            stdout = io.StringIO()
            with (
                mock.patch.object(tool, "CONFIG_DIR", config_dir),
                mock.patch("sys.stdout", stdout),
            ):
                tool.switch("p-opus")
            written = json.loads(
                (config_dir / tool.OUTPUT_FILE).read_text(encoding="utf-8")
            )

        self.assertEqual(
            written,
            {
                "agent": "Orchestrator",
                "env": {"CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH": "16"},
                "permissions": {
                    "defaultMode": "auto",
                    "deny": ["Agent(Explore)"],
                },
                "model": "claude-opus-5-5",
                "effortLevel": "high",
            },
        )
        self.assertEqual(
            stdout.getvalue(),
            "✓ Successfully switched to preset: p-opus\n"
            "✓ Generated settings.json\n"
            "\n"
            "  agent       │ Orchestrator\n"
            "  model       │ claude-opus-5-5\n"
            "  effortLevel │ high\n"
            "\n",
        )

    def test_hides_common_presets_from_selection_help(self) -> None:
        presets = {"base": {"common": True}, "default": {"extends": ["base"]}}
        self.assertEqual(tool.format_presets(presets), "  - default")


if __name__ == "__main__":
    unittest.main(verbosity=2)
