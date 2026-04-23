# Core Rules

- `ga` 是执行平台, 不是 skill 集.
- skill 负责路由场景, 文件范围, 是否长任务, 是否续同一会话, 是否需要 subagent.
- CLI 负责稳定 JSON 协议, 状态机, 观测, 续跑, 完成标记.
- 默认输出按 agent 协议处理:
  - 普通命令直接 `ga ...`
  - 需要人类可读文本时才用 `ga --text ...`
- 默认优先继续同一会话:
  - 同一产物
  - 同一 review loop
  - 同一网页 / 同一 bug / 同一文件集
- 不要为了命令好看而偏离协议.
- 不要把 `doctor`, `summary`, `logs`, `complete` 这些 CLI 功能拆成多个 skill.
- 长任务闭环统一走:
  - `start`
  - `summary`
  - `revise-job`
  - `complete`
- 不把 `watch`, `status`, `revise`, `session-*` 暴露给 skill 默认路径.
