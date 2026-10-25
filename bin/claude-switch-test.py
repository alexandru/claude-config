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


JUNIOR = """---
name: Junior
description: "Use for evidence."
model: sonnet
effort: low
disallowedTools: Agent
---

You are Junior.
model: body lines stay untouched
"""


class UpdateFrontmatterTest(unittest.TestCase):
    def test_replaces_settings_after_the_description(self) -> None:
        updated = tool.update_frontmatter(
            JUNIOR, {"model": "claude-sonnet-5-5", "effort": "high"}
        )
        self.assertEqual(
            updated,
            JUNIOR.replace(
                "model: sonnet\neffort: low", "model: claude-sonnet-5-5\neffort: high"
            ),
        )

    def test_omitted_keys_are_removed(self) -> None:
        updated = tool.update_frontmatter(JUNIOR, {"model": "claude-haiku-5-5"})
        self.assertNotIn("effort:", updated)
        self.assertIn(
            'description: "Use for evidence."\nmodel: claude-haiku-5-5\n', updated
        )
        self.assertIn("model: body lines stay untouched", updated)

    def test_rejects_definitions_without_frontmatter(self) -> None:
        with self.assertRaises(tool.ConfigError):
            tool.update_frontmatter("You are Junior.\n", {"model": "sonnet"})


class AgentSettingsTest(unittest.TestCase):
    def test_rejects_unsupported_keys(self) -> None:
        with self.assertRaises(tool.ConfigError) as raised:
            tool.agent_settings({"agents": {"Junior": {"tools": "Read"}}})
        self.assertIn("unsupported key 'tools'", str(raised.exception))

    def test_rejects_values_that_are_not_plain_scalars(self) -> None:
        with self.assertRaises(tool.ConfigError):
            tool.agent_settings({"agents": {"Junior": {"model": "x\ntools: Bash"}}})


class AgentTableTest(unittest.TestCase):
    def test_main_agent_uses_settings_then_configured_agents(self) -> None:
        config = {
            "agent": "Orchestrator",
            "model": "claude-opus-5-5",
            "effortLevel": "high",
        }
        agents = {"Junior": {"model": "claude-sonnet-5-5", "effort": "high"}}
        self.assertEqual(
            tool.format_agent_table(config, agents),
            "\n"
            "  Agent        │ Model             │ Effort\n"
            "  ─────────────┼───────────────────┼───────\n"
            "  Orchestrator │ claude-opus-5-5   │ high\n"
            "  Junior       │ claude-sonnet-5-5 │ high\n"
            "\n",
        )

    def test_without_models_there_is_no_table(self) -> None:
        self.assertIsNone(tool.format_agent_table({"permissions": {}}, {}))


SOLO = """---
name: Solo
description: "Use for judgment."
---

You are Solo.
"""


def write_config(config_dir: Path, common: object, presets: object) -> None:
    (config_dir / tool.COMMON_FILE).write_text(json.dumps(common), encoding="utf-8")
    (config_dir / tool.PRESETS_FILE).write_text(json.dumps(presets), encoding="utf-8")
    templates = config_dir / tool.TEMPLATES_DIR
    templates.mkdir()
    (templates / "Junior.md").write_text(JUNIOR, encoding="utf-8")
    (templates / "Solo.md").write_text(SOLO, encoding="utf-8")


def read_agent(config_dir: Path, name: str) -> str:
    path: Path = config_dir / tool.AGENTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


class SwitchTest(unittest.TestCase):
    def test_writes_settings_and_generates_agents_from_templates(self) -> None:
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
                "agents": {"Junior": {"model": "claude-sonnet-5-5", "effort": "high"}},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            write_config(config_dir, common, presets)
            stdout = io.StringIO()
            with (
                mock.patch.object(tool, "CONFIG_DIR", config_dir),
                mock.patch("sys.stdout", stdout),
            ):
                tool.switch("p-opus")
            written = json.loads(
                (config_dir / tool.OUTPUT_FILE).read_text(encoding="utf-8")
            )
            junior = read_agent(config_dir, "Junior")
            solo = read_agent(config_dir, "Solo")
            template = (config_dir / tool.TEMPLATES_DIR / "Junior.md").read_text(
                encoding="utf-8"
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
        self.assertIn("model: claude-sonnet-5-5\neffort: high\n", junior)
        self.assertEqual(solo, SOLO)
        self.assertEqual(template, JUNIOR)
        self.assertEqual(
            stdout.getvalue(),
            "✓ Successfully switched to preset: p-opus\n"
            "✓ Generated settings.json\n"
            "✓ Generated agents/Junior.md\n"
            "✓ Generated agents/Solo.md\n"
            "\n"
            "  Agent        │ Model             │ Effort\n"
            "  ─────────────┼───────────────────┼───────\n"
            "  Orchestrator │ claude-opus-5-5   │ high\n"
            "  Junior       │ claude-sonnet-5-5 │ high\n"
            "\n",
        )

    def test_agents_a_preset_omits_do_not_keep_earlier_models(self) -> None:
        presets = {
            "a": {"agents": {"Junior": {"model": "claude-haiku-5-5", "effort": "low"}}},
            "b": {"model": "claude-opus-5-5"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            write_config(config_dir, {}, presets)
            with (
                mock.patch.object(tool, "CONFIG_DIR", config_dir),
                mock.patch("sys.stdout", io.StringIO()),
            ):
                tool.switch("a")
                tool.switch("b")
            junior = read_agent(config_dir, "Junior")
        self.assertEqual(junior, JUNIOR.replace("model: sonnet\neffort: low\n", ""))

    def test_agents_without_templates_write_nothing(self) -> None:
        presets = {
            "p": {
                "model": "claude-opus-5-5",
                "agents": {"../Nobody": {"model": "sonnet"}},
            }
        }
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            write_config(config_dir, {}, presets)
            with (
                mock.patch.object(tool, "CONFIG_DIR", config_dir),
                self.assertRaises(tool.ConfigError) as raised,
            ):
                tool.switch("p")
            self.assertFalse((config_dir / tool.OUTPUT_FILE).exists())
            self.assertFalse((config_dir / tool.AGENTS_DIR).exists())
        self.assertIn(
            "without a template in agent-templates/: ../Nobody", str(raised.exception)
        )

    def test_hides_common_presets_from_selection_help(self) -> None:
        presets = {"base": {"common": True}, "default": {"extends": ["base"]}}
        self.assertEqual(tool.format_presets(presets), "  - default")


if __name__ == "__main__":
    unittest.main(verbosity=2)
