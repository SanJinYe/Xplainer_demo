param(
    [string]$BaseUrl = "http://127.0.0.1:8766/api/v1"
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $repoRoot

$resetViaApi = $false

try {
    $null = Invoke-RestMethod -Method Get -Uri "$BaseUrl/admin/health" -TimeoutSec 2
    $response = Invoke-RestMethod -Method Post -Uri "$BaseUrl/admin/reset-state" -TimeoutSec 10
    Write-Host "Reset backend state via API."
    Write-Host (
        "Deleted events={0}, entities={1}, relations={2}, task_steps={3}, cancelled_tasks={4}" -f
        $response.events_deleted,
        $response.entities_deleted,
        $response.relations_deleted,
        $response.task_steps_deleted,
        $response.cancelled_tasks
    )
    $resetViaApi = $true
} catch {
    Write-Host "Backend reset API unavailable. Falling back to offline DB cleanup."
}

if (-not $resetViaApi) {
    $dbFiles = @(
        ".tmp\vscode-extension-manual.db",
        ".tmp\vscode-extension-manual.db-shm",
        ".tmp\vscode-extension-manual.db-wal"
    )

    foreach ($relativePath in $dbFiles) {
        $absolutePath = Join-Path $repoRoot $relativePath
        if (Test-Path $absolutePath) {
            Remove-Item -LiteralPath $absolutePath -Force -ErrorAction Stop
            Write-Host "Removed $relativePath"
        }
    }
}

$manualFiles = @(
    "vscode-extension/manual_test_target.py"
)

& git restore --source=HEAD --worktree -- $manualFiles

if ($LASTEXITCODE -ne 0) {
    throw "Failed to restore manual test files."
}

$complexBaselinePath = Join-Path $PSScriptRoot "manual_test_complex_target.baseline.txt"
$complexTargetPath = Join-Path $repoRoot "vscode-extension/manual_test_complex_target.py"
Copy-Item -LiteralPath $complexBaselinePath -Destination $complexTargetPath -Force

Write-Host "Restored manual test files."
