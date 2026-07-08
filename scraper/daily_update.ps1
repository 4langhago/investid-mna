# 매일 매물 수집 + GitHub Pages 자동 배포
# 작업 스케줄러(MNA-99co-scraper)에서 매일 07:00 실행됨

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$logDir = Join-Path $repoRoot "scraper\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("update_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

try {
    "===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 수집 시작 =====" | Tee-Object -FilePath $logFile -Append

    py -3 scraper\scrape_99co.py 2>&1 | Tee-Object -FilePath $logFile -Append

    $status = git status --porcelain
    if ([string]::IsNullOrWhiteSpace($status)) {
        "변경 사항 없음 - push 생략" | Tee-Object -FilePath $logFile -Append
    } else {
        git add js/live_data.js 2>&1 | Tee-Object -FilePath $logFile -Append
        git commit -m "chore: 매물 데이터 자동 갱신 ($(Get-Date -Format 'yyyy-MM-dd'))" 2>&1 | Tee-Object -FilePath $logFile -Append
        git push origin main 2>&1 | Tee-Object -FilePath $logFile -Append
        "push 완료" | Tee-Object -FilePath $logFile -Append
    }
} catch {
    "오류 발생: $_" | Tee-Object -FilePath $logFile -Append
}
