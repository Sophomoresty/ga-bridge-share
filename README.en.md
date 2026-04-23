# ga-bridge

[中文说明](./README.md)

`ga-bridge` is a shareable WSL bridge for running GenericAgent on Windows and exposing it as a reusable Codex skill.

This repository packages two things together:

- a portable `ga` CLI for WSL
- a `ga-bridge` skill that can be installed into `~/.codex/skills`

After installation, users can call GenericAgent from WSL through a stable command surface and let their agent route GA tasks through the installed skill.

## Background

This project exists for a straightforward reason:

- GA performs well as an execution-oriented agent and is useful for real task execution, not just conversational replies.
- GA is especially well adapted to Windows, which makes it a strong fit for browser control, GUI automation, OCR, Windows-side file operations, and reverse-oriented runtime inspection that depends on a real Windows environment.
- For browser-driven reverse work, page behavior capture, and runtime observation, GA's execution model and Windows integration are both valuable.
- However, the current native GA GUI is not ideal as a primary daily interface.
- At the same time, the Codex app works better as the main workspace for organizing tasks, reading context, and continuing work across sessions.

The goal of this repository is therefore:

- keep GA's strength in execution and Windows integration
- expose GA through a stable CLI from WSL
- connect GA back into Codex through an installable skill

This makes it possible to stay in the Codex app for normal work, while still handing off tasks to GA whenever Windows-native execution, browser control, or reverse-oriented inspection is required.

## Features

- Stable CLI command surface: `doctor`, `start`, `revise-job`, `summary`, `logs`, `complete`
- Skill-based routing for browser, Windows ops, analysis, review, frontend, and long-task workflows
- Config-driven paths instead of machine-specific hardcoding
- Automatic `owner` rewrite during installation
- Shareable bundle without local jobs, sessions, tokens, or cache state

## Repository contents

- `ga_cli.py`: portable `ga` CLI
- `skill/ga-bridge/`: installable Codex skill bundle
- `install.sh`: one-step WSL installer
- `install.ps1`: Windows PowerShell wrapper that invokes the WSL installer
- `config.example.json`: user configuration template

## Prerequisites

The target machine should have:

- WSL2
- Python 3 available inside WSL
- GenericAgent already installed
- a Codex-compatible skills directory, usually `~/.codex/skills`

Default path assumptions:

- Windows GA root: `D:\GenericAgent`
- WSL mapping: `/mnt/d/GenericAgent`

If the target machine uses different paths, adjust the generated config after installation.

## Quick start

### Install from GitHub

```bash
git clone https://github.com/Sophomoresty/ga-bridge-share.git
cd ga-bridge-share
chmod +x ./install.sh
./install.sh
```

### Install from an extracted archive

```bash
cd ga-bridge-package
chmod +x ./install.sh
./install.sh
```

## Installation

### WSL

```bash
chmod +x ./install.sh
./install.sh
```

### Windows PowerShell

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

## Installed outputs

### CLI

- `~/.local/bin/ga`
- `~/.local/bin/ga_cli.py`

### Skill

- `~/.codex/skills/ga-bridge/`

### User config

- `~/.config/ga/config.json`

## Verify the installation

Run:

```bash
ga doctor
```

Expected output should include:

- detected GA runtime paths
- detected skills root
- available LLM options
- supported profiles and subagent policies

## Configuration

The installer creates:

```text
~/.config/ga/config.json
```

Adjust these fields if the target machine differs from the defaults:

| Key | Purpose |
| --- | --- |
| `ga_root_win` | Windows path to GenericAgent |
| `ga_root_wsl` | Matching WSL path |
| `windows_temp_win` | Windows temp directory |
| `windows_temp_wsl` | WSL mapping of that temp directory |
| `wsl_distro` | WSL distro name used by the wrapper |
| `default_llm_no` | Default LLM slot reported by `ga doctor` |
| `skills_root` | Skill install root, usually `~/.codex/skills` |
| `ga_webui_bin` | Optional WebUI wrapper path |

## Basic usage

### Check the runtime

```bash
ga doctor
```

### Start a task

```bash
ga start --task "open browser visit https://example.com and screenshot"
```

### Run a read-only review

```bash
ga start --profile review --task "read-only audit this page and summarize findings"
```

### Continue the same job

```bash
ga revise-job --job <job_id> --feedback "continue review and verify these points"
```

### Read the result

```bash
ga summary --job <job_id>
```

## Using the installed skill

After installation, the skill is available at:

```text
~/.codex/skills/ga-bridge/
```

An agent can then route tasks through this skill for:

- browser automation
- Windows GUI and OCR
- independent review and verification
- execution-oriented delegation from WSL to GA

## Repository layout

```text
.
├── README.md
├── README.en.md
├── config.example.json
├── ga_cli.py
├── install.ps1
├── install.sh
└── skill/
    └── ga-bridge/
        ├── SKILL.md
        ├── references/
        ├── rules/
        └── workflows/
```

## Notes

- `install.sh` rewrites the `owner` field in `skill/ga-bridge/SKILL.md` to match the current Linux user.
- This repository does not ship local tokens, jobs, sessions, or cache state.
- Machine-specific settings live in `~/.config/ga/config.json`.
