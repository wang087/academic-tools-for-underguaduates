param(
    [Parameter(Mandatory=$true)][string]$Path,
    [string]$Lang = "en-US"
)
# Windows 自带 OCR（Windows.Media.Ocr）——无第三方依赖，离线可用。
# 输出：每行一个 JSON（UTF-8），含整行文本与逐词外接矩形（像素坐标）。
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]
function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

[Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null
[Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime] | Out-Null

$full = (Resolve-Path -LiteralPath $Path).Path
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($full)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

$engine = $null
try { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage((New-Object Windows.Globalization.Language $Lang)) } catch {}
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $engine) { Write-Error "no OCR engine available"; exit 2 }

$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

$out = New-Object System.Collections.ArrayList
foreach ($line in $result.Lines) {
    $words = @()
    foreach ($w in $line.Words) {
        $r = $w.BoundingRect
        $words += [ordered]@{ t = $w.Text; x = [math]::Round($r.X,1); y = [math]::Round($r.Y,1); w = [math]::Round($r.Width,1); h = [math]::Round($r.Height,1) }
    }
    $null = $out.Add([ordered]@{ text = $line.Text; words = $words })
}
$pw = $bitmap.PixelWidth; $ph = $bitmap.PixelHeight
[ordered]@{ width = $pw; height = $ph; lines = $out } | ConvertTo-Json -Depth 8 -Compress
