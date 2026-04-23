# ga-bridge share bundle

WSL 下的 `ga` CLI + Codex skill 分享包.

## 包内容

- `ga_cli.py`: 通用版 CLI.
- `skill/ga-bridge/`: 可直接安装到 `~/.codex/skills/ga-bridge/` 的 skill.
- `config.example.json`: 配置模板.
- `install.sh`: WSL 一键安装.
- `install.ps1`: Windows 侧一键转调到 WSL 安装.

## 前置条件

- WSL2.
- Python 3.
- Windows 侧已安装 GenericAgent.
- 目标机器的 agent 使用 `~/.codex/skills`.

## 一键安装

### 从 GitHub

```bash
git clone <repo-url>
cd ga-bridge-package
chmod +x ./install.sh
./install.sh
```

### WSL

```bash
cd ga-bridge-package
chmod +x ./install.sh
./install.sh
```

### Windows PowerShell

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

## 安装结果

- CLI:
  - `~/.local/bin/ga`
  - `~/.local/bin/ga_cli.py`
- Skill:
  - `~/.codex/skills/ga-bridge/`
- 配置:
  - `~/.config/ga/config.json`

## 安装后需要检查

执行:

```bash
ga doctor
```

如果这些值不对, 修改 `~/.config/ga/config.json`:

- `ga_root_win`
- `ga_root_wsl`
- `windows_temp_win`
- `windows_temp_wsl`
- `wsl_distro`
- `default_llm_no`

## 给别人的最短说明

1. 解压这个包.
2. 在 WSL 跑 `./install.sh`, 或在 Windows 跑 `.\install.ps1`.
3. 执行 `ga doctor`.
4. 按机器实际路径改 `~/.config/ga/config.json`.

## 说明

- 安装脚本会把 `skill/ga-bridge/SKILL.md` 的 `owner` 自动改成当前 Linux 用户名.
- 这个包不会改你当前仓库里的 skill 源文件, 只安装到用户目录.
- 仓库本身不包含用户本地 token, job, session, 或缓存状态.
