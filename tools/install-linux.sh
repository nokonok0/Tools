#!/usr/bin/env bash
# Linux向けの導入手順。Windowsの .venv ・ uv.lock ・ %LOCALAPPDATA% には触れない。
#
#   tools/install-linux.sh                導入（デスクトップ登録と自動起動を含む）
#   tools/install-linux.sh --no-autostart  自動起動だけ登録しない
#   tools/install-linux.sh --uninstall    このスクリプトが作ったものだけ削除する
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
share="$HOME/.local/share/meter"
bindir="$HOME/.local/bin"
appdir="$HOME/.local/share/applications"
autostart="$HOME/.config/autostart"
apps="CodexMeter DeepSeekMeter"

die() { printf 'エラー: %s\n' "$*" >&2; exit 1; }

describe() {
    case "$1" in
        CodexMeter) key=codex-meter; name="Codex Meter"
            comment="Codexの利用枠と上限までの推定時間"; extra="" ;;
        DeepSeekMeter) key=deepseek-meter; name="DeepSeek Meter"
            comment="DeepSeekの残高と枯渇までの推定時間"; extra="zstandard>=0.23.0" ;;
    esac
}

if [ "${1:-}" = "--uninstall" ]; then
    for app in $apps; do
        describe "$app"
        rm -f "$bindir/$key" "$appdir/$key.desktop" "$autostart/$key.desktop"
        rm -rf "$repo/$app/.venv-linux"
    done
    rm -rf "$share"
    update-desktop-database "$appdir" >/dev/null 2>&1 || true
    echo "削除しました。保存データ（~/.local/share/CodexMeter など）は残しています。"
    exit 0
fi

autostart_enabled=1
if [ "${1:-}" = "--no-autostart" ]; then
    autostart_enabled=0
fi

# PyGObject（gi）とTkはシステムのものを使うため、--system-site-packages で仮想環境を作る。
python=python3.12
command -v "$python" >/dev/null 2>&1 || python=python3
"$python" -c 'import gi, tkinter' 2>/dev/null \
    || die "python3-gi と python3-tk が必要です（sudo apt install python3-gi gir1.2-gtk-3.0 python3-tk）"

typelib="$share/typelib/AyatanaAppIndicator3-0.1.typelib"
if [ ! -f "$typelib" ] \
    && ! GI_TYPELIB_PATH="$share/typelib" "$python" -c 'import gi; gi.require_version("AyatanaAppIndicator3", "0.1")' 2>/dev/null; then
    # typelibだけをsudoなしでローカルに置く。共有ライブラリは libayatana-appindicator3-1 を使う。
    mkdir -p "$share/typelib"
    work=$(mktemp -d)
    trap 'rm -rf "$work"' EXIT
    (cd "$work" && apt-get download gir1.2-ayatanaappindicator3-0.1 >/dev/null 2>&1) \
        || die "typelibを取得できません。sudo apt install gir1.2-ayatanaappindicator3-0.1 を実行してください"
    dpkg -x "$work"/gir1.2-ayatanaappindicator3-0.1_*.deb "$work/extract"
    cp "$work"/extract/usr/lib/*/girepository-1.0/AyatanaAppIndicator3-0.1.typelib "$share/typelib/"
fi

mkdir -p "$bindir" "$appdir" "$autostart"
# uvがあればpipを同梱しない仮想環境でも依存を導入できる。なければvenv+pipを使う。
if command -v uv >/dev/null 2>&1; then
    uv=uv
elif [ -x "$HOME/.local/bin/uv" ]; then
    uv="$HOME/.local/bin/uv"
else
    uv=""
fi
for app in $apps; do
    describe "$app"
    venv="$repo/$app/.venv-linux"
    if [ ! -x "$venv/bin/python" ] \
        || ! grep -q 'include-system-site-packages = true' "$venv/pyvenv.cfg" 2>/dev/null; then
        rm -rf "$venv"
        if [ -n "$uv" ]; then
            "$uv" venv --python "${python##*python}" --system-site-packages "$venv" >/dev/null
        else
            "$python" -m venv --system-site-packages "$venv" \
                || die "仮想環境を作れません。sudo apt install python3.12-venv を実行するかuvを入れてください"
        fi
    fi
    "$venv/bin/python" -c 'import gi, tkinter' 2>/dev/null \
        || die "$venv からPyGObjectを読み込めません。python3-gi とpython3-tk を確認してください"
    if [ -n "$uv" ]; then
        "$uv" pip install --python "$venv/bin/python" --quiet --disable-pip-version-check \
            "pillow>=13.0.0" "pystray>=0.19.5,<0.20" $extra
    else
        "$venv/bin/python" -m pip install --quiet --disable-pip-version-check \
            "pillow>=13.0.0" "pystray>=0.19.5,<0.20" $extra
    fi

    cat > "$bindir/$key" <<EOF
#!/bin/sh
# $name（Linux用。Windowsでは使わない）
export GI_TYPELIB_PATH="\$HOME/.local/share/meter/typelib\${GI_TYPELIB_PATH:+:\$GI_TYPELIB_PATH}"
cd "$repo/$app" || exit 1
exec ./.venv-linux/bin/python ./$app.pyw
EOF
    chmod 755 "$bindir/$key"

    cat > "$appdir/$key.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$name
Comment=$comment
Exec=$bindir/$key
Icon=utilities-system-monitor
Terminal=false
Categories=Utility;
StartupNotify=false
EOF
    if [ "$autostart_enabled" = 1 ]; then
        cp "$appdir/$key.desktop" "$autostart/$key.desktop"
        printf 'X-GNOME-Autostart-enabled=true\n' >> "$autostart/$key.desktop"
    fi
done
update-desktop-database "$appdir" >/dev/null 2>&1 || true

echo "導入しました。"
echo "  起動: $bindir/codex-meter / $bindir/deepseek-meter（アプリ一覧にも出ます）"
echo "  保存先: ~/.local/share/{CodexMeter,DeepSeekMeter,MeterCommon}"
if [ "$autostart_enabled" = 1 ]; then
    echo "  自動起動: ~/.config/autostart（次回サインインから）"
fi
echo "  削除: tools/install-linux.sh --uninstall"
echo "Windowsの .venv と %LOCALAPPDATA%、uv.lock は変更していません。"
