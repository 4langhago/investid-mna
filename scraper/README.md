# 실시간 매물 수집 (99.co)

## 동작 방식
```
scraper/scrape_99co.py  →  js/live_data.js (LIVE_LISTINGS)  →  db.js가 LISTINGS에 병합  →  사이트에 표시
                        →  scraper/output/live_listings.json (원본 백업)
```
- 실시간 매물은 id가 `live-` 접두사로 구분되며, 카드에 🛰️ 아이콘과 "원문 ↗" 링크(99.co 상세 페이지)가 표시됩니다.
- 실시간 매물은 localStorage에 저장되지 않고 스크래퍼 실행 시마다 통째로 교체됩니다 (어드민에서 수정해도 다음 수집 때 원복).
- 수집 0건이면 기존 live_data.js를 덮어쓰지 않고 종료합니다(안전장치).

## 실행
```
py -3 scraper/scrape_99co.py                 # 기본 7개 지역 (탕그랑, 자카르타 남부/서부, 데포, 브카시, 반둥, 수라바야)
py -3 scraper/scrape_99co.py tangerang depok # 특정 지역만 (99.co 도시 슬러그)
```
표준 라이브러리만 사용 — 별도 pip 설치 불필요.

## 자동 갱신 (Windows 작업 스케줄러)
관리자 PowerShell에서 1회 실행 (매일 07:00 수집):
```powershell
schtasks /Create /TN "MNA-99co-scraper" /SC DAILY /ST 07:00 `
  /TR "py -3 D:\0.Cursor\MNA\scraper\scrape_99co.py" /F
```
해제: `schtasks /Delete /TN "MNA-99co-scraper" /F`

## Rate Limit 주의 (완화 금지)
- 요청 간 딜레이 15초, 429 수신 시 45초 대기 후 1회 재시도.
- 2.5초 간격에서 두 번째 요청부터 429가 발생하는 것을 실측으로 확인함. 딜레이를 줄이면 IP가 장기 차단될 수 있습니다.
- 하루 1~2회 수집이면 충분합니다. 수집 주기를 분 단위로 줄이지 마세요.

## rumah123.com 미지원 사유
Cloudflare 봇 차단(403 + JS challenge)으로 직접 HTTP와 자동화 브라우저 모두 차단 확인(2026-07-05). data.js의 기존 rumah123 매물은 수동 수집분입니다.
