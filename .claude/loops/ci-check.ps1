<#
.SYNOPSIS
  Vòng kiểm định kỳ của Holocron (CLAUDE.md §10.4): ruff + mypy + pytest trên một worktree riêng ngoài repo.

.DESCRIPTION
  - Worktree riêng: ..\holocron-ci-wt (cạnh repo), checkout --detach lại nhánh mỗi lần chạy.
  - Chỉ chạy khi HEAD của nhánh đổi so với lần kiểm trước (trừ khi có -Force).
  - Log đầy đủ ghi vào %LOCALAPPDATA%\holocron-ci\runs\; tóm tắt vài dòng ở %LOCALAPPDATA%\holocron-ci\ci-status.json.
  - Hỏng thì đưa đuôi log qua stdin cho `claude -p --model haiku`, chạy từ thư mục ngoài repo; kết quả ghi ở ci-summary.txt cạnh ci-status.json.
  - Phiên chính chỉ đọc ci-status.json (hoặc giao agent ci-checker), không mở log.
  Tương thích Windows PowerShell 5.1.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .claude\loops\ci-check.ps1 -Branch develop
  powershell -NoProfile -ExecutionPolicy Bypass -File .claude\loops\ci-check.ps1 -ShowSchedule
#>
[CmdletBinding()]
param(
    [string]$Branch = 'develop',
    [string]$Repo = '',
    [string]$Worktree = '',
    [int]$StepTimeoutMinutes = 30,
    [switch]$Force,
    [switch]$NoSummary,
    [switch]$ShowSchedule
)

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Set-StrictMode -Version Latest

$ScriptPath = $MyInvocation.MyCommand.Path
if (-not $Repo) { $Repo = (Resolve-Path (Join-Path (Split-Path $ScriptPath -Parent) '..\..')).Path }
if (-not $Worktree) { $Worktree = Join-Path (Split-Path $Repo -Parent) 'holocron-ci-wt' }
$LogRoot = Join-Path $env:LOCALAPPDATA 'holocron-ci'
$RunsDir = Join-Path $LogRoot 'runs'
$StatusPath = Join-Path $LogRoot 'ci-status.json'
$SummaryPath = Join-Path $LogRoot 'ci-summary.txt'
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

if ($ShowSchedule) {
    $tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$ScriptPath`" -Branch $Branch"
    Write-Output "schtasks /Create /TN `"Holocron CI check`" /SC MINUTE /MO 30 /TR `"$($tr.Replace('"', '\"'))`" /F"
    exit 0
}

New-Item -ItemType Directory -Force -Path $RunsDir | Out-Null

# Không cho hai lần chạy chồng nhau (Task Scheduler gọi khi lần trước chưa xong).
$mutex = New-Object System.Threading.Mutex($false, 'Local\holocron-ci-check')
if (-not $mutex.WaitOne(0)) { Write-Output 'ci-check: lần chạy trước chưa xong, bỏ qua.'; exit 0 }

function Write-Utf8([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, $Utf8NoBom)
}

function Add-Log([string]$Text) {
    [System.IO.File]::AppendAllText($script:LogPath, $Text + "`r`n", $Utf8NoBom)
}

# Chạy một dòng lệnh qua cmd.exe, ghi stdout+stderr nguyên byte vào log; trả về mã thoát (-1 khi quá thời gian).
function Invoke-Step([string]$Name, [string]$WorkDir, [string]$CommandLine, [int]$TimeoutMinutes) {
    Add-Log ("===== [{0}] {1} :: {2}" -f (Get-Date -Format 'HH:mm:ss'), $Name, $CommandLine)
    $inner = "cd /d `"$WorkDir`" && $CommandLine >> `"$script:LogPath`" 2>&1"
    $proc = Start-Process -FilePath 'cmd.exe' -ArgumentList "/d /s /c `"$inner`"" -NoNewWindow -PassThru
    $null = $proc.Handle
    if (-not $proc.WaitForExit($TimeoutMinutes * 60 * 1000)) {
        & taskkill.exe /T /F /PID $proc.Id | Out-Null
        Add-Log "===== ${Name}: QUÁ THỜI GIAN ($TimeoutMinutes phút), đã dừng"
        return -1
    }
    Add-Log ("===== {0}: exit {1}" -f $Name, $proc.ExitCode)
    return $proc.ExitCode
}

function Git-Out([string[]]$GitArgs) {
    $out = & git @GitArgs 2>$null
    if ($LASTEXITCODE -ne 0) { return $null }
    return ($out | Select-Object -First 1)
}

try {
    $sha = Git-Out @('-C', $Repo, 'rev-parse', '--verify', "$Branch^{commit}")
    if (-not $sha) { Write-Output "ci-check: không tìm thấy nhánh '$Branch' trong $Repo."; exit 2 }
    $sha7 = $sha.Substring(0, 7)

    if (-not $Force -and (Test-Path $StatusPath)) {
        $last = Get-Content -Raw -Encoding UTF8 $StatusPath | ConvertFrom-Json
        if ($last.branch -eq $Branch -and $last.sha -eq $sha) {
            Write-Output "ci-check: $Branch @ $sha7 không đổi (ok=$($last.ok)), bỏ qua."
            exit 0
        }
    }

    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $script:LogPath = Join-Path $RunsDir ("{0}-{1}-{2}.log" -f $stamp, ($Branch -replace '[\\/]', '-'), $sha7)
    Write-Utf8 $script:LogPath ("ci-check {0} @ {1} — {2}`r`n" -f $Branch, $sha, (Get-Date -Format 's'))

    # Worktree riêng ngoài repo; tạo một lần, các lần sau checkout --detach lại đúng commit của nhánh.
    if (-not (Test-Path (Join-Path $Worktree '.git'))) {
        & git -C $Repo worktree prune 2>$null
        $code = Invoke-Step 'worktree-add' $Repo "git worktree add --detach `"$Worktree`" $sha" 5
    } else {
        $code = Invoke-Step 'checkout' $Worktree "git checkout --detach --force $sha && git clean -fdq" 5
    }
    if ($code -ne 0) { throw "không chuẩn bị được worktree $Worktree (xem $script:LogPath)" }

    $backend = Join-Path $Worktree 'holocron\backend'
    # Gói cài editable trỏ về repo chính, nên đặt PYTHONPATH=. để import code của worktree.
    $env:PYTHONPATH = '.'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONDONTWRITEBYTECODE = '1'

    $steps = @(
        @{ name = 'ruff-check';  cmd = "`"$Python`" -m ruff check ." },
        @{ name = 'ruff-format'; cmd = "`"$Python`" -m ruff format --check ." },
        @{ name = 'mypy';        cmd = "`"$Python`" -m mypy --strict holocron_core" },
        @{ name = 'pytest';      cmd = "`"$Python`" -m pytest -q --tb=short -p no:cacheprovider" }
    )
    $failed = @()
    foreach ($s in $steps) {
        $code = Invoke-Step $s.name $backend $s.cmd $StepTimeoutMinutes
        if ($code -ne 0) { $failed += $s.name }
    }
    $ok = ($failed.Count -eq 0)

    $summaryFile = $null
    if (-not $ok -and -not $NoSummary) {
        $tailPath = Join-Path $LogRoot 'ci-tail.txt'
        $tail = Get-Content -Encoding UTF8 -Tail 150 $script:LogPath
        Write-Utf8 $tailPath (($tail -join "`r`n") + "`r`n")
        $prompt = 'Day la duoi log cua vong kiem CI (ruff, mypy, pytest) cho repo Holocron. Tom loi goc bang tieng Viet, toi da 10 dong: buoc hong, test hoac file:dong, thong diep loi. Bo qua loi day chuyen. Khong de xuat sua neu log khong du.'
        $claudeCmd = "claude -p `"$prompt`" --model haiku --no-session-persistence --strict-mcp-config --tools `"`" < `"$tailPath`" > `"$SummaryPath`" 2>&1"
        # Chạy từ thư mục ngoài repo để không nạp CLAUDE.md hay settings của dự án.
        $proc = Start-Process -FilePath 'cmd.exe' -ArgumentList "/d /s /c `"cd /d `"$LogRoot`" && $claudeCmd`"" -NoNewWindow -PassThru
        $null = $proc.Handle
        if ($proc.WaitForExit(5 * 60 * 1000) -and $proc.ExitCode -eq 0) { $summaryFile = $SummaryPath }
        else {
            if (-not $proc.HasExited) { & taskkill.exe /T /F /PID $proc.Id | Out-Null }
            Add-Log '===== claude -p tóm lỗi: không thành công (xem ci-summary.txt)'
        }
    }

    $status = [ordered]@{
        branch       = $Branch
        sha          = $sha
        ok           = $ok
        failed_step  = $(if ($ok) { $null } else { $failed[0] })
        failed_steps = $failed
        log          = $script:LogPath
        summary      = $summaryFile
        checked_at   = (Get-Date -Format 's')
    }
    Write-Utf8 $StatusPath ($status | ConvertTo-Json)

    # Giữ 50 log gần nhất.
    Get-ChildItem $RunsDir -Filter '*.log' | Sort-Object LastWriteTime -Descending | Select-Object -Skip 50 | Remove-Item -Force

    Write-Output ("ci-check: {0} @ {1} ok={2}{3}" -f $Branch, $sha7, $ok, $(if ($ok) { '' } else { " hỏng: $($failed -join ', ')" }))
    if ($ok) { exit 0 } else { exit 1 }
}
catch {
    Write-Output "ci-check: lỗi — $($_.Exception.Message)"
    exit 3
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
