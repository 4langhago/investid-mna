# MNA (investid-mna)
인도네시아 M&A·사업체·부동산 매물 정적 사이트(GitHub Pages) + 매물 스크래퍼 + 텔레그램 추천 알림.

## 상태
- **Live** — GitHub Actions 가 매일 자동 수집·커밋. 로컬 작업 스케줄러 `MNA-99co-scraper` (07:00, 99.co 수집).
- **PUBLIC repo** (`4langhago/investid-mna`, 브랜치 main). 시크릿·개인정보·토큰·chat ID 커밋 금지.

## 구조
- `index.html`, `admin.html`, `css/`, `js/` 정적 사이트 (`js/*_data.js` 는 스크래퍼 산출물, `db.js` 가 병합), `sw.js`, `manifest.json`
- `scraper/` scrape_99co, scrape_olx, scrape_business, scrape_smergers, scrape_indoweb, enrich, legal_check, telegram_recommend, `tests/`
- `.github/workflows/daily-scrape.yml` 매일 00:00 UTC 수집 → 커밋·푸시 → 텔레그램 알림
- `docs/` 확정 매물 문서

## 실행·테스트
- **작업 전 반드시 `git pull`** (Actions 가 매일 자동 커밋하므로 충돌 방지).
- 테스트: `python -m pytest` (pytest.ini 가 `scraper/tests` 지정)
- 수집: `py -3 scraper/scrape_99co.py [지역슬러그...]` (표준 라이브러리만 사용)
- 로컬 확인: 정적 서버로 `index.html` 서빙

## 환경변수
Actions secrets / 로컬 환경변수에만 저장. 값은 읽거나 커밋 금지.
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GOOGLE_PLACES_API_KEY`

## 안전 규칙
- 스크래퍼 rate limit 완화 금지 (요청 간 15초, 429 시 45초 후 1회 재시도). 차단되면 IP 장기 차단 위험.
- 수집 0건이면 기존 data.js 를 덮어쓰지 않는 안전장치 유지.
- 공개 저장소이므로 개인 연락처·계정·내부 판단 메모를 데이터/문서에 넣지 않는다.
- OLX 는 현재 차단 상태 — 우회 시도 금지.
- 커밋 전 `git status` 로 자동 생성 파일과 수동 변경을 구분.

## 연동 도구
- `devops-infra-ops` Actions·스케줄러·시크릿 점검 (공개 저장소 유출 감사)
- OMC `executor`, `test-engineer`, `code-reviewer`, `security-reviewer`, `designer`(사이트 UI)
