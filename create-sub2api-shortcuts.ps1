<#
.SYNOPSIS
    Create desktop shortcuts and icons for the Codex Sub2API helper.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$Desktop = [Environment]::GetFolderPath("Desktop")
$Root = "C:\Users\24668\Desktop\auro-reg"
$Script = Join-Path $Root "ensure-codex-sub2api.ps1"
$IconDir = Join-Path $Root "icons"
New-Item -ItemType Directory -Force -Path $IconDir | Out-Null

function New-IconPng {
    param(
        [string]$Path,
        [ValidateSet("start", "check", "fix")]
        [string]$Mode
    )

    $size = 256
    $bmp = [System.Drawing.Bitmap]::new($size, $size)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.Clear([System.Drawing.Color]::Transparent)

    $rect = [System.Drawing.Rectangle]::new(14, 14, 228, 228)
    $pathObj = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $pathObj.AddEllipse($rect)

    if ($Mode -eq "start") {
        $c1 = [System.Drawing.Color]::FromArgb(36, 186, 118)
        $c2 = [System.Drawing.Color]::FromArgb(18, 116, 214)
    } elseif ($Mode -eq "fix") {
        $c1 = [System.Drawing.Color]::FromArgb(245, 158, 11)
        $c2 = [System.Drawing.Color]::FromArgb(225, 29, 72)
    } else {
        $c1 = [System.Drawing.Color]::FromArgb(34, 139, 230)
        $c2 = [System.Drawing.Color]::FromArgb(115, 76, 242)
    }
    $brush = [System.Drawing.Drawing2D.LinearGradientBrush]::new($rect, $c1, $c2, 45.0)
    $border = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(245, 255, 255, 255), 7.0)
    $g.FillPath($brush, $pathObj)
    $g.DrawEllipse($border, $rect)

    $fontSmall = [System.Drawing.Font]::new("Segoe UI", 28, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $fontBig = [System.Drawing.Font]::new("Segoe UI", 62, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $white = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)
    $muted = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(230, 255, 255, 255))
    $centerFmt = [System.Drawing.StringFormat]::new()
    $centerFmt.Alignment = [System.Drawing.StringAlignment]::Center
    $centerFmt.LineAlignment = [System.Drawing.StringAlignment]::Center

    $g.DrawString("S2A", $fontBig, $white, [System.Drawing.RectangleF]::new(0, 44, 256, 80), $centerFmt)

    if ($Mode -eq "start") {
        $tri = [System.Drawing.Point[]]@(
            [System.Drawing.Point]::new(104, 128),
            [System.Drawing.Point]::new(104, 192),
            [System.Drawing.Point]::new(164, 160)
        )
        $g.FillPolygon($white, $tri)
        $g.DrawString("START", $fontSmall, $muted, [System.Drawing.RectangleF]::new(0, 194, 256, 34), $centerFmt)
    } elseif ($Mode -eq "check") {
        $pen = [System.Drawing.Pen]::new([System.Drawing.Color]::White, 13.0)
        $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
        $g.DrawLine($pen, 78, 160, 116, 196)
        $g.DrawLine($pen, 116, 196, 182, 124)
        $g.DrawString("CHECK", $fontSmall, $muted, [System.Drawing.RectangleF]::new(0, 194, 256, 34), $centerFmt)
        $pen.Dispose()
    } else {
        $pen = [System.Drawing.Pen]::new([System.Drawing.Color]::White, 12.0)
        $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
        $g.DrawLine($pen, 82, 182, 156, 108)
        $g.DrawEllipse($pen, [System.Drawing.Rectangle]::new(148, 86, 36, 36))
        $g.DrawLine($pen, 72, 192, 96, 216)
        $g.DrawString("FIX", $fontSmall, $muted, [System.Drawing.RectangleF]::new(0, 194, 256, 34), $centerFmt)
        $pen.Dispose()
    }

    $bmp.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    $centerFmt.Dispose()
    $muted.Dispose()
    $white.Dispose()
    $fontBig.Dispose()
    $fontSmall.Dispose()
    $border.Dispose()
    $brush.Dispose()
    $pathObj.Dispose()
    $g.Dispose()
    $bmp.Dispose()
}

function Convert-PngToIco {
    param([string]$PngPath, [string]$IcoPath)

    $pngBytes = [System.IO.File]::ReadAllBytes($PngPath)
    $fs = [System.IO.FileStream]::new($IcoPath, [System.IO.FileMode]::Create)
    try {
        $bw = [System.IO.BinaryWriter]::new($fs)
        $bw.Write([UInt16]0)
        $bw.Write([UInt16]1)
        $bw.Write([UInt16]1)
        $bw.Write([Byte]0)
        $bw.Write([Byte]0)
        $bw.Write([Byte]0)
        $bw.Write([Byte]0)
        $bw.Write([UInt16]1)
        $bw.Write([UInt16]32)
        $bw.Write([UInt32]$pngBytes.Length)
        $bw.Write([UInt32]22)
        $bw.Write($pngBytes)
        $bw.Flush()
        $bw.Dispose()
    } finally {
        $fs.Dispose()
    }
}

function New-Shortcut {
    param(
        [string]$Name,
        [string]$ExtraArgs,
        [string]$Icon
    )

    $lnkPath = Join-Path $Desktop ($Name + ".lnk")
    $wsh = New-Object -ComObject WScript.Shell
    $sc = $wsh.CreateShortcut($lnkPath)
    $sc.TargetPath = "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    $sc.Arguments = "-NoExit -ExecutionPolicy Bypass -File `"$Script`" $ExtraArgs"
    $sc.WorkingDirectory = $Root
    $sc.IconLocation = "$Icon,0"
    $sc.Description = "Codex local Sub2API gateway helper"
    $sc.Save()
    return $lnkPath
}

$startPng = Join-Path $IconDir "sub2api-start.png"
$checkPng = Join-Path $IconDir "sub2api-check.png"
$fixPng = Join-Path $IconDir "sub2api-fix.png"
$startIco = Join-Path $IconDir "sub2api-start.ico"
$checkIco = Join-Path $IconDir "sub2api-check.ico"
$fixIco = Join-Path $IconDir "sub2api-fix.ico"

New-IconPng -Path $startPng -Mode start
New-IconPng -Path $checkPng -Mode check
New-IconPng -Path $fixPng -Mode fix
Convert-PngToIco -PngPath $startPng -IcoPath $startIco
Convert-PngToIco -PngPath $checkPng -IcoPath $checkIco
Convert-PngToIco -PngPath $fixPng -IcoPath $fixIco

$startLink = New-Shortcut -Name "启动 Codex Sub2API 中转站" -ExtraArgs "-Start" -Icon $startIco
$checkLink = New-Shortcut -Name "检测 Codex Sub2API 中转站" -ExtraArgs "" -Icon $checkIco
$fixLink = New-Shortcut -Name "修复 Codex Sub2API 配置" -ExtraArgs "-FixCodexConfig" -Icon $fixIco

Write-Host "created: $startLink"
Write-Host "created: $checkLink"
Write-Host "created: $fixLink"
Write-Host "icons: $startIco"
Write-Host "icons: $checkIco"
Write-Host "icons: $fixIco"
