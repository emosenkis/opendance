[CmdletBinding()]
param([string]$Version = "")

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root
$Python = Join-Path $Root "build\package-venv\Scripts\python.exe"
$env:UV_PROJECT_ENVIRONMENT = Join-Path $Root "build\package-venv"
$env:UV_CACHE_DIR = Join-Path $Root "build\uv-cache"

uv python install 3.12
uv sync --frozen --extra vision --no-dev --python 3.12
uv pip install --python $Python "pyinstaller==6.20.0"
& $Python -m unittest discover -s tests -v
if ($LASTEXITCODE) { throw "Tests failed" }

$ProjectVersion = & $Python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])'
if (-not $Version) { $Version = $ProjectVersion }
if ($Version -ne $ProjectVersion) { throw "Tag version $Version != project version $ProjectVersion" }

$Model = Join-Path $Root "build\yolo26n-pose.pt"
curl.exe -fL --retry 3 -o $Model https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-pose.pt
$Hash = (Get-FileHash -Algorithm SHA256 $Model).Hash.ToLowerInvariant()
if ($Hash -ne "eb3bb8268828aeaf515cec23a4bfafd793944a86fe9af94ba7823609c14522a9") { throw "Model checksum mismatch" }

& $Python -c 'import torch; assert torch.version.cuda == "13.0", torch.__version__'
uv cache clean
$env:OPENDANCE_BUNDLE_MODEL = $Model
& $Python packaging\make_icon.py build\opendance.ico
& $Python -m PyInstaller --noconfirm --clean packaging\opendance.spec
Copy-Item LICENSE dist\OpenDance\LICENSE
& dist\OpenDance\opendance-extract.exe --diagnostics
if ($LASTEXITCODE) { throw "Frozen bundle diagnostics failed" }
Remove-Item -Recurse -Force $env:UV_PROJECT_ENVIRONMENT
if (Test-Path $env:UV_CACHE_DIR) { Remove-Item -Recurse -Force $env:UV_CACHE_DIR }

$Compiler = @(
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Compiler) { throw "Install Inno Setup 6 first" }
& $Compiler "/DAppVersion=$Version" packaging\windows.iss
if ($LASTEXITCODE) { throw "Inno Setup failed" }

$Assets = Get-ChildItem release\OpenDance-*-windows-x86_64-setup*
if ($Assets | Where-Object Length -gt 1999000000) { throw "Release asset exceeds 2 GB" }
$Lines = $Assets | ForEach-Object { "{0}  {1}" -f (Get-FileHash -Algorithm SHA256 $_).Hash.ToLowerInvariant(), $_.Name }
$Lines | Set-Content -Encoding ascii release\SHA256SUMS-windows-x86_64.txt
