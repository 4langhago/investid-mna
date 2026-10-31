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
- 실제 수집 실행은 사이트 부하·차단 위험이 있으므로 필요할 때만, 지역 슬러그를 좁혀서: `py -3 scraper/scrape_99co.py <지역슬러그>`.
- 텔레그램 발송은 토큰이 GitHub Secrets 에만 있어 로컬 테스트 불가. 발송만 시험하려면 사용자가 Actions 에서 `telegram_only` 입력으로 수동 실행한다(실제 발송이므로 사용자 승인).

## 4. 공개 저장소 유출 점검 (커밋 전 스테이징 diff)

- 토큰·chat ID·`GOOGLE_PLACES_API_KEY` 값, 개인 연락처(전화·WhatsApp·이메일), 계정명, 내부 판단 메모·예산 등 사적 정보가 데이터·문서·주석에 들어가지 않았는지.
- 매물 원문에 포함된 판매자 개인 연락처를 그대로 공개 데이터로 옮기지 않았는지.
- 공통 절차는 `secrets-security-hardening` 스킬.

## 5. 금지

- 사용자 승인 없는 push. force push 금지(자동 커밋 이력 손실).
- rate limit·재시도 간격 완화, 0건 안전장치 제거, 차단 우회.
- 워크플로 cron·concurrency 변경을 확인 없이 진행.
