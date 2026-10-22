# One-shot real run on the club clip (Windows PowerShell), from the repo folder:
#   powershell -ExecutionPolicy Bypass -File scripts\run_real.ps1
# Needs .env (OPENAI_API_KEY), data\videos\"5min .mp4" and a sponsor CSV. The reference labels
# (data\gold\reference_intervals.csv) are optional: without them only the measured seconds and cost are produced.
# Spends at most $MaxUsd on the vision API. Writes data\runs\<Name>\ and a run record in eval\runs\.
param(
    [string]$Video = "data\videos\5min .mp4",
    [string]$Sponsors = "data\gold\sponsors.reference.csv",
    [string]$Gold = "data\gold\reference_intervals.csv",
    [double]$Fps = 1,
    [double]$MaxUsd = 1.0,
    [string]$Name = "gpt-5min"
)
$ErrorActionPreference = "Stop"
$py = ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

& $py -m pip install -q "openai>=1.50" "anthropic>=0.40" "opencv-python-headless>=4.10" "pillow>=10.4"
foreach ($f in @($Video, $Sponsors, ".env")) {
    if (-not (Test-Path -LiteralPath $f)) { Write-Error "missing file: $f" }
}

& $py -m vision.identify run --video $Video --sponsors $Sponsors --fps $Fps --max-usd $MaxUsd --name $Name --workers 8
if ($LASTEXITCODE -ne 0) { Write-Error "vision run failed" }

$out = "data\runs\$Name"
$summary = Get-Content "$out\summary.json" | ConvertFrom-Json
Write-Host "`nMeasured seconds per brand (RECORDED, $($summary.model), no accuracy check):"
$summary.brand_seconds.PSObject.Properties | ForEach-Object { "{0,-30} {1,8:N1} s" -f $_.Name, $_.Value }
Write-Host ("frames {0}, errors {1}, cost `${2} for {3} min -> `${4:N4} per match-minute" -f `
    $summary.frames, $summary.errors, $summary.cost_usd, $summary.minutes, ($summary.cost_usd / [math]::Max($summary.minutes, 0.001)))

if (-not (Test-Path -LiteralPath $Gold)) {
    Write-Host "`nNo reference labels at ${Gold}: skipped the accuracy run (eval.harness)."
    exit 0
}
$ids = (Import-Csv $Sponsors | ForEach-Object { $_.brand_id }) -join ","
& $py -m eval.harness --pred "$out\pred.json" --decisions "$out\decisions.json" --gold $Gold `
    --minutes $summary.minutes --name $Name --source RECORDED --sponsors $ids
if ($LASTEXITCODE -ne 0) { Write-Error "eval harness failed" }
Write-Host "`nDone. Run record is the newest file in eval\runs\. Cost: `$$($summary.cost_usd)"
