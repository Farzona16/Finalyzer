# Start the FastAPI backend with backend\.venv (has FastAPI; the root .venv does not).
# Usage:  .\start.ps1
# Extra uvicorn args are forwarded, e.g.  .\start.ps1 --no-reload
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script = Join-Path $Root "run_backend.py"
$VenvPy = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (Test-Path $VenvPy) {
    & $VenvPy $Script @args
} else {
    py -3.12 $Script @args
}
exit $LASTEXITCODE
