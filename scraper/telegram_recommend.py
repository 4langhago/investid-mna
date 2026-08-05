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
    5) 외국인(한국인) 취득 가능 - foreign_eligibility.classify 가 '불가'로 본 매물은 제외.
       Girik 미등기 토지, 외국인 투자 유보 업종(노점·생필품 소매 등)이 여기에 해당한다.
       '조건부'(PT PMA 설립·HGB 전환 필요)는 절차를 함께 안내하고 발송한다.
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
  python scraper/telegram_recommend.py --foreign-only-eligible  # 외국인 '가능' 등급만 발송
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import foreign_eligibility as fe  # noqa: E402
from korean_brief import build_korean_brief  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
LIVE_JS = ROOT / "js" / "live_data.js"
BUSINESS_JS = ROOT / "js" / "business_data.js"
OLX_JS = ROOT / "js" / "olx_data.js"
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

# 외국인(한국인) 취득 가능성 필터.
#   기본값: '불가'로 판정된 매물은 발송하지 않고, '가능'을 '조건부'보다 앞에 배치한다.
#   --foreign-only-eligible 을 주면 '가능'만 발송한다(표본이 크게 줄어들 수 있음).
# 조건부를 기본 포함하는 이유: 인도네시아에서 외국인의 상업용 부동산·사업체 인수는
# PT PMA 설립을 전제로 하는 것이 통상적이며, 이를 전부 제외하면 추천 대상이 사실상
# 아파트만 남는다. 대신 각 매물에 필요한 절차를 메시지에 명시한다.
ALLOWED_FOREIGN_STATUSES = (fe.ELIGIBLE, fe.CONDITIONAL)

# 순번 플레이스홀더 번호(+6281234567001 ~ +6281234567105). 실제 매도인 연락처가 아님.
PLACEHOLDER_WA = re.compile(r"^\+?6281234567\d{3}$")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
URL_CHECK_TIMEOUT = 15
TELEGRAM_MAX_CHARS = 4096  # sendMessage text 상한


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
    """사업체 인수 매물을 두 소스에서 합친다. 파일이 없는 소스는 건너뛴다.

    부동산이 함께 딸린 매물(propertyIncluded)은 사업체 인수와 성격이 달라
    추천 대상에서 제외한다. 사이트에는 그대로 노출된다.
    """
    listings, updated = [], []
    for js_file, var, ts_var in (
            (BUSINESS_JS, "BUSINESS_LISTINGS", "BUSINESS_LISTINGS_UPDATED_AT"),
            (OLX_JS, "OLX_LISTINGS", "OLX_LISTINGS_UPDATED_AT")):
        if not js_file.exists():
            continue
        listings.extend(export_var(js_file, var))
        updated.append(export_var(js_file, ts_var))

    listings = [x for x in listings if not x.get("propertyIncluded")]
    return listings, (max(updated) if updated else None)


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


def validate(item, check_url=False, min_price=None, allowed_statuses=ALLOWED_FOREIGN_STATUSES):
    """실재성 + 외국인 취득 가능성 검증. 통과하면 (True, ''), 아니면 (False, 탈락사유)."""
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
    # m²당 단가 규칙은 루코·상가처럼 '건물 면적'이 값을 결정하는 매물에만 유효하다.
    # 사업체 인수(양계장·농장 등 대지가 넓은 업종)에 적용하면 정상 매물이 탈락한다.
    # 예: 양계장 Rp 16 M / 32,000m² = m²당 500 → 오탐.
    area = item.get("area")
    if (item.get("subtype") != "akuisisi" and area
            and price_num / float(area) < MIN_PLAUSIBLE_PRICE_PER_M2):
        return False, (f"m²당 단가 비정상({item.get('price')} / {area}m²) - 단위 오기재로 보임")

    # 연락처는 없어도 된다(원본 링크로 안내). 단, 있다면 진짜여야 한다.
    wa = str(item.get("whatsapp") or "").replace(" ", "").replace("-", "")
    if wa and PLACEHOLDER_WA.match(wa):
        return False, f"연락처가 플레이스홀더 패턴({wa})"

    # 외국인이 취득할 수 없는 구조의 매물은 추천 대상이 아니다.
    status, reason, _steps = fe.classify(item)
    if status not in allowed_statuses:
        return False, f"외국인 취득 {status}: {reason}"

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


def select_candidates(listings, check_url=False, min_price=None,
                      allowed_statuses=ALLOWED_FOREIGN_STATUSES):
    valid, rejected = [], 0
    for x in listings:
        ok, reason = validate(x, check_url=check_url, min_price=min_price,
                              allowed_statuses=allowed_statuses)
        if ok:
            valid.append(x)
        else:
            rejected += 1
            print(f"  !! 검증 실패로 제외: id={x.get('id')} {x.get('title')} - {reason}")
    if rejected:
        print(f"  -- 검증 탈락 합계: {rejected}건")
    # 외국인이 바로 취득 가능한 매물을 앞에, 그 안에서는 가격 오름차순.
    valid.sort(key=lambda x: (fe.rank_key(fe.classify(x)[0]), price_value(x)))
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


def maps_link(item):
    """지도 링크. 좌표가 있으면 좌표로, 없으면 주소/지역명 검색으로 연결.

    좌표는 수집기가 매물 지역에서 받아온 값이며 건물 단위 정확도가 아닐 수 있다.
    그래서 '대략 위치'로 표기한다 - 없는 정확도를 있는 것처럼 말하지 않는다.
    """
    lat, lng = item.get("lat"), item.get("lng")
    if lat and lng:
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
    where = item.get("address") or item.get("location")
    if not where:
        return None
    return ("https://www.google.com/maps/search/?api=1&query="
            + urllib.parse.quote_plus(str(where)))


def median(values):
    s = sorted(values)
    n = len(s)
    if not n:
        return None
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def build_review(item, peers):
    """수집된 값만으로 만드는 자동 분석 코멘트.

    ⚠️ 여기서 만들어내는 문장은 전부 실제 수집 필드에서 계산된 것이어야 한다.
    매물 품질·입지 평가처럼 데이터에 없는 판단은 넣지 않는다(추측 금지).
    비교군(peers)은 같은 검증 통과 후보들이며, 표본이 5건 미만이면 시세 비교를
    생략한다 - 표본이 적으면 중앙값이 시세를 대표하지 못한다.
    """
    notes = []
    price = price_value(item)
    area = item.get("area")

    # 1) 같은 지역 동일 유형 대비 가격 위치
    same = [p for p in peers
            if p.get("id") != item.get("id")
            and p.get("location") == item.get("location")
            and p.get("type") == item.get("type")]
    scope = f"{md_safe(item.get('locationKo') or item.get('location'))} 동일 유형"
    if len(same) < 5:  # 지역 표본이 얇으면 유형 전체로 넓힌다
        same = [p for p in peers
                if p.get("id") != item.get("id") and p.get("type") == item.get("type")]
        scope = "수집 매물 동일 유형"
    if len(same) >= 5:
        med = median([price_value(p) for p in same])
        if med:
            diff = (price / med - 1) * 100
            word = "낮음" if diff < -10 else ("높음" if diff > 10 else "비슷")
            notes.append(f"가격: {scope} {len(same)}건 중앙값 대비 {diff:+.0f}% ({word})")

    # 2) m²당 단가 - 면적이 있는 매물끼리만 비교
    if area:
        unit = price / float(area)
        notes.append(f"m²당 {unit/1_000_000:.1f} jt (건물 {float(area):g}m²)")
        peer_units = [price_value(p) / float(p["area"]) for p in same if p.get("area")]
        if len(peer_units) >= 5:
            med_unit = median(peer_units)
            if med_unit:
                notes.append(f"m²당 단가는 비교군 중앙값 대비 {(unit/med_unit - 1)*100:+.0f}%")

    # 3) 수익 관련 - 값이 있을 때만. 대부분의 실수집 매물에는 없다.
    if item.get("monthlyRevenue"):
        line = f"월매출 {md_safe(item['monthlyRevenue'])}"
        rev = item.get("monthlyRevenueNum")
        if rev:
            try:
                months = price / float(rev)
                line += f" · 매매가 회수까지 약 {months:.0f}개월치 매출 규모"
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        notes.append(line)
    if item.get("profit"):
        notes.append(f"수익 정보: {md_safe(item['profit'])}")
    if item.get("established"):
        notes.append(f"영업 개시 {md_safe(item['established'])}")
    else:
        if item.get("subtype") == "akuisisi":
            notes.append("영업 기간·매출 미공개 - 매도인에게 장부 확인 필요")

    # 4) 게시 신선도
    if item.get("postedAt"):
        days = data_age_hours(item["postedAt"])
        if days is not None:
            d = days / 24
            notes.append(f"원본 게시 {d:.0f}일 전"
                         + (" (오래된 글, 거래 완료 여부 확인 필요)" if d > 30 else ""))

    if item.get("propertyIncluded"):
        notes.append("부동산 포함 매각 - 사업체만 인수하는 조건과 가격 구조가 다름")
    if not item.get("whatsapp"):
        notes.append("연락처 미공개 - 원본 링크 내 채팅으로 문의")

    return notes


def format_item(item, rank, total, peers):
    """매물 1건을 텔레그램 메시지 하나로 상세 포맷."""
    wa = str(item.get("whatsapp") or "").replace("+", "").replace(" ", "")
    desc = md_safe(item.get("description")).strip()
    # 설명 앞머리의 "Lokasi: <주소>"는 바로 아래 📍 줄과 중복이므로 잘라낸다.
    desc = re.sub(r"^(?:Lokasi|Alamat)\s*[:\-]\s*", "", desc, flags=re.I)
    if item.get("address") and desc.startswith(item["address"]):
        desc = desc[len(item["address"]):].lstrip(" .,-")
    if len(desc) > 700:
        desc = desc[:697] + "..."

    spec = []
    if item.get("area"):
        spec.append(f"건물 {float(item['area']):g}m²")
    if item.get("floors"):
        spec.append(f"{item['floors']}층")

    where = md_safe(item.get("address") or item.get("locationKo") or item.get("location"))
    kind = " / ".join(x for x in (md_safe(item.get("category")), md_safe(item.get("badge"))) if x)

    lines = [f"*[{rank}/{total}] {md_safe(item['title'])}*", ""]
    if kind:
        lines.append(f"🏷 {kind}")
    lines.append(f"📍 {where}")
    lines.append(f"💰 *{md_safe(item['price'])}*" + (f"\n📐 {' · '.join(spec)}" if spec else ""))
    if item.get("postedAt"):
        lines.append(f"🗓 게시일 {item['postedAt'][:10]}")

    facilities = [md_safe(f) for f in (item.get("facilities") or []) if f]
    if facilities:
        lines.append(f"✅ {' · '.join(facilities)}")

    # 한글 요약을 원문보다 먼저 둔다. 수신자가 인도네시아어를 읽지 않아도
    # 매물 성격을 파악할 수 있어야 한다. 요약은 원문에서 뽑은 항목만으로 구성된다.
    brief = build_korean_brief(item)
    if brief:
        lines.append("\n🇰🇷 *한글 요약* (원문에서 추출)")
        lines.extend(f"• {md_safe(b)}" for b in brief)

    status, reason, steps = fe.classify(item)
    mark = {fe.ELIGIBLE: "🟢", fe.CONDITIONAL: "🟡"}.get(status, "🔴")
    lines.append(f"\n{mark} *외국인 취득: {status}*")
    lines.append(f"• {md_safe(reason)}")
    lines.extend(f"• {md_safe(s)}" for s in steps)

    if desc:
        lines.append(f"\n📝 *매물 설명(원문)*\n{desc}")

    review = build_review(item, peers)
    if review:
        lines.append("\n🔍 *자동 분석* (수집 데이터 기준)")
        lines.extend(f"• {n}" for n in review)

    lines.append("\n🔗 *링크*")
    lines.append(f"원본({md_safe(item.get('source'))}): {item['sourceUrl']}")
    m = maps_link(item)
    if m:
        kind_of_pin = "좌표 기준 대략 위치" if (item.get("lat") and item.get("lng")) else "지역명 검색"
        lines.append(f"지도({kind_of_pin}): {m}")
    if wa:
        lines.append(f"연락처: https://wa.me/{wa}")

    return "\n".join(lines)


def build_messages(business, property_items, updated_at, peers):
    """머리말 1건 + 매물 1건당 1메시지. 텔레그램 4096자 제한을 넘기지 않기 위함."""
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    total = len(business) + len(property_items)
    messages = [f"*오늘의 매물 추천* ({today})\n"
                f"실수집 매물 {total}건 (사업체 인수 {len(business)} · 부동산 {len(property_items)})\n"
                f"부동산 수집시각 {updated_at}\n\n"
                f"_모든 매물은 외국인(한국인) 취득 가능 여부를 1차 스크리닝해 "
                f"'불가' 판정 매물을 제외한 것입니다._\n"
                f"_🟢 외국인 명의로 바로 취득 가능 · 🟡 PT PMA 설립 등 절차 필요_\n"
                f"_아래 분석은 수집된 공개 정보만으로 자동 계산한 것이며 법률 자문이 아닙니다._\n"
                f"_계약 전 원본 링크·현장 실사와 공증인(notaris) 확인이 필요합니다._"]

    rank = 0
    for label, group in (("■ 사업체 인수 (운영 중)", business),
                         ("■ 부동산/루코 매매", property_items)):
        if not group:
            continue
        messages.append(f"*{label}* {len(group)}건")
        for x in group:
            rank += 1
            messages.append(format_item(x, rank, total, peers))
    return messages


def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 설정되지 않음")

    # 상세 포맷은 길어질 수 있다. 4096자를 넘으면 텔레그램이 전송을 거부하므로 잘라 보낸다.
    if len(message) > TELEGRAM_MAX_CHARS:
        message = message[:TELEGRAM_MAX_CHARS - 20] + "\n…(생략)"

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
    allowed = ((fe.ELIGIBLE,) if "--foreign-only-eligible" in sys.argv
               else ALLOWED_FOREIGN_STATUSES)
    print(f"[외국인 취득 필터] 발송 허용 등급: {' / '.join(allowed)}")

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

    candidates = select_candidates(listings, check_url=check_url, allowed_statuses=allowed)
    print(f"[검증] 부동산 {len(listings)}건 중 {len(candidates)}건 검증 통과")

    biz_all, biz_updated = load_business_listings()
    biz_candidates = select_candidates(biz_all, check_url=check_url,
                                       min_price=MIN_BUSINESS_PRICE,
                                       allowed_statuses=allowed) if biz_all else []
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
    # 비교군은 검증을 통과한 전체 후보. 분석 수치는 여기서만 계산한다.
    messages = build_messages(biz_picked, picked, updated_at,
                              peers=biz_candidates + candidates)

    print("----- 발송 내용 미리보기 -----")
    for m in messages:
        print(m)
        print("- - - - -")
    print("-----------------------------")

    if dry_run:
        print("[dry-run] 전송 생략")
        return

    for m in messages:
        send_telegram(m)
    state["cursor"] = next_cursor
    state["bizCursor"] = biz_next
    state["lastRunAt"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    # 두 섹션을 모두 기록한다. 부동산만 남기면 실제 발송분과 기록이 어긋난다.
    state["lastSentBusinessIds"] = [x["id"] for x in biz_picked]
    state["lastSentPropertyIds"] = [x["id"] for x in picked]
    state["lastSentIds"] = [x["id"] for x in biz_picked + picked]
    save_state(state)
    print(f"[전송 완료] 총 {len(biz_picked) + len(picked)}건"
          f" (사업체 {len(biz_picked)} / 부동산 {len(picked)})"
          f", 다음 커서 - 사업체 {biz_next} · 부동산 {next_cursor}")


if __name__ == "__main__":
    main()
