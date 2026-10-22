# One-shot real run on the club clip (Windows PowerShell), from the repo folder:
#   powershell -ExecutionPolicy Bypass -File scripts\run_real.ps1
# Needs .env (OPENAI_API_KEY), data\videos\"5min .mp4", data\gold\reference_intervals.csv and a sponsor CSV.
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
foreach ($f in @($Video, $Sponsors, $Gold, ".env")) {
    if (-not (Test-Path -LiteralPath $f)) { Write-Error "missing file: $f" }
}

& $py -m vision.identify run --video $Video --sponsors $Sponsors --fps $Fps --max-usd $MaxUsd --name $Name
if ($LASTEXITCODE -ne 0) { Write-Error "vision run failed" }

$out = "data\runs\$Name"
$summary = Get-Content "$out\summary.json" | ConvertFrom-Json
$ids = (Import-Csv $Sponsors | ForEach-Object { $_.brand_id }) -join ","
& $py -m eval.harness --pred "$out\pred.json" --decisions "$out\decisions.json" --gold $Gold `
    --minutes $summary.minutes --name $Name --source RECORDED --sponsors $ids
if ($LASTEXITCODE -ne 0) { Write-Error "eval harness failed" }
Write-Host "`nDone. Run record is the newest file in eval\runs\. Cost: `$$($summary.cost_usd)"
