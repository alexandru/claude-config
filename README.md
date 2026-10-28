# My Claude Code configuration

Part of [alexandru/agents-config](https://github.com/alexandru/agents-config).

## Installation

<details>
<summary>STEP 1 — Install</summary>

### Install

```bash
curl -fsSL https://alexandru.github.io/claude-config/install.sh | bash
```

If the path isn't standard, you may need to set this in your `~/.zshrc`, `~/.bashrc` or `~/.profile`:
```sh
export CLAUDE_CONFIG_DIR=/absolute/path/to/claude-config
```

The skills are installed under `~/.agents/skills`, where Claude Code, OpenCode,
Copilot CLI, Codex, and Pi can share them.
</details>

<details>
<summary>STEP 2 — Choose a configuration preset (optional)</summary>

### Choose a configuration preset (optional)

The [claude-switch](./bin/claude-switch.py) utility is for quickly switching between multiple setting presets (e.g., multiple sets of models assigned to your agents).

```sh
# Example
~/.claude/bin/claude-switch p-fable
```

The switcher generates `settings.json` (Claude Code's user settings file) from:

- [settings.common.jsonc](./settings.common.jsonc)
- [settings.presets.jsonc](./settings.presets.jsonc)
</details>

## Defined agents

Main agents:

- [Orchestrator](./agent-templates/Orchestrator.md) (default agent): designs and implements changes; delegates evidence, research, and execution to Junior.
- [Solo](./agent-templates/Solo.md): designs and implements changes; works directly.

Sub-agents:

- [Junior](./agent-templates/Junior.md): codebase and external evidence gathering, bounded execution, mechanical work.

Start a Solo session with `claude --agent Solo`.

## Shared skills

- [alexandru/skills](https://github.com/alexandru/skills/tree/v13.3.1)
  - `code-review`: user-invoked adapter for `code-reviewing`.
  - `code-reviewing`: review changed code for bugs, structural problems, performance issues, and unintended behavior.
  - `code-refine`: behavior-preserving refinement using the `code-refinements` lenses.
  - `code-refinements`: refinement lenses of constraints, simplicity, and parametricity.
  - `external-research`: research external documentation, public repositories, and dependency artifacts with a persistent cache.
- [mattpocock/skills](https://github.com/mattpocock/skills/tree/v1.3.1)
  - `codebase-design`: deep-module design vocabulary and principles.
  - `diagnosing-bugs`: disciplined diagnosis for hard bugs and regressions.
  - `domain-modeling`: domain language and architectural decisions.
  - `grill-me`: user-invoked adapter for `grilling`.
  - `grill-with-docs`: sharpen a plan or design while creating domain documentation.
  - `grilling`: structured decision-tree interviews.
  - `handoff`: prepare context for another agent or session.
  - `implement`: implement work from a specification or set of tickets.
  - `improve-codebase-architecture`: find and work through codebase architecture improvements.
  - `setup-matt-pocock-skills`: configure a repository for the engineering skills.
  - `tdd`: test-first development guidance.
  - `teach`: teach the user a topic through a stateful learning workspace with lessons and reference documents.
  - `to-spec`: turn the current conversation into a published specification.
  - `to-tickets`: break a plan or specification into tracer-bullet tickets.
- [VirtusLab/cellar](https://github.com/VirtusLab/cellar/)
  - `cellar`: query the APIs of JVM dependencies (Scala, Java).
- [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman)
  - `caveman`: token-efficient response modes with preserved technical accuracy.

## Updating shared skills

```sh
make update-skills
```

This reinstalls the configured global skill roster from its upstream sources.
