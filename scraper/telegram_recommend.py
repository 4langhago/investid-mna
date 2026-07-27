# -*- coding: utf-8 -*-
"""
99.co에서 실제로 수집된 매물만 골라 매일 5건을 텔레그램으로 추천.

- 대상: js/live_data.js 의 LIVE_LISTINGS (scrape_99co.py 가 99.co에서 직접 수집)
  ※ js/data.js 는 사용하지 않는다. 해당 105건은 whatsapp 번호가 순번 플레이스홀더
    (+6281234567001~105)이고 sourceUrl 이 없어 실재를 확인할 수 없는 데이터다.
    2026-07-27 이전까지 이 파일이 발송 소스였고, 그래서 발송을 중단했었다.

- 검증: 아래를 모두 통과한 매물만 발송 — 사람 개입 없음
    1) 데이터 신선도: LIVE_LISTINGS_UPDATED_AT 이 MAX_DATA_AGE_HOURS 이내
       (같은 워크플로에서 scrape_99co.py 가 방금 99.co 목록에서 실제로 가져온 것이므로,
        신선도 자체가 '해당 시점에 게시 중이었다'는 증거다)
    2) 필수 필드(제목/지역/가격/면적) 누락 없음
    3) sourceUrl 보유 — 수신자가 원본을 직접 확인할 수 있어야 함
    4) whatsapp 이 플레이스홀더 패턴이 아님 (값이 없으면 원본 링크로 안내)
  --verify-urls 를 주면 sourceUrl HTTP 확인을 추가로 수행한다. 다만 99.co는 상세
  페이지 봇 요청에 404를 주므로 기본값으로는 켜지 않는다 (url_is_live docstring 참고).

- 선정: 가격(priceNum) 오름차순 정렬 후, 이전 발송 위치(scraper/telegram_state.json)
        다음부터 5건씩 순환(로테이션). 끝까지 가면 처음부터 다시 순환.
  ※ 수익률 정렬을 쓰지 않는 이유: 실수집 매물에는 월매출·수익률 데이터가 없다.
    없는 수치를 추천 근거로 만들어내지 않는다.
- 전송: Telegram Bot API (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수 필요)

사용법:
  python scraper/telegram_recommend.py             # 실제 전송
  python scraper/telegram_recommend.py --dry-run   # 전송 없이 선정 결과만 출력
  python scraper/telegram_recommend.py --verify-urls  # 원본 URL 생존까지 확인
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
LIVE_JS = ROOT / "js" / "live_data.js"
BUSINESS_JS = ROOT / "js" / "business_data.js"
EXPORT_JS = Path(__file__).resolve().parent / "export_listings.js"
STATE_FILE = Path(__file__).resolve().parent / "telegram_state.json"

# 실수집 매물에 반드시 있어야 하는 값. 월매출/수익률은 99.co가 제공하지 않으므로 넣지 않는다.
REQUIRED_FIELDS = ["title", "location", "price", "priceNum", "sourceUrl"]
BATCH_SIZE = 5
# 이 시간을 넘긴 데이터는 '현재 게시 중'이라고 말할 수 없으므로 발송하지 않는다.
MAX_DATA_AGE_HOURS = 48

# 가격 타당성 하한. 99.co에는 매도인이 단위를 잘못 입력한 매물이 섞여 있다
# (예: 건물 130m² 루코가 "Rp 2,3 Juta" = 약 20만원). 실재하는 매물이지만 표시가가
# 틀렸으므로 추천에서 제외한다. 가격 오름차순 정렬 특성상 걸러내지 않으면
# 이런 매물이 매번 추천 최상단을 차지한다.
MIN_PLAUSIBLE_PRICE = 100_000_000        # 루코 최저가 Rp 100 jt
MIN_PLAUSIBLE_PRICE_PER_M2 = 1_000_000   # m²당 Rp 1 jt
# 사업체 인수는 소규모(세탁소·카페 등)가 많아 하한을 따로 둔다.
MIN_BUSINESS_PRICE = 10_000_000          # Rp 10 jt

BUSINESS_SLOTS = 3   # 한 번에 보낼 사업체 인수 매물 수(나머지는 부동산으로 채움)

# 순번 플레이스홀더 번호(+6281234567001 ~ +6281234567105). 실제 매도인 연락처가 아님.
PLACEHOLDER_WA = re.compile(r"^\+?6281234567\d{3}$")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
URL_CHECK_TIMEOUT = 15


def export_var(js_file, var_name):
    out = subprocess.run(
        ["node", str(EXPORT_JS), str(js_file), var_name],
        capture_output=True, check=True,
    )
    return json.loads(out.stdout.decode("utf-8"))


def load_listings():
    """99.co 실수집 매물과 그 수집 시각을 함께 반환."""
    listings = export_var(LIVE_JS, "LIVE_LISTINGS")
    updated_at = export_var(LIVE_JS, "LIVE_LISTINGS_UPDATED_AT")
    return listings, updated_at


def load_business_listings():
    """사업체 인수 매물(scrape_business.py 수집). 파일이 없으면 빈 목록."""
    if not BUSINESS_JS.exists():
        return [], None
    listings = export_var(BUSINESS_JS, "BUSINESS_LISTINGS")
    updated_at = export_var(BUSINESS_JS, "BUSINESS_LISTINGS_UPDATED_AT")
    return listings, updated_at


def data_age_hours(updated_at):
    """수집 시각으로부터 경과 시간(시간 단위). 파싱 불가면 None."""
    try:
        ts = datetime.fromisoformat(updated_at)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600


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


def validate(item, check_url=False, min_price=None):
    """실재성 검증. 통과하면 (True, ''), 아니면 (False, 탈락사유)."""
    min_price = MIN_PLAUSIBLE_PRICE if min_price is None else min_price
    for field in REQUIRED_FIELDS:
        v = item.get(field)
        if v is None or v == "":
            return False, f"필수 필드 누락: {field}"

    source_url = str(item.get("sourceUrl")).strip()
    if not source_url.startswith("http"):
        return False, f"sourceUrl 형식 오류: {source_url}"

    price_num = price_value(item)
    if price_num < min_price:
        return False, f"표시가 비정상({item.get('price')}) - 단위 오기재로 보임"
    area = item.get("area")
    if area and price_num / float(area) < MIN_PLAUSIBLE_PRICE_PER_M2:
        return False, (f"m²당 단가 비정상({item.get('price')} / {area}m²) - 단위 오기재로 보임")

    # 연락처는 없어도 된다(원본 링크로 안내). 단, 있다면 진짜여야 한다.
    wa = str(item.get("whatsapp") or "").replace(" ", "").replace("-", "")
    if wa and PLACEHOLDER_WA.match(wa):
        return False, f"연락처가 플레이스홀더 패턴({wa})"

    if check_url:
        live, detail = url_is_live(source_url)
        if not live:
            return False, f"원본 URL 확인 실패({detail}): {source_url}"

    return True, ""


def price_value(item):
    try:
        return float(item.get("priceNum") or 0)
    except (TypeError, ValueError):
        return 0.0


def select_candidates(listings, check_url=False, min_price=None):
    valid, rejected = [], 0
    for x in listings:
        ok, reason = validate(x, check_url=check_url, min_price=min_price)
        if ok:
            valid.append(x)
        else:
            rejected += 1
            print(f"  !! 검증 실패로 제외: id={x.get('id')} {x.get('title')} - {reason}")
    if rejected:
        print(f"  -- 검증 탈락 합계: {rejected}건")
    valid.sort(key=price_value)
    return valid


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"cursor": 0}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def pick_batch(candidates, cursor, size=BATCH_SIZE):
    n = len(candidates)
    if n == 0 or size <= 0:
        return [], cursor
    picked = [candidates[(cursor + i) % n] for i in range(min(size, n))]
    next_cursor = (cursor + len(picked)) % n
    return picked, next_cursor


def md_safe(text):
    """텔레그램 Markdown 파싱이 깨지지 않도록 서식 문자를 제거."""
    return re.sub(r"[*_`\[\]]", "", str(text or ""))


def format_item(item, rank):
    wa = str(item.get("whatsapp") or "").replace("+", "").replace(" ", "")
    desc = md_safe(item.get("description")).strip()
    # 설명 앞머리의 "Lokasi: <주소>"는 바로 위 📍 줄과 중복이므로 잘라낸다.
    desc = re.sub(r"^(?:Lokasi|Alamat)\s*[:\-]\s*", "", desc, flags=re.I)
    if item.get("address") and desc.startswith(item["address"]):
        desc = desc[len(item["address"]):].lstrip(" .,-")
    if len(desc) > 150:
        desc = desc[:147] + "..."

    spec = []
    if item.get("area"):
        spec.append(f"건물 {item['area']:g}m²")
    if item.get("floors"):
        spec.append(f"{item['floors']}층")

    where = md_safe(item.get("address") or item.get("locationKo") or item.get("location"))
    lines = [
        f"{rank}. *{md_safe(item['title'])}*",
        f"   📍 {where}",
        f"   💰 {md_safe(item['price'])}" + (f" · {' · '.join(spec)}" if spec else ""),
    ]
    if item.get("postedAt"):
        lines.append(f"   🗓 게시일 {item['postedAt'][:10]}")
    if desc:
        lines.append(f"   {desc}")
    if wa:
        lines.append(f"   📞 https://wa.me/{wa}")
    lines.append(f"   🔗 원본: {item['sourceUrl']}")
    return "\n".join(lines)


def build_message(business, property_items, updated_at):
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    total = len(business) + len(property_items)
    parts = [f"*오늘의 매물 추천* ({today})\n"
             f"실수집 매물 {total}건 · 부동산 수집시각 {updated_at}\n"
             f"_계약 전 원본 링크와 현장 실사로 직접 확인하세요._"]

    rank = 0
    if business:
        parts.append(f"*■ 사업체 인수 (운영 중)* {len(business)}건")
        section = []
        for x in business:
            rank += 1
            section.append(format_item(x, rank))
        parts.append("\n\n".join(section))

    if property_items:
        parts.append(f"*■ 부동산/루코 매매* {len(property_items)}건")
        section = []
        for x in property_items:
            rank += 1
            section.append(format_item(x, rank))
        parts.append("\n\n".join(section))

    return "\n\n".join(parts)


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
    check_url = "--verify-urls" in sys.argv

    listings, updated_at = load_listings()

    age = data_age_hours(updated_at)
    if age is None:
        print(f"!! 수집 시각을 해석할 수 없음({updated_at}) - 발송하지 않고 종료")
        sys.exit(1)
    print(f"[신선도] 수집 시각 {updated_at} (경과 {age:.1f}시간 / 허용 {MAX_DATA_AGE_HOURS}시간)")
    if age > MAX_DATA_AGE_HOURS:
        print("!! 데이터가 오래되어 현재 게시 중이라고 보장할 수 없음 - 발송하지 않고 종료")
        print("!! scrape_99co.py 를 먼저 실행해 js/live_data.js 를 갱신할 것")
        sys.exit(1)

    candidates = select_candidates(listings, check_url=check_url)
    print(f"[검증] 부동산 {len(listings)}건 중 {len(candidates)}건 검증 통과")

    biz_all, biz_updated = load_business_listings()
    biz_candidates = select_candidates(biz_all, check_url=check_url,
                                       min_price=MIN_BUSINESS_PRICE) if biz_all else []
    print(f"[검증] 사업체 인수 {len(biz_all)}건 중 {len(biz_candidates)}건 검증 통과"
          + (f" (수집시각 {biz_updated})" if biz_updated else " (수집 파일 없음)"))

    if not candidates and not biz_candidates:
        print("!! 실재가 확인된 매물이 0건 - 발송하지 않고 종료")
        print("!! 확인되지 않은 매물을 추천으로 내보내지 않는 것이 의도된 동작임")
        sys.exit(1)

    state = load_state()
    biz_picked, biz_next = pick_batch(biz_candidates, state.get("bizCursor", 0),
                                      size=min(BUSINESS_SLOTS, len(biz_candidates)))
    picked, next_cursor = pick_batch(candidates, state.get("cursor", 0),
                                     size=BATCH_SIZE - len(biz_picked))
    message = build_message(biz_picked, picked, updated_at)

    print("----- 발송 내용 미리보기 -----")
    print(message)
    print("-----------------------------")

    if dry_run:
        print("[dry-run] 전송 생략")
        return

    send_telegram(message)
    state["cursor"] = next_cursor
    state["bizCursor"] = biz_next
    state["lastRunAt"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    state["lastSentIds"] = [x["id"] for x in picked]
    save_state(state)
    print(f"[전송 완료] {len(picked)}건, 다음 커서 위치: {next_cursor}")


if __name__ == "__main__":
    main()
