# -*- coding: utf-8 -*-
"""
인도웹(indoweb.org) 한인 커뮤니티 사업체 매물 — 색인형(index-only) 수집기

⚠️ 이 수집기는 의도적으로 '색인'만 만든다. 본문·글쓴이·연락처·이메일은 저장하지 않는다.

이유:
  게시글은 개인이 작성한 저작물이고 본문에 개인 휴대폰 번호가 들어 있다.
  robots.txt 가 크롤링을 허용(User-agent: * / Allow: /)하는 것은 색인 허용일 뿐
  재게시 허락이 아니다. 원문과 개인정보를 그대로 옮기면 저작물 무단 전재 및
  개인정보 무단 처리(인니 PDP법·국내 개인정보보호법)에 해당할 수 있다.
  따라서 제목·지역·가격·게시일만 보관하고, 상세는 sourceUrl 로 원문에 보낸다.
  ── 전체 전재가 필요하면 인도웹 운영진 제휴 동의와 게시자 동의를 먼저 확보할 것.

수집 예의:
  이 사이트는 연속 요청 시 SSL 핸드셰이크 타임아웃으로 응답을 끊는다.
  REQUEST_DELAY_SEC(기본 12초)를 줄이지 말 것.

사용법:
  py -3 scraper/scrape_indoweb.py
  py -3 scraper/scrape_indoweb.py --pages 3
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
BOARDS = [("real_estate", "업체 부동산 매물"), ("market", "벼룩시장")]
DEFAULT_PAGES = 2
# 커뮤니티 글은 팔린 뒤에도 지워지지 않고 남는다. 오래된 글은 매물로 보지 않는다.
MAX_AGE_DAYS = 365

# 사업체 매매글 판정: '거래' 표현 + '사업체' 표현이 함께 있어야 채택
DEAL_RE = re.compile(r"매매|매각|양도|인수|넘김|권리금|팝니다|판매합니다")
BIZ_RE = re.compile(
    r"식당|레스토랑|카페|커피|베이커리|제과|호프|주점|바\b|"
    r"가게|점포|상가|매장|사업체|업체|공장|법인|브랜드|프랜차이즈|"
    r"미용실|살롱|마사지|스파|헬스|짐\b|학원|유치원|"
    r"세탁|런드리|편의점|마트|숙소|호텔|게스트하우스|펜션|농장")
# 매물이 아닌 글 걸러내기
NOISE_RE = re.compile(r"구인|구직|채용|모집|후원|공모전|문의드립니다|추천해주세요|"
                      r"찾습니다|구합니다|광고/제휴")

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
    """gnuboard 목록의 날짜. 'YY-MM-DD' 또는 'MM-DD'(올해) 형태."""
    raw = raw.strip()
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


def to_model(row):
    # 게시판 분류 접두어("식당/식품 | ") 제거
    title = re.sub(r"^[^|]{1,12}\|\s*", "", row["title"]).strip()

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
        "badge": "커뮤니티 매물",
        "source": "indoweb.org",
        "sourceUrl": row["url"],
        "postedAt": posted.isoformat(),
        "indexOnly": True,
        "lat": None,
        "lng": None,
    }, None


def collect(pages):
    results, rejected = [], {}
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
    return unique, rejected


def write_outputs(items):
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    body = json.dumps(items, ensure_ascii=False, indent=2)
    OUTPUT_JS.write_text(
        "// 자동 생성 파일 — scraper/scrape_indoweb.py 가 갱신합니다. 직접 수정 금지.\n"
        "// 색인형 데이터: 제목/지역/게시일/원문링크만 보관하며 본문·연락처는 담지 않는다.\n"
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

    items, rejected = collect(pages)
    print("\n[제외 사유]")
    for reason, count in sorted(rejected.items(), key=lambda x: -x[1]):
        print(f"  {count:>4}건  {reason}")
    print(f"\n[결과] 커뮤니티 매물 {len(items)}건")
    for x in items:
        print(f"  - {(x['postedAt'] or '')[:10]} [{x['locationKo']}] {x['title'][:55]}")

    if not items:
        print("!! 수집 0건 - 기존 파일을 덮어쓰지 않고 종료")
        sys.exit(1)
    write_outputs(items)


if __name__ == "__main__":
    main()
