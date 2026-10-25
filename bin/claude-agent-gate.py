#!/usr/bin/env python3

"""PreToolUse hook that limits which subagent types an Agent call may spawn.

Claude Code sends the hook event as JSON on stdin. The call passes when
tool_input.subagent_type names one of the allowed types, ignoring case, spaces,
hyphens, and underscores, the way Claude Code resolves agent types. Any other
value exits 2, and Claude Code shows the one-line stderr message to the model.
Malformed input also exits 2, so the gate fails closed.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from typing import TextIO

SEPARATORS = frozenset(" -_")


class GateError(Exception):
    """A malformed invocation or hook input, reported as one line, not a traceback."""


def normalize(name: str) -> str:
    return "".join(char for char in name.casefold() if char not in SEPARATORS)


def printable(value: str) -> str:
    return "".join(char if char.isprintable() else repr(char)[1:-1] for char in value)


def parse_allowed(argv: Sequence[str]) -> tuple[str, ...]:
    if not argv:
        raise GateError(
            "no allowed agent types given; usage: claude-agent-gate AGENT_TYPE..."
        )
    if any(not normalize(name) for name in argv):
        raise GateError("allowed agent types must not be empty")
    return tuple(argv)


def read_tool_input(stdin: TextIO) -> Mapping[str, object]:
    try:
        payload: object = json.loads(stdin.read())
    except (OSError, ValueError, RecursionError) as error:
        raise GateError("hook input is not valid JSON") from error
    if not isinstance(payload, dict):
        raise GateError("hook input must be a JSON object")
    tool_input: object = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        raise GateError("hook input has no tool_input object")
    return tool_input


def deny_reason(allowed: Sequence[str], tool_input: Mapping[str, object]) -> str | None:
    allowed_list = ", ".join(allowed)
    value = tool_input.get("subagent_type")
    if value is None:
        return f"Agent type is missing; allowed: {allowed_list}"
    if not isinstance(value, str):
        return f"Agent type must be a string; allowed: {allowed_list}"
    normalized = normalize(value)
    if not normalized:
        return f"Agent type is empty; allowed: {allowed_list}"
    if normalized in {normalize(name) for name in allowed}:
        return None
    return (
        f"Agent type '{printable(value)}' is not allowed here; allowed: {allowed_list}"
    )


def main(argv: Sequence[str], stdin: TextIO, stderr: TextIO) -> int:
    try:
        allowed = parse_allowed(argv)
        tool_input = read_tool_input(stdin)
    except GateError as error:
        print(f"Error: {error}", file=stderr)
        return 2
    reason = deny_reason(allowed, tool_input)
    if reason is None:
        return 0
    print(reason, file=stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], sys.stdin, sys.stderr))
