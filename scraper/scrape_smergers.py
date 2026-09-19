# -*- coding: utf-8 -*-
"""SMERGERS(SME 전문 M&A 플랫폼)에서 자카르타·수도권 사업체 인수 매물을 수집한다.

왜 이 소스를 추가했는가 — 기존 4개 소스(OLX·tempat-usaha·indoweb·99.co)의 한계:
  1) 재무가 없다. 'omzet 15jt/bulan' 같은 매도인 한 줄이 전부여서 검증할 것이 없다.
  2) 전부 자산 양수(take over usaha)다. 인도네시아는 인허가(NIB)의 법인 간 이전
     제도가 없어서, 자산만 사면 인수자가 허가를 전부 새로 받아야 한다.
  3) ₩3억 이상 구간이 비어 있다. 공개 C2C 게시판에는 그 규모의 딜이 올라오지 않는다.
  SMERGERS 는 매출·EBITDA·자산·부채를 공개하고 지분 양도(share deal)가 가능한 건이
  섞여 있으며, 자카르타만 195건 규모다. 위 세 구멍을 정확히 메운다.

이 수집기가 걸러내는 것(추천 대상이 아니라 아예 담지 않는다):
  • /franchise/ 경로       - 신규 가맹점 개설이지 영업 중 사업체 인수가 아니다
  • Seeking Loan           - 대출 모집이지 매각이 아니다
  • Run Rate Sales = Nil   - 영업 중이 아닌 법인(껍데기 인수)
  • 자카르타·수도권 밖     - 지역 1~3순위(자카르타/브까시·찌카랑/보고르·땅그랑) 외

⚠️ 게시일(postedAt)이 없는 소스다. SMERGERS 는 대신 매도인의 최근 접속 상태
   (Active / Moderately Active / Inactive)를 카드에 노출한다. 게시일을 지어내지 않고
   listingActivity 로 남기고, 신선도 판정은 business_gate 가 그 필드로 한다.

사용법:
  python scraper/scrape_smergers.py              # 수집 후 js/smergers_data.js 갱신
  python scraper/scrape_smergers.py --dry-run    # 파일을 쓰지 않고 결과만 출력
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import deal_tier as dt  # noqa: E402
import enrich  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_JS = ROOT / "js" / "smergers_data.js"
OUTPUT_JSON = Path(__file__).resolve().parent / "output" / "smergers_listings.json"

BASE = "https://www.smergers.com"
# 자카르타 지역 목록 페이지. 수도권 외곽(브까시·땅그랑 등)은 전국 목록에서 지역명으로 건진다.
LIST_PATHS = [
    "/businesses-for-sale-and-investment-in-jakarta/c71b/",
    "/businesses-for-sale-and-investment-opportunities-in-indonesia/c69b/",
]
MAX_PAGES = 8              # 페이지당 15건 → 소스당 최대 120건. 그 이상은 지역 밖이 대부분이다.
REQUEST_TIMEOUT = 30
POLITE_DELAY_SECONDS = 2.0  # 목록 페이지 사이 간격. 플랫폼에 부담을 주지 않는다.
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# USD 표기 매물을 IDR 로 환산할 때 쓰는 근사 환율(foreign_eligibility 의 기준과 맞춘다).
USD_TO_IDR = 16_000

# 지역 1~3순위. 이 밖은 담지 않는다.
REGION_PRIORITY = [
    (1, "자카르타", r"\bjakarta\b|\bdki\b"),
    (2, "브까시·찌카랑", r"\bbekasi\b|\bcikarang\b"),
    (3, "보고르·땅그랑·데뽁", r"\bbogor\b|\btangerang\b|\bdepok\b|\bserpong\b|\bbsd\b"),
    # SMERGERS 는 지역을 주(州) 단위로만 쓰는 매물이 있다("Banten, Indonesia").
    # 반뜬주에는 땅그랑(수도권)과 세랑·찔레곤(수도권 밖)이 함께 있어 도시를 특정할 수 없다.
    # 버리면 땅그랑 매물을 놓치므로 담되, locationKo 에 '도시 미표기'를 남겨 드러낸다.
    (3, "반뜬(도시 미표기)", r"\bbanten\b"),
]
LOCATION_KO = [
    (r"jakarta\s+selatan|south\s+jakarta", "자카르타 남부"),
    (r"jakarta\s+pusat|central\s+jakarta", "자카르타 중부"),
    (r"jakarta\s+barat|west\s+jakarta", "자카르타 서부"),
    (r"jakarta\s+utara|north\s+jakarta", "자카르타 북부"),
    (r"jakarta\s+timur|east\s+jakarta", "자카르타 동부"),
    (r"jakarta", "자카르타"),
    (r"bekasi", "브까시"),
    (r"cikarang", "찌카랑"),
    (r"tangerang|serpong|bsd", "땅그랑"),
    (r"bogor", "보고르"),
    (r"depok", "데뽁"),
    (r"banten", "반뜬(도시 미표기)"),
]

# 업종 라벨(제목·본문에서 추출) → 사이트 카테고리.
# 외국인 개방 여부 판정은 business_gate.SECTORS 가 따로 본다(여기는 표시용).
CATEGORY_KO = [
    (r"food\s*processing|central\s*kitchen|frozen|snack|nutrition", "식품 제조"),
    (r"restaurant|cafe|food\s*(?:&|and)\s*beverage|bakery|catering", "카페 & 레스토랑"),
    (r"freight|logistic|forwarding|courier|3pl|warehous", "물류"),
    (r"advertis|marketing|media|agency|influencer", "광고·마케팅"),
    (r"manufactur|factory|printing|packaging|garment", "제조"),
    (r"software|saas|technology|fintech", "IT·소프트웨어"),
    (r"salon|spa|beauty|wellness|clinic", "뷰티 & 살롱"),
    (r"school|education|training|course", "교육"),
    (r"retail|store|shop|e-?commerce|trading|distribut", "소매·유통"),
    (r"gym|fitness|sport|padel", "피트니스"),
    (r"hotel|villa|resort|homestay", "숙박"),
    (r"mining|coal|silica", "광업"),
    (r"construction|contractor|interior|architect", "건설·인테리어"),
    (r"travel|tour", "여행"),
]

CARD_SPLIT = '<div class="listing-card listing-item-wrapper'
_RE_URL = re.compile(r'data-url="([^"]+)"')
_RE_TITLE = re.compile(r'<h2 class="fs-2[^"]*">\s*<a href="[^"]*"[^>]*title="([^"]+)"', re.S)
_RE_HEADLINE = re.compile(r'itemprop="name">\s*(.*?)\s*</div>', re.S)
_RE_DESC = re.compile(r'itemprop="description">\s*(.*?)\s*</div>', re.S)
_RE_ACTIVITY = re.compile(r'data-title="\s*([^"]*?)"\s*class="icon-circle lastseen-')
_RE_VERIFIED = re.compile(r'data-title="(\w+) Verified"')
_RE_RATING = re.compile(r'itemprop="ratingValue" content="([\d.]+)"')
_RE_RATING_N = re.compile(r'itemprop="ratingCount" content="(\d+)"')
_RE_LOCATION = re.compile(r'class="icon-map-marker location"[^>]*data-title="([^"]+)"')
_RE_DEAL_LABEL = re.compile(r'<div class="sme-v3-smalltext" style="color:#999">\s*([^<\n]+)')
_RE_REASON = re.compile(r'data-content="([^"]*)"\s*data-title="Reason"')
_RE_PRICE = re.compile(r'class="sme-v3-bolder"\s+data-toggle="tooltip"\s+data-title="([^"]+)"')
# 지분 매각은 금액 뒤에 지분율이 붙는다: "IDR 13500 Mn for 30%".
_RE_STAKE = re.compile(r"for\s+([\d.]+)\s*%")
# 지표 값 칸에는 <span class="currency-symbol">IDR</span> 같은 태그가 섞여 있어
# 값을 그대로 캡처할 수 없다. 칸 전체를 잡아 태그를 벗겨서 읽는다.
_RE_METRIC_ROW = re.compile(
    r'sme-v3-single-line">\s*(Run Rate Sales|EBITDA Margin|Reported Sales)\b.*?'
    r'text-right sme-v3-single-line fs-2"\s*>(.*?)</div>', re.S)

_TAG_RE = re.compile(r"<[^>]+>")
# SMERGERS 는 같은 값을 카드에서는 "67214 million", 툴팁에서는 "IDR 67,214 Mn" 으로 쓴다.
_AMOUNT_RE = re.compile(
    r"(IDR|USD|SGD)?\s*([\d,]+(?:\.\d+)?)\s*"
    r"(Mn|Bn|K|Cr|million|billion|thousand|crore|lakh)?", re.I)
_UNIT = {"k": 1_000, "thousand": 1_000,
         "mn": 1_000_000, "million": 1_000_000,
         "bn": 1_000_000_000, "billion": 1_000_000_000,
         "lakh": 100_000, "cr": 10_000_000, "crore": 10_000_000}


def _text(html):
    return _TAG_RE.sub("", html or "").replace("&amp;", "&").replace("&nbsp;", " ").strip()


def _group(pattern, card, default=""):
    m = pattern.search(card)
    return _text(m.group(1)) if m else default


def fetch(path, page=1):
    url = BASE + path + (f"?page={page}" if page > 1 else "")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Language": "en-US,en;q=0.9"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.read().decode("utf-8", "replace")


def parse_amount(raw):
    """'IDR 15,000 Mn' / 'IDR 67214 million' → IDR 정수. 못 읽으면 None.

    통화도 단위도 없는 맨숫자는 None 으로 둔다 — 'for 30%' 의 30 같은 값을
    금액으로 잘못 읽으면 가격이 조용히 틀린다.
    """
    if not raw:
        return None
    m = _AMOUNT_RE.search(raw.replace("\xa0", " "))
    if not m:
        return None
    currency = (m.group(1) or "").upper()
    unit = (m.group(3) or "").lower()
    if not currency and not unit:
        return None
    try:
        value = float(m.group(2).replace(",", ""))
    except ValueError:
        return None
    value *= _UNIT.get(unit, 1)
    if currency in ("USD", "SGD"):
        value *= USD_TO_IDR
    return int(value)


def parse_stake(raw):
    """'IDR 13500 Mn for 30%' → 30.0. 지분율 표기가 없으면 None(=전부 양도)."""
    m = _RE_STAKE.search(raw or "")
    return float(m.group(1)) if m else None


def region_of(location):
    """(우선순위, 지역명) 또는 (None, None). 수도권 밖이면 None."""
    low = (location or "").lower()
    for rank, name, pattern in REGION_PRIORITY:
        if re.search(pattern, low):
            return rank, name
    return None, None


def _first_match(table, text, default=""):
    low = (text or "").lower()
    for pattern, value in table:
        if re.search(pattern, low):
            return value
    return default


def classify_deal(label, title, stake=None):
    """(딜 구조, 제외 사유 또는 None).

    'Business for Sale' 은 법인 전부 양도라 주식 양수 구조가 성립한다.
    지분 매각은 금액 툴팁의 지분율로 경영권 유무를 가른다(50% 미만 = 소수지분).
    지분율을 못 읽은 지분 매각은 소수지분으로 본다 — 모르는 것을 유리하게 가정하지 않는다.
    """
    low = (label or "").lower()
    text = (title or "").lower()
    if "loan" in low:
        return None, "대출 모집 - 매각 매물이 아님"
    if "franchise" in low or "franchise" in text:
        return None, "프랜차이즈 가맹 - 영업 중 사업체 인수가 아님"
    is_stake = ("stake" in low or "investment" in low or "investor" in low
                or "equity" in text or stake is not None)
    if is_stake:
        if stake is not None and stake >= 50:
            return dt.SHARE_DEAL, None
        return dt.MINORITY, None
    if "sale" in low:
        return dt.SHARE_DEAL, None
    return dt.UNKNOWN, None


def parse_card(card):
    """카드 HTML 1개 → (매물 dict, None) 또는 (None, 제외 사유)."""
    m = _RE_URL.search(card)
    if not m:
        return None, "카드에서 링크를 찾지 못함"
    # 카드 링크에 추적 쿼리가 붙는 경우가 있다(".../?source=list-business-card").
    # 그대로 두면 id 가 'smg-?source=...' 가 되어 매물 식별이 깨진다.
    path = m.group(1).split("?")[0].split("#")[0]
    if "/franchise/" in path:
        return None, "프랜차이즈 가맹 - 영업 중 사업체 인수가 아님"
    # 목록 안에는 매물이 아닌 유도 카드도 섞여 있다(/create-business-profile/ 등).
    if not path.startswith("/business/"):
        return None, "매물 카드가 아님(플랫폼 안내·유도 카드)"
    listing_id = path.rstrip("/").rsplit("/", 1)[-1]
    if not listing_id:
        return None, "매물 식별자를 읽지 못함"

    title = _group(_RE_TITLE, card)
    headline = _group(_RE_HEADLINE, card)
    desc = _group(_RE_DESC, card)
    location = _group(_RE_LOCATION, card)

    rank, region_ko = region_of(location)
    if rank is None:
        return None, f"자카르타·수도권 밖({location or '지역 미표기'})"

    price_raw = _group(_RE_PRICE, card)
    stake = parse_stake(price_raw)
    deal_label = _group(_RE_DEAL_LABEL, card)
    structure, skip = classify_deal(deal_label, title, stake)
    if skip:
        return None, skip

    metrics = {label: _text(value) for label, value in _RE_METRIC_ROW.findall(card)}
    revenue = parse_amount(metrics.get("Run Rate Sales") or metrics.get("Reported Sales"))
    ebitda_raw = (metrics.get("EBITDA Margin") or "").strip()
    price = parse_amount(price_raw)

    if not revenue:
        return None, "매출 미표기·Nil - 영업 중 사업체로 볼 수 없음"
    if not price:
        return None, "인수 희망가 미표기"

    activity = _group(_RE_ACTIVITY, card)
    verified = sorted(set(_RE_VERIFIED.findall(card)))
    rating = _RE_RATING.search(card)
    rating_n = _RE_RATING_N.search(card)
    reason = _group(_RE_REASON, card)

    monthly = int(revenue / 12) if revenue else None
    facilities = [f"SMERGERS {structure}", f"연매출 IDR {revenue / 1e9:.2f}B"]
    if ebitda_raw and ebitda_raw.lower() != "nil":
        facilities.append(f"EBITDA 마진 {ebitda_raw}")
    if verified:
        facilities.append("플랫폼 인증: " + "·".join(verified))
    if activity:
        facilities.append(f"매도인 활동 {activity}")

    # 본문에 원문 전체(헤드라인+상세+매각사유)를 남긴다. business_gate 의 업종 판정과
    # operability 의 영업 실체 판정이 이 텍스트를 읽는다.
    description = " ".join(x for x in (headline, desc,
                                       (f"매각 사유: {reason}" if reason else "")) if x)

    item = {
        "id": f"smg-{listing_id}",
        "type": "bisnis",
        "subtype": "akuisisi",
        "title": title or headline,
        "category": _first_match(CATEGORY_KO, f"{title} {headline} {desc}", "기타 사업"),
        "location": location.split(",")[0].strip() or region_ko,
        "locationKo": _first_match(LOCATION_KO, location, region_ko),
        "address": location,
        "monthlyRevenue": f"IDR {monthly / 1e6:.0f} jt/월" if monthly else None,
        "monthlyRevenueNum": monthly,
        # profit 은 '순이익 금액' 필드다. EBITDA 마진(%)을 넣으면 한글 요약이
        # 금액으로 읽어 "순이익 약 Rp ," 처럼 깨진다. 마진은 ebitdaMargin 에만 둔다.
        "profit": None,
        "price": (f"IDR {price / 1e9:.2f} M" if price >= 1e9 else f"IDR {price / 1e6:.0f} jt"),
        "priceNum": price,
        "established": None,
        "area": None,
        "floors": None,
        "description": description,
        "facilities": facilities,
        "whatsapp": None,
        "c2c": False,
        "images": "\U0001F4CA",
        "badge": "M&A 플랫폼",
        "source": "smergers.com",
        "sourceUrl": BASE + path,
        # 게시일이 없는 소스다. 지어내지 않고, 신선도 근거를 별도 필드로 남긴다.
        "postedAt": None,
        "listingActivity": activity or "미표기",
        "platformRating": float(rating.group(1)) if rating else None,
        "platformRatingCount": int(rating_n.group(1)) if rating_n else None,
        "platformVerified": verified,
        "dealStructure": structure,
        "dealLabel": deal_label,
        "stakePercent": stake,
        "ebitdaMargin": ebitda_raw or None,
        "annualRevenueNum": revenue,
        "sellReason": reason or None,
        "regionPriority": rank,
        "lat": None,
        "lng": None,
    }
    return item, None


def collect():
    """(매물 목록, 제외 사유별 건수) 반환."""
    items, rejected, seen = [], {}, set()
    for path in LIST_PATHS:
        for page in range(1, MAX_PAGES + 1):
            try:
                html = fetch(path, page)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                print(f"  !! 요청 실패 {path} p{page}: {exc}")
                break
            cards = html.split(CARD_SPLIT)[1:]
            if not cards:
                break
            print(f"  {path} p{page}: 카드 {len(cards)}건")
            for card in cards:
                item, why = parse_card(card)
                if item is None:
                    rejected[why] = rejected.get(why, 0) + 1
                    continue
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                items.append(item)
            time.sleep(POLITE_DELAY_SECONDS)
    # 지역 우선순위 → 인수가 오름차순. 자카르타 안쪽이 먼저 보이게 둔다.
    items.sort(key=lambda x: (x["regionPriority"], x["priceNum"]))
    return items, rejected


def write_outputs(items):
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    enrich.annotate_all(items)
    for item in items:
        dt.annotate(item)
    print("[판정]")
    for line in enrich.summarize(items):
        print(line)
    body = json.dumps(items, ensure_ascii=False, indent=2)
    OUTPUT_JS.write_text(
        "// 자동 생성 파일 — scraper/scrape_smergers.py 가 갱신합니다. 직접 수정 금지.\n"
        f"// 갱신 시각: {now}\n"
        f'const SMERGERS_LISTINGS_UPDATED_AT = "{now}";\n'
        f"const SMERGERS_LISTINGS = {body};\n",
        encoding="utf-8")
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(body, encoding="utf-8")
    print(f"[저장] {OUTPUT_JS} ({len(items)}건)")
    print(f"[저장] {OUTPUT_JSON}")


def main():
    dry_run = "--dry-run" in sys.argv
    print("[수집] SMERGERS 자카르타·수도권 사업체 인수 매물")
    items, rejected = collect()

    print("\n[제외 사유]")
    for why, n in sorted(rejected.items(), key=lambda x: -x[1]):
        print(f"  {n:>4}건  {why}")

    print(f"\n[결과] 수도권 사업체 매물 {len(items)}건")
    for x in items:
        tier = dt.classify_tier(x)[1]
        print(f"  {tier:14} {x['locationKo']:10} {x['price']:>16}  "
              f"{x['dealStructure']:6} {x['title'][:58]}")

    if not items:
        # 0건일 때 기존 파일을 덮어쓰면, 수집 실패와 '정말 매물이 없음'을 구분할 수 없게 된다.
        print("!! 수집 0건 - 기존 파일을 덮어쓰지 않고 종료")
        sys.exit(1)
    if dry_run:
        print("[dry-run] 파일 저장 생략")
        return
    write_outputs(items)


if __name__ == "__main__":
    main()
