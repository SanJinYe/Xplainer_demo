param(
    [string]$BaseUrl = "http://127.0.0.1:8766/api/v1"
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\..")).Path
$targetFileRelative = "vscode-extension/manual_test_target.py"
$targetFilePath = Join-Path $repoRoot $targetFileRelative

if (-not (Test-Path $targetFilePath)) {
    throw "Manual test target not found: $targetFilePath"
}

$finalSnapshot = (Get-Content -Path $targetFilePath -Raw).Replace("`r`n", "`n")
$sessionId = "vscode-manual-{0}" -f (Get-Date -Format "yyyyMMddHHmmss")

$events = @(
    @{
        action_type = "create"
        file_path = $targetFileRelative
        code_snapshot = @'
def fetch_profile(user_id: str) -> str:
    return f"user-{user_id}"
'@
        intent = "create a reusable user label helper for card rendering"
        reasoning = "start with the smallest callable unit before building the UI service"
        agent_step_id = "manual_step_1"
        session_id = $sessionId
        line_range = @(1, 2)
        external_refs = @()
    },
    @{
        action_type = "rename"
        file_path = $targetFileRelative
        code_snapshot = @'
def fetch_user_profile(user_id: str) -> str:
    return f"user-{user_id}"
'@
        intent = "rename fetch_profile to fetch_user_profile so the helper name matches its responsibility"
        reasoning = "make the function name explicit before other callers depend on it"
        agent_step_id = "manual_step_2"
        session_id = $sessionId
        line_range = @(1, 2)
        external_refs = @()
    },
    @{
        action_type = "modify"
        file_path = $targetFileRelative
        code_snapshot = $finalSnapshot
        intent = "create UserCardService.build_card to assemble a UI-ready card"
        reasoning = "keep fetching and card formatting in separate entities while reusing the helper"
        decision_alternatives = @(
            "build the card directly inside fetch_user_profile",
            "inline the helper logic inside the method"
        )
        agent_step_id = "manual_step_3"
        session_id = $sessionId
        line_range = @(1, 8)
        external_refs = @()
    }
)

try {
    $responses = @()
    foreach ($event in $events) {
        $responses += Invoke-RestMethod `
            -Method Post `
            -Uri "$BaseUrl/events" `
            -ContentType "application/json" `
            -Body ($event | ConvertTo-Json -Depth 8)
    }
} catch {
    throw "Failed to seed manual test data at $BaseUrl. Ensure the backend is running. Details: $($_.Exception.Message)"
}

$escapedFile = [System.Uri]::EscapeDataString($targetFileRelative)
$helperEntity = Invoke-RestMethod -Method Get -Uri "$BaseUrl/entities/by-location?file=$escapedFile&line=1"
$methodEntity = Invoke-RestMethod -Method Get -Uri "$BaseUrl/entities/by-location?file=$escapedFile&line=6"

Write-Host "Seeded $($responses.Count) events."
Write-Host "Session ID: $sessionId"
Write-Host "Helper entity: $($helperEntity.qualified_name)"
Write-Host "Method entity: $($methodEntity.qualified_name)"
