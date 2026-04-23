# ga-bridge

[English README](./README.en.md)

`ga-bridge` 是一个可分享的 WSL 桥接方案, 用来在 Windows 上运行 GenericAgent, 并把它暴露成可复用的 Codex skill.

这个仓库打包了两部分:

- 一个面向 WSL 的通用 `ga` CLI
- 一个可安装到 `~/.codex/skills` 的 `ga-bridge` skill

安装完成后, 用户可以在 WSL 中通过稳定的命令面调用 GenericAgent, 并让自己的 agent 通过 skill 路由 GA 任务.

## 项目背景

这个项目的出发点很直接:

- GA 作为执行型 agent 的效果很好, 适合真正去做任务, 而不只是做对话式回答.
- GA 对 Windows 的适配很强, 特别适合浏览器控制, GUI 自动化, OCR, Windows 文件操作, 以及依赖真实 Windows 环境的逆向观察工作.
- 在浏览器驱动的逆向, 页面行为抓取, 运行态观察这类任务上, GA 的执行能力和 Windows 侧适配能力都很有价值.
- 但 GA 当前原生 GUI 的日常使用体验不够理想.
- 同时, Codex app 更适合作为主工作界面来组织任务, 阅读上下文, 和持续协作.

因此, 这个仓库的目标是:

- 保留 GA 在执行能力和 Windows 适配上的优势
- 通过一个稳定 CLI 从 WSL 调用 GA
- 再通过一个可安装的 skill 把 GA 接回 Codex 的任务路由体系

这样, 日常工作可以继续留在 Codex app 中完成, 但在需要 Windows 原生执行能力, 浏览器控制, 或逆向观察时, 可以直接把任务切给 GA.

## 功能

- 稳定 CLI 命令面: `doctor`, `start`, `revise-job`, `summary`, `logs`, `complete`
- skill 路由支持: browser, Windows ops, analysis, review, frontend, long-task loop
- 配置驱动路径, 不依赖单机硬编码
- 安装时自动重写 skill `owner`
- 可分享安装包, 不包含本地 jobs, sessions, tokens 或缓存状态

## 仓库内容

- `ga_cli.py`: 通用版 `ga` CLI
- `skill/ga-bridge/`: 可安装的 Codex skill bundle
- `install.sh`: WSL 一键安装脚本
- `install.ps1`: Windows PowerShell 包装脚本, 用于转调 WSL 安装
- `config.example.json`: 用户配置模板

## 前置条件

目标机器需要具备:

- WSL2
- WSL 内可用的 Python 3
- 已安装的 GenericAgent
- 可用的 Codex skills 目录, 通常为 `~/.codex/skills`

默认路径假设为:

- Windows GA 根目录: `D:\GenericAgent`
- WSL 映射路径: `/mnt/d/GenericAgent`

如果目标机器使用不同路径, 安装后修改配置文件即可.

## 快速开始

### 从 GitHub 安装

```bash
git clone https://github.com/Sophomoresty/ga-bridge-share.git
cd ga-bridge-share
chmod +x ./install.sh
./install.sh
```

### 从压缩包安装

```bash
cd ga-bridge-package
chmod +x ./install.sh
./install.sh
```

## 安装方式

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

## 安装结果

### CLI

- `~/.local/bin/ga`
- `~/.local/bin/ga_cli.py`

### Skill

- `~/.codex/skills/ga-bridge/`

### 用户配置

- `~/.config/ga/config.json`

## 验证安装

执行:

```bash
ga doctor
```

正常输出应包含:

- 检测到的 GA 运行时路径
- skills 根目录
- 可用 LLM 选项
- 支持的 profiles 和 subagent policies

## 配置

安装器会生成:

```text
~/.config/ga/config.json
```

如果目标机器和默认值不同, 修改以下字段:

| Key | 作用 |
| --- | --- |
| `ga_root_win` | Windows 侧 GenericAgent 路径 |
| `ga_root_wsl` | 对应的 WSL 路径 |
| `windows_temp_win` | Windows 临时目录 |
| `windows_temp_wsl` | 该临时目录的 WSL 映射路径 |
| `wsl_distro` | 包装器使用的 WSL 发行版名称 |
| `default_llm_no` | `ga doctor` 返回的默认 LLM slot |
| `skills_root` | skill 安装根目录, 通常为 `~/.codex/skills` |
| `ga_webui_bin` | 可选的 WebUI 包装器路径 |

## 基本用法

### 检查运行环境

```bash
ga doctor
```

### 启动任务

```bash
ga start --task "open browser visit https://example.com and screenshot"
```

### 运行只读审查

```bash
ga start --profile review --task "read-only audit this page and summarize findings"
```

### 续跑同一个 job

```bash
ga revise-job --job <job_id> --feedback "continue review and verify these points"
```

### 读取结果

```bash
ga summary --job <job_id>
```

## 使用已安装的 skill

安装完成后, skill 位于:

```text
~/.codex/skills/ga-bridge/
```

此后, agent 可以通过这个 skill 路由以下类型的任务:

- 浏览器自动化
- Windows GUI 与 OCR
- 独立复核与验收
- 从 WSL 委派到 GA 的执行型任务

## 仓库结构

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

## 说明

- `install.sh` 会把 `skill/ga-bridge/SKILL.md` 中的 `owner` 改成当前 Linux 用户名.
- 这个仓库不会分发本地 token, job, session, 或缓存状态.
- 机器相关配置统一放在 `~/.config/ga/config.json`.
