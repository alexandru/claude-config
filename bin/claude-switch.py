#!/usr/bin/env -S pipx run

# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "json5==0.17.3",
#   "typer==0.27.2",
# ]
# ///

"""Generate Claude Code's settings file from a preset.

Merges settings.common.jsonc with a preset from settings.presets.jsonc and
writes settings.json, then generates agents/<name>.md from every
agent-templates/<name>.md. A preset's "agents" entry sets an agent's model and
effort in the generated frontmatter, because Claude Code reads per-agent models
only from agent definitions. Presets inherit from other presets via "extends";
fragments marked "common": true stay hidden from direct selection.
"""

import json
import re
import sys
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, TypeAlias

import json5
import typer

COMMON_FILE = "settings.common.jsonc"
PRESETS_FILE = "settings.presets.jsonc"
OUTPUT_FILE = "settings.json"
AGENTS_DIR = "agents"
TEMPLATES_DIR = "agent-templates"
AGENTS_KEY = "agents"
AGENT_KEYS: tuple[str, ...] = ("model", "effort")
# Plain YAML scalars only, so a preset value cannot alter the frontmatter's structure.
AGENT_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\[\]-]*")
FRONTMATTER_START = "---\n"
FRONTMATTER_END = "\n---\n"

CONFIG_DIR = Path(__file__).resolve().parent.parent

Json: TypeAlias = dict[str, "Json"] | list["Json"] | str | int | float | bool | None
JsonObject: TypeAlias = dict[str, Json]


class ConfigError(Exception):
    """A configuration problem, reported as one clear message, not a traceback."""


def merge_objects(source: JsonObject, target: JsonObject) -> JsonObject:
    """Target values win, shared objects merge recursively, a target null keeps
    the source object, and the result keeps the source's key order, then keys
    new in the target.
    """
    merged = dict(source)
    for key, target_value in target.items():
        source_value = merged.get(key)
        if isinstance(source_value, dict) and target_value is None:
            merged[key] = source_value
        elif isinstance(source_value, dict) and isinstance(target_value, dict):
            merged[key] = merge_objects(source_value, target_value)
        else:
            merged[key] = target_value
    return merged


def extends_names(preset: JsonObject, name: str) -> list[str]:
    extends = preset.get("extends")
    if extends is None:
        return []
    invalid = (
        f"Preset '{name}' has invalid 'extends'; expected an array of preset names."
    )
    if not isinstance(extends, list):
        raise ConfigError(invalid)
    names: list[str] = []
    for item in extends:
        if not isinstance(item, str):
            raise ConfigError(invalid)
        names.append(item)
    return names


def strip_preset_metadata(preset: JsonObject) -> JsonObject:
    """The preset as Claude Code settings, without the switcher's own keys."""
    return {
        key: value for key, value in preset.items() if key not in ("common", "extends")
    }


def resolve_preset(
    presets: JsonObject, name: str, ancestors: tuple[str, ...]
) -> JsonObject:
    """The preset with every "extends" parent merged in.

    Later parents override earlier ones, and the preset overrides its parents.
    `ancestors` is the resolution path, so inheritance cycles can be reported.
    """
    preset = presets.get(name)
    if preset is None:
        raise ConfigError(f"Preset '{name}' not found in {PRESETS_FILE}")
    if not isinstance(preset, dict):
        raise ConfigError(f"Preset '{name}' must be a JSON object")
    if name in ancestors:
        cycle = ancestors[ancestors.index(name) :] + (name,)
        raise ConfigError("Circular preset inheritance detected: " + " -> ".join(cycle))

    inherited: JsonObject = {}
    for parent in extends_names(preset, name):
        inherited = merge_objects(
            inherited, resolve_preset(presets, parent, (*ancestors, name))
        )
    return merge_objects(inherited, strip_preset_metadata(preset))


def read_jsonc_object(path: Path) -> JsonObject:
    """The root object of a JSONC file; configuration files must be objects."""
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"configuration file missing: {path}") from None
    except OSError as error:
        raise ConfigError(f"failed to read {path}: {error}") from None
    try:
        value = json5.loads(content)
    except ValueError as error:
        raise ConfigError(f"failed to parse {path}: {error}") from None
    if not isinstance(value, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return value


def write_text(path: Path, content: str) -> None:
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"failed to write {path}: {error}") from None


def format_json(value: Json) -> str:
    """Pretty-printed JSON with a trailing newline."""
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


AgentSettings: TypeAlias = dict[str, str]


def agent_settings(preset: JsonObject) -> dict[str, AgentSettings]:
    """The preset's per-agent model settings, keyed by agent name."""
    agents = preset.get(AGENTS_KEY, {})
    if not isinstance(agents, dict):
        raise ConfigError(f"'{AGENTS_KEY}' must map agent names to model settings")
    result: dict[str, AgentSettings] = {}
    for agent, settings in agents.items():
        if not isinstance(settings, dict):
            raise ConfigError(f"Agent '{agent}' settings must be an object")
        values: AgentSettings = {}
        for key, value in settings.items():
            if key not in AGENT_KEYS:
                raise ConfigError(
                    f"Agent '{agent}' has unsupported key '{key}'; "
                    f"expected one of: {', '.join(AGENT_KEYS)}"
                )
            if not isinstance(value, str) or AGENT_VALUE.fullmatch(value) is None:
                raise ConfigError(f"Agent '{agent}' has invalid '{key}': {value!r}")
            values[key] = value
        result[agent] = values
    return result


def is_agent_setting(line: str) -> bool:
    return any(line.startswith(f"{key}:") for key in AGENT_KEYS)


def update_frontmatter(source: str, settings: AgentSettings) -> str:
    """The agent template with new model settings after its `description:`
    line and every other line untouched. Keys the preset omits are removed, so
    those settings fall back to Claude Code's defaults.
    """
    end = source.find(FRONTMATTER_END, len(FRONTMATTER_START) - 1)
    if not source.startswith(FRONTMATTER_START) or end < 0:
        raise ConfigError("missing YAML frontmatter")
    lines = source[len(FRONTMATTER_START) : end + 1].splitlines(keepends=True)
    kept = [line for line in lines if not is_agent_setting(line)]
    descriptions = [i for i, line in enumerate(kept) if line.startswith("description:")]
    if len(descriptions) != 1:
        raise ConfigError("frontmatter must have exactly one 'description:' line")
    position = descriptions[0] + 1
    added = [f"{key}: {settings[key]}\n" for key in AGENT_KEYS if key in settings]
    header = "".join(kept[:position] + added + kept[position:])
    return FRONTMATTER_START + header + source[end + 1 :]


@dataclass(frozen=True)
class FileWrite:
    path: Path
    contents: str


def agent_writes(agents: Mapping[str, AgentSettings]) -> list[FileWrite]:
    """Every generated agent definition, computed before any file is changed.

    Each template is generated on every switch, so an agent the preset does not
    configure gets its template unchanged rather than an earlier preset's models.
    """
    templates_dir = CONFIG_DIR / TEMPLATES_DIR
    try:
        templates = {path.stem: path for path in sorted(templates_dir.glob("*.md"))}
    except OSError as error:
        raise ConfigError(f"failed to list {templates_dir}: {error}") from None
    unknown = [agent for agent in agents if agent not in templates]
    if unknown:
        raise ConfigError(
            f"Preset configures agents without a template in {TEMPLATES_DIR}/: "
            + ", ".join(unknown)
        )
    writes: list[FileWrite] = []
    for agent, template in templates.items():
        try:
            source = template.read_text(encoding="utf-8")
        except OSError as error:
            raise ConfigError(f"failed to read {template}: {error}") from None
        try:
            contents = update_frontmatter(source, agents.get(agent, {}))
        except ConfigError as error:
            raise ConfigError(f"failed to generate from {template}: {error}") from None
        writes.append(
            FileWrite(path=CONFIG_DIR / AGENTS_DIR / f"{agent}.md", contents=contents)
        )
    return writes


def visible_preset_names(presets: JsonObject) -> Iterator[str]:
    """Presets selectable on the command line; "common": true fragments only get inherited."""
    for name, preset in presets.items():
        if not (isinstance(preset, dict) and preset.get("common") is True):
            yield name


def format_presets(presets: JsonObject) -> str:
    return "\n".join(f"  - {name}" for name in visible_preset_names(presets))


def print_help(presets: JsonObject) -> None:
    print("Claude Code Configuration Preset Switcher\n")
    print("Usage: claude-switch <preset-name>\n")
    print(f"Available presets:\n{format_presets(presets)}\n")
    print("Description:")
    print("  Merges settings.common.jsonc with the selected preset")
    print("  from settings.presets.jsonc to generate settings.json,")
    print("  and generates agents/*.md from agent-templates/*.md")


@dataclass(frozen=True)
class AgentRow:
    name: str
    model: str
    effort: str


def setting(values: Mapping[str, Json], key: str) -> str | None:
    value = values.get(key)
    return value if isinstance(value, str) else None


def agent_rows(
    config: JsonObject, agents: Mapping[str, AgentSettings]
) -> list[AgentRow]:
    """The main agent, which falls back to the settings model and effort, then
    every agent the preset configures.
    """
    main = setting(config, "agent")
    main_settings = agents.get(main, {}) if main is not None else {}
    rows = [
        AgentRow(
            name=main or "(main)",
            model=main_settings.get("model") or setting(config, "model") or "-",
            effort=main_settings.get("effort") or setting(config, "effortLevel") or "-",
        )
    ]
    for name, settings in agents.items():
        if name != main:
            rows.append(
                AgentRow(
                    name=name,
                    model=settings.get("model", "-"),
                    effort=settings.get("effort", "-"),
                )
            )
    return rows


def column_width(header: str, values: Iterator[str]) -> int:
    return max([len(header), *(len(value) for value in values)])


def format_agent_table(
    config: JsonObject, agents: Mapping[str, AgentSettings]
) -> str | None:
    """Each agent's model and effort, or None when nothing sets them."""
    rows = agent_rows(config, agents)
    if not agents and rows[0].model == "-" and rows[0].effort == "-":
        return None

    name_width = column_width("Agent", (row.name for row in rows))
    model_width = column_width("Model", (row.model for row in rows))
    effort_width = column_width("Effort", (row.effort for row in rows))

    header = f"  {'Agent':<{name_width}} │ {'Model':<{model_width}} │ Effort"
    separator = f"  {'─' * name_width}─┼─{'─' * model_width}─┼─{'─' * effort_width}"
    body = "\n".join(
        f"  {row.name:<{name_width}} │ {row.model:<{model_width}} │ {row.effort}"
        for row in rows
    )
    return f"\n{header}\n{separator}\n{body}\n\n"


def switch(preset: str | None) -> None:
    """Generates settings.json and the agent definitions from the selected
    preset, or prints help.
    """
    presets = read_jsonc_object(CONFIG_DIR / PRESETS_FILE)
    if preset is None:
        print_help(presets)
        return
    if preset not in presets:
        raise ConfigError(
            f"Preset '{preset}' not found in {PRESETS_FILE}\n\n"
            f"Available presets:\n{format_presets(presets)}\n\n"
            "Usage: claude-switch <preset-name>"
        )
    resolved = resolve_preset(presets, preset, ())
    agents = agent_settings(resolved)
    settings = {key: value for key, value in resolved.items() if key != AGENTS_KEY}
    common = read_jsonc_object(CONFIG_DIR / COMMON_FILE)
    config = merge_objects(common, settings)
    writes = [
        FileWrite(path=CONFIG_DIR / OUTPUT_FILE, contents=format_json(config)),
        *agent_writes(agents),
    ]
    agents_dir = CONFIG_DIR / AGENTS_DIR
    try:
        agents_dir.mkdir(exist_ok=True)
    except OSError as error:
        raise ConfigError(f"failed to create {agents_dir}: {error}") from None
    for write in writes:
        write_text(write.path, write.contents)
    print(f"✓ Successfully switched to preset: {preset}")
    for write in writes:
        print(f"✓ Generated {write.path.relative_to(CONFIG_DIR)}")
    table = format_agent_table(config, agents)
    if table is not None:
        print(table, end="")


app = typer.Typer(add_completion=False, context_settings={"help_option_names": []})


@app.command()
def main(
    preset: Annotated[
        str | None, typer.Argument(metavar="PRESET", help="Preset to apply")
    ] = None,
    show_help: Annotated[bool, typer.Option("--help", "-h", help="Print help")] = False,
) -> None:
    """Generate a Claude Code configuration from a preset."""
    try:
        switch(None if show_help else preset)
    except ConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise typer.Exit(code=1) from None


if __name__ == "__main__":
    app()
