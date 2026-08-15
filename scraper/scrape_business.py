# -*- coding: utf-8 -*-
"""
사업체 인수(take over / 완전인수) 매물 실시간 수집기 — tempat-usaha.com

99.co(scrape_99co.py)는 부동산 포털이라 '운영 중인 사업체 인수' 매물이 없다.
tempat-usaha.com 은 WordPress REST API(wp-json)를 공개하고 있고, 게시글 본문에
실제 도로명 주소·가격·매도인 연락처가 들어 있어 인수 매물 소스로 사용한다.

- 수집: /wp-json/wp/v2/posts?search=... (SEARCH_TERMS 로 후보를 모은 뒤 제목/본문을
        TAKEOVER_RE 로 재차 거르는 2단 방식. 검색어만으로는 일반 부동산이 대량 섞인다)
- 결과: js/business_data.js (BUSINESS_LISTINGS) 및 scraper/output/business_listings.json
- 각 매물은 sourceUrl(원본 게시글)을 반드시 갖는다. 역추적 불가한 매물은 만들지 않는다.

사용법:
  py -3 scraper/scrape_business.py              # 기본 수집
  py -3 scraper/scrape_business.py --max-age-days 365
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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enrich  # noqa: E402  한인 인수·운영 가능 판정

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_JS = ROOT / "js" / "business_data.js"
OUTPUT_JSON = Path(__file__).resolve().parent / "output" / "business_listings.json"

API = "https://www.tempat-usaha.com/wp-json/wp/v2/posts"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
REQUEST_DELAY_SEC = 2      # 안전장치: 요청 간 최소 딜레이 (완화 금지)
PER_PAGE = 100

# 1단계: 후보 수집용 검색어
SEARCH_TERMS = ["take over", "takeover", "oper usaha",
                "masih operasional", "sudah berjalan", "dijual bisnis"]

# 2단계: 실제 '사업체 인수'인지 판정. 제목+본문에 이 표현이 있어야 채택한다.
TAKEOVER_RE = re.compile(
    r"take\s?over|takeover|oper\s?usaha|alih\s?usaha|"
    r"(?:usaha|bisnis|resto|restoran|cafe|kafe|salon|barbershop|laundry|gym)\s+"
    r"(?:yang\s+)?(?:masih\s+)?(?:beroperasi|operasional|berjalan|aktif)",
    re.I)

# 게시 후 이 기간이 지난 글은 '아직 매물로 살아있다'고 보기 어렵다.
DEFAULT_MAX_AGE_DAYS = 540

# 가격 타당성 범위 (사업체 인수 기준)
MIN_PRICE = 10_000_000          # Rp 10 jt
MAX_PRICE = 500_000_000_000     # Rp 500 M

LOCATION_KO = {
    "Jakarta Selatan": "자카르타 남부", "Jakarta Utara": "자카르타 북부",
    "Jakarta Timur": "자카르타 동부", "Jakarta Barat": "자카르타 서부",
    "Jakarta Pusat": "자카르타 중부", "Jakarta": "자카르타",
    "Tangerang Selatan": "탕그랑 남부", "Tangerang": "탕그랑",
    "Bekasi": "브카시", "Depok": "데포", "Bogor": "보고르",
    "Bandung": "반둥", "Surabaya": "수라바야", "Semarang": "스마랑",
    "Yogyakarta": "욕야카르타", "Bali": "발리", "Denpasar": "발리",
}

# 업종 추정 (제목/본문 키워드 → 한국어 카테고리)
CATEGORY_RULES = [
    (r"resto|restoran|foodcourt|warteg|warung|kuliner", "카페 & 레스토랑"),
    (r"coffee|kopi|cafe|kafe", "카페 & 레스토랑"),
    (r"salon|barbershop|beauty|kecantikan|spa", "뷰티 & 살롱"),
    (r"gym|fitness|mma|yoga", "피트니스"),
    (r"laundry|londri", "세탁"),
    (r"hotel|kost|guest\s?house|villa", "숙박"),
    (r"minimarket|toko|retail|swalayan", "소매점"),
    (r"peternakan|pertanian|kebun", "농축산"),
    (r"pabrik|manufaktur|produksi", "제조"),
    (r"online|ecommerce|e-commerce|marketplace", "온라인 사업"),
    (r"klinik|apotek|farmasi", "의료 & 약국"),
    (r"sekolah|kursus|bimbel|les", "교육"),
]


def api_get(params):
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept-Language": "id,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8", errors="replace"))


def strip_html(html):
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = (text.replace("&#8211;", "-").replace("&#038;", "&").replace("&amp;", "&")
                .replace("&nbsp;", " ").replace("&#8217;", "'").replace("&quot;", '"'))
    text = re.sub(r"&#\d+;", "", text)
    return re.sub(r"\s+", " ", text).strip()


PRICE_RE = re.compile(
    r"(?:Rp|IDR)\.?\s*([\d][\d\.,]*)\s*(miliar|milyar|jt|juta|ribu|m\b)?", re.I)


def parse_price(text):
    """인도네시아 표기 가격을 정수 루피아로. 타당 범위 내 첫 값을 반환."""
    for m in PRICE_RE.finditer(text):
        raw, unit = m.group(1), (m.group(2) or "").lower()
        num = raw.rstrip(".,")
        try:
            if unit:
                # 단위가 있으면 소수점 표기가 섞인다. 인니식("2,4 M")과 영미식("2.9 M")이
                # 모두 쓰이므로, 마지막 구분자 뒤가 1~2자리면 소수점으로 본다.
                # (이 판정을 빼면 "2.9 M"이 29 M으로 10배 부풀려진다)
                sep = max(num.rfind("."), num.rfind(","))
                if sep != -1 and len(num) - sep - 1 <= 2:
                    value = float(re.sub(r"[.,]", "", num[:sep]) + "." + num[sep + 1:])
                else:
                    value = float(re.sub(r"[.,]", "", num))
                mult = {"miliar": 1e9, "milyar": 1e9, "m": 1e9,
                        "jt": 1e6, "juta": 1e6, "ribu": 1e3}[unit]
                value *= mult
            else:
                # 단위가 없으면 . 와 , 는 천단위 구분자 (예: "650.000.000", "400,000,000")
                value = float(re.sub(r"[.,]", "", num))
        except (ValueError, KeyError):
            continue
        if MIN_PRICE <= value <= MAX_PRICE:
            return int(value)
    return None


ADDRESS_RE = re.compile(
    r"(?:Lokasi|Alamat)\s*[:\-]\s*(.{8,140}?)"
    r"(?=\s*(?:Luas|Harga|Keterangan|Spesifikasi|Fasilitas|Hub|Kontak|$))", re.I)
# 대소문자를 구분한다. 소문자 "jalan dan ..." 같은 일반 문장이 주소로 잡히는 것을 막기 위해
# 'Jl.'/'Jalan' 뒤에 대문자로 시작하는 고유명사가 와야 주소로 인정한다.
JALAN_RE = re.compile(r"((?:Jl\.?|Jalan)\s+[A-Z][^,]{2,60}(?:,[^,]{3,40}){0,4})")
# 주소로 보기 어려운 홍보 문구가 섞였는지 확인
NOT_ADDRESS_RE = re.compile(r"autopilot|running|profit|omzet|hubungi|whatsapp", re.I)


def parse_address(text):
    for candidate in (ADDRESS_RE.search(text), JALAN_RE.search(text)):
        if not candidate:
            continue
        value = candidate.group(1).strip(" .,-")
        if NOT_ADDRESS_RE.search(value):
            continue
        return value
    return None


PHONE_RE = re.compile(r"(?:\+?62|0)8\d{1,3}[\s\-]?\d{3,4}[\s\-]?\d{3,5}")


def parse_phone(text):
    m = PHONE_RE.search(text)
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(0))
    if digits.startswith("0"):
        digits = "62" + digits[1:]
    return "+" + digits if 11 <= len(digits) <= 15 else None


def parse_city(text):
    for name in sorted(LOCATION_KO, key=len, reverse=True):
        if re.search(r"\b" + re.escape(name) + r"\b", text, re.I):
            return name
    return None


def parse_category(text):
    for pattern, label in CATEGORY_RULES:
        if re.search(pattern, text, re.I):
            return label
    return "기타 사업"


def parse_area(text):
    m = re.search(r"Luas\s*(?:Bangunan|Tanah)?\s*[:\-]?\s*([\d\.,]+)\s*m2", text, re.I)
    if not m:
        return None
    try:
        return float(m.group(1).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def to_front_model(post):
    title = strip_html(post["title"]["rendered"])
    body = strip_html(post["content"]["rendered"])
    blob = title + " " + body

    if not TAKEOVER_RE.search(blob):
        return None, "인수 매물 표현 없음"

    price_num = parse_price(blob)
    if not price_num:
        return None, "가격 파싱 실패 또는 타당 범위 밖"

    address = parse_address(body)
    if not address:
        return None, "주소 없음"

    city = parse_city(address) or parse_city(blob)
    price_jt = price_num / 1_000_000
    price_label = (f"Rp {price_num / 1e9:,.2f} M".replace(".00", "")
                   if price_num >= 1e9 else f"Rp {price_jt:,.0f} jt")

    return {
        "id": "biz-" + str(post["id"]),
        "type": "bisnis",
        "subtype": "akuisisi",
        "title": title,
        "category": parse_category(blob),
        "location": city or "Indonesia",
        "locationKo": LOCATION_KO.get(city, city or "인도네시아"),
        "address": address,
        # 월매출/수익률은 원문이 제공하지 않는다. 추정치를 만들어 넣지 않는다.
        "monthlyRevenue": None,
        "monthlyRevenueNum": None,
        "profit": None,
        "price": price_label,
        "priceNum": price_num,
        "established": None,
        "area": parse_area(body),
        "floors": None,
        "description": body[:300] + ("..." if len(body) > 300 else ""),
        "facilities": ["운영 중 사업체", "tempat-usaha.com 수집"],
        "whatsapp": parse_phone(body),
        "c2c": False,
        "images": "🏪",
        "badge": "완전인수",
        "source": "tempat-usaha.com",
        "sourceUrl": post["link"],
        "postedAt": post["date"],
        "lat": None,
        "lng": None,
    }, None


def collect(max_age_days):
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    candidates, seen = {}, set()

    for term in SEARCH_TERMS:
        page = 1
        while True:
            try:
                posts = api_get({"search": term, "per_page": PER_PAGE, "page": page,
                                 "_fields": "id,date,link,title,content"})
            except urllib.error.HTTPError as e:
                if e.code == 400:      # 마지막 페이지 초과
                    break
                raise
            if not posts:
                break
            for p in posts:
                if p["id"] not in seen:
                    seen.add(p["id"])
                    candidates[p["id"]] = p
            print(f"[수집] '{term}' p{page} → {len(posts)}건 (누적 후보 {len(candidates)})")
            if len(posts) < PER_PAGE:
                break
            page += 1
            time.sleep(REQUEST_DELAY_SEC)
        time.sleep(REQUEST_DELAY_SEC)

    results, rejected = [], {}
    for p in candidates.values():
        try:
            posted = datetime.fromisoformat(p["date"]).replace(tzinfo=timezone.utc)
        except ValueError:
            rejected["게시일 파싱 실패"] = rejected.get("게시일 파싱 실패", 0) + 1
            continue
        if posted < cutoff:
            rejected["기간 초과"] = rejected.get("기간 초과", 0) + 1
            continue

        item, reason = to_front_model(p)
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
        "// 자동 생성 파일 — scraper/scrape_business.py 가 갱신합니다. 직접 수정 금지.\n"
        f"// 갱신 시각: {now}\n"
        f'const BUSINESS_LISTINGS_UPDATED_AT = "{now}";\n'
        f"const BUSINESS_LISTINGS = {body};\n",
        encoding="utf-8")
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(body, encoding="utf-8")
    print(f"[저장] {OUTPUT_JS} ({len(items)}건)")
    print(f"[저장] {OUTPUT_JSON}")


def main():
    max_age_days = DEFAULT_MAX_AGE_DAYS
    if "--max-age-days" in sys.argv:
        max_age_days = int(sys.argv[sys.argv.index("--max-age-days") + 1])

    items, rejected = collect(max_age_days)
    print("\n[제외 사유]")
    for reason, count in sorted(rejected.items(), key=lambda x: -x[1]):
        print(f"  {count:>4}건  {reason}")
    print(f"\n[결과] 인수 매물 {len(items)}건")

    if not items:
        print("!! 수집된 매물이 0건 - 기존 파일을 덮어쓰지 않고 종료")
        sys.exit(1)
    write_outputs(items)


if __name__ == "__main__":
    main()
