#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || {
  echo "The macOS release must be built natively on Apple silicon." >&2
  exit 2
}

python_bin="$root/build/package-venv/bin/python"
export UV_PROJECT_ENVIRONMENT="$root/build/package-venv"
export UV_CACHE_DIR="$root/build/uv-cache"
export MACOSX_DEPLOYMENT_TARGET=14.0
uv python install 3.12
uv sync --frozen --extra vision --no-dev --python 3.12
uv pip install --python "$python_bin" 'pyinstaller==6.20.0'
"$python_bin" -m unittest discover -s tests -v

project_version="$($python_bin -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
version="${1:-$project_version}"
[[ "$version" == "$project_version" ]] || {
  echo "Tag version $version != project version $project_version" >&2
  exit 2
}

model="$root/build/yolo26n-pose.pt"
curl -fL --retry 3 -o "$model" \
  https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-pose.pt
echo 'eb3bb8268828aeaf515cec23a4bfafd793944a86fe9af94ba7823609c14522a9  build/yolo26n-pose.pt' | shasum -a 256 -c -

"$python_bin" -c 'import platform, torch; assert platform.machine() == "arm64"; assert torch.backends.mps.is_built()'
"$python_bin" packaging/make_icon.py build/opendance.icns
export OPENDANCE_BUNDLE_MODEL="$model"
export OPENDANCE_BUNDLE_VERSION="$version"
"$python_bin" -m PyInstaller --noconfirm --clean packaging/opendance.spec

app="dist/OpenDance.app"
test -d "$app"
test -f "$app/Contents/Resources/LICENSE"
file "$app/Contents/MacOS/OpenDance" | grep -q arm64
/usr/libexec/PlistBuddy -c 'Print :NSCameraUsageDescription' "$app/Contents/Info.plist" >/dev/null
codesign --verify --deep --strict "$app"
"$app/Contents/MacOS/opendance-extract" --diagnostics

mkdir -p release
archive="release/OpenDance-$version-macos-arm64.zip"
ditto -c -k --sequesterRsrc --keepParent "$app" "$archive"
shasum -a 256 "$archive" | sed 's|  release/|  |' > release/SHA256SUMS-macos-arm64.txt
