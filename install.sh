#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${HOME:?HOME is required}"
CONFIG_BASE="${XDG_CONFIG_HOME:-$HOME_DIR/.config}"
CONFIG_DIR="$CONFIG_BASE/ga"
CONFIG_FILE="$CONFIG_DIR/config.json"
BIN_DIR="$HOME_DIR/.local/bin"
SKILL_DIR="$HOME_DIR/.codex/skills/ga-bridge"
WINDOWS_USER="$(cmd.exe /c echo %USERNAME% 2>/dev/null | tr -d '\r' | tail -n 1)"
WINDOWS_USER="${WINDOWS_USER:-__WINDOWS_USER__}"
WSL_DISTRO_VALUE="${WSL_DISTRO_NAME:-Ubuntu}"
OWNER_VALUE="$(printf '%s' "${USER:-shared}" | sed 's/[&/]/\\&/g')"

mkdir -p "$BIN_DIR" "$SKILL_DIR" "$CONFIG_DIR"

cp "$SCRIPT_DIR/ga_cli.py" "$BIN_DIR/ga_cli.py"
cat > "$BIN_DIR/ga" <<'WRAPPER'
#!/usr/bin/env bash
exec python3 "$HOME/.local/bin/ga_cli.py" "$@"
WRAPPER
chmod +x "$BIN_DIR/ga" "$BIN_DIR/ga_cli.py"

cp -R "$SCRIPT_DIR/skill/ga-bridge/." "$SKILL_DIR/"
sed -i "s/^owner: __OWNER__$/owner: $OWNER_VALUE/" "$SKILL_DIR/SKILL.md"

if [ ! -f "$CONFIG_FILE" ]; then
  sed \
    -e "s|__WINDOWS_USER__|$WINDOWS_USER|g" \
    -e "s|__WSL_DISTRO__|$WSL_DISTRO_VALUE|g" \
    -e "s|__HOME__|$HOME_DIR|g" \
    "$SCRIPT_DIR/config.example.json" > "$CONFIG_FILE"
fi

echo "ga-bridge installed."
echo "CLI: $BIN_DIR/ga"
echo "Skill: $SKILL_DIR"
echo "Config: $CONFIG_FILE"
echo "Next: $BIN_DIR/ga doctor"
