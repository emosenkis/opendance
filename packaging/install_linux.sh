#!/bin/sh
set -eu

here=$(dirname -- "$(readlink -f -- "$0")")
set -- "$here"/OpenDance-*-linux-x86_64.tar.gz.part-*
[ -f "$1" ] || { echo "Download every OpenDance .part-* beside this installer." >&2; exit 1; }

base=${1%.part-*}
archive=$(mktemp "${TMPDIR:-/tmp}/opendance.XXXXXX.tar.gz")
trap 'rm -f "$archive"' EXIT HUP INT TERM
cat "$@" > "$archive"
expected=$(tr -d '[:space:]' < "$base.sha256")
actual=$(sha256sum "$archive" | cut -d ' ' -f 1)
[ "$actual" = "$expected" ] || { echo "Archive checksum mismatch." >&2; exit 1; }

version=${base##*/OpenDance-}
version=${version%-linux-x86_64.tar.gz}
data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
install="$data_home/opendance/$version"
icon_dir="$data_home/icons/hicolor/scalable/apps"
mkdir -p "$install" "$HOME/.local/bin" "$data_home/applications" "$icon_dir"
tar -xzf "$archive" -C "$install"
ln -sfn "$install/OpenDance" "$data_home/opendance/current"
ln -sfn "$data_home/opendance/current/OpenDance" "$HOME/.local/bin/opendance"
ln -sfn "$data_home/opendance/current/opendance-extract" "$HOME/.local/bin/opendance-extract"
cp "$install/OpenDance/_internal/opendance/assets/icon.svg" "$icon_dir/opendance.svg"
cat > "$data_home/applications/opendance.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=OpenDance
Exec=$data_home/opendance/current/OpenDance
Icon=opendance
Terminal=false
Categories=Game;
EOF
echo "Installed OpenDance $version. Run $HOME/.local/bin/opendance"
