# Review Workflow

## 触发条件

- 任务以独立复核, 验收支持, 或二次确认执行结果为主:
  - 检查网页是否真被改到目标状态
  - 核对文件是否按要求生成
  - 独立验证浏览器行为
  - 复核桌面 GUI 操作结果

## 步骤

1. 明确验收标准:
   - 看哪些文件
   - 看哪些页面
   - 看哪些交互
   - 判定通过的条件
2. 复核任务首轮一律启动并 watch:

```bash
ga start --task "..."
ga summary --job <job_id>
```

- 首次启动只用 `ga start ...`.
- 不要拆出第二个公开跟随命令.

3. 若首轮发现问题, 在同一 review loop 继续:

```bash
ga revise-job --job <job_id> --feedback "继续复核这些点: 1. ... 2. ... 3. ..."
```

4. 若主线程确认通过:

```bash
ga complete --job <job_id>
```

## 边界

- `review` 负责独立验证, 不负责把实现和验收混成一轮.
- 若任务本质是前端落地, 先走 `frontend`; 验收时再走 `review`.
