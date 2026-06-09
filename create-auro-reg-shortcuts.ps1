<#
.SYNOPSIS
    Create desktop shortcuts and icons for starting/stopping auro-reg.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$Desktop = [Environment]::GetFolderPath("Desktop")
$Root = "C:\Users\24668\Desktop\auro-reg"
$RunAll = Join-Path $Root "run-all.ps1"
$IconDir = Join-Path $Root "icons"
New-Item -ItemType Directory -Force -Path $IconDir | Out-Null

function New-AuroIconPng {
    param(
        [string]$Path,
        [ValidateSet("start", "stop", "auto")]
        [string]$Mode
    )

    $size = 256
    $bmp = [System.Drawing.Bitmap]::new($size, $size)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.Clear([System.Drawing.Color]::Transparent)

    $shadowRect = [System.Drawing.Rectangle]::new(23, 25, 210, 210)
    $mainRect = [System.Drawing.Rectangle]::new(18, 16, 220, 220)
    $round = [System.Drawing.Drawing2D.GraphicsPath]::new()
    $round.AddArc($mainRect.X, $mainRect.Y, 52, 52, 180, 90)
    $round.AddArc($mainRect.Right - 52, $mainRect.Y, 52, 52, 270, 90)
    $round.AddArc($mainRect.Right - 52, $mainRect.Bottom - 52, 52, 52, 0, 90)
    $round.AddArc($mainRect.X, $mainRect.Bottom - 52, 52, 52, 90, 90)
    $round.CloseFigure()

    if ($Mode -eq "start") {
        $c1 = [System.Drawing.Color]::FromArgb(15, 157, 132)
        $c2 = [System.Drawing.Color]::FromArgb(28, 111, 220)
        $label = "START"
    } elseif ($Mode -eq "auto") {
        $c1 = [System.Drawing.Color]::FromArgb(250, 204, 21)
        $c2 = [System.Drawing.Color]::FromArgb(22, 163, 74)
        $label = "AUTO"
    } else {
        $c1 = [System.Drawing.Color]::FromArgb(225, 29, 72)
        $c2 = [System.Drawing.Color]::FromArgb(124, 58, 237)
        $label = "STOP"
    }

    $shadow = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(50, 0, 0, 0))
    $g.FillEllipse($shadow, $shadowRect)

    $brush = [System.Drawing.Drawing2D.LinearGradientBrush]::new($mainRect, $c1, $c2, 35.0)
    $border = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(245, 255, 255, 255), 6.0)
    $g.FillPath($brush, $round)
    $g.DrawPath($border, $round)

    $white = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)
    $muted = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(225, 255, 255, 255))
    $fontBig = [System.Drawing.Font]::new("Segoe UI", 62, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $fontSmall = [System.Drawing.Font]::new("Segoe UI", 25, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $fmt = [System.Drawing.StringFormat]::new()
    $fmt.Alignment = [System.Drawing.StringAlignment]::Center
    $fmt.LineAlignment = [System.Drawing.StringAlignment]::Center

    $g.DrawString("AR", $fontBig, $white, [System.Drawing.RectangleF]::new(0, 36, 256, 80), $fmt)

    if ($Mode -eq "start") {
        $tri = [System.Drawing.Point[]]@(
            [System.Drawing.Point]::new(103, 123),
            [System.Drawing.Point]::new(103, 184),
            [System.Drawing.Point]::new(163, 154)
        )
        $g.FillPolygon($white, $tri)
    } elseif ($Mode -eq "auto") {
        $pen = [System.Drawing.Pen]::new([System.Drawing.Color]::White, 11.0)
        $pen.StartCap = [System.Drawing.Drawing2D.LineCap]::Round
        $pen.EndCap = [System.Drawing.Drawing2D.LineCap]::Round
        $g.DrawArc($pen, [System.Drawing.Rectangle]::new(82, 120, 84, 60), 35, 260)
        $arrow = [System.Drawing.Point[]]@(
            [System.Drawing.Point]::new(167, 120),
            [System.Drawing.Point]::new(186, 134),
            [System.Drawing.Point]::new(164, 143)
        )
        $g.FillPolygon($white, $arrow)
        $pen.Dispose()
    } else {
        $square = [System.Drawing.Rectangle]::new(98, 126, 60, 60)
        $g.FillRectangle($white, $square)
    }
    $g.DrawString($label, $fontSmall, $muted, [System.Drawing.RectangleF]::new(0, 196, 256, 32), $fmt)

    $bmp.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)

    $fmt.Dispose()
    $fontSmall.Dispose()
    $fontBig.Dispose()
    $muted.Dispose()
    $white.Dispose()
    $border.Dispose()
    $brush.Dispose()
    $shadow.Dispose()
    $round.Dispose()
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
    $sc.Arguments = "-NoExit -ExecutionPolicy Bypass -File `"$RunAll`" $ExtraArgs"
    $sc.WorkingDirectory = $Root
    $sc.IconLocation = "$Icon,0"
    $sc.Description = "auro-reg launcher"
    $sc.Save()
    return $lnkPath
}

if (-not (Test-Path -LiteralPath $RunAll)) {
    throw "run-all.ps1 not found: $RunAll"
}

$startPng = Join-Path $IconDir "auro-reg-start.png"
$stopPng = Join-Path $IconDir "auro-reg-stop.png"
$autoPng = Join-Path $IconDir "auro-reg-auto.png"
$startIco = Join-Path $IconDir "auro-reg-start.ico"
$stopIco = Join-Path $IconDir "auro-reg-stop.ico"
$autoIco = Join-Path $IconDir "auro-reg-auto.ico"

New-AuroIconPng -Path $startPng -Mode start
New-AuroIconPng -Path $stopPng -Mode stop
New-AuroIconPng -Path $autoPng -Mode auto
Convert-PngToIco -PngPath $startPng -IcoPath $startIco
Convert-PngToIco -PngPath $stopPng -IcoPath $stopIco
Convert-PngToIco -PngPath $autoPng -IcoPath $autoIco

$startLink = New-Shortcut -Name "一键启动 auro-reg" -ExtraArgs "" -Icon $startIco
$stopLink = New-Shortcut -Name "一键关闭 auro-reg" -ExtraArgs "-Stop" -Icon $stopIco
$autoLink = New-Shortcut -Name "一键自动跑 auro-reg" -ExtraArgs "-Auto" -Icon $autoIco

Write-Host "created: $startLink"
Write-Host "created: $stopLink"
Write-Host "created: $autoLink"
Write-Host "icons: $startIco"
Write-Host "icons: $stopIco"
Write-Host "icons: $autoIco"
