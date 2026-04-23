# Gotchas

- `ga` 默认 JSON, 不是默认文本.
- `ga logs` 默认返回文本渲染, 这是特例.
- `session-*` 是高级命令, 不是默认 workflow 入口.
- 同一任务默认续同一会话, 不要静默新开.
- `subagent_policy=auto` 是默认协议, 不要无条件改成 `force`.
- 前端任务默认显式注入:
  - `frontend-skill`
  - `frontend-design`
