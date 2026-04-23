# ga-bridge

Shareable WSL bridge for running GenericAgent on Windows and exposing it as a reusable Codex skill.

This repository packages two things together:

- a portable `ga` CLI for WSL
- a `ga-bridge` skill that agents can install into `~/.codex/skills`

After installation, another user can run GenericAgent from WSL with a stable command surface and let their agent route GA tasks through the installed skill.

## What this repo includes

- `ga_cli.py`: portable `ga` CLI
- `skill/ga-bridge/`: installable Codex skill bundle
- `install.sh`: one-step installer for WSL
- `install.ps1`: Windows PowerShell wrapper that calls the WSL installer
- `config.example.json`: user config template

## Features

- Stable CLI surface: `doctor`, `start`, `revise-job`, `summary`, `logs`, `complete`
- Skill-based routing for browser tasks, Windows ops, analysis, review, frontend, and long-task loops
- Config-driven paths instead of hardcoded local machine values
- Automatic skill `owner` rewrite during install
- Works as a shareable bundle without shipping local jobs, sessions, or tokens

## Prerequisites

Before installing, the target machine should have:

- WSL2
- Python 3 available inside WSL
- GenericAgent installed on Windows
- a Codex-compatible skills directory at `~/.codex/skills`

Default paths assume:

- Windows GA root: `D:\GenericAgent`
- WSL GA root: `/mnt/d/GenericAgent`

If the target machine uses different paths, update the generated config after install.

## Quick start

### Clone from GitHub

```bash
git clone https://github.com/Sophomoresty/ga-bridge-share.git
cd ga-bridge-share
chmod +x ./install.sh
./install.sh
```

### Or install from an extracted archive

```bash
cd ga-bridge-package
chmod +x ./install.sh
./install.sh
```

## Installation

### Option 1: WSL

```bash
chmod +x ./install.sh
./install.sh
```

### Option 2: Windows PowerShell

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

## What gets installed

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

Successful output should show:

- detected GA runtime paths
- detected skill root
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
| `ga_root_wsl` | WSL path to the same GenericAgent install |
| `windows_temp_win` | Windows temp directory |
| `windows_temp_wsl` | WSL view of that Windows temp directory |
| `wsl_distro` | WSL distro name used by the wrapper |
| `default_llm_no` | Default LLM slot returned by `ga doctor` |
| `skills_root` | Skill install root, normally `~/.codex/skills` |
| `ga_webui_bin` | Optional WebUI wrapper path |

## Basic usage

### Check runtime

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

An agent can then route tasks through the installed skill, for example:

- use GA for browser automation
- use GA for Windows GUI and OCR
- use GA for independent review
- use GA as a delegated execution bridge from WSL

## Repository layout

```text
.
├── README.md
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

- `install.sh` rewrites `skill/ga-bridge/SKILL.md` so `owner` matches the current Linux user.
- This repository does not ship local tokens, jobs, sessions, or cache state.
- The repo is meant to be shareable; machine-specific values live in `~/.config/ga/config.json`.
