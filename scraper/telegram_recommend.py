# -*- coding: utf-8 -*-
"""
'완전인수(akuisisi)' 매물 중 실제 운영 가능한 사업체만 골라 매일 5건을 텔레그램으로 추천.

- 대상: js/data.js LISTINGS 중 subtype === "akuisisi" && source 필드 존재(A그룹, 샘플 제외)
- 검증: 아래 4단계를 모두 통과한 매물만 발송 — 사람 개입 없음
    1) 필수 필드(연락처/가격/월매출/수익률/설명) 누락 없음
    2) sourceUrl 보유 (원본 역추적 가능해야 함)
    3) whatsapp 번호가 플레이스홀더 패턴(+62812345670XX)이 아님
    4) sourceUrl 이 실제로 살아있음 (HTTP 200)
  ⚠️ 2026-07-27 기준 js/data.js 105건은 (2)(3)에서 전량 탈락한다.
     실재가 확인되지 않는 매물을 추천으로 내보내는 것을 막기 위한 의도된 동작이다.
- 선정: 수익률(profit) 내림차순 정렬 후, 이전 발송 위치(scraper/output/telegram_state.json)
        다음부터 5건씩 순환(로테이션). 끝까지 가면 처음부터 다시 순환.
- 전송: Telegram Bot API (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수 필요)

사용법:
  python scraper/telegram_recommend.py            # 실제 전송
  python scraper/telegram_recommend.py --dry-run   # 전송 없이 선정 결과만 출력
"""
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA_JS = ROOT / "js" / "data.js"
EXPORT_JS = Path(__file__).resolve().parent / "export_listings.js"
STATE_FILE = Path(__file__).resolve().parent / "telegram_state.json"

REQUIRED_FIELDS = ["title", "location", "price", "priceNum", "monthlyRevenue", "profit", "whatsapp"]
BATCH_SIZE = 5

# 순번 플레이스홀더 번호(+6281234567001 ~ +6281234567105). 실제 매도인 연락처가 아님.
PLACEHOLDER_WA = re.compile(r"^\+?6281234567\d{3}$")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
URL_CHECK_TIMEOUT = 15


def load_listings():
    out = subprocess.run(
        ["node", str(EXPORT_JS), str(DATA_JS), "LISTINGS"],
        capture_output=True, check=True,
    )
    return json.loads(out.stdout.decode("utf-8"))


def url_is_live(url):
    """원본 게시글이 아직 살아있는지 확인. 200 이면 통과.

    ⚠️ 알려진 한계 (2026-07-27 실측):
      99.co 는 상세 페이지(/id/properti/...)에 대해 봇 요청에 404 를 반환한다.
      검색엔진이 색인 중인 = 확실히 살아있는 URL 로 테스트해도 404 가 나왔고,
      목록 페이지(/id/jual/...)만 200 이 나온다. 즉 이 함수는 현재 99.co 매물에
      대해 '죽은 매물'과 '봇 차단'을 구분하지 못한다.
      따라서 전부 탈락시키며, 이는 안전한 방향의 오탐(false negative)이다.
      제대로 고치려면 scrape_99co.py 가 쓰는 JSON API 로 매물 id 를 재조회해야 한다.
    """
    req = urllib.request.Request(url, method="GET",
                                 headers={"User-Agent": USER_AGENT,
                                          "Accept-Language": "id-ID,id;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=URL_CHECK_TIMEOUT) as res:
            return res.status == 200, f"HTTP {res.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # 타임아웃/DNS/SSL 등 - 확인 불가면 발송하지 않는다
        return False, f"접속 실패({type(e).__name__})"


def validate(item, check_url=True):
    """실재성 검증. 통과하면 (True, ''), 아니면 (False, 탈락사유)."""
    for field in REQUIRED_FIELDS:
        v = item.get(field)
        if v is None or v == "":
            return False, f"필수 필드 누락: {field}"

    source_url = (item.get("sourceUrl") or "").strip()
    if not source_url:
        return False, "sourceUrl 없음 - 원본 역추적 불가"

    wa = str(item.get("whatsapp", "")).replace(" ", "").replace("-", "")
    if PLACEHOLDER_WA.match(wa):
        return False, f"연락처가 플레이스홀더 패턴({wa})"

    if check_url:
        live, detail = url_is_live(source_url)
        if not live:
            return False, f"원본 URL 확인 실패({detail}): {source_url}"

    return True, ""


def profit_value(item):
    try:
        return float(str(item.get("profit", "0")).replace("%", "").strip())
    except ValueError:
        return 0.0


def select_candidates(listings, check_url=True):
    akuisisi = [x for x in listings if x.get("subtype") == "akuisisi" and x.get("source")]
    valid = []
    for x in akuisisi:
        ok, reason = validate(x, check_url=check_url)
        if ok:
            valid.append(x)
        else:
            print(f"  !! 검증 실패로 제외: id={x.get('id')} {x.get('title')} - {reason}")
    valid.sort(key=profit_value, reverse=True)
    return valid


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"cursor": 0}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def pick_batch(candidates, cursor):
    n = len(candidates)
    if n == 0:
        return [], cursor
    picked = [candidates[(cursor + i) % n] for i in range(min(BATCH_SIZE, n))]
    next_cursor = (cursor + len(picked)) % n
    return picked, next_cursor


def format_item(item, rank):
    wa = item.get("whatsapp", "")
    wa_digits = wa.replace("+", "").replace(" ", "")
    wa_link = f"https://wa.me/{wa_digits}" if wa_digits else ""
    desc = (item.get("description") or "").strip()
    if len(desc) > 150:
        desc = desc[:147] + "..."
    lines = [
        f"{rank}. *{item['title']}*",
        f"   📍 {item.get('locationKo') or item.get('location')}",
        f"   💰 인수가 {item['price']} · 월매출 {item['monthlyRevenue']} · 수익률 {item['profit']}",
    ]
    if desc:
        lines.append(f"   {desc}")
    if wa_link:
        lines.append(f"   📞 {wa_link}")
    if item.get("sourceUrl"):
        lines.append(f"   🔗 원본: {item['sourceUrl']}")
    return "\n".join(lines)


def build_message(picked):
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    header = f"*오늘의 인수 추천 매물* ({today})\n실제 운영 중인 사업체 완전인수 매물 {len(picked)}건\n"
    body = "\n\n".join(format_item(x, i + 1) for i, x in enumerate(picked))
    return header + "\n" + body


def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 설정되지 않음")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as res:
        result = json.loads(res.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"텔레그램 전송 실패: {result}")


def main():
    dry_run = "--dry-run" in sys.argv
    check_url = "--skip-url-check" not in sys.argv

    listings = load_listings()
    candidates = select_candidates(listings, check_url=check_url)
    print(f"[검증] 완전인수(실사) 대상 {len(candidates)}건 (검증 통과)")

    if not candidates:
        print("!! 실재가 확인된 매물이 0건 - 발송하지 않고 종료")
        print("!! 확인되지 않은 매물을 추천으로 내보내지 않는 것이 의도된 동작임")
        sys.exit(1)

    state = load_state()
    picked, next_cursor = pick_batch(candidates, state.get("cursor", 0))
    message = build_message(picked)

    print("----- 발송 내용 미리보기 -----")
    print(message)
    print("-----------------------------")

    if dry_run:
        print("[dry-run] 전송 생략")
        return

    send_telegram(message)
    state["cursor"] = next_cursor
    state["lastRunAt"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    state["lastSentIds"] = [x["id"] for x in picked]
    save_state(state)
    print(f"[전송 완료] {len(picked)}건, 다음 커서 위치: {next_cursor}")


if __name__ == "__main__":
    main()
