param(
    [string]$BaseUrl = "http://127.0.0.1:8766/api/v1"
)

$extensionRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $extensionRoot

Write-Host "Compiling VSCode extension..."
& npm run compile
if ($LASTEXITCODE -ne 0) {
    throw "Failed to compile the VSCode extension."
}

Write-Host "Resetting manual test state..."
& (Join-Path $PSScriptRoot "reset-manual-test.ps1") -BaseUrl $BaseUrl
if ($LASTEXITCODE -ne 0) {
    throw "Failed to reset manual test state."
}

Write-Host "Seeding manual test data..."
& (Join-Path $PSScriptRoot "seed-manual-test.ps1") -BaseUrl $BaseUrl
if ($LASTEXITCODE -ne 0) {
    throw "Failed to seed manual test data."
}

Write-Host "Manual test workspace is ready."
