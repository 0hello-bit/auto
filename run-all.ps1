<#
.SYNOPSIS
    一键启动整套系统 / One-click launcher.

    启动并自检以下组件 / starts & health-checks:
      1. 项目专用 Chrome（CDP 9222，独立 profile）
      2. 项目 A：邮箱验证码服务 owned-mail-code-service (127.0.0.1:5050)
      3. 项目 B：网页注册 + Sub2API 导入 web-register-sub2api-automation (127.0.0.1:5060)
      4. 检查 Sub2API (127.0.0.1:8080，需你本机已有服务监听)

    已在运行的组件不会重复启动（按端口判断）。项目 A / B 各开一个窗口显示日志。

.PARAMETER Stop
    只关闭项目 A / B 的 python 进程（不关 Chrome / Sub2API）。

.PARAMETER Auto
    服务就绪后，自动同步 emails.txt（从项目A accounts.txt）并启动「按邮箱顺序取号、
    并行执行」的流式自动化批处理（POST /api/auto/run-batch）。

.EXAMPLE
    .\run-all.ps1            # 启动全部服务 / start everything
    .\run-all.ps1 -Auto      # 启动服务 + 自动跑完整流程（流式并行邮箱）
    .\run-all.ps1 -Stop      # 关闭项目 A / B
#>
[CmdletBinding()]
param(
    [switch]$Stop,
    [switch]$Auto
)

$ErrorActionPreference = "Stop"
# 让管道/输出按 UTF-8 处理，避免中文乱码 / keep output UTF-8.
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

# ---- 路径配置（按需修改）/ paths ----
$ROOT           = $PSScriptRoot
$ROOT_A         = Join-Path $ROOT "owned-mail-code-service"
$ROOT_B         = Join-Path $ROOT "web-register-sub2api-automation"
$CHROME         = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$CHROME_PROFILE = "D:\chrome-sub2api-automation-profile"
$CDP_PORT       = 9222
$SUB2API_DIR    = "C:\Users\24668\Documents\Codex\2026-06-05\wei-shaw-sub2api-https-github-com\work\sub2api-win"
$B_PORT         = 5060
$A_PORT         = 5050
$PYTHON         = "D:\py\python.exe"

function Test-Port([int]$port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $iar = $c.BeginConnect("127.0.0.1", $port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(800)
        if ($ok -and $c.Connected) { $c.Close(); return $true }
        $c.Close(); return $false
    } catch { return $false }
}

function Venv-Python([string]$root) {
    $p = Join-Path $root ".venv\Scripts\python.exe"
    if (Test-Path $p) { return $p }
    if (Test-Path $PYTHON) { return $PYTHON }
    return "python"
}

# 从 .env 读取某个 KEY 的值（用于 -Auto 调 API）/ read a KEY from a .env file.
function Get-EnvValue([string]$path, [string]$key) {
    if (-not (Test-Path $path)) { return $null }
    foreach ($line in (Get-Content -LiteralPath $path)) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith("#") -or -not $t.Contains("=")) { continue }
        $idx = $t.IndexOf("=")
        $k = $t.Substring(0, $idx).Trim()
        if ($k -eq $key) { return $t.Substring($idx + 1).Trim() }
    }
    return $null
}

# 调本机 API（绕过系统代理，兼容 PS5.1 / PS7）/ call a local API, bypassing proxy.
function Invoke-LocalApi([string]$url, [string]$method, $headers, [string]$body) {
    $p = @{ Uri = $url; Method = $method; Headers = $headers; TimeoutSec = 120 }
    if ($body) { $p["Body"] = $body }
    if ($PSVersionTable.PSVersion.Major -ge 6) { $p["NoProxy"] = $true }
    return Invoke-RestMethod @p
}

# ---- 关闭模式 / stop mode ----
if ($Stop) {
    Write-Host "==> 关闭项目 A / B 的 python 进程..." -ForegroundColor Yellow
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -match "app\.main" } |
        ForEach-Object {
            Write-Host ("    stop PID {0}" -f $_.ProcessId)
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
    Write-Host "完成（Chrome 与 Sub2API 未关闭）/ done (Chrome and Sub2API left running)." -ForegroundColor Green
    return
}

Write-Host "===== 一键启动 / Starting system =====" -ForegroundColor Cyan

# ---- 1. Chrome (CDP) ----
if (Test-Port $CDP_PORT) {
    Write-Host "[1/4] Chrome CDP 已在 $CDP_PORT 运行，跳过 / already running." -ForegroundColor Green
} elseif (-not (Test-Path $CHROME)) {
    Write-Warning ("[1/4] 找不到 Chrome: {0} —— 请修改脚本顶部的 CHROME 路径。" -f $CHROME)
} else {
    Write-Host "[1/4] 启动项目专用 Chrome（独立 profile，CDP $CDP_PORT）..." -ForegroundColor Cyan
    Start-Process -FilePath $CHROME -ArgumentList @(
        "--remote-debugging-port=$CDP_PORT",
        "--user-data-dir=$CHROME_PROFILE",
        "--no-first-run",
        "--no-default-browser-check",
        "about:blank"
    )
}

# ---- 2. 项目 A：邮箱验证码服务 (5050) ----
if (Test-Port $A_PORT) {
    Write-Host "[2/4] 项目 A 已在 $A_PORT 运行，跳过 / already running." -ForegroundColor Green
} elseif (-not (Test-Path $ROOT_A)) {
    Write-Warning ("[2/4] 找不到项目 A: {0}" -f $ROOT_A)
} else {
    Write-Host "[2/4] 启动项目 A（新窗口，日志可见）..." -ForegroundColor Cyan
    $pyA = Venv-Python $ROOT_A
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command",
        "Set-Location '$ROOT_A'; Write-Host 'mail-code-service :$A_PORT' -ForegroundColor Cyan; & '$pyA' -m app.main"
    )
}

# ---- 3. 项目 B：注册 + 导入 (5060) ----
if (Test-Port $B_PORT) {
    Write-Host "[3/4] 项目 B 已在 $B_PORT 运行，跳过 / already running." -ForegroundColor Green
} elseif (-not (Test-Path $ROOT_B)) {
    Write-Warning ("[3/4] 找不到项目 B: {0}" -f $ROOT_B)
} else {
    Write-Host "[3/4] 启动项目 B（新窗口，日志可见）..." -ForegroundColor Cyan
    $pyB = Venv-Python $ROOT_B
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-Command",
        "Set-Location '$ROOT_B'; Write-Host 'register-sub2api :$B_PORT' -ForegroundColor Cyan; & '$pyB' -m app.main"
    )
}

# ---- 4. Sub2API (8080，local exe) ----
if (Test-Port 8080) {
    Write-Host "[4/4] Sub2API 已在 8080 运行 / running." -ForegroundColor Green
} else {
    $sub2ApiExe = if ($SUB2API_DIR) { Join-Path $SUB2API_DIR "sub2api.exe" } else { "" }
    if ($sub2ApiExe -and (Test-Path $sub2ApiExe)) {
        Write-Warning ("[4/4] Sub2API 未运行。请双击桌面「启动 Codex Sub2API 中转站」，或执行: cd '{0}'; .\start_noproxy.bat" -f $SUB2API_DIR)
    } else {
        Write-Warning "[4/4] Sub2API 未运行。请先启动你本机的 Sub2API，并确认 127.0.0.1:8080 可访问。"
    }
}

# ---- 健康自检 / health summary ----
Write-Host "`n等待服务就绪 / waiting for services ..." -ForegroundColor Cyan
foreach ($i in 1..30) {
    if ((Test-Port $A_PORT) -and (Test-Port $B_PORT) -and (Test-Port $CDP_PORT)) { break }
    Start-Sleep -Seconds 1
}
Write-Host "`n===== 状态 / Status =====" -ForegroundColor Cyan
"{0,-26} {1}" -f "Chrome CDP   :$CDP_PORT", $(if (Test-Port $CDP_PORT) { "OK" } else { "DOWN" })
"{0,-26} {1}" -f "项目A 邮箱码 :$A_PORT",   $(if (Test-Port $A_PORT)   { "OK" } else { "DOWN" })
"{0,-26} {1}" -f "项目B 注册导入:$B_PORT",  $(if (Test-Port $B_PORT)   { "OK" } else { "DOWN" })
"{0,-26} {1}" -f "Sub2API      :8080",      $(if (Test-Port 8080)      { "OK" } else { "DOWN" })

if (-not (Test-Port $B_PORT)) {
    Write-Warning "项目 B 未就绪：请看刚弹出的「register-sub2api」窗口里的报错日志。"
}

# ---- 自动并行流式执行 / auto parallel streaming batch ----
if ($Auto) {
    Write-Host "`n===== 自动并行流式执行 / Auto parallel streaming batch =====" -ForegroundColor Cyan
    if (-not (Test-Port $B_PORT)) {
        Write-Warning "项目 B 未运行，无法启动自动化。请先排查 B 窗口日志。"
        return
    }
    if (-not (Test-Port 8080)) {
        Write-Warning "Sub2API(8080) 未运行：导入会失败（注册仍会成功并保留为 registered，可稍后续传）。"
    }

    $apiKey = Get-EnvValue (Join-Path $ROOT_B ".env") "API_KEY"
    if (-not $apiKey) {
        Write-Warning "读不到项目 B .env 的 API_KEY，无法调用 API。"
        return
    }
    $headers = @{ "x-api-key" = $apiKey; "Content-Type" = "application/json" }
    $base = "http://127.0.0.1:$B_PORT"

    try {
        Write-Host "==> 同步 emails.txt（从 accounts.txt，只保留未接入 Sub2API 的邮箱）..." -ForegroundColor Cyan
        $sync = Invoke-LocalApi "$base/api/emails/sync" "Post" $headers $null
        $kept = $sync.data.kept_count
        $skipped = $sync.data.skipped_count
        Write-Host ("    保留 {0} 个，跳过 {1} 个（已导入/已用/不可用）。" -f $kept, $skipped) -ForegroundColor Green
        if ($sync.data.kept) { $sync.data.kept | ForEach-Object { Write-Host ("      - {0}" -f $_) } }

        $batchPayload = @{ sync = $false }
        $parallelismValue = Get-EnvValue (Join-Path $ROOT_B ".env") "BATCH_PARALLELISM"
        $parallelismLabel = "服务默认"
        if ($parallelismValue) {
            $parallelismNumber = 0
            if ([int]::TryParse($parallelismValue, [ref]$parallelismNumber) -and $parallelismNumber -gt 0) {
                $batchPayload["parallelism"] = $parallelismNumber
                $parallelismLabel = $parallelismNumber
            } else {
                $parallelismLabel = "$parallelismValue（无效，服务默认）"
            }
        }

        Write-Host ("==> 启动并行流式批处理（按邮箱顺序取号，最多 {0} 个邮箱同时执行）..." -f $parallelismLabel) -ForegroundColor Cyan
        $body = $batchPayload | ConvertTo-Json
        $run = Invoke-LocalApi "$base/api/auto/run-batch" "Post" $headers $body
        $batchId = $run.data.batch_id
        Write-Host ("    batch_id = {0}" -f $batchId) -ForegroundColor Green
        if ($run.data.parallelism) {
            Write-Host ("    parallelism = {0}" -f $run.data.parallelism) -ForegroundColor Green
        }
        Write-Host "    实时进度看「register-sub2api」窗口日志；或查询：" -ForegroundColor DarkGray
        Write-Host ("      irm '{0}/api/auto/batches/{1}' -Headers @{{ 'x-api-key'='{2}' }} | ConvertTo-Json -Depth 6" -f $base, $batchId, $apiKey) -ForegroundColor DarkGray
        Write-Host ("      irm '{0}/api/emails' -Headers @{{ 'x-api-key'='{1}' }} | ConvertTo-Json -Depth 6" -f $base, $apiKey) -ForegroundColor DarkGray
    } catch {
        Write-Warning ("自动化调用失败: {0}" -f $_.Exception.Message)
    }
    return
}

Write-Host "`n启动完成：现在只是服务就绪，不会自动注册、接码、买号或导入。" -ForegroundColor Green
Write-Host "如果要开始自动跑完整邮箱池，请在本目录执行：.\run-all.ps1 -Auto" -ForegroundColor Cyan
Write-Host "如果只想单个邮箱启动/续传，请用项目 B 的 /api/auto/start 接口。" -ForegroundColor Cyan
Write-Host ("文档：{0}" -f (Join-Path $ROOT "操作文档.md")) -ForegroundColor DarkGray
