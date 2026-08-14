# -*- coding: utf-8 -*-
"""
사업체 인수 매물 수집기 — OLX 인도네시아 (api.olx.co.id)

tempat-usaha.com(scrape_business.py)은 신규 인수 매물이 거의 올라오지 않아
최근 540일 기준 8건이 한계였다. 검색어를 12개 추가해 후보 1,366건을 더 모아도
최종 채택은 0건이었다. 즉 그 소스는 이미 고갈 상태다.
OLX는 같은 조건에서 210건 규모가 나와 인수 매물의 주력 소스로 사용한다.

- 수집: api.olx.co.id/relevance/v4/search (공개 JSON API)
        www.olx.co.id 는 봇 요청을 타임아웃 처리하지만 api 서브도메인은 응답한다.
- 판정: 검색어만으로는 중고물품 광고가 섞이므로 제목/본문을 TAKEOVER_RE 로 재차 거른다.
- 개인정보: 판매자 이름(user_name)과 연락처는 저장하지 않는다. 문의는 원문 링크로 보낸다.

⚠️ 원문 URL 생존 확인 불가:
   www.olx.co.id 가 봇 요청에 응답하지 않아 상세 페이지 200 확인을 할 수 없다.
   대신 API 가 주는 status=active 와 created_at 신선도를 게시 중 증거로 사용한다.

사용법:
  py -3 scraper/scrape_olx.py
  py -3 scraper/scrape_olx.py --max-age-days 90
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enrich  # noqa: E402  한인 인수·운영 가능 판정
from scrape_business import CATEGORY_RULES, TAKEOVER_RE  # 판정·분류 규칙 공유

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_JS = ROOT / "js" / "olx_data.js"
OUTPUT_JSON = Path(__file__).resolve().parent / "output" / "olx_listings.json"

API = "https://api.olx.co.id/relevance/v4/search"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
REQUEST_DELAY_SEC = 1.5   # 안전장치: 완화 금지
RETRY_BACKOFF_SEC = 5     # 네트워크 오류 재시도 전 대기
PAGE_SIZE = 40
MAX_PAGES = 5

# 검색어 선정 근거(2026-07-28 수율 측정):
#  - 카테고리 열거(category=5090 등)는 512건당 0~2건으로 수율이 낮아 쓰지 않는다.
#  - 'dijual usaha' 같은 광범위 검색어는 512건을 훑어도 4건뿐이다.
#  - 반면 양수·양도를 직접 가리키는 표현은 수율이 높다.
#    예: 'over usaha' 40건 중 23건(58%), 'usaha aktif dijual' 40건 중 19건.
#  즉 물량은 페이지 깊이가 아니라 '정밀한 표현'에서 나온다.
QUERIES = [
    # 핵심 양수·양도 표현 (고수율)
    "take over usaha", "takeover usaha", "over usaha", "oper usaha", "alih usaha",
    "usaha aktif dijual", "jual usaha berjalan", "usaha berjalan", "jual bisnis",
    "dijual usaha", "take over franchise",
    # 업종별 (건수는 적지만 정확도가 높다)
    "take over cafe", "take over kopi", "take over restoran", "take over warung",
    "take over warteg", "take over bakso", "take over ayam geprek",
    "take over laundry", "take over salon", "take over barbershop",
    "take over toko", "take over minimarket", "take over klinik", "take over apotek",
    "take over bengkel", "take over cuci mobil", "take over gym", "take over kos",
    "take over kontrakan", "take over catering", "take over percetakan",
    "take over konter", "take over sekolah", "take over pet shop",
]

DEFAULT_MAX_AGE_DAYS = 180
MIN_PRICE = 10_000_000
MAX_PRICE = 500_000_000_000

LOCATION_KO = {
    "Jakarta Selatan": "자카르타 남부", "Jakarta Utara": "자카르타 북부",
    "Jakarta Timur": "자카르타 동부", "Jakarta Barat": "자카르타 서부",
    "Jakarta Pusat": "자카르타 중부", "DKI Jakarta": "자카르타",
    "Tangerang Selatan Kota": "탕그랑 남부", "Tangerang Kota": "탕그랑",
    "Tangerang": "탕그랑", "Bekasi Kota": "브카시", "Bekasi": "브카시",
    "Depok Kota": "데포", "Depok": "데포", "Bogor Kota": "보고르", "Bogor": "보고르",
    "Bandung Kota": "반둥", "Bandung": "반둥", "Surabaya Kota": "수라바야",
    "Surabaya": "수라바야", "Semarang Kota": "스마랑", "Bali": "발리",
    "Jawa Barat": "서부자바", "Jawa Timur": "동부자바", "Jawa Tengah": "중부자바",
    "Banten": "반튼",
}


def api_search(query, page):
    url = API + "?" + urllib.parse.urlencode(
        {"query": query, "page": page, "size": PAGE_SIZE})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    # DNS·연결 순간 장애(getaddrinfo failed 등)로 검색어 하나가 통째로 날아가지 않도록
    # 네트워크 오류에 한해 1회 재시도한다. HTTP 오류(429/403)는 재시도하지 않는다.
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.loads(res.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt == 1:
                raise
            time.sleep(RETRY_BACKOFF_SEC)


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:80] or "item"


def parse_category(text):
    for pattern, label in CATEGORY_RULES:
        if re.search(pattern, text, re.I):
            return label
    return "기타 사업"


# 제목에 사업체 양수·양도 신호가 있으면 '사업체 인수' 매물로 본다.
TRANSFER_TITLE_RE = re.compile(
    r"take\s?over|takeover|oper usaha|alih usaha|jual bisnis|"
    r"dijual usaha|usaha dijual", re.I)
# 제목이 부동산 매각을 가리키면 '부동산 + 운영 중 사업체' 동반 매각으로 분류한다.
# (예: "JUAL RUKO BESERTA USAHA", "Rumah Hook dan Usaha Berjalan")
# 사업체 자체 인수와 성격이 달라 추천 시 섞지 않으려고 표시해 둔다.
PROPERTY_TITLE_RE = re.compile(r"ruko|rumah|tempat usaha|tanah|gudang", re.I)


def price_of(ad):
    return (((ad.get("price") or {}).get("value") or {}).get("raw")) or 0


def location_of(ad):
    loc = ad.get("locations_resolved") or {}
    for key in ("ADMIN_LEVEL_3_name", "ADMIN_LEVEL_2_name", "ADMIN_LEVEL_1_name"):
        name = loc.get(key)
        if name:
            return name
    return None


def to_front_model(ad, cutoff):
    title = (ad.get("title") or "").strip()
    desc = re.sub(r"\s+", " ", ad.get("description") or "").strip()
    if not title:
        return None, "제목 없음"

    if (ad.get("status") or {}).get("status") != "active":
        return None, "게시 종료(active 아님)"

    try:
        posted = datetime.fromisoformat(ad["created_at"])
    except (KeyError, ValueError):
        return None, "게시일 파싱 실패"
    if posted < cutoff:
        return None, "기간 초과"

    if not TAKEOVER_RE.search(title + " " + desc):
        return None, "인수 매물 표현 없음"

    price_num = price_of(ad)
    if not (MIN_PRICE <= price_num <= MAX_PRICE):
        return None, "가격 타당 범위 밖"

    city = location_of(ad)
    coords = (ad.get("locations") or [{}])[0]
    property_included = (not TRANSFER_TITLE_RE.search(title)
                         and bool(PROPERTY_TITLE_RE.search(title)))

    return {
        "id": "olx-" + str(ad["ad_id"]),
        "type": "bisnis",
        "subtype": "akuisisi",
        "title": title,
        "category": parse_category(title + " " + desc),
        "location": city or "Indonesia",
        "locationKo": LOCATION_KO.get(city, city or "인도네시아"),
        # OLX는 정확한 주소를 공개하지 않는다(시·군 단위). 없는 값을 지어내지 않는다.
        "address": None,
        "monthlyRevenue": None,
        "monthlyRevenueNum": None,
        "profit": None,
        "price": (ad.get("price") or {}).get("value", {}).get("display"),
        "priceNum": int(price_num),
        "established": None,
        "area": None,
        "floors": None,
        "description": desc[:300] + ("..." if len(desc) > 300 else ""),
        "facilities": (["부동산 포함 매각", "운영 중 사업체", "OLX 실시간"]
                       if property_included else ["운영 중 사업체", "OLX 실시간"]),
        # 부동산과 함께 넘기는 매물. 사업체 인수 추천에서 섞이지 않도록 표시.
        "propertyIncluded": property_included,
        # 판매자 이름·연락처는 저장하지 않는다. 문의는 원문 링크로.
        "whatsapp": None,
        "c2c": True,
        "images": "🏪",
        "badge": "부동산+사업체" if property_included else "완전인수",
        "source": "olx.co.id",
        "sourceUrl": f"https://www.olx.co.id/item/{slugify(title)}-iid-{ad['ad_id']}",
        "postedAt": ad["created_at"],
        "lat": coords.get("lat"),
        "lng": coords.get("lon"),
    }, None


def collect(max_age_days):
    cutoff = datetime.now(timezone.utc).astimezone() - timedelta(days=max_age_days)
    ads = {}
    for query in QUERIES:
        for page in range(MAX_PAGES):
            try:
                data = api_search(query, page)
            except urllib.error.HTTPError as e:
                print(f"  '{query}' p{page} HTTP {e.code} - 중단")
                break
            except Exception as e:
                print(f"  '{query}' p{page} {type(e).__name__} - 중단")
                break

            batch = data.get("data") or []
            if not batch:
                break
            for ad in batch:
                ads[ad["ad_id"]] = ad
            # metadata.total_pages 는 신뢰하지 않는다 — size 와 무관하게 항상 1을 준다
            # (2026-08-14 실측: total_ads 93건인데 total_pages=1, page 1·2 에 53건이 더 있었다).
            # 마지막 페이지 판정은 실제 수신 건수로 한다.
            if len(batch) < PAGE_SIZE:
                break
            time.sleep(REQUEST_DELAY_SEC)
        print(f"[수집] '{query}' 누적 유니크 {len(ads)}건")
        time.sleep(REQUEST_DELAY_SEC)

    results, rejected = [], {}
    for ad in ads.values():
        item, reason = to_front_model(ad, cutoff)
        if item is None:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        results.append(item)

    results.sort(key=lambda x: x["postedAt"], reverse=True)
    return results, rejected


def write_outputs(items):
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    # 저장 직전에 한인 인수·운영 가능 판정을 붙인다(사이트 필터가 이 필드를 쓴다).
    enrich.annotate_all(items)
    print("[판정]")
    for line in enrich.summarize(items):
        print(line)
    body = json.dumps(items, ensure_ascii=False, indent=2)
    OUTPUT_JS.write_text(
        "// 자동 생성 파일 — scraper/scrape_olx.py 가 갱신합니다. 직접 수정 금지.\n"
        f"// 갱신 시각: {now}\n"
        f'const OLX_LISTINGS_UPDATED_AT = "{now}";\n'
        f"const OLX_LISTINGS = {body};\n",
        encoding="utf-8")
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(body, encoding="utf-8")
    print(f"[저장] {OUTPUT_JS} ({len(items)}건)")


def main():
    max_age_days = DEFAULT_MAX_AGE_DAYS
    if "--max-age-days" in sys.argv:
        max_age_days = int(sys.argv[sys.argv.index("--max-age-days") + 1])

    items, rejected = collect(max_age_days)
    print("\n[제외 사유]")
    for reason, count in sorted(rejected.items(), key=lambda x: -x[1]):
        print(f"  {count:>4}건  {reason}")
    print(f"\n[결과] 인수 매물 {len(items)}건")
    for x in items[:10]:
        print(f"  - {x['postedAt'][:10]} | {x['price']:<16} | {x['locationKo']:<10} | {x['title'][:45]}")

    if not items:
        print("!! 수집 0건 - 기존 파일을 덮어쓰지 않고 종료")
        sys.exit(1)
    write_outputs(items)


if __name__ == "__main__":
    main()
