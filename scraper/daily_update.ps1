# 매일 매물 수집 + GitHub Pages 자동 배포
# 작업 스케줄러(MNA-99co-scraper)에서 매일 07:00 실행됨

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$logDir = Join-Path $repoRoot "scraper\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("update_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

function Log($msg) { $msg | Tee-Object -FilePath $logFile -Append }

Log "===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 수집 시작 ====="

py -3 scraper\scrape_99co.py 2>&1 | ForEach-Object { Log $_ }
if ($LASTEXITCODE -ne 0) {
    Log "스크래퍼 실패 (exit $LASTEXITCODE) - push 생략"
    exit 1
}

$status = git status --porcelain
if ([string]::IsNullOrWhiteSpace($status)) {
    Log "변경 사항 없음 - push 생략"
    exit 0
}

git add js/live_data.js 2>&1 | ForEach-Object { Log $_ }
git commit -m "chore: 매물 데이터 자동 갱신 ($(Get-Date -Format 'yyyy-MM-dd'))" 2>&1 | ForEach-Object { Log $_ }
git push origin main 2>&1 | ForEach-Object { Log $_ }

if ($LASTEXITCODE -eq 0) {
    Log "push 완료"
} else {
    Log "push 실패 (exit $LASTEXITCODE) - 로그 확인 필요"
}
