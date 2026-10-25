#!/usr/bin/env python3

"""Unit tests for claude-agent-gate. Run: make test-claude-agent-gate"""

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import ModuleType

TOOL_PATH = Path(__file__).resolve().parent.parent / "bin" / "claude-agent-gate.py"


def load_tool() -> ModuleType:
    """Imports claude-agent-gate.py; its dashed file name needs an explicit file load."""
    spec = importlib.util.spec_from_file_location("claude_agent_gate", TOOL_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = load_tool()


def run_gate(args: list[str], stdin: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(TOOL_PATH), *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def hook_input(subagent_type: object) -> str:
    return json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": "Agent",
            "tool_input": {"description": "d", "prompt": "p", "subagent_type": subagent_type},
        }
    )


class DenyReasonTest(unittest.TestCase):
    allowed = ("Junior", "Orchestrator")

    def test_exact_match_is_allowed(self) -> None:
        self.assertIsNone(
            tool.deny_reason(self.allowed, {"subagent_type": "Junior"})
        )

    def test_case_and_separator_variants_are_allowed(self) -> None:
        for value in ("junior", "JUNIOR", "jun-ior", "jun_ior", "Jun ior", "orchestrator"):
            with self.subTest(value=value):
                self.assertIsNone(
                    tool.deny_reason(self.allowed, {"subagent_type": value})
                )

    def test_separators_in_allowed_names_are_ignored(self) -> None:
        self.assertIsNone(
            tool.deny_reason(("Code Reviewer",), {"subagent_type": "code-reviewer"})
        )

    def test_other_type_is_denied_with_exact_message(self) -> None:
        self.assertEqual(
            tool.deny_reason(self.allowed, {"subagent_type": "solo"}),
            "Agent type 'solo' is not allowed here; allowed: Junior, Orchestrator",
        )

    def test_missing_subagent_type_is_blocked(self) -> None:
        self.assertEqual(
            tool.deny_reason(self.allowed, {}),
            "Agent type is missing; allowed: Junior, Orchestrator",
        )

    def test_null_subagent_type_is_blocked_as_missing(self) -> None:
        self.assertEqual(
            tool.deny_reason(self.allowed, {"subagent_type": None}),
            "Agent type is missing; allowed: Junior, Orchestrator",
        )

    def test_empty_subagent_type_is_blocked(self) -> None:
        for value in ("", "  ", "-_ "):
            with self.subTest(value=value):
                self.assertEqual(
                    tool.deny_reason(self.allowed, {"subagent_type": value}),
                    "Agent type is empty; allowed: Junior, Orchestrator",
                )

    def test_non_string_subagent_type_is_blocked(self) -> None:
        for value in (1, ["Junior"], {"name": "Junior"}, True):
            with self.subTest(value=value):
                self.assertEqual(
                    tool.deny_reason(self.allowed, {"subagent_type": value}),
                    "Agent type must be a string; allowed: Junior, Orchestrator",
                )

    def test_control_characters_keep_the_message_on_one_line(self) -> None:
        reason = tool.deny_reason(self.allowed, {"subagent_type": "a\nb"})
        self.assertEqual(
            reason,
            "Agent type 'a\\nb' is not allowed here; allowed: Junior, Orchestrator",
        )


class CliTest(unittest.TestCase):
    def test_allowed_type_exits_zero_silently(self) -> None:
        result = run_gate(["Junior", "Boss"], hook_input("Junior"))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_case_variant_exits_zero(self) -> None:
        result = run_gate(["Junior", "Boss"], hook_input("jun-ior"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_denied_type_exits_two_with_one_line_reason(self) -> None:
        result = run_gate(["Junior", "Boss"], hook_input("solo"))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stderr,
            "Agent type 'solo' is not allowed here; allowed: Junior, Boss\n",
        )

    def test_blocked_inputs_name_the_allowed_types(self) -> None:
        for value in (None, "", 7):
            with self.subTest(value=value):
                result = run_gate(["Junior", "Boss"], hook_input(value))
                self.assertEqual(result.returncode, 2)
                self.assertIn("allowed: Junior, Boss", result.stderr)
                self.assertEqual(result.stderr.count("\n"), 1)
                self.assertNotIn("Traceback", result.stderr)

    def test_malformed_invocations_fail_closed_with_error_line(self) -> None:
        cases = {
            "no args": ([], hook_input("Junior")),
            "invalid JSON": (["Junior"], "{not json"),
            "empty stdin": (["Junior"], ""),
            "non-object JSON": (["Junior"], "[1, 2]"),
            "missing tool_input": (["Junior"], json.dumps({"hook_event_name": "PreToolUse"})),
            "non-object tool_input": (["Junior"], json.dumps({"tool_input": "Junior"})),
            "deep nesting": (["Junior"], "[" * 100000),
        }
        for name, (args, stdin) in cases.items():
            with self.subTest(case=name):
                result = run_gate(args, stdin)
                self.assertEqual(result.returncode, 2)
                self.assertTrue(result.stderr.startswith("Error: "), result.stderr)
                self.assertEqual(result.stderr.count("\n"), 1)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
