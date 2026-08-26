#!/usr/bin/env bash
set -e

PKG="token-tracker"

say() { printf '%s\n' "$*"; }
err() { printf 'Error: %s\n' "$*" >&2; }

# 安装方式优先级：uv > pipx > 自建私有 venv > pip --user。
# 前两者隔离 + 自带 PATH 处理；都没有时自建 venv（绕开 PEP 668 externally-managed、
# 不需要 sudo、不往系统装任何新工具）；venv 也建不了才最后退到 pip --user。
TT_BIN=""

if command -v uv >/dev/null 2>&1; then
    say "Installing $PKG with uv..."
    uv tool install --force "$PKG"
    # 用确定的安装落点，不用 command -v tt——遮蔽场景下它会返回 PATH 更靠前的旧版，
    # 导致后面清理把新版当遮蔽误卸。uv 落点：UV_TOOL_BIN_DIR / XDG_BIN_HOME（默认 ~/.local/bin）。
    # 万一仍与实际不符，下面有「TT_BIN 不存在则跳过清理」的存在性兜底。
    TT_BIN="${UV_TOOL_BIN_DIR:-${XDG_BIN_HOME:-$HOME/.local/bin}}/tt"

elif command -v pipx >/dev/null 2>&1; then
    say "Installing $PKG with pipx..."
    pipx install --force "$PKG"
    # 同理用确定落点；pipx 的 console script 落在 PIPX_BIN_DIR（默认 ~/.local/bin）。
    TT_BIN="${PIPX_BIN_DIR:-$HOME/.local/bin}/tt"

elif command -v python3 >/dev/null 2>&1 && python3 -m venv -h >/dev/null 2>&1; then
    say "Installing $PKG into a private venv (~/.local/share/token-tracker)..."
    VENV_DIR="$HOME/.local/share/token-tracker/venv"
    BIN_DIR="$HOME/.local/bin"
    python3 -m venv "$VENV_DIR"
    # Windows（Git Bash / MSYS2）下 venv 可执行目录是 Scripts/ 而非 bin/，
    # 可执行文件带 .exe 后缀；按实际布局探测，Unix 行为不变。
    VENV_BIN="$VENV_DIR/bin"
    [ -d "$VENV_BIN" ] || VENV_BIN="$VENV_DIR/Scripts"
    "$VENV_BIN/pip" install --upgrade "$PKG"
    mkdir -p "$BIN_DIR"
    TT_NAME="tt"
    [ -f "$VENV_BIN/tt.exe" ] && TT_NAME="tt.exe"
    ln -sf "$VENV_BIN/$TT_NAME" "$BIN_DIR/tt"
    TT_BIN="$BIN_DIR/tt"

else
    # 最后保险：pip --user（可能撞 PEP 668，再加 --break-system-packages 重试）。
    # 一般是 Debian/Ubuntu 缺 python3-venv 包才会走到这里。
    PIP="$(command -v pip3 || command -v pip || true)"
    if [ -z "$PIP" ]; then
        err "Need one of: uv / pipx / python3(+venv) / pip. Please install Python 3.11+ first."
        exit 1
    fi
    say "uv / pipx / venv unavailable, falling back to pip --user..."
    "$PIP" install --user --upgrade "$PKG" \
        || "$PIP" install --user --break-system-packages --upgrade "$PKG"
    # 同理用确定落点：pip --user 的 scripts 落在 site --user-base 的 bin 下
    TT_BIN="$(python3 -m site --user-base 2>/dev/null)/bin/tt"
    [ -f "$TT_BIN" ] || TT_BIN="$HOME/.local/bin/tt"
fi

# ---------------------------------------------------------------------------
# PATH 健康检查 + 旧版自动清理：确保装完后 `tt` 一定是刚装的版本。
# 两类问题会让用户跑到旧版：
#   1. PATH 不含安装目录（macOS 默认不含 ~/.local/bin）
#   2. 另一个同名 tt 在 PATH 前面遮蔽（conda / homebrew / system pip 装过旧版）
# 对 case 2，读每个被遮蔽 tt 的 shebang 拿到当初装它的 python，用那个 python
# 精准卸载（pip / pipx / uv 分别处理）。物理移除旧二进制同时顺带消除 shell 的
# 命令 hash 缓存——旧路径文件没了，shell 下次会重新搜 PATH 找到新版。
# 只清理「能确认是本包」的，查不到来源的同名 tt 一律不动（零误删）。
# 清理全程对用户静默——内部动作不暴露给用户，最后只保留必要的 PATH 健康提示。
# ---------------------------------------------------------------------------

# resolve symlink，portable：优先 realpath，退到 python3，再退原样返回
_realpath() {
    if command -v realpath >/dev/null 2>&1; then
        realpath "$1" 2>/dev/null
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$1" 2>/dev/null
    else
        printf '%s\n' "$1"
    fi
}

# 关键容错：只有可靠定位到「刚装的 tt」（TT_BIN 是真实存在的文件）才动清理。
# 否则（落点判断与实际不符、安装异常）NEW_REAL 会指向不存在的路径，导致下面
# 把所有 tt（含新版）都当成遮蔽误删。定位不到就整段跳过，安全优先。
NEW_REAL=""
if [ -f "$TT_BIN" ]; then
    NEW_REAL="$(_realpath "$TT_BIN")"
    [ -n "$NEW_REAL" ] || NEW_REAL="$TT_BIN"
fi

if [ -n "$NEW_REAL" ]; then
    # 按 : 拆 PATH 遍历——POSIX 的 IFS 分词写法（for 进入时按 IFS 一次性分好词，
    # 体内把 IFS 设回来不影响已分好的列表），避开 bash 专属的 read -ra / here-string。
    _seen=""
    _OLD_IFS="$IFS"
    IFS=':'
    for _d in $PATH; do
        IFS="$_OLD_IFS"
        [ -n "$_d" ] || continue
        _cand="$_d/tt"
        [ -f "$_cand" ] || continue
        _rp="$(_realpath "$_cand")"; [ -n "$_rp" ] || _rp="$_cand"
        [ "$_rp" = "$NEW_REAL" ] && continue            # 跳过刚装的
        case ":$_seen:" in *":$_rp:"*) continue;; esac  # 同一真实文件只处理一次
        _seen="$_seen:$_rp"

        # 读 shebang 拿到当初装它的 python（pip 生成的 console script 都是绝对路径）
        _shebang="$(head -1 "$_cand" 2>/dev/null)"
        _py=""
        case "$_shebang" in
            '#!'*) _py="${_shebang#\#!}"; _py="${_py%%[[:space:]]*}";;
        esac

        # 判断这个 tt 是不是本包装的，并选对卸载方式。注意 uv / pipx 的工具 venv
        # 默认不带 pip，不能用 pip show 识别；好在它们的 venv 目录名就是包名，直接据
        # shebang 路径确认（~/…/uv/tools/<pkg>/、~/…/pipx/venvs/<pkg>/）——还顺带覆盖了
        # python 升级后 shebang 失效的死链。其余（普通 pip / venv）才用 pip show 确认。
        _kill=""
        case "$_py" in
            *"/uv/tools/$PKG/"*)   _kill="uv" ;;
            *"/pipx/venvs/$PKG/"*) _kill="pipx" ;;
            *)  if [ -n "$_py" ] && [ -x "$_py" ] && "$_py" -m pip show "$PKG" >/dev/null 2>&1; then
                    _kill="pip"
                fi ;;
        esac

        # 只清理「确认是本包」的；查不到来源的一律不动（零误删）。全程静默
        if [ -n "$_kill" ]; then
            case "$_kill" in
                uv)   uv tool uninstall "$PKG"  >/dev/null 2>&1 || rm -f "$_cand" ;;
                pipx) pipx uninstall "$PKG"     >/dev/null 2>&1 || rm -f "$_cand" ;;
                pip)  "$_py" -m pip uninstall "$PKG" -y >/dev/null 2>&1 || rm -f "$_cand" ;;
            esac
            [ -e "$_cand" ] && rm -f "$_cand" 2>/dev/null || true
        fi
    done
    IFS="$_OLD_IFS"
fi

# 健康检查：装完后 `tt` 没解析到刚装的版本才出声（两种需用户动手的情况）。
# 清理成功时这里静默；只有「PATH 没配」或「旧版没清掉仍遮蔽」（权限/工具缺失）才提示。
TT_DIR="$(dirname "$TT_BIN")"
ACTIVE_TT="$(command -v tt 2>/dev/null || true)"
if [ -z "$ACTIVE_TT" ]; then
    say ""
    say "Note: $TT_DIR is not on your PATH."
    say "  bash / zsh — add to ~/.zshrc (or ~/.bashrc), then restart your shell:"
    say "    export PATH=\"$TT_DIR:\$PATH\""
    say "  fish — run once:"
    say "    fish_add_path $TT_DIR"
elif [ -n "$NEW_REAL" ]; then
    _active_real="$(_realpath "$ACTIVE_TT")"; [ -n "$_active_real" ] || _active_real="$ACTIVE_TT"
    if [ "$_active_real" != "$NEW_REAL" ]; then
        say ""
        say "Note: an old 'tt' still shadows the newly installed one:"
        say "    in PATH        : $ACTIVE_TT"
        say "    just installed : $TT_BIN"
        say "Remove the old one, then reopen your shell:"
        say "    pip uninstall token-tracker"
    fi
fi

# 跑配置向导：用 tt 绝对路径调，避免 PATH 尚未更新时 command not found。
# 不把 stdin 重定向到 /dev/tty——curl|bash 下 bash 没 controlling terminal，
# 这种「pipe stdin → /dev/tty 重定向」会触发 prompt_toolkit issue #1943（kqueue
# 注册 fd 失败 OSError [Errno 22]）。改用：让 tt setup 看到 non-tty stdin →
# 自动走 _auto_setup 默认全装（语言跟随系统 / mocha / 组件全开），不崩。
# 想自定义的用户末尾会看到明确提示，去独立终端跑 `tt setup` 即可。
# PYTHONUTF8=1：Windows GBK 控制台下 rich 输出 ✓ 等字符会
# UnicodeEncodeError 导致 setup 崩溃，强制 UTF-8 模式规避（其他平台无影响）。
say ""
say "Configuring status bar..."
PYTHONUTF8=1 "$TT_BIN" setup

say ""
say "Done! Run 'tt' to start."
