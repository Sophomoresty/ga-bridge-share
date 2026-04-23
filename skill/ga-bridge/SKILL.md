---
name: ga-bridge
description: Use when delegating tasks to GenericAgent (GA) on Windows through the local `ga` CLI. GA handles browser automation, Windows GUI, file ops, web scraping, OCR.
owner: __OWNER__
---

# GA Bridge

## Overview

- 这是 `ga` 的总入口 routed skill.
- 它负责按场景路由, 不把 `doctor`, `summary`, `logs`, `complete` 这类 CLI 功能拆成平铺 skill.
- `browser`, `windows-ops`, `frontend`, `analysis`, `review`, `long-task-loop` 都应先经过这个入口再落到具体 workflow.
- `ga` 只负责执行命令, skill 负责路由约束, 文件范围, 是否续同一会话, 是否需要 subagent.
- 历史 memory:
  - `memory/runbooks/genericagent-windows-install-d-drive-and-wsl-codex-call-path.md`
  只保留安装路径, 运行基线, 与 route 关键词.
- 完整 review loop 协议只维护在:
  - `workflows/long-task-loop.md`
  - `references/review-loop.md`

## Always Read

1. `rules/core-rules.md`
2. `workflows/default-workflow.md`
3. `references/gotchas.md`

## Common Tasks

- 默认场景路由 -> `workflows/default-workflow.md`
- 浏览器自动化或网页交互 -> `workflows/browser.md`
- Windows 侧 GUI / OCR / 文件操作 -> `workflows/windows-ops.md`
- 让 GA 直接实现前端 -> `workflows/frontend.md`
- 只读分析当前页面, 文件集, 或桌面状态 -> `workflows/analysis.md`
- 用 GA 做独立复核或验收支持 -> `workflows/review.md`
- 长任务 `summary -> revise-job -> complete` 闭环 -> `workflows/long-task-loop.md`
- 从旧 memory 命中回流到 skill 主入口 -> 继续读 `workflows/long-task-loop.md` 与 `references/review-loop.md`
- 查看更细的 review loop 说明 -> `references/review-loop.md`
- Other / unlisted task -> 先完成 `Always Read`, 再只打开最贴近场景的 workflow

## Known Gotchas

- `ga` 是执行平台, 不是 skill 集; 不要把 CLI 子命令拆成一堆功能 skill.
- `ga` 的自动 watch 路径默认输出人类可读文本; 结构化机器输出只在显式传 `--json` 时使用.
- `--profile review` 是 wrapper 侧只读审查协议. 它通过 prompt 注入强约束 read-only review, 但不是 GA 源码级硬限制.
- 公共命令面只保留:
  - `doctor`
  - `start`
  - `revise-job`
  - `summary`
  - `logs`
  - `complete`
- 同一任务默认续同一会话, 不要静默新开上下文.
- `subagent_policy=auto` 保持默认; 只在用户明确要求或验收协议要求时再用 `force`.
- 不要把整个桌面, 整个仓库, 或无关目录直接交给 GA.

## References

- `rules/core-rules.md`
- `workflows/default-workflow.md`
- `workflows/browser.md`
- `workflows/windows-ops.md`
- `workflows/frontend.md`
- `workflows/analysis.md`
- `workflows/review.md`
- `workflows/long-task-loop.md`
- `references/gotchas.md`
- `references/review-loop.md`

## Usage

```bash
# Always verify runtime first
ga doctor

# Start and watch in one command
ga start --task "YOUR_TASK_HERE"
ga start --llm-no 0 --task "YOUR_TASK_HERE"
ga start --profile review --task "READ_ONLY_AUDIT_TASK"
ga start --skill frontend-skill --skill frontend-design --task "YOUR_TASK_HERE"
ga start --skill frontend-skill --skill frontend-design --skill-mode summary --task "YOUR_TASK_HERE"
ga start --subagent-policy force --skill frontend-skill --skill frontend-design --task "YOUR_TASK_HERE"

# Read result or continue
ga summary --job <job_id>
ga logs --job <job_id>
ga complete --job <job_id>
ga revise-job --job <job_id> --feedback "FOLLOW_UP_HERE"

```

## How it works

1. `ga` is the stable entrypoint from WSL.
2. `ga start` calls the new GA at `D:\GenericAgent`.
3. The CLI manages local job state under `~/.local/share/ga/jobs/`.
4. `ga webui ...` proxies the existing WebUI wrapper.
5. Default model slot is `llm_no=1` when the name contains `glm-5.1` or `glm`.
6. Override with `--llm-no <n>` when you need another configured API.
7. Default `subagent_policy` is `auto`.
8. `ga start` 和 `ga revise-job` 的自动 watch 默认输出文本事件流; 需要机器流时显式传 `--json`.
9. `--profile review` injects a read-only review contract at the wrapper layer.
10. `ga start` can inject local `SKILL.md` files with `--skill`.
11. `--skill-mode summary` is the default. It stages full `SKILL.md` files to a readable path and injects only concise summaries plus staged paths into the prompt.
12. `ga start` is the only public launch command and it immediately opens live watch.
13. `ga summary --job <job_id>` is the only public snapshot command.
14. `ga revise-job --job <job_id>` is the only public continuation command.
15. `ga session-*`, `watch`, `status`, and `revise` remain internal backend control and are not part of the public skill surface.

## Default delegation policy

- `subagent_policy=auto` is the default.
- `auto` injects this runtime rule into every GA task:
  - Simple single-step work: main agent does it directly.
  - Start subagent when any condition is true:
    - read many files or a large codebase
    - explore multiple directions in parallel
    - require independent verification
    - produce long exploratory output that would pollute main context
- Main agent stays responsible for planning, synthesis, and final write-back.
- Use `--subagent-policy off` only when you explicitly want no injected delegation rule.
- Use `--subagent-policy force` when the task must visibly use subagent workflow.

## Skill injection

- Use `--skill <name>` to resolve `~/.codex/skills/<name>/SKILL.md`.
- Use `--skill /abs/path/to/SKILL.md` to resolve an explicit skill file.
- Repeat `--skill` to inject multiple skills.
- Default mode is `--skill-mode summary`.
- `summary` behavior:
  - CLI stages full `SKILL.md` files to a readable path.
  - Prompt only injects concise summaries plus staged paths.
  - GA must `file_read` staged skill files before execution.
- `full` behavior:
  - Prompt embeds full `SKILL.md` content.
  - Use only when summary mode is insufficient.
- `--skill` is not Codex-native runtime triggering. It is CLI-level staging plus prompt protocol.
- Frontend tasks should normally inject:
  - `--skill frontend-skill`
  - `--skill frontend-design`

## Default continuation and subagent behavior

- Same artifact, same task family, same review loop:
  - Prefer the same GA conversation.
  - Use `ga revise-job --job <id>`.
  - Do not silently restart from a new context.
- Keep `subagent_policy=auto` as default.
- `auto` should visibly use subagent when any condition is true:
  - read many files or a large codebase
  - explore multiple directions in parallel
  - require independent verification
  - long exploratory output would pollute main context
  - frontend or design work needs exploration and verification split across multiple rounds
- Do not use subagent by default for:
  - single-file, single-step, explicit edits
  - short mechanical tasks
  - tasks with no independent verification need

## When to use GA

- **Browser automation**: web scraping, form filling, screenshot capture
- **Windows GUI**: window manipulation, click/type simulation
- **File operations on Windows side**: `D:\*`, `C:\Users\*`
- **OCR / screen reading**: reading screen content, comparing UI states
- **Web searches** with browser control
- **Cross-platform tasks**: GA can call back into WSL if needed

## Examples

```bash
ga start --task "open browser visit https://example.com and screenshot"

ga start --llm-no 0 --task "open browser visit https://example.com and screenshot"

ga start --skill frontend-skill --skill frontend-design --task "build a Claude-style personal homepage and write files on the Windows desktop"

ga --text summary --job <job_id>
ga revise-job --job <job_id> --feedback "按 review 再修一轮"

ga start --task "read first 50 lines of D:\some_project\README.md"
ga summary --job <job_id>
ga complete --job <job_id>

ga start --subagent-policy force --skill frontend-skill --skill frontend-design --task "redesign the homepage, use subagent for exploration and independent verification, then overwrite the desktop files"

ga webui status
```

## Notes

- `ga doctor` should be the first command in a fresh thread.
- `ga doctor` shows `llm_options` and `default_llm_no`.
- `ga start` is the only public launch command and it must immediately watch.
- `ga revise-job` is the only public continuation path for the same task in the same GA conversation.
- `ga session-*`, `watch`, `status`, and `revise` are internal backend control, not part of the public skill surface.
- Current default slot is `glm-5.1` if that name is present. Use `--llm-no 0` to switch back to `gpt-5.4`.
- Default `subagent_policy` is `auto`. Use `force` for tasks that must visibly delegate, or `off` when prompt-level delegation rules would get in the way.
- Default `skill_mode` is `summary`. This keeps prompts shorter and still forces GA to read staged skill files.
- `--skill` stages or injects local skill documents into the GA task contract. This is how you make GA follow `frontend-skill`, `frontend-design`, or other local skills.
- `ga revise-job --job <id> --feedback "..."` is the default review-loop continuation command from a prior job.
- `ga --help` 默认只暴露主命令面:
  - `doctor`
  - `start`
  - `revise-job`
  - `complete`
  - `summary`
  - `logs`
- 兼容命令默认隐藏:
  - `wait`
  - `list`
  - `stop`
  - `webui`
- Prefer `ga summary --job <job_id>` for the compact snapshot that matches ccglm usage habits.
- First launch of any resumable GA task must use `ga start ...` and consume its live watch output in the same command.
- Do not use `ga start --detach`, do not expose `ga watch`, and do not split launch from live follow.
- Use `ga logs --job <job_id>` to read structured recent events after the run.
- Long-running GA jobs should not rely on short wall-clock shell timeouts. The bridge now disables the `win-shell-utf8` hard timeout for GA worker launches.
- Current Windows runtime root is `D:\GenericAgent`.
- GA has its own memory system at `D:\GenericAgent\memory\`.
