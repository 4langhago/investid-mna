# -*- coding: utf-8 -*-
"""
99.co (인도네시아) 실시간 매물 수집기
- 검색 페이지의 __NEXT_DATA__ JSON을 파싱하여 매물을 수집
- 결과를 js/live_data.js (프론트 자동 병합)와 scraper/output/live_listings.json에 저장
- Supabase 연동 시: SUPABASE_URL / SUPABASE_SERVICE_KEY 환경변수를 설정하면 DB에도 upsert

사용법:
  py -3 scraper/scrape_99co.py                 # 기본 지역 전체 수집
  py -3 scraper/scrape_99co.py tangerang depok # 특정 지역만

주의: 요청 간 딜레이(기본 2.5초)를 유지하세요. 과도한 요청은 차단 및 상대 서버 부담을 유발합니다.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request

# Windows 콘솔(cp949)에서도 로그 출력이 깨지지 않도록
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_JS = ROOT / "js" / "live_data.js"
OUTPUT_JSON = Path(__file__).resolve().parent / "output" / "live_listings.json"

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
REQUEST_DELAY_SEC = 15   # 안전장치: 요청 간 최소 딜레이 (완화 금지 — 2.5초에서는 429 발생 확인)
RETRY_BACKOFF_SEC = 45   # 429 수신 시 대기 후 1회 재시도

# 수집 대상: (도시 슬러그, 매물종류 슬러그) — https://www.99.co/id/jual/{type}/{city}
DEFAULT_TARGETS = [
    ("tangerang", "ruko"),
    ("jakarta-selatan", "ruko"),
    ("jakarta-barat", "ruko"),
    ("depok", "ruko"),
    ("bekasi", "ruko"),
    ("bandung", "ruko"),
    ("surabaya", "ruko"),
]

LOCATION_KO = {
    "Tangerang": "탕그랑", "Tangerang Selatan": "탕그랑 남부",
    "Jakarta Selatan": "자카르타 남부", "Jakarta Utara": "자카르타 북부",
    "Jakarta Timur": "자카르타 동부", "Jakarta Barat": "자카르타 서부",
    "Jakarta Pusat": "자카르타 중부",
    "Depok": "데포", "Bekasi": "브카시", "Bandung": "반둥",
    "Surabaya": "수라바야", "Semarang": "스마랑", "Bali": "발리", "Badung": "발리",
}

# 99.co property_type → 프론트 type/카테고리
TYPE_MAP = {
    "ruko": ("ruko", "루코"),
    "rumah": ("properti", "주택"),
    "apartemen": ("properti", "아파트"),
    "tanah": ("properti", "토지"),
    "gedung": ("properti", "사무실 빌딩"),
    "villa": ("properti", "빌라 & 부동산"),
}


def fetch_html(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Language": "id,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8", errors="replace")


def parse_next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("__NEXT_DATA__ 블록을 찾을 수 없음 (페이지 구조 변경 가능성)")
    return json.loads(m.group(1))


def extract_listings(next_data):
    groups = (next_data.get("props", {}).get("pageProps", {})
              .get("data", {}).get("listings", []))
    items = []
    for g in groups:
        items.extend(g.get("data", []) or [])
    return items


def to_front_model(item):
    """99.co 매물 → 프론트 LISTINGS 모델"""
    attrs = item.get("attributes") or {}
    price = item.get("price") or {}
    loc = item.get("location") or {}
    city = (loc.get("city") or {}).get("name") or ""
    district = (loc.get("district") or {}).get("name") or city
    coord = ((loc.get("district") or {}).get("coordinate")
             or (loc.get("city") or {}).get("coordinate") or {})

    ptype = ((attrs.get("property_type") or {}).get("value") or "").lower()
    front_type, category = TYPE_MAP.get(ptype, ("properti", ptype or "부동산"))
    price_type = ((attrs.get("price_type") or {}).get("value") or "").lower()
    subtype = "sewa" if price_type == "rent" else "jual"

    builtup = (attrs.get("builtup_area") or {}).get("value")
    land = (attrs.get("land_area") or {}).get("value")
    cert = (attrs.get("certificate") or {}).get("value")

    facilities = []
    if land and land not in ("0", ""):
        facilities.append(f"토지 {land}m²")
    if cert and cert not in ("-", ""):
        facilities.append(f"{cert} 증서")
    if attrs.get("floors"):
        facilities.append(f"{attrs['floors']}층")
    facilities.append("99.co 실시간")

    desc = re.sub(r"\s+", " ", item.get("description") or "").strip()
    if len(desc) > 300:
        desc = desc[:297] + "..."

    price_num = price.get("price") or price.get("last_price") or 0
    if not price_num or not item.get("title"):
        return None  # 필수값 없는 매물 제외

    return {
        "id": "live-" + str(item.get("business_id") or item.get("id")),
        "type": front_type,
        "subtype": subtype,
        "title": item.get("title"),
        "category": category,
        "location": city,
        "locationKo": LOCATION_KO.get(city, LOCATION_KO.get(district, city)),
        "monthlyRevenue": None,
        "monthlyRevenueNum": None,
        "profit": None,
        "price": price.get("price_tag") or f"Rp {price_num:,}",
        "priceNum": price_num,
        "established": None,
        "area": float(builtup) if builtup and builtup not in ("0", "") else None,
        "floors": attrs.get("floors") or None,
        "description": desc,
        "facilities": facilities,
        "whatsapp": None,  # 연락은 원문 링크를 통해
        "c2c": False,
        "images": "🛰️",
        "badge": "임대" if subtype == "sewa" else "매매",
        "source": "99.co",
        "sourceUrl": "https://www.99.co/id/properti/" + item["url"] if item.get("url") else None,
        "lat": coord.get("lat"),
        "lng": coord.get("lng"),
    }


def scrape(targets):
    results, seen = [], set()
    for city, ptype in targets:
        url = f"https://www.99.co/id/jual/{ptype}/{city}"
        try:
            print(f"[수집] {url}")
            try:
                html = fetch_html(url)
            except urllib.error.HTTPError as e:
                if e.code != 429:
                    raise
                print(f"  .. 429 rate limit, {RETRY_BACKOFF_SEC}초 대기 후 재시도")
                time.sleep(RETRY_BACKOFF_SEC)
                html = fetch_html(url)
            items = extract_listings(parse_next_data(html))
            ok = 0
            for it in items:
                mapped = to_front_model(it)
                if mapped and mapped["id"] not in seen:
                    seen.add(mapped["id"])
                    results.append(mapped)
                    ok += 1
            print(f"  → {ok}건 수집")
        except Exception as e:
            print(f"  !! 실패 (스킵): {e}")
        time.sleep(REQUEST_DELAY_SEC)
    return results


def write_outputs(listings):
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(json.dumps(
        {"updatedAt": now, "count": len(listings), "listings": listings},
        ensure_ascii=False, indent=2), encoding="utf-8")

    js = ("// 자동 생성 파일 — scraper/scrape_99co.py 가 갱신합니다. 직접 수정 금지.\n"
          f"// 갱신 시각: {now}\n"
          f"const LIVE_LISTINGS_UPDATED_AT = {json.dumps(now)};\n"
          f"const LIVE_LISTINGS = {json.dumps(listings, ensure_ascii=False, indent=2)};\n")
    OUTPUT_JS.write_text(js, encoding="utf-8")
    print(f"[저장] {OUTPUT_JS} ({len(listings)}건)")
    print(f"[저장] {OUTPUT_JSON}")


def main():
    args = sys.argv[1:]
    targets = [(c, "ruko") for c in args] if args else DEFAULT_TARGETS
    listings = scrape(targets)
    if not listings:
        print("!! 수집 결과 0건 — 기존 live_data.js를 덮어쓰지 않고 종료합니다.")
        sys.exit(1)
    write_outputs(listings)


if __name__ == "__main__":
    main()
