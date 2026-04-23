# GA Review Loop

## 触发条件

- 已经决定把任务委派给 GenericAgent.
- 任务需要:
  - 后台 job
  - 状态跟踪
  - 人工审查
  - 修订闭环

## 操作步骤

1. 首轮:

```bash
ga start --task "..."
```

2. 读取状态:

```bash
ga summary --job <job_id>
```

- 首次启动入口只能是 `ga start ...`.
- 启动与 live follow 不拆成两条公开命令.

3. 继续同一上下文:

```bash
ga revise-job --job <job_id> --feedback "..."
```

4. 完成:

```bash
ga complete --job <job_id>
```

## 验证方法

- `ga doctor` 默认返回 JSON.
- `ga --text doctor` 返回人类文本.
- `ga start` 返回 `job_id`, `session_id`, `continue_hint`, 并必须同步输出 live watch.
- `ga summary` 返回 `state`, `session_id`, `result_text`, `continue_hint`.
- `ga revise-job` 会续同一 `session_id`.
- `ga complete` 会显式标记人工验收完成.
