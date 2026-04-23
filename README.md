# ga-bridge

一个可分享的 WSL 桥接方案, 用来在 Windows 上运行 GenericAgent, 并把它暴露成可复用的 Codex skill.

A shareable WSL bridge for running GenericAgent on Windows and exposing it as a reusable Codex skill.

这个仓库打包了两部分:

- 一个面向 WSL 的通用 `ga` CLI
- 一个可安装到 `~/.codex/skills` 的 `ga-bridge` skill

This repository packages two things together:

- a portable `ga` CLI for WSL
- a `ga-bridge` skill that agents can install into `~/.codex/skills`

安装后, 其他用户就可以在 WSL 里用稳定的命令面调用 GenericAgent, 并让自己的 agent 通过这个 skill 路由 GA 任务.

After installation, another user can run GenericAgent from WSL with a stable command surface and let their agent route GA tasks through the installed skill.

## 为什么做这个

我做这个仓库, 不是为了替代 GA, 而是为了把 GA 接到一个更顺手的工作流里.

- GA 本身作为执行型 agent 的效果很好, 特别适合真正去做事, 而不只是聊天.
- GA 对 Windows 的适配非常强, 在浏览器控制, GUI 自动化, OCR, Windows 文件操作这类任务上明显比普通 WSL agent 更合适.
- 对我自己来说, 这种能力尤其适合控制浏览器做逆向, 抓页面行为, 观察运行态, 或处理必须依赖 Windows 环境的工作.
- 但 GA 目前原生 GUI 不够好用, 日常切换和管理成本偏高.
- 同时我自己更习惯用 Codex app 作为主工作界面.

所以这个仓库的目标很直接:

- 保留 GA 在执行和 Windows 适配上的优势.
- 用一个稳定的 CLI 把它从 WSL 调起来.
- 再用一个 skill 把它接进 Codex 的任务路由里.

这样做完以后, 我仍然可以在自己熟悉的 Codex app 里工作, 但在需要 Windows 执行能力, 浏览器操作, 或逆向观察时, 可以直接把任务切给 GA.

## Why this exists

This repo is not meant to replace GA. It is meant to make GA fit a workflow that is easier to use every day.

- GA is already very good as an execution-oriented agent.
- Its Windows integration is especially strong for browser control, GUI automation, OCR, and Windows-side file operations.
- That makes it a strong fit for tasks like browser-driven reverse work, runtime inspection, and other jobs that need real Windows behavior.
- However, the native GA GUI is still hard to use as a primary daily interface.
- At the same time, I personally prefer using the Codex app as my main workspace.

So the goal of this repository is simple:

- keep GA's strength in execution and Windows integration
- expose it through a stable CLI from WSL
- connect it back into Codex through an installable skill

That way I can stay inside the Codex app for normal work, and still hand off tasks to GA whenever I need Windows-native execution, browser control, or reverse-oriented inspection.

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
