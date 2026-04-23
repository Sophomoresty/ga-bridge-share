# Browser Workflow

## 触发条件

- 任务以浏览器自动化为主:
  - 打开页面
  - 抓取内容
  - 表单填写
  - 截图
  - 登录后流程复用

## 步骤

1. 先跑:

```bash
ga doctor
```

2. 浏览器任务首轮一律启动并 watch:

```bash
ga start --task "打开浏览器访问 ... 并 ..."
ga summary --job <job_id>
```

- 首次启动只用 `ga start ...`.
- 不要拆出第二个公开跟随命令.

3. 若需要在同一上下文继续:

```bash
ga revise-job --job <job_id> --feedback "继续完成 ..."
```

## 边界

- 登录态, 浏览器上下文, 页面状态属于同一任务时, 优先续同一会话.
- 不要把后续步骤拆成无关新任务.
