param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    python -m evidence_detective.server --host 127.0.0.1 --port $Port
} finally {
    Pop-Location
}
