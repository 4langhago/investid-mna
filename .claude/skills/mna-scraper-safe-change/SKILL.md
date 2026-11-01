---
name: mna-scraper-safe-change
description: investid-mna(PUBLIC 저장소, GitHub Actions 매일 자동 수집·커밋)의 스크래퍼(scraper/*.py), daily-scrape.yml 워크플로, 텔레그램 추천(telegram_recommend.py), js/*_data.js 데이터, docs/ 매물 문서를 수정하거나 커밋·푸시할 때 트리거. 작업 전 git pull, rate limit·0건 덮어쓰기 방지 유지, 차단 우회 금지, 공개 저장소 유출 점검을 다룬다.
---

# MNA Scraper Safe Change (investid-mna)

공개 저장소이고 Actions 가 매일 00:00 UTC 에 수집·커밋·푸시한다. 로컬 스케줄 작업 `MNA-99co-scraper` 도 매일 커밋한다.

## 1. 작업 시작 전

1. `git status --short --branch` 와 `git pull --ff-only`. 분기(diverged)로 실패하면 **임의로 rebase/merge/force 하지 말고** 사용자에게 상태를 알린다.
2. 자동 생성 파일(`js/*_data.js`, 수집 산출물)과 수동 변경을 구분한다. 자동 산출물은 손으로 고치지 않는다.

## 2. 스크래퍼 불변식 (완화 금지)

- `scrape_99co.py`: `REQUEST_DELAY_SEC = 15`, `RETRY_BACKOFF_SEC = 45` (429 시 1회 재시도). 2.5초에서 429 확인된 값이다. 다른 스크래퍼의 `REQUEST_DELAY_SEC` 도 낮추지 않는다.
- **수집 0건이면 기존 데이터 파일을 덮어쓰지 않고 종료**하는 분기 유지(scrape_99co / scrape_business / scrape_indoweb 의 "0건 - 덮어쓰지 않고 종료").
- OLX 는 차단 상태 — 프록시·UA 위장·우회 시도 금지. 99.co·tempat-usaha.com 은 데이터센터 IP 차단이라 로컬 `scraper/daily_update.ps1` 이 수집한다(Actions 에서 0건이어도 정상).
- 워크플로의 소스별 `continue-on-error: true` 유지 — 한 소스 실패가 잡 전체를 멈추면 텔레그램 추천까지 한 달간 멈춘 전례(2026-08-15)가 있다.
- `business_gate.py` 의 "기준 미달이면 0건이 정상" 원칙: 추천 수를 늘리려고 기준을 느슨하게 하지 않는다. 기준 변경은 사용자 결정.

## 3. 검증

- `py -3 -m pytest` (pytest.ini 가 `scraper/tests` 지정).
- 기본 검증은 pytest 만으로 한다. 실제 수집은 사이트 부하·차단 위험이 있어 사용자 승인 후에만.
- **주의: `py -3 scraper/scrape_99co.py <지역슬러그>` 는 `js/live_data.js`·`scraper/output/live_listings.json` 을 그 지역 결과만으로 덮어쓴다**(0건 가드는 0건일 때만 동작). 실행했다면 `git diff --stat` 으로 확인하고 `git checkout -- js/live_data.js scraper/output/live_listings.json` 으로 되돌린다. 축소된 데이터를 커밋하지 않는다(로컬 예약 작업이 main 에서 이 파일을 그대로 커밋·푸시한다).
- 텔레그램 발송은 토큰이 GitHub Secrets 에만 있어 로컬 테스트 불가. 발송만 시험하려면 사용자가 Actions 에서 `telegram_only` 입력으로 수동 실행한다. 저장소 변수 `TELEGRAM_ENABLED` 가 `false` 면(2026-10-05 부터 중지) 발송 없이 선정 요약만 돈다. 아니면 실제 발송이므로 사용자 승인.

## 4. 공개 저장소 유출 점검 (커밋 전 스테이징 diff)

- 토큰·chat ID·`GOOGLE_PLACES_API_KEY` 값, 개인 연락처(전화·WhatsApp·이메일), 계정명, 내부 판단 메모·예산 등 사적 정보가 데이터·문서·주석에 들어가지 않았는지.
- 매물 원문에 포함된 판매자 개인 연락처를 그대로 공개 데이터로 옮기지 않았는지.
- 공통 절차는 `secrets-security-hardening` 스킬.

## 5. 금지

- 사용자 승인 없는 push. force push 금지(자동 커밋 이력 손실).
- 로컬 예약 작업(`daily_update.ps1`)은 main 에서만 커밋·`pull --rebase`·push 하고, 다른 브랜치면 exit 1 로 건너뛴다. 작업 브랜치를 체크아웃한 채로 두지 말고 끝나면 main 으로 돌아간다.
- rate limit·재시도 간격 완화, 0건 안전장치 제거, 차단 우회.
- 워크플로 cron·concurrency 변경을 확인 없이 진행.

## 6. 에이전트 연동 (단계별)

| 단계 | 에이전트 | 역할 |
|---|---|---|
| 워크플로·스케줄러 | `devops-infra-ops` | daily-scrape.yml, 로컬 `MNA-99co-scraper`·daily_update.ps1, Actions secrets |
| 공개 저장소 감사 | `security-reviewer` | 커밋 전 토큰·chat ID·판매자 연락처 유출 점검 |
| 테스트 보강 | `test-engineer` | 0건 안전장치·추천 기준(business_gate) 변경 시 scraper/tests 회귀 테스트 |
| 코드 검수 | `code-reviewer` | rate limit·재시도·소스별 continue-on-error 가 유지되는지 |
| 수집 실패 원인 | `debugger` / `tracer` | 403·429·0건의 원인 추적(IP 차단 vs 코드 결함) |
| git 정리 | `git-master` | 자동 커밋과 수동 커밋이 섞여 분기됐을 때 rebase 계획 |
| 사이트 UI | `designer` | index.html·admin.html 화면 변경 |
