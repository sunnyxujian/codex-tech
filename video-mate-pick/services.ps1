param(
    [ValidateSet('start', 'stop', 'status', 'restart')][string]$Action = 'status',
    [ValidateSet('all', 'audio', 'video')][string]$Service = 'all'
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$runtimeDir = Join-Path $projectRoot '.runtime'
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$definitions = @(
    @{ Name = 'audio'; Port = 8765; Entry = 'audio_service.py' },
    @{ Name = 'video'; Port = 8766; Entry = 'frame_service.py' }
) | Where-Object { $Service -eq 'all' -or $_.Name -eq $Service }
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

function Get-Health($definition) {
    try { return Invoke-RestMethod -Uri "http://127.0.0.1:$($definition.Port)/health" -TimeoutSec 3 }
    catch { return $null }
}

function Test-HealthOwner($health, $definition) {
    return $null -ne $health -and $health.service -eq $definition.Name -and
        [string]::Equals($health.project_root, $projectRoot, [StringComparison]::OrdinalIgnoreCase)
}

function Get-OwnedProcess($processId, $definition) {
    if (-not $processId) { return $null }
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$processId)"
    $entryPath = Join-Path $projectRoot $definition.Entry
    $entryPattern = '(?:^|\s)"?' + [regex]::Escape($entryPath) + '"?(?:\s|$)'
    if ($processInfo -and $processInfo.CommandLine -and $processInfo.CommandLine -match $entryPattern) {
        return $processInfo
    }
    return $null
}

function Save-State($health, $definition) {
    $processInfo = Get-OwnedProcess $health.pid $definition
    if (-not $processInfo) { throw "Cannot verify $($definition.Name) process ownership." }
    @{ pid = $health.pid; created = $processInfo.CreationDate.ToString('o'); project_root = $projectRoot } |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeDir "$($definition.Name).json") -Encoding UTF8
}

function Start-ServiceLocal($definition) {
    $health = Get-Health $definition
    if (Test-HealthOwner $health $definition) {
        if (-not $health.ok) { throw "$($definition.Name) is running but unhealthy." }
        Save-State $health $definition
        Write-Host "$($definition.Name): already running at http://127.0.0.1:$($definition.Port)"
        return
    }
    $listener = Get-NetTCPConnection -LocalPort $definition.Port -State Listen -ErrorAction SilentlyContinue
    if ($health -or $listener) {
        throw "Port $($definition.Port) belongs to another service. It was left untouched."
    }
    if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run setup.cmd first to create the project Python environment.' }
    $entryPath = Join-Path $projectRoot $definition.Entry
    $logPrefix = Join-Path $runtimeDir $definition.Name
    $env:PYTHONUTF8 = '1'
    $launcherPid = & $pythonExe (Join-Path $projectRoot 'scripts\launch_service.py') $definition.Name
    if ($LASTEXITCODE -ne 0) { throw "Failed to launch $($definition.Name)." }
    $processInfo = Get-Process -Id ([int]$launcherPid)
    Save-State @{ pid = $processInfo.Id } $definition
    $deadline = (Get-Date).AddSeconds(120)
    do {
        $health = Get-Health $definition
        if (Test-HealthOwner $health $definition) {
            if (-not $health.ok) { throw "$($definition.Name) started but its model is missing. See $logPrefix.stderr.log" }
            Save-State $health $definition
            Write-Host "$($definition.Name): ready at http://127.0.0.1:$($definition.Port) [$($health.model)]"
            return
        }
        $processInfo.Refresh()
        if ($processInfo.HasExited) { throw "$($definition.Name) exited. See $logPrefix.stderr.log" }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw "$($definition.Name) startup timed out. Check status and $logPrefix.stderr.log before retrying."
}

function Stop-ServiceLocal($definition) {
    $health = Get-Health $definition
    $statePath = Join-Path $runtimeDir "$($definition.Name).json"
    $processInfo = $null
    if (Test-HealthOwner $health $definition) {
        $processInfo = Get-OwnedProcess $health.pid $definition
        if (-not $processInfo) { throw 'Refusing to stop a process whose command line cannot be verified.' }
    } elseif (Test-Path -LiteralPath $statePath) {
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        $candidate = Get-OwnedProcess $state.pid $definition
        if ($candidate -and $candidate.CreationDate.ToString('o') -eq $state.created) { $processInfo = $candidate }
    }
    if ($processInfo) {
        $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($processInfo.ProcessId)")
        foreach ($child in $children) {
            if (Get-OwnedProcess $child.ProcessId $definition) { Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue }
        }
        Stop-Process -Id $processInfo.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "$($definition.Name): stopped"
    } else {
        Write-Host "$($definition.Name): no managed process to stop; other services left untouched"
    }
    if (Test-Path -LiteralPath $statePath) { Remove-Item -LiteralPath $statePath }
}

function Show-Status($definition) {
    $health = Get-Health $definition
    if (Test-HealthOwner $health $definition) {
        Write-Host "$($definition.Name): $(if ($health.ok) {'ready'} else {'unhealthy'}) | PID $($health.pid) | $($health.model)"
        Write-Host "  Project: $($health.project_root)"
        Write-Host "  Python:  $($health.python)"
        Write-Host "  Output:  $($health.output)"
        return [bool]$health.ok
    }
    $listener = Get-NetTCPConnection -LocalPort $definition.Port -State Listen -ErrorAction SilentlyContinue
    Write-Host "$($definition.Name): $(if ($health -or $listener) {'port occupied by another service'} else {'stopped'})"
    return $false
}

$rootHash = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($projectRoot))).Replace('-', '')
$mutex = New-Object Threading.Mutex($false, "Local\codex-tech-$rootHash")
$held = $false
try {
    $held = $mutex.WaitOne(0)
    if (-not $held) { throw 'Another service management command is running. Try again when it finishes.' }
    $success = $true
    foreach ($definition in $definitions) {
        if ($Action -in 'stop', 'restart') { Stop-ServiceLocal $definition }
        if ($Action -in 'start', 'restart') { Start-ServiceLocal $definition }
        if ($Action -eq 'status') { if (-not (Show-Status $definition)) { $success = $false } }
    }
    if (-not $success) { exit 1 }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
} finally {
    if ($held) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
