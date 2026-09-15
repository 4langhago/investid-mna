# 매일 매물 수집 + GitHub Pages 자동 배포
# 작업 스케줄러(MNA-99co-scraper)에서 매일 07:00 실행됨
#
# 2026-09-15: 99.co 와 tempat-usaha.com(scrape_business.py) 이 GitHub Actions
# 러너(데이터센터 IP)에서는 403으로 막히지만, 이 PC(가정용 회선)에서는 정상 응답한다
# (같은 요청을 로컬에서 재현해 확인함). 그래서 두 소스는 로컬 스케줄 작업으로 수집해
# 커밋 → GitHub Actions 워크플로가 그 커밋을 그대로 배포하게 한다.
# OLX(scrape_olx.py)는 이 PC에서도 연결이 끊기거나 타임아웃돼 로컬 우회가 통하지 않는다
# (데이터센터 IP 차단이 아니라 더 넓은 차단으로 보인다) — 여기 포함하지 않는다.

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$logDir = Join-Path $repoRoot "scraper\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("update_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

function Log($msg) { $msg | Tee-Object -FilePath $logFile -Append }

# Windows PowerShell 5.1 은 외부 프로그램 출력을 시스템 코드페이지(CP949)로 읽는다.
# 파이썬이 UTF-8 로 찍는 한글이 로그에서 전부 깨져 결과 건수를 읽을 수 없었다.
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# git 은 진행 메시지("From ...", "To ...")를 stderr 로 내보낸다. 2>&1 로 받으면 5.1 이
# 정상 메시지까지 NativeCommandError(빨간 오류)로 감싸 로그에 실패처럼 남는다.
# 오류 레코드를 문자열로 풀어서 기록하고, 성공 여부는 종료 코드로만 판단한다.
function LogNative { process { Log ($(if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { $_ })) } }

Log "===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') 수집 시작 ====="

# 두 스크래퍼는 서로 독립적이다 — 하나가 실패(0건)해도 다른 하나는 계속 시도한다.
# 각 스크립트 자체가 0건이면 기존 js/*.js를 덮어쓰지 않으므로 여기서 별도 롤백은 불필요.
Log "-- 99.co 수집 --"
py -3 scraper\scrape_99co.py 2>&1 | LogNative
$co99Ok = ($LASTEXITCODE -eq 0)
Log $(if ($co99Ok) { "99.co: 성공" } else { "99.co: 실패 (exit $LASTEXITCODE)" })

Log "-- tempat-usaha.com 수집 --"
py -3 scraper\scrape_business.py 2>&1 | LogNative
$businessOk = ($LASTEXITCODE -eq 0)
Log $(if ($businessOk) { "tempat-usaha.com: 성공" } else { "tempat-usaha.com: 실패 (exit $LASTEXITCODE)" })

if (-not $co99Ok -and -not $businessOk) {
    Log "두 소스 모두 실패 - push 생략"
    exit 1
}

$status = git status --porcelain -- js/live_data.js js/business_data.js
if ([string]::IsNullOrWhiteSpace($status)) {
    Log "변경 사항 없음 - push 생략"
    exit 0
}

# 이 PC 가 main 이 아닌 작업 브랜치에 있으면 수집 커밋이 엉뚱한 브랜치로 들어간다.
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -ne "main") {
    Log "현재 브랜치가 main 이 아님($branch) - push 생략"
    exit 1
}

git add js/live_data.js js/business_data.js 2>&1 | LogNative
git commit -m "chore: auto-update listings ($(Get-Date -Format 'yyyy-MM-dd'))" 2>&1 | LogNative
# GitHub Actions 도 매일 같은 브랜치에 수집 커밋을 올린다. 먼저 받아오지 않으면
# push 가 non-fast-forward 로 거절돼 이 PC 에서 모은 데이터가 올라가지 않는다.
git pull --rebase --autostash origin main 2>&1 | LogNative
git push origin main 2>&1 | LogNative

if ($LASTEXITCODE -eq 0) {
    Log "push 완료"
} else {
    Log "push 실패 (exit $LASTEXITCODE) - 로그 확인 필요"
}
