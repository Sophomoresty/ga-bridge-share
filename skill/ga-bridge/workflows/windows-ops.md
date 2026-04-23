# Windows Ops Workflow

## 触发条件

- 任务以 Windows 侧 GUI, 文件, OCR, 桌面状态为主:
  - 读写 `D:\` / `C:\Users\...`
  - 桌面窗口操作
  - 屏幕阅读
  - OCR

## 步骤

1. 首轮一律启动并 watch:

```bash
ga start --task "在 Windows 上 ..."
ga summary --job <job_id>
ga revise-job --job <job_id> --feedback "继续 ..."
ga complete --job <job_id>
```

- 首次启动已经包含 watch.
- 不要拆出第二个公开跟随命令.

## 边界

- 文件路径保持绝对路径.
- 同一文件集的后续轮次默认续同一会话.
