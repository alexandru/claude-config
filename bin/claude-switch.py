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
writes settings.json. Presets inherit from other presets via "extends";
fragments marked "common": true stay hidden from direct selection.
"""

import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, TypeAlias

import json5
import typer

COMMON_FILE = "settings.common.jsonc"
PRESETS_FILE = "settings.presets.jsonc"
OUTPUT_FILE = "settings.json"
SUMMARY_KEYS: tuple[str, ...] = ("agent", "model", "effortLevel")

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


def write_json(path: Path, value: Json) -> None:
    """Pretty-printed JSON with a trailing newline."""
    content = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as error:
        raise ConfigError(f"failed to write {path}: {error}") from None


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
    print("  from settings.presets.jsonc to generate settings.json")


def format_summary(config: JsonObject) -> str | None:
    """The main session's agent, model, and effort, or None when none are set."""
    rows = [
        (key, value)
        for key in SUMMARY_KEYS
        if isinstance(value := config.get(key), str)
    ]
    if not rows:
        return None
    width = max(len(key) for key, _ in rows)
    body = "\n".join(f"  {key:<{width}} │ {value}" for key, value in rows)
    return f"\n{body}\n\n"


def switch(preset: str | None) -> None:
    """Generates settings.json from the selected preset, or prints help."""
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
    common = read_jsonc_object(CONFIG_DIR / COMMON_FILE)
    config = merge_objects(common, resolve_preset(presets, preset, ()))
    write_json(CONFIG_DIR / OUTPUT_FILE, config)
    print(f"✓ Successfully switched to preset: {preset}")
    print(f"✓ Generated {OUTPUT_FILE}")
    summary = format_summary(config)
    if summary is not None:
        print(summary, end="")


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
