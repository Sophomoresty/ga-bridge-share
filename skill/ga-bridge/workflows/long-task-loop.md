# Long Task Loop

## 触发条件

- 任务需要后台 job, 跟踪状态, 人工审查, 修订闭环.

## 步骤

1. 首轮:

```bash
ga start --task "..."
```

2. 读取状态:

```bash
ga summary --job <job_id>
```

- 首轮进入 loop 时, 启动入口只能是 `ga start ...`.
- 公开命令面不再单独暴露 `ga watch`.

3. 若结果不达标:

```bash
ga revise-job --job <job_id> --feedback "..."
```

4. 若人工验收通过:

```bash
ga complete --job <job_id>
```

## 边界

- `long-task-loop` 是闭环协议, 不是新的业务场景.
- 业务场景仍先归入 `browser`, `windows-ops`, `frontend`, `analysis`, `review`.
