#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

python_bin="$root/build/package-venv/bin/python"
export UV_PROJECT_ENVIRONMENT="$root/build/package-venv"
export UV_CACHE_DIR="$root/build/uv-cache"
uv python install 3.12
uv sync --frozen --extra vision --no-dev --python 3.12
uv pip install --python "$python_bin" 'pyinstaller==6.20.0'

project_version="$($python_bin -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
version="${1:-$project_version}"
[[ "$version" == "$project_version" ]] || { echo "Tag version $version != project version $project_version" >&2; exit 2; }

model="$root/build/yolo26n-pose.pt"
curl -fL --retry 3 -o "$model" \
  https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-pose.pt
echo 'eb3bb8268828aeaf515cec23a4bfafd793944a86fe9af94ba7823609c14522a9  build/yolo26n-pose.pt' | sha256sum -c -

"$python_bin" -c 'import torch; assert torch.version.cuda == "13.0", torch.__version__'
uv cache clean
export OPENDANCE_BUNDLE_MODEL="$model"
"$python_bin" -m PyInstaller --noconfirm --clean packaging/opendance.spec
cp LICENSE dist/OpenDance/LICENSE
dist/OpenDance/opendance-extract --diagnostics

archive="OpenDance-$version-linux-x86_64.tar.gz"
mkdir -p release
tar -C dist -czf "release/$archive" OpenDance
sha256sum "release/$archive" | cut -d' ' -f1 > "release/$archive.sha256"
split -b 1900M -d -a 2 "release/$archive" "release/$archive.part-"
rm "release/$archive"
cp packaging/install_linux.sh "release/OpenDance-$version-linux-x86_64-install.sh"
(cd release && sha256sum "$archive".part-* "$archive.sha256" "OpenDance-$version-linux-x86_64-install.sh" > SHA256SUMS-linux-x86_64.txt)
find release -type f -size +1999M -print -quit | grep -q . && { echo 'Release asset exceeds 2 GB' >&2; exit 3; } || true
