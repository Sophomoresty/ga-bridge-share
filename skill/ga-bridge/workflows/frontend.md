# Frontend Workflow

## 触发条件

- 需要让 GA 直接实现或修改前端文件.

## 步骤

1. 默认显式注入:

```bash
--skill frontend-skill --skill frontend-design
```

2. 首轮默认走长任务闭环:

```bash
ga start \
  --skill frontend-skill \
  --skill frontend-design \
  --task "直接完成这个前端任务, 写完后自检并返回结果"
```

3. 读取状态:

```bash
ga summary --job <job_id>
```

- 首次启动已经自带 watch.
- 不要拆出第二个公开跟随命令.

4. 若结果不达标, 继续同一会话:

```bash
ga revise-job --job <job_id> --feedback "这里有 3 个问题: 1. ... 2. ... 3. ... 请直接修改并重新验证."
```

5. 若人工验收通过:

```bash
ga complete --job <job_id>
```

## 默认 subagent 规则

- 保持 `subagent_policy=auto`.
- 前端多轮任务若需要:
  - 探索现有页面
  - 读取较多文件
  - 独立验证视觉一致性
  - 长输出避免污染主上下文
  则应真实使用 subagent.

## 边界

- 同一网页的后续轮次默认续同一会话.
- 不要把单一网页迭代拆成无关新上下文.
