# Default Workflow

## 目的

- 先判定场景, 再决定是否走一次性任务或长任务闭环.

## 步骤

1. 先判断任务属于哪一类:
   - `browser`
   - `windows-ops`
   - `frontend`
   - `analysis`
   - `review`
   - `long-task-loop`
2. 整理本轮最小必要文件集:
   - 一律绝对路径
   - 不把整个仓库或整个桌面直接交给 GA
3. 首次启动一律使用 `ga start`:
   - 启动必须直接消费同一条命令的 live watch 输出
   - 不要拆出第二个公开跟随命令
4. 若首轮结果不达标:
   - 后台 job 闭环继续 -> `ga revise-job --job ...`
5. 若长任务通过人工验收:
   - `ga complete --job ...`

## 跳转

- `browser` -> `browser.md`
- `windows-ops` -> `windows-ops.md`
- `frontend` -> `frontend.md`
- `analysis` -> `analysis.md`
- `review` -> `review.md`
- `long-task-loop` -> `long-task-loop.md`
