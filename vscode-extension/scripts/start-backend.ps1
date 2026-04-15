param(
    [string]$ListenHost = "127.0.0.1",
    [int]$Port = 8766,
    [string]$DbPath = ".tmp\\vscode-extension-manual.db"
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..")).Path
Set-Location $repoRoot

if (-not (Test-Path ".tmp")) {
    New-Item -ItemType Directory -Path ".tmp" | Out-Null
}

$pythonCandidates = @(
    (Join-Path $repoRoot ".venv\\Scripts\\python.exe"),
    "C:\\Users\\16089\\agent\\.venv\\Scripts\\python.exe",
    "python"
)

$python = $null
foreach ($candidate in $pythonCandidates) {
    if ($candidate -eq "python" -or (Test-Path $candidate)) {
        $python = $candidate
        break
    }
}

if ($null -eq $python) {
    throw "Python interpreter not found."
}

Write-Host "Starting TailEvents backend at http://$ListenHost`:$Port/docs"
Write-Host "Using DB: $DbPath"

& $python -m tailevents.main --host $ListenHost --port $Port --db-path $DbPath
