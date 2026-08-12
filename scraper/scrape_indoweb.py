# -*- coding: utf-8 -*-
"""
인도웹(indoweb.org) 한인 커뮤니티 사업체·부동산 매물 수집기

한국인이 실제로 인수해 운영하는 매물은 인니 현지 사이트(99.co/OLX)보다
한인 커뮤니티에 먼저 올라온다. 그래서 이 수집기가 추천 파이프라인의 1순위 소스다.

게시판 선정 근거 (2026-08-11 실측, 1페이지 기준):
  real_estate_mb  28건 — 실제 매매/양도 글이 매일 올라오는 주력 게시판
  biz_promo       28건 — 업체 양도 글이 홍보글에 섞여 올라옴
  real_estate      4건 — 사실상 방치된 구 게시판(부동산 업체 광고 위주)
  market          28건 — 벼룩시장. 대부분 중고물품이나 사업체 매각 글이 가끔 섞임
  ※ 기존 코드는 real_estate + market 만 봤고 real_estate_mb 를 놓쳐 수집이 1건에
    그쳤다. 오늘 올라온 '끌라빠가딩 카페 양도', '땅그랑 세차장 매각'이 모두 그 게시판이다.

수집 범위 (개인정보 취급):
  ⚠️ 글쓴이 이름·휴대폰 번호·이메일·카카오 ID 는 저장하지 않는다(MASK_RE 로 제거).
  robots.txt 의 크롤링 허용은 색인 허용일 뿐 재게시 허락이 아니므로, 본문을 통째로
  옮기지 않고 게시판이 제공하는 정형 항목(거래분류/주소/가격/형태/면적)과 본문에서
  추출한 사실 요약만 보관한다. 상세와 연락은 sourceUrl 로 원문에 보낸다.

수집 예의:
  이 사이트는 연속 요청 시 SSL 핸드셰이크 타임아웃으로 응답을 끊는다.
  REQUEST_DELAY_SEC(기본 12초)를 줄이지 말 것.
  상세 페이지까지 받으므로 --pages 를 크게 주면 실행이 길어진다(1요청당 12초).

사용법:
  py -3 scraper/scrape_indoweb.py
  py -3 scraper/scrape_indoweb.py --pages 3
  py -3 scraper/scrape_indoweb.py --no-detail   # 목록만(빠름, 상세 정보 없음)
"""
import html as html_lib
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_JS = ROOT / "js" / "community_data.js"
OUTPUT_JSON = Path(__file__).resolve().parent / "output" / "community_listings.json"

BASE = "https://indoweb.org/love/bbs/board.php"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
REQUEST_DELAY_SEC = 12   # 안전장치: 완화 금지 (짧으면 사이트가 연결을 끊는다)
RETRY_SLEEP_SEC = 15
BOARDS = [
    ("real_estate_mb", "부동산·업체 매매(주력)"),
    ("biz_promo", "업체 홍보·양도"),
    ("real_estate", "업체 부동산 매물(구)"),
    ("market", "벼룩시장"),
]
DEFAULT_PAGES = 3
# 커뮤니티 글은 팔린 뒤에도 지워지지 않고 남는다. 오래된 글은 매물로 보지 않는다.
MAX_AGE_DAYS = 365

# 사업체 매매글 판정: '거래' 표현 + '사업체' 표현이 함께 있어야 채택
DEAL_RE = re.compile(r"매매|매각|양도|양수|인수|넘김|넘깁|권리금|팝니다|판매합니다|처분")
BIZ_RE = re.compile(
    r"식당|레스토랑|카페|커피|베이커리|제과|호프|주점|바\b|"
    r"가게|점포|상가|매장|사업체|업체|공장|법인|브랜드|프랜차이즈|루꼬|루코|"
    r"미용실|살롱|마사지|스파|헬스|짐\b|학원|유치원|세차장|정비소|"
    r"세탁|런드리|편의점|마트|숙소|호텔|게스트하우스|펜션|농장|창고|사무실|"
    r"토지|땅|부지|주택|아파트|빌라")
# 매물이 아닌 글 걸러내기.
# '임대/렌트'는 인수 대상이 아니므로 여기서 뺀다 — 인도웹 부동산 게시판 글의 절반이
# 임대 안내라, 이걸 거르지 않으면 추천 목록이 임대 광고로 채워진다.
NOISE_RE = re.compile(r"구인|구직|채용|모집|후원|공모전|문의드립니다|추천해주세요|"
                      r"찾습니다|구합니다|광고/제휴|임대|렌트|월세\s*놓|세놓|"
                      r"인사드립니다|안내드립니다")

LOCATION_KO = {
    "자카르타": "자카르타", "땅그랑": "탕그랑", "탕그랑": "탕그랑",
    "브카시": "브카시", "찌까랑": "찌까랑", "데뽁": "데포", "데포": "데포",
    "보고르": "보고르", "반둥": "반둥", "수라바야": "수라바야",
    "스마랑": "스마랑", "발리": "발리", "메단": "메단", "찌비뚱": "찌비뚱",
    "가딩세르퐁": "가딩 세르퐁", "BSD": "BSD", "끌라빠가딩": "끌라빠 가딩",
}

PRICE_RE = re.compile(r"(\d[\d,\.]*)\s*(억|천만|만불|백만|만|미불|USD|\$)?", re.I)


def fetch(url, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                       "Accept-Language": "ko,id;q=0.8"})
            with urllib.request.urlopen(req, timeout=40) as res:
                return res.read().decode("utf-8", errors="replace")
        except Exception as e:
            if attempt == tries - 1:
                raise
            print(f"    (재시도 {attempt + 1}: {type(e).__name__})")
            time.sleep(RETRY_SLEEP_SEC)


# 행의 시작을 td_subject 로 잡는다. 앞의 번호 칸(td_num)은 공지글에서 숫자가 아니거나
# page 파라미터 유무에 따라 마크업이 달라져, 이를 앵커로 쓰면 매치가 통째로 실패한다.
ROW_RE = re.compile(
    # 제목 칸 안에 분류 링크(bo_cate_link, 예: "식당/식품 |")가 먼저 오는 게시판이 있어
    # td_subject 와 글 링크 사이를 열어 둔다. wr_id 뒤에도 &page=N 등이 더 붙을 수 있다.
    r'<td class="td_subject">.*?<a href="(?P<url>[^"]*?wr_id=(?P<wr_id>\d+)[^"]*)"[^>]*>\s*'
    r'(?P<title>.*?)\s*</a>.*?'
    r'<td class="td_date">\s*(?P<date>[\d\.\-:]+)\s*</td>',
    re.S)


def parse_rows(page_html, board, board_ko):
    """목록 페이지에서 제목/링크/날짜만 뽑는다. 글쓴이·이메일은 의도적으로 수집하지 않는다."""
    items = []
    for m in ROW_RE.finditer(page_html):
        title = html_lib.unescape(re.sub(r"<[^>]+>", "", m.group("title"))).strip()
        if not title:
            continue
        items.append({
            "wr_id": m.group("wr_id"),
            "title": title,
            "date": m.group("date").strip(),
            "url": html_lib.unescape(m.group("url")).replace(":443", ""),
            "board": board,
            "boardKo": board_ko,
        })
    return items


def parse_date(raw):
    """gnuboard 목록의 날짜. 'YY-MM-DD' / 'MM-DD'(올해) / 'HH:MM'(오늘) 형태."""
    raw = raw.strip()
    # 당일 글은 시각만 표시된다. 이걸 파싱하지 못해 가장 신선한 매물이 통째로
    # 버려지고 있었다(예: 오늘 올라온 '끌라빠가딩 카페 양도').
    if re.fullmatch(r"\d{1,2}:\d{2}", raw):
        return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    for fmt, full in (("%Y-%m-%d", True), ("%y-%m-%d", True), ("%m-%d", False)):
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if not full:
            dt = dt.replace(year=datetime.now(timezone.utc).year)
        return dt.replace(tzinfo=timezone.utc)
    return None


def parse_location(title):
    for key, ko in LOCATION_KO.items():
        if key in title:
            return ko
    return None


def is_business_deal(title):
    if NOISE_RE.search(title):
        return False, "매물 아님(구인·문의 등)"
    if not DEAL_RE.search(title):
        return False, "거래 표현 없음"
    if not BIZ_RE.search(title):
        return False, "사업체 표현 없음"
    return True, ""


# --- 상세 페이지 ------------------------------------------------------------
# real_estate_mb 게시판은 본문이 '라벨/값' 표로 되어 있다.
#   거래 분류 / 주소 / 상세 주소 / 매물 가격 / 건물 형태 / 면적/방 / 연락처 / 설명
# 이 정형 항목만 뽑으면 본문 전재 없이도 매물을 판단할 수 있다.
FIELD_RE = re.compile(
    r'<td[^>]*font-weight:\s*bold;?[^>]*>\s*(?P<label>[^<]{1,20}?)\s*</td>\s*'
    r'<td[^>]*>(?P<value>.*?)</td>', re.S)

# 저장 전에 지우는 개인정보. 남기면 본인 동의 없는 개인정보 처리가 된다.
MASK_RE = [
    (re.compile(r"(?:\+?62|0)\s?8[\d\-\s\.]{7,14}"), "[연락처 비공개]"),
    (re.compile(r"[\w\.\-]+@[\w\.\-]+\.\w+"), "[이메일 비공개]"),
    (re.compile(r"(?:카톡|카카오톡|카카오|kakao|line|위챗|wechat)\s*(?:id)?\s*[:：]?\s*[\w\.\-]+",
                re.I), "[메신저 ID 비공개]"),
]

# 인니 루피아·달러 표기를 숫자로. 커뮤니티 글은 'Rp 1.500.000.000', '28,000 USD',
# '15억 루피아', '150jt' 등 표기가 제각각이라 단위를 명시적으로 처리한다.
USD_TO_IDR = 16_000  # 환산은 규모 비교용 근사치. 표시가는 항상 원문 표기를 함께 남긴다.


def _digits(raw):
    """'1.500.000.000' / '28,000' → 1500000000 / 28000. 구분자만 제거한다."""
    return float(re.sub(r"[^\d]", "", raw) or 0)


def parse_price(raw):
    """(표시 문자열, 루피아 환산값) 반환. 해석 불가면 (원문, None)."""
    if not raw:
        return None, None
    text = raw.strip()
    low = text.lower()
    m = re.search(r"([\d][\d,\.]*)", text)
    if not m:
        return text, None
    n = _digits(m.group(1))
    if n <= 0:
        return text, None

    if re.search(r"usd|\$|달러|불\b", low):
        return f"{text} (≈ Rp {n * USD_TO_IDR:,.0f})", n * USD_TO_IDR
    if re.search(r"\b(?:m|miliar|milyar)\b|십억", low):
        return text, n * 1_000_000_000
    if re.search(r"\bjt\b|juta|백만", low):
        return text, n * 1_000_000
    if "억" in low:
        return text, n * 100_000_000        # 한국어 '억' 은 원화가 아니라 루피아 억으로 쓰인다
    return text, n


def strip_html(fragment):
    text = re.sub(r"<(?:script|style)[^>]*>.*?</(?:script|style)>", " ", fragment, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def mask_personal(text):
    for pat, repl in MASK_RE:
        text = pat.sub(repl, text)
    return text


def parse_detail(page_html):
    """상세 페이지에서 정형 항목과 본문 사실 요약을 뽑는다. 개인정보는 제거한다."""
    body = re.search(r'id="bo_v_con"[^>]*>(.*?)(?:<div[^>]*id="bo_v_share"|</section>)',
                     page_html, re.S)
    con = body.group(1) if body else page_html

    fields = {}
    for m in FIELD_RE.finditer(con):
        label = m.group("label").replace(" ", "")
        value = strip_html(m.group("value"))
        if value:
            fields[label] = value
    # 연락처 칸은 개인정보라 통째로 버린다.
    fields.pop("연락처", None)

    # 설명 본문: 표 뒤쪽 텍스트. 전재하지 않고 길이를 제한하고 개인정보를 지운다.
    desc = ""
    tail = re.split(r"설명\s*<", con, maxsplit=1)
    if len(tail) == 2:
        desc = strip_html(tail[1])
    if not desc:
        desc = strip_html(con)
    desc = mask_personal(desc)
    # 이미지 파일명·게시판 UI 문자열이 섞여 들어오는 것을 제거
    desc = re.sub(r"\S+\.(?:jpg|jpeg|png|gif)\b", " ", desc, flags=re.I)
    desc = re.sub(r"(?m)^\s*(?:목록|답변|수정|삭제|이전글|다음글|댓글|추천|조회)\s*$", "", desc)
    desc = re.sub(r"\n{2,}", "\n", desc).strip()
    if len(desc) > 900:
        desc = desc[:897] + "..."

    # 사진: 매물 판단에 결정적이다(간판·집기·상태). 이미지 파일을 복제하지 않고
    # 원문 서버의 URL 만 보관한다.
    photos = []
    for m in re.finditer(r'href="(https://indoweb\.org[^"]*view_image\.php[^"]*)"', page_html):
        url = html_lib.unescape(m.group(1)).replace(":443", "")
        if url not in photos:
            photos.append(url)
    # 목록 제목은 길면 '…'로 잘려 있다. 상세 페이지의 전체 제목으로 바로잡는다.
    m = re.search(r'id="bo_v_title"[^>]*>(.*?)</h\d>', page_html, re.S)
    full_title = strip_html(m.group(1)) if m else ""

    return fields, desc, photos[:5], full_title


def detail_url(row):
    url = row["url"]
    if url.startswith("http"):
        return url
    return "https://indoweb.org/love/bbs/" + url.lstrip("./")


def apply_detail(item, fields, desc, photos=()):
    """상세 페이지에서 얻은 값을 매물 모델에 채운다."""
    item["description"] = desc or None
    item["indexOnly"] = not (fields or desc)
    item["photoUrls"] = list(photos)

    if fields.get("거래분류"):
        item["dealType"] = fields["거래분류"]
    addr = fields.get("주소") or fields.get("상세주소")
    if addr:
        item["address"] = mask_personal(addr)
    if fields.get("건물형태"):
        item["category"] = f"한인 커뮤니티 매물 · {fields['건물형태']}"

    price_raw = fields.get("매물가격") or fields.get("가격")
    if not price_raw:
        m = re.search(r"(?:가격|매매가|권리금|인수가)\s*[:\-]?\s*([^\n]{1,40})", desc or "")
        price_raw = m.group(1).strip() if m else None
    if price_raw:
        shown, num = parse_price(price_raw)
        item["price"] = shown
        item["priceNum"] = num

    area = fields.get("면적/방") or fields.get("면적")
    if area:
        m = re.search(r"([\d,\.]+)\s*(?:㎡|m2|m²)", area)
        if m:
            item["area"] = _digits(m.group(1))
        item["facilities"] = list(dict.fromkeys(
            (item.get("facilities") or []) + [f"면적/방: {area}"]))
    return item


def to_model(row):
    # 게시판 분류 접두어("식당/식품 | ")와 목록 UI 꼬리표("댓글1개", "좋아요2") 제거.
    # 꼬리표가 붙은 채로 두면 제목이 잘려 보이고 키워드 판정도 어긋난다.
    title = re.sub(r"^[^|]{1,12}\|\s*", "", row["title"]).strip()
    title = re.sub(r"\s*(?:댓글\s*\d+\s*개|좋아요\s*\d+|새글|인기글)\s*", " ", title).strip()
    title = re.sub(r"\s{2,}", " ", title)

    ok, reason = is_business_deal(title)
    if not ok:
        return None, reason

    posted = parse_date(row["date"])
    if posted is None:
        return None, "게시일 파싱 실패"
    if posted < datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS):
        return None, f"게시 {MAX_AGE_DAYS}일 초과"

    return {
        "id": f"iw-{row['board']}-{row['wr_id']}",
        "type": "bisnis",
        "subtype": "akuisisi",
        "title": title,
        "category": "한인 커뮤니티 매물",
        "dealType": None,
        "board": row["boardKo"],
        "location": "Indonesia",
        "locationKo": parse_location(row["title"]) or "지역 미상",
        # 색인형: 본문/주소/연락처는 저장하지 않는다. 상세는 원문에서 확인.
        "address": None,
        "description": None,
        "whatsapp": None,
        "monthlyRevenue": None,
        "monthlyRevenueNum": None,
        "profit": None,
        "price": None,
        "priceNum": None,
        "area": None,
        "floors": None,
        "established": None,
        "facilities": ["한인 커뮤니티", "원문 링크에서 상세 확인"],
        "c2c": False,
        "images": "🇰🇷",
        "photoUrls": [],
        "badge": "커뮤니티 매물",
        "source": "indoweb.org",
        "sourceUrl": detail_url(row),
        "postedAt": posted.isoformat(),
        "indexOnly": True,
        "lat": None,
        "lng": None,
    }, None


# 매물 글은 아니지만 '매물을 가진 곳'인 한인 중개·컨설팅 업체 글.
# 공개 크롤링으로 잡히는 매물은 빙산의 일각이고, 실제 물건은 이런 업체가 들고 있다.
# 그래서 매물에서 걸러낸 글 중 이 유형만 따로 모아 연락 채널 목록으로 남긴다.
BROKER_RE = re.compile(
    r"부동산|컨설팅|법인\s*설립|인허가|비자|KITAS|회계|세무|중개|매물\s*(?:문의|안내)|"
    r"공장\s*(?:임대|매매)\s*전문|투자\s*자문")
BROKER_JSON = Path(__file__).resolve().parent / "output" / "korean_brokers.json"


def collect_brokers(rows, sink):
    """중개·컨설팅 업체 글을 모은다. 업체명은 제목에 드러난 것만, 연락처는 담지 않는다."""
    for row in rows:
        title = re.sub(r"\s*(?:댓글\s*\d+\s*개|좋아요\s*\d+)\s*", " ", row["title"]).strip()
        if not BROKER_RE.search(title) or DEAL_RE.search(title):
            continue
        posted = parse_date(row["date"])
        sink.append({
            "title": title,
            "board": row["boardKo"],
            "postedAt": posted.isoformat() if posted else None,
            "sourceUrl": detail_url(row),
        })


def collect(pages, with_detail=True):
    results, rejected, brokers = [], {}, []
    for board, board_ko in BOARDS:
        for page in range(1, pages + 1):
            url = f"{BASE}?bo_table={board}&page={page}"
            print(f"[수집] {url}")
            try:
                page_html = fetch(url)
            except Exception as e:
                print(f"  실패({type(e).__name__}) - 이 페이지 건너뜀")
                time.sleep(REQUEST_DELAY_SEC)
                continue

            rows = parse_rows(page_html, board, board_ko)
            print(f"  → 목록 {len(rows)}건")
            collect_brokers(rows, brokers)
            for row in rows:
                item, reason = to_model(row)
                if item is None:
                    rejected[reason] = rejected.get(reason, 0) + 1
                    continue
                results.append(item)
            time.sleep(REQUEST_DELAY_SEC)

    seen, unique = set(), []
    for item in results:
        if item["id"] not in seen:
            seen.add(item["id"])
            unique.append(item)
    unique.sort(key=lambda x: x["postedAt"] or "", reverse=True)

    seen_b, uniq_b = set(), []
    for b in sorted(brokers, key=lambda x: x["postedAt"] or "", reverse=True):
        if b["sourceUrl"] not in seen_b:
            seen_b.add(b["sourceUrl"])
            uniq_b.append(b)
    BROKER_JSON.parent.mkdir(parents=True, exist_ok=True)
    BROKER_JSON.write_text(json.dumps(uniq_b[:30], ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"[저장] 한인 중개·컨설팅 채널 {len(uniq_b)}건 → {BROKER_JSON.name}")

    if with_detail:
        # 목록만으로는 가격·면적·영업현황을 알 수 없어 '운영 가능한 매물'인지 판단할 수 없다.
        # 채택된 글에 한해서만 상세를 받는다(요청 수를 최소화하기 위함).
        for item in unique:
            url = item["sourceUrl"]
            print(f"[상세] {item['title'][:40]} …")
            try:
                fields, desc, photos, full_title = parse_detail(fetch(url))
            except Exception as e:
                print(f"  상세 실패({type(e).__name__}) - 목록 정보만 사용")
                time.sleep(REQUEST_DELAY_SEC)
                continue
            apply_detail(item, fields, desc, photos)
            if full_title and len(full_title) > len(item["title"]):
                item["title"] = full_title
            # 분류 접두어("식당/식품 | ")는 목록·상세 어느 쪽에서 온 제목이든 붙어 있을 수 있다.
            item["title"] = re.sub(r"^[^|]{1,20}\|\s*", "", item["title"]).strip()
            time.sleep(REQUEST_DELAY_SEC)

    return unique, rejected


def write_outputs(items):
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    body = json.dumps(items, ensure_ascii=False, indent=2)
    OUTPUT_JS.write_text(
        "// 자동 생성 파일 — scraper/scrape_indoweb.py 가 갱신합니다. 직접 수정 금지.\n"
        "// 제목/지역/게시일/가격/면적/사실 요약만 보관한다.\n"
        "// 글쓴이 이름·전화·이메일·메신저 ID 는 저장하지 않는다(MASK_RE 로 제거).\n"
        f"// 갱신 시각: {now}\n"
        f'const COMMUNITY_LISTINGS_UPDATED_AT = "{now}";\n'
        f"const COMMUNITY_LISTINGS = {body};\n",
        encoding="utf-8")
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_JSON.write_text(body, encoding="utf-8")
    print(f"[저장] {OUTPUT_JS} ({len(items)}건)")


def main():
    pages = DEFAULT_PAGES
    if "--pages" in sys.argv:
        pages = int(sys.argv[sys.argv.index("--pages") + 1])

    items, rejected = collect(pages, with_detail="--no-detail" not in sys.argv)
    print("\n[제외 사유]")
    for reason, count in sorted(rejected.items(), key=lambda x: -x[1]):
        print(f"  {count:>4}건  {reason}")
    print(f"\n[결과] 커뮤니티 매물 {len(items)}건")
    for x in items:
        print(f"  - {(x['postedAt'] or '')[:10]} [{x['locationKo']}] "
              f"{x['title'][:50]} | {x.get('price') or '가격 미표기'}")

    if not items:
        print("!! 수집 0건 - 기존 파일을 덮어쓰지 않고 종료")
        sys.exit(1)
    write_outputs(items)


if __name__ == "__main__":
    main()
