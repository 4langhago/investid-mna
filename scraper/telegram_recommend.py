"""수집한 매물 중 '한국인이 인수해서 실제로 사업을 굴릴 수 있는 것'만 텔레그램으로 보낸다.

2026-09-17 정책: 건수를 채우지 않는다. 기준을 넘는 매물이 하나도 없으면 0건이라고 알린다.
예전에는 커뮤니티·사업체·부동산 세 섹션에 슬롯(2/2/나머지)을 배분해 매일 5건을 채웠고,
그래서 기준에 못 미치는 날에도 애매한 물건이 추천으로 나갔다.

관문 (순서대로, 하나라도 못 넘으면 탈락)
  1) 사업 운영 관문 (business_gate) — 업종이 외국인에게 열려 있는가, 취득 구조가 성립하는가,
     영업 실체가 확인되는가, 규모(인수가·매출)를 알 수 있는가, 90일 이내 글인가.
     루코·아파트·토지 같은 순수 부동산은 '인수해서 운영할 사업'이 아니므로 여기서 빠진다.
  2) 실재성 — 필수 필드·원문 링크·플레이스홀더 연락처·단위 오기재 가격 검증.
  3) 실물 확인 — 구글 Places 로 영구 폐업 제외, 인도웹 원문 글의 삭제·거래완료 확인.

메시지에는 한글 상세, 법적 쟁점, 인수 후 운영 개시 절차, 비용 구조, 사진·지도·평판 링크,
공적 장부 조회 링크가 함께 붙는다. 업종 개방 여부는 2차 자료 기준이므로 계약 전 OSS 확인이 필요하다.

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
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import business_gate as bg  # noqa: E402  사업 운영 가능성 최종 관문
import foreign_eligibility as fe  # noqa: E402
import legal_check as lc  # noqa: E402
import listing_history as lh  # noqa: E402
import operability as op  # noqa: E402
import places_check as pc  # noqa: E402
from korean_brief import build_korean_brief, build_korean_detail  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
LIVE_JS = ROOT / "js" / "live_data.js"
BUSINESS_JS = ROOT / "js" / "business_data.js"
OLX_JS = ROOT / "js" / "olx_data.js"
COMMUNITY_JS = ROOT / "js" / "community_data.js"
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
# m²당 하한은 '건물 면적이 값을 결정하는' 루코·상가에만 맞는 기준이다. 토지·공장·창고는
# 대지가 넓을수록 m²당 단가가 싸지는 게 정상이라(Subang 농지 Rp 692k/m² 등) 이 규칙을
# 대면 정상 매물이 오탈락한다.
LAND_OR_FACTORY_RE = re.compile(r"토지|땅|부지|공장|창고|농장|농지|\btanah\b|\bpabrik\b|\bgudang\b|\bkebun\b",
                                re.I)
# 사업체 인수는 소규모(세탁소·카페 등)가 많아 하한을 따로 둔다.
MIN_BUSINESS_PRICE = 10_000_000          # Rp 10 jt

# 운영 가능성 필터. '부적합'(영업 실체 미확인·임대글·거래 종료)은 발송하지 않는다.
ALLOWED_OPERABILITY = (op.OPERABLE, op.UNCERTAIN)

# 인도네시아 주요 지역 월 최저임금(UMP) 대략치. 인건비 증가를 전망에 반영하기 위한
# 기준값이며, 매년 개정되므로 정확한 수치는 주(州) 고시를 확인해야 한다.
UMP_REFERENCE = 5_000_000  # 자카르타권 기준 근사치(Rp/월)

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


def load_community_listings():
    """한인 커뮤니티(인도웹) 매물. 파일이 없으면 빈 목록."""
    if not COMMUNITY_JS.exists():
        return [], None
    return (export_var(COMMUNITY_JS, "COMMUNITY_LISTINGS"),
            export_var(COMMUNITY_JS, "COMMUNITY_LISTINGS_UPDATED_AT"))


def load_brokers():
    """한인 중개·컨설팅 업체 글 목록(scrape_indoweb.py 가 생성). 없으면 빈 목록."""
    path = Path(__file__).resolve().parent / "output" / "korean_brokers.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []


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


# 인도웹은 초당 요청 제한이 엄격해 이보다 빨리 요청하면 접속을 끊는다(다른 스크레이퍼
# 코드의 기존 제약과 동일). 발송 직전 후보 몇 건만 확인하므로 이 정도 대기는 무시할 만하다.
INDOWEB_RATE_LIMIT_SECONDS = 12
_last_indoweb_request_at = 0.0

SOLD_MARKERS_RE = re.compile(r"거래완료|판매완료|매각완료|계약완료|\bsold\b", re.I)
# 본문 영역만 추출한다. scrape_indoweb.py의 parse_detail() 과 같은 경계를 쓴다.
_BO_V_CON_RE = re.compile(
    r'id="bo_v_con"[^>]*>(.*?)(?:<div[^>]*id="bo_v_share"|</section>)', re.S)
_BO_V_TITLE_RE = re.compile(r'id="bo_v_title"[^>]*>(.*?)</h\d>', re.S)


def check_indoweb_alive(url):
    """인도웹 게시글이 아직 살아있고 '거래완료' 표시가 없는지 본문으로 직접 확인한다.

    gnuboard(인도웹의 게시판 엔진)는 삭제된 글도 HTTP 200 으로 "오류안내 페이지"를
    반환한다 - 상태 코드만으로는 죽은 글을 구분할 수 없다.

    ⚠️ 판매완료 문구는 반드시 글 제목/본문(bo_v_title, bo_v_con) 안에서만 찾는다.
    페이지 전체를 검사하면 모든 real_estate_mb 게시물에 공통으로 깔리는 게시판
    이용안내("거래가 완료되면 분류를 거래완료로 수정...")에 걸려 살아있는 글도
    전부 '거래완료'로 오탐된다(실측 확인함, wr_id=10427/10428).

    반환: (상태, 사유) - 상태는 "alive" / "dead" / "unknown"(네트워크 실패 - 발송 대상에서
    빼지 않고 로그만 남긴다. 접속이 원래 불안정한 사이트라, 확인 실패를 '팔렸다'로
    해석하면 매번 접속이 불안정할 때마다 추천 목록이 텅 비게 된다).
    """
    global _last_indoweb_request_at
    wait = INDOWEB_RATE_LIMIT_SECONDS - (time.monotonic() - _last_indoweb_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_indoweb_request_at = time.monotonic()

    req = urllib.request.Request(url, method="GET",
                                 headers={"User-Agent": USER_AGENT,
                                          "Accept-Language": "ko-KR,ko;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=URL_CHECK_TIMEOUT) as res:
            body = res.read().decode("utf-8", errors="replace")
    except Exception as e:  # 타임아웃/DNS/HTTP 오류 등 - 확인 불가, 발송 목록은 그대로 둔다
        return "unknown", f"접속 실패({type(e).__name__})"

    if "오류안내 페이지" in body:
        return "dead", "게시글이 삭제됨(오류안내 페이지)"

    con = _BO_V_CON_RE.search(body)
    title = _BO_V_TITLE_RE.search(body)
    post_text = (title.group(1) if title else "") + " " + (con.group(1) if con else "")
    if not con and not title:
        # 글 영역 자체를 못 찾았다 - 페이지 구조가 바뀌었거나 확인 불가 상태.
        # '팔렸다'로 단정하지 않는다.
        return "unknown", "게시글 본문 영역을 찾을 수 없음(페이지 구조 확인 필요)"
    if SOLD_MARKERS_RE.search(post_text):
        return "dead", "거래완료/판매완료 표시가 글 제목·본문에 있음"
    return "alive", ""


def validate(item, check_url=False, min_price=None, allowed_statuses=ALLOWED_FOREIGN_STATUSES,
             require_price=True):
    """실재성 + 외국인 취득 가능성 + 운영 가능성 검증.

    통과하면 (True, ''), 아니면 (False, 탈락사유).

    require_price=False 는 한인 커뮤니티 매물용이다. 커뮤니티 글은 가격을 본문에
    안 적고 '연락 주세요'로 끝나는 경우가 흔한데, 이 매물들이 오히려 실제 인수 대상인
    경우가 많다. 대신 운영 가능성 판정(operability)을 통과해야 한다.
    """
    min_price = MIN_PLAUSIBLE_PRICE if min_price is None else min_price
    fields = REQUIRED_FIELDS if require_price else [
        f for f in REQUIRED_FIELDS if f not in ("price", "priceNum")]
    for field in fields:
        v = item.get(field)
        if v is None or v == "":
            return False, f"필수 필드 누락: {field}"

    source_url = str(item.get("sourceUrl")).strip()
    if not source_url.startswith("http"):
        return False, f"sourceUrl 형식 오류: {source_url}"

    price_num = price_value(item)
    if (require_price or price_num) and price_num < min_price:
        return False, f"표시가 비정상({item.get('price')}) - 단위 오기재로 보임"
    # m²당 단가 규칙은 루코·상가처럼 '건물 면적'이 값을 결정하는 매물에만 유효하다.
    # 사업체 인수(양계장·농장 등 대지가 넓은 업종)에 적용하면 정상 매물이 탈락한다.
    # 예: 양계장 Rp 16 M / 32,000m² = m²당 500 → 오탐.
    # price_num 이 0인 것(가격 미표기 커뮤니티 글, priceNum=None)까지 이 규칙에 들어오면
    # 0/area=0 이 항상 하한선 미만이라 가격 미표기 매물이 전부 '단가 비정상'으로 오탈락한다.
    # 실가격이 있는 매물끼리만 비교한다.
    area = item.get("area")
    is_land_or_factory = LAND_OR_FACTORY_RE.search(
        f"{item.get('category') or ''} {item.get('title') or ''}")
    if (item.get("subtype") != "akuisisi" and not is_land_or_factory and area and price_num
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

    # 제도상 취득이 가능해도 '실제로 인수해 운영할 수 있는 매물'이 아니면 추천하지 않는다.
    op_status, _score, op_reasons, _todos = op.classify(item)
    if op_status not in ALLOWED_OPERABILITY:
        return False, f"운영 가능성 {op_status}: {'; '.join(op_reasons) or '근거 부족'}"

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


BOARD_PREFIX_RE = re.compile(r"^[^|]{1,12}\|\s*")
_TITLE_PUNCT_RE = re.compile(r"[^\w가-힣]+")


def normalize_title(title):
    """게시판 접두어("식당/식품 | ")와 구두점을 지워 같은 매물을 비교 가능하게 만든다.

    인도웹 게시판은 같은 매물이 카테고리별 다른 게시판(iw-market, iw-biz_promo 등)에
    복수 등록되는 게 흔하다. 접두어만 다르고 본문 제목은 동일한 경우가 많다
    (예: "식당/식품 | 반둥 한국 감성 디저트 카페 브랜드 매각..." ≡ "반둥 한국 감성 디저트
    카페 브랜드 매각...").
    """
    t = BOARD_PREFIX_RE.sub("", str(title or ""))
    t = _TITLE_PUNCT_RE.sub(" ", t).strip().lower()
    return re.sub(r"\s+", " ", t)


def _title_tokens(title):
    return set(t for t in normalize_title(title).split(" ") if len(t) >= 2)


def _dupe_quality(item):
    """중복군에서 '가장 상세하거나 최신인' 쪽을 고르기 위한 비교 키(클수록 우선)."""
    return (
        bool(item.get("priceNum")),
        bool(item.get("area")),
        len(item.get("description") or ""),
        str(item.get("postedAt") or ""),
    )


def dedupe_listings(items):
    """같은 매물이 여러 게시판에 중복 등록된 것을 제거한다(슬롯 배분 전에 적용).

    1) 정규화한 제목이 완전히 같으면 중복(게시판 접두어 차이 등).
    2) 한쪽 제목의 토큰 대부분이 다른 쪽 제목에 포함되면(한 글이 여러 매물을 묶어
       재게시한 경우, 예: "카페 및 세차장 사업체 양도"가 개별 글 2건을 묶어 재등록)
       중복으로 본다. 토큰 3개 미만인 제목은(오탐 위험이 커서) 이 규칙에서 제외한다.
    각 중복군에서는 가격·면적·설명이 더 상세하거나 더 최근에 올라온 쪽만 남긴다.
    """
    groups = []  # list[list[item]]
    for item in items:
        tokens = _title_tokens(item.get("title"))
        placed = False
        for group in groups:
            g_tokens = _title_tokens(group[0].get("title"))
            if normalize_title(item.get("title")) == normalize_title(group[0].get("title")):
                placed = True
            elif len(tokens) >= 3 and len(g_tokens) >= 3:
                overlap = len(tokens & g_tokens) / min(len(tokens), len(g_tokens))
                if overlap >= 0.7:
                    placed = True
            if placed:
                group.append(item)
                break
        if not placed:
            groups.append([item])

    kept, dropped = [], 0
    for group in groups:
        if len(group) == 1:
            kept.append(group[0])
            continue
        best = max(group, key=_dupe_quality)
        kept.append(best)
        dropped += len(group) - 1
        losers = ", ".join(f"id={g.get('id')}" for g in group if g is not best)
        print(f"  -- 중복 매물 제외(→ {best.get('id')} 유지): {best.get('title')} [{losers}]")
    if dropped:
        print(f"  -- 중복 제외 합계: {dropped}건")
    return kept


def pick_verified_batch(candidates, cursor, size, cache):
    """후보를 뽑되, 구글 Places 로 '영구 폐업'이 확인된 매물은 버리고 다음 후보로 채운다.

    Places 조회는 유료라 발송 후보에만 건다. 전체 후보를 조회하면 매일 수백 건이 되고,
    어차피 발송되지 않을 매물에 요금을 쓰게 된다.
    """
    picked, n = [], len(candidates)
    if n == 0 or size <= 0:
        return [], cursor, []
    dropped = []
    i = 0
    # 후보를 한 바퀴 이상 돌지 않는다(전부 폐업이면 그냥 적게 보낸다).
    while len(picked) < size and i < n:
        item = candidates[(cursor + i) % n]
        i += 1
        place = pc.search(item, cache=cache) if pc.enabled() else None
        if place:
            item["_place"] = place
            pts, why = pc.verdict(place)
            if pts <= -99:
                dropped.append((item, why))
                continue
        picked.append(item)
    return picked, (cursor + i) % n, dropped


def verify_community_liveness(picked, pool, slots):
    """발송 직전 커뮤니티 후보만 실물 페이지를 확인해 죽은 글을 빼고 다음 후보로 채운다.

    인도웹 게시글은 팔린 뒤에도 글이 그대로 남아 있는 경우가 흔해(URL 이 200 을 계속
    반환) validate() 의 존재 확인만으로는 걸러지지 않는다. 전체 후보를 다 확인하면
    요청이 너무 많아지고(≥12초 간격 제한) 어차피 보내지 않을 글까지 확인하게 되므로,
    실제로 보낼 몇 건만 확인한다.
    """
    tried_ids = {x.get("id") for x in picked}
    backfill = [x for x in pool if x.get("id") not in tried_ids]
    bi = 0
    alive, queue = [], list(picked)
    while queue and len(alive) < slots:
        item = queue.pop(0)
        if item.get("source") != "indoweb.org" or not item.get("sourceUrl"):
            alive.append(item)
            continue
        status, reason = check_indoweb_alive(item["sourceUrl"])
        if status == "dead":
            print(f"  !! 발송 직전 실물 확인 실패로 제외: {item.get('title')} - {reason}")
            if bi < len(backfill):
                queue.append(backfill[bi])
                bi += 1
            continue
        if status == "unknown":
            print(f"  !! 실물 확인 불가(발송은 유지, 로그만 남김): {item.get('title')} - {reason}")
        else:
            # 살아 있다는 것도 남긴다. 아무 줄도 안 남으면 확인을 건너뛴 건지 알 수 없다.
            print(f"  [실물 확인] 게시 중: {item.get('title')} - {reason}")
        alive.append(item)
    return alive


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"cursor": 0}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def md_safe(text):
    """텔레그램 Markdown 파싱이 깨지지 않도록 서식 문자를 제거."""
    return re.sub(r"[*_`\[\]]", "", str(text or ""))


def md_safe_url(url):
    """URL은 문자를 지울 수 없으니(링크가 깨짐) 이스케이프한다.

    인도웹 상세글 URL(wr_id=10373 등)이나 사진 파일명에 밑줄(_)이 흔한데,
    텔레그램 레거시 Markdown은 링크가 아닌 평문 속 '_'도 이탤릭 시작/끝으로 파싱한다.
    글마다 밑줄 개수가 짝이 안 맞으면 "can't find end of the entity" 400 이 난다.
    레거시 Markdown은 '\\'로 서식 문자를 이스케이프하는 것을 지원한다.
    """
    return re.sub(r"([*_`\[\]])", r"\\\1", str(url or ""))


def maps_link(item):
    """지도 링크. 좌표가 있으면 좌표로, 없으면 주소/지역명 검색으로 연결.

    좌표는 수집기가 매물 지역에서 받아온 값이며 건물 단위 정확도가 아닐 수 있다.
    그래서 '대략 위치'로 표기한다 - 없는 정확도를 있는 것처럼 말하지 않는다.
    """
    lat, lng = item.get("lat"), item.get("lng")
    if lat and lng:
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
    # location 은 커뮤니티 매물에서 'Indonesia' 같은 국가명이라 지도 검색에 쓸모가 없다.
    # 실제 주소 → 한글 지역명 순으로 고른다.
    where = item.get("address") or item.get("locationKo") or item.get("location")
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
    if not price:
        # 가격이 없으면 시세 비교가 성립하지 않는다. 예전에는 0원으로 계산해
        # '중앙값 대비 -100%'라는 무의미한 문장이 나갔다.
        same = []
    else:
        same = [p for p in peers
                if p.get("id") != item.get("id")
                and p.get("location") == item.get("location")
                and p.get("type") == item.get("type")
                and price_value(p)]
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

    # 2) m²당 단가 - 가격과 면적이 모두 있는 매물끼리만 비교
    if area and price:
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


def build_outlook(item, peers):
    """운영 관점의 전망. 수집 수치로 계산 가능한 것만 쓰고, 나머지는 비용 구조로 대체한다.

    '전망이 밝다' 같은 문장은 만들지 않는다. 대신 인수 후 실제로 손익을 좌우하는
    항목(회수 기간, 인건비, 임대료, 시세 위치)을 숫자로 보여 준다.
    """
    lines = []
    price = price_value(item)
    rev = item.get("monthlyRevenueNum")

    if price and rev:
        try:
            months = price / float(rev)
            lines.append(f"매도인 제시 매출 기준 원금 회수까지 매출 {months:.0f}개월치 "
                         f"(순이익 기준이 아니므로 실제 회수 기간은 이보다 길다)")
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    elif price:
        lines.append("매출 수치가 없어 회수 기간을 계산할 수 없음 - "
                     "최근 12개월 매출·비용 자료를 받은 뒤에야 값을 매길 수 있는 매물")

    kinds = " ".join(str(item.get(k) or "") for k in ("title", "description", "category"))
    if re.search(r"karyawan|pegawai|직원|종업원", kinds, re.I):
        lines.append(f"인건비: 직원 1인당 월 최저임금 약 Rp {UMP_REFERENCE:,.0f} 수준(자카르타권) "
                     f"+ THR(연 1개월분)·BPJS 사용자 부담분이 추가된다")
    if re.search(r"\bsewa\b|\bkontrak\b|임차|월세", kinds, re.I):
        lines.append("임차료: 인도네시아 상가는 연 단위 선불이 일반적이다. "
                     "갱신 시 인상폭이 수익을 결정하므로 잔여 기간과 갱신 조건을 먼저 확정")
    if re.search(r"restoran|resto|cafe|café|kedai|식당|카페|음식점", kinds, re.I):
        lines.append("업종 특성: 요식업은 임대료·인건비·식자재가 매출의 대부분을 차지해 "
                     "매출보다 순이익 검증이 중요하다. 배달 플랫폼 수수료(15~20%)도 확인")
    if re.search(r"pabrik|공장|제조|gudang|창고", kinds, re.I):
        lines.append("업종 특성: 전기 용량·폐수 처리·소방 적합성이 인수 후 추가 투자로 "
                     "이어지는 대표 항목이다. 설비 잔존 수명과 함께 확인")

    # 같은 유형 대비 가격 위치 - 표본이 충분할 때만
    same = [p for p in peers if p.get("id") != item.get("id")
            and p.get("type") == item.get("type") and price_value(p)]
    if price and len(same) >= 5:
        med = median([price_value(p) for p in same])
        if med:
            lines.append(f"가격 위치: 같은 유형 {len(same)}건 중앙값 대비 "
                         f"{(price / med - 1) * 100:+.0f}%")
    return lines


def seller_document_checklist(item):
    """매도인에게 먼저 받아야 할 서류 목록.

    2026-09-17 추가: 통과 매물을 실제로 검증해 보니, 글에 적힌 매출·점포 수·인증은 전부
    매도인 주장이고 공개 정보로는 확인되지 않았다(반둥 카페 건: 브랜드 실체는 확인됐지만
    매출은 현지 벤치마크 상단을 넘었고, 글쓴이가 소유자인지도 확인 불가).
    추천은 '연락해 볼 가치가 있다'는 뜻이지 '검증됐다'는 뜻이 아니므로, 무엇을 받아
    확인해야 하는지를 매물마다 함께 보낸다.
    """
    text = " ".join(str(item.get(k) or "") for k in ("title", "description"))
    items = [
        "법인 등기(AHU)·NIB·KBLI 사본 - 매각 대상 법인이 실재하고 글쓴이가 대표·수임인인지 확인",
        "최근 12~24개월 은행 거래내역과 세무신고서 - 내부 엑셀·구두 수치는 근거로 보지 않는다",
        "매각 구조 명시 요구 - 지분(법인) 인수인지, 자산·브랜드만 넘기는지에 따라 절차와 위험이 달라진다",
        "매각 사유 - 영업이 잘된다는 매물일수록 파는 이유를 직접 물을 것",
    ]
    if re.search(r"매장|점포|지점|아울렛|outlet|cabang|카페|식당|resto|cafe", text, re.I):
        items.append("전 점포 임대차계약서 - 잔여 기간·양도 가능 여부·임대인 승계 동의 절차")
    if re.search(r"할랄|halal", text, re.I):
        items.append("할랄 인증서 명의 - 법인 단위 발급이라 소유자가 바뀌면 재인증이 필요할 수 있다(BPJPH 확인)")
    if re.search(r"직원|karyawan|pegawai|staff", text, re.I):
        items.append("직원 명부·근속연수·퇴직충당금 - 인력 승계 시 퇴직금 부담이 인수가에 숨어 있다")
    if re.search(r"설비|기계|장비|mesin|peralatan|equipment", text, re.I):
        items.append("설비 목록과 인수 포함 여부 - 생산·검사 장비가 제외되면 인수 후 가동이 안 된다")
    return items


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

    # 인도웹 벼룩시장 글은 제목 앞에 게시판 분류가 붙는다("식당/식품 | ..."). 수집기가
    # 상세 페이지를 못 받은 글에는 그대로 남아 있어, 표시할 때 한 번 더 걷어낸다.
    title = re.sub(r"^[^|]{1,20}\|\s*", "", str(item.get("title") or ""))
    lines = [f"*[{rank}/{total}] {md_safe(title)}*", ""]
    if kind:
        lines.append(f"🏷 {kind}")
    lines.append(f"📍 {where}")
    # 가격 미표기 매물(커뮤니티 글에 흔하다)을 빈 굵은글씨로 내보내면 오해를 부른다.
    price_line = md_safe(item.get("price")) or "가격 미표기 - 매도인 문의"
    lines.append(f"💰 *{price_line}*" + (f"\n📐 {' · '.join(spec)}" if spec else ""))
    if item.get("postedAt"):
        lines.append(f"🗓 게시일 {item['postedAt'][:10]}")

    facilities = [md_safe(f) for f in (item.get("facilities") or []) if f]
    if facilities:
        lines.append(f"✅ {' · '.join(facilities)}")

    # 한글 상세 설명을 원문보다 먼저 둔다. 수신자가 인도네시아어를 읽지 않아도
    # 매물 성격을 파악할 수 있어야 한다. 모든 줄은 원문에서 뽑은 항목이다.
    detail = build_korean_detail(item)
    if detail:
        lines.append("\n🇰🇷 *한글 상세* (원문에서 추출한 사실만)")
        for section, rows in detail:
            lines.append(f"— {section} —")
            lines.extend(f"• {md_safe(r)}" for r in rows)

    # 구글 Places 실측 - 매도인 주장과 독립된 유일한 검증 수단이라 판정보다 앞에 둔다.
    place = item.get("_place")
    if place:
        lines.append("\n🌐 *구글 실측* (매도인 주장이 아닌 구글 등록 정보)")
        lines.extend(f"• {md_safe(x)}" if not x.startswith("http") else md_safe_url(x)
                     for x in pc.format_lines(place))

    # 운영 가능성 판정 - '살 수 있는가'와 별개로 '굴릴 수 있는가'를 본다.
    op_status, op_score, op_reasons, op_todos = op.classify(item)
    place_pts, place_why = pc.verdict(place)
    if place_why and place_pts > 0:
        op_score += place_pts
        op_reasons = op_reasons + [place_why]
    op_mark = {op.OPERABLE: "🟩", op.UNCERTAIN: "🟨"}.get(op_status, "🟥")
    lines.append(f"\n{op_mark} *운영 가능성: {op_status}* (신호 점수 {op_score})")
    lines.extend(f"• {md_safe(r)}" for r in op_reasons)
    if op_todos:
        lines.append("확인 필요:")
        lines.extend(f"  - {md_safe(t)}" for t in op_todos)

    status, reason, steps = fe.classify(item)
    mark = {fe.ELIGIBLE: "🟢", fe.CONDITIONAL: "🟡"}.get(status, "🔴")
    lines.append(f"\n{mark} *외국인(한국인) 취득: {status}*")
    lines.append(f"• {md_safe(reason)}")
    lines.extend(f"• {md_safe(s)}" for s in steps)

    legal = lc.build_legal_section(item)
    if legal:
        lines.append("\n⚖️ *법적 쟁점·지분 구조·반복 사고 유형*")
        # 이미 들여쓰기된 줄(└ / •)에 불릿을 또 붙이면 두 번 찍힌다.
        lines.extend((md_safe(x) if x.startswith("  ") else f"• {md_safe(x)}") for x in legal)

    lines.append("\n🛠 *인수 후 운영 개시까지* (이 순서로 진행)")
    lines.extend(f"• {md_safe(x)}" for x in lc.operating_playbook(item))

    lines.append("\n📑 *접촉하면 먼저 요구할 서류* (받기 전에는 가격 협상 금지)")
    lines.extend(f"• {md_safe(x)}" for x in seller_document_checklist(item))

    hist = item.get("_historyNotes") or []
    if hist:
        lines.append("\n📊 *매물 추적* (우리 수집 이력 기준)")
        lines.extend(f"• {md_safe(x)}" for x in hist)

    outlook = build_outlook(item, peers)
    if outlook:
        lines.append("\n📈 *운영 전망 · 비용 구조* (수집 수치 기준)")
        lines.extend(f"• {md_safe(x)}" for x in outlook)

    if desc:
        lines.append(f"\n📝 *매물 설명(원문)*\n{desc}")

    review = build_review(item, peers)
    if review:
        lines.append("\n🔍 *자동 분석* (수집 데이터 기준)")
        lines.extend(f"• {n}" for n in review)

    lines.append("\n🔗 *링크*")
    lines.append(f"원본({md_safe(item.get('source'))}): {md_safe_url(item['sourceUrl'])}")
    m = maps_link(item)
    if m:
        kind_of_pin = "좌표 기준 대략 위치" if (item.get("lat") and item.get("lng")) else "지역명 검색"
        lines.append(f"지도({kind_of_pin}): {md_safe_url(m)}")

    # 실제 이용자 평판은 우리가 요약하지 않는다(상호 불일치 위험). 바로 확인할 검색 링크를 준다.
    lines.append("\n🔎 *실제 평판·리뷰 직접 확인*")
    for label, url in lc.review_links(item):
        lines.append(f"{label}: {md_safe_url(url)}")

    photos = [p for p in (item.get("photoUrls") or item.get("imageUrls") or []) if p][:3]
    if photos:
        lines.append("\n📷 *사진(원문 서버)*")
        lines.extend(md_safe_url(p) for p in photos)

    lines.append("\n📋 *계약 전 직접 조회할 공적 장부*")
    for label, url in lc.DUE_DILIGENCE_LINKS:
        lines.append(f"{label}: {url}")

    if wa:
        lines.append(f"\n연락처: https://wa.me/{wa}")

    return "\n".join(lines)


def build_empty_message(scanned, rejected):
    """추천 0건인 날의 알림. 아무것도 안 보내면 파이프라인이 죽은 것과 구분되지 않는다."""
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    top = sorted(rejected.items(), key=lambda x: -x[1])[:5]
    lines = [f"*오늘의 매물 추천* ({today})",
             "",
             f"기준을 넘는 매물이 없습니다. 수집 {scanned}건 전부 탈락했습니다.",
             "",
             "*주요 탈락 사유*"]
    lines += [f"• {md_safe(why)} — {n}건" for why, n in top]
    lines.append("")
    lines.append("_인수해서 실제로 운영할 수 있는 매물만 보냅니다. "
                 "기준에 못 미치는 날은 건너뜁니다._")
    return "\n".join(lines)


def build_messages(picked, updated_at, peers):
    """머리말 1건 + 매물 1건당 1메시지. 텔레그램 4096자 제한을 넘기지 않기 위함."""
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    total = len(picked)
    messages = [f"*오늘의 매물 추천* ({today})\n"
                f"인수해서 운영할 수 있다고 본 매물 {total}건\n\n"
                f"_아래 관문을 모두 통과한 매물만 보냅니다._\n"
                f"_① 업종 — 외국인 투자가 열려 있다고 확인된 업종만 "
                f"(세탁·미용실·소형 소매·노점은 제외)_\n"
                f"_② 취득 구조 — 🟢 바로 취득 가능 · 🟡 PT PMA 설립 등 절차 필요_\n"
                f"_③ 영업 실체 — 영업 중·매출·업력·직원 신호가 확인된 매물만_\n"
                f"_④ 규모와 신선도 — 인수가나 매출이 제시되고 90일 이내 게시_\n"
                f"_업종 개방 여부는 2차 자료 기준입니다. 계약 전 OSS 에서 해당 KBLI 를 "
                f"직접 확인하세요._\n"
                f"_모든 수치는 매도인 게시 정보이며 검증된 값이 아닙니다. "
                f"법률 자문이 아니고, 계약 전 현장 실사와 공증인(notaris) 확인이 필요합니다._"]

    # 공개 게시판에 뜨는 매물은 일부다. 실제 물건을 들고 있는 한인 중개·컨설팅 채널을
    # 함께 안내해, 직접 문의라는 다음 행동으로 이어지게 한다.
    brokers = load_brokers()
    if brokers:
        lines = ["*■ 한인 중개·컨설팅 채널* (게시판에 안 뜨는 매물 문의처)"]
        for b in brokers[:4]:
            lines.append(f"• {md_safe(b['title'])}\n  {b['sourceUrl']}")
        lines.append("_매물이 공개 게시판에 다 올라오지는 않습니다. "
                     "위 업체에 조건(업종·예산·지역)을 직접 제시하면 비공개 물건을 받습니다._")
        messages.append("\n".join(lines))

    for rank, x in enumerate(picked, start=1):
        messages.append(format_item(x, rank, total, peers))
    return messages


def split_message(message, limit=TELEGRAM_MAX_CHARS):
    """4096자 제한에 맞춰 줄 단위로 나눈다.

    예전에는 초과분을 잘라 버렸는데, 상세 설명을 붙이면서 법률·실사 항목처럼
    메시지 뒤쪽에 있는 중요한 내용이 통째로 사라졌다. 그래서 자르지 않고 나눈다.
    """
    chunks, buf = [], ""
    for line in str(message).split("\n"):
        # 한 줄이 제한을 넘는 극단적인 경우만 강제로 자른다.
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        if len(buf) + len(line) + 1 > limit:
            chunks.append(buf.rstrip("\n"))
            buf = ""
        buf += line + "\n"
    if buf.strip():
        chunks.append(buf.rstrip("\n"))
    return chunks or [""]


def send_telegram(message):
    for i, chunk in enumerate(split_message(message)):
        _send_one(chunk if i == 0 else f"(이어서 {i + 1})\n{chunk}")


def _post_telegram(token, chat_id, message, parse_mode):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "disable_web_page_preview": "true",
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.loads(res.read().decode("utf-8"))


def _send_one(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 설정되지 않음")

    try:
        result = _post_telegram(token, chat_id, message, "Markdown")
        if not result.get("ok"):
            raise RuntimeError(f"텔레그램 전송 실패: {result}")
        return
    except urllib.error.HTTPError as e:
        # 예전에는 응답 본문을 버려서 400의 실제 원인(대개 "can't find end of the entity"
        # 같은 Markdown 엔티티 파싱 실패)이 로그에 안 남았다. 원인 확인용으로 본문을 읽는다.
        body = e.read().decode("utf-8", errors="replace")
        print(f"  !! 텔레그램 전송 실패 HTTP {e.code}: {body}")
        if e.code != 400:
            raise
        # Markdown 파싱 실패로 400이 난 매물 하나 때문에 배치 전체가 중단되면 안 된다.
        # 서식을 포기하고 순수 텍스트로 재시도한다(메시지 자체는 대부분 유효한 내용이라
        # 발송하지 않는 것보다 서식 없이라도 보내는 게 낫다).
        plain = re.sub(r"[*_`\[\]]", "", str(message))
        result = _post_telegram(token, chat_id, plain, None)
        if not result.get("ok"):
            raise RuntimeError(f"텔레그램 전송 실패(평문 재시도 후에도 실패): {result}")
        print("  -- Markdown 파싱 실패로 평문으로 재전송함")


def main():
    dry_run = "--dry-run" in sys.argv
    check_url = "--verify-urls" in sys.argv

    # 2026-09-17 정책 변경: '인수해서 실제로 사업을 굴릴 수 있는 매물'만 보낸다.
    # 예전에는 세 섹션(커뮤니티/사업체/부동산)을 슬롯 수만큼 채워 보냈다. 그러면 기준을
    # 넘는 매물이 없는 날에도 목록을 채우려고 애매한 물건이 올라갔다.
    # 이제 business_gate 를 통과한 것만 보내고, 0건이면 0건이라고 알린다.
    listings, updated_at = load_listings()
    biz_all, biz_updated = load_business_listings()
    comm_all, comm_updated = load_community_listings()
    print(f"[수집] 부동산 {len(listings)} (수집시각 {updated_at}) · "
          f"사업체 {len(biz_all)} ({biz_updated}) · 커뮤니티 {len(comm_all)} ({comm_updated})")

    # 매물 이력은 선정과 무관하게 '수집된 전체'로 갱신해야 사라진 매물·가격 인하를 놓치지 않는다.
    history = lh.load()
    new_n, drop_n, gone_n = lh.update(history, listings + biz_all + comm_all)
    lh.save(history)
    print(f"[이력] 신규 {new_n}건 · 가격 인하 {drop_n}건 · 사라짐 {gone_n}건 "
          f"(누적 {len(history)}건 추적)")

    # 1차: 사업 운영 관문(업종 개방·취득 구조·영업 실체·규모·신선도)
    pool = dedupe_listings(listings + biz_all + comm_all)
    gated, rejected = bg.screen(pool)
    print(f"[선정] 수집 {len(pool)}건 중 {len(gated)}건이 사업 운영 관문 통과")
    for why, n in sorted(rejected.items(), key=lambda x: -x[1]):
        print(f"  {n:>4}건  {why}")

    # 2차: 실재성 검증(필수 필드·원문 링크·연락처·단위 오기재 가격)
    candidates = []
    for x in gated:
        ok, why = validate(x, check_url=check_url, min_price=MIN_BUSINESS_PRICE,
                           require_price=bool(x.get("priceNum")))
        if ok:
            candidates.append(x)
        else:
            print(f"  !! 실재성 검증 탈락: {x.get('title')} - {why}")

    candidates.sort(key=lambda x: (fe.rank_key(fe.classify(x)[0]),
                                   -(op.classify(x)[1] or 0),
                                   data_age_hours(x.get("postedAt")) or float("inf")))

    state = load_state()
    place_cache = pc._load_cache() if pc.enabled() else {}
    print(f"[구글 Places] {'사용' if pc.enabled() else '비활성(GOOGLE_PLACES_API_KEY 없음)'}")

    # 3차: 구글 실측(영구 폐업 제외) + 원문 글 생존 확인
    picked, _next, dropped = pick_verified_batch(candidates, 0, min(BATCH_SIZE, len(candidates)),
                                                 place_cache)
    for item, why in dropped:
        print(f"  !! 구글 확인으로 제외: {item.get('title')} - {why}")
    if pc.enabled():
        pc._save_cache(place_cache)

    community_picked = [x for x in picked if x.get("source") == "indoweb.org"]
    if community_picked and (not dry_run or check_url):
        alive = verify_community_liveness(community_picked, candidates, len(community_picked))
        alive_ids = {x["id"] for x in alive}
        picked = [x for x in picked
                  if x.get("source") != "indoweb.org" or x["id"] in alive_ids]

    for x in picked:
        x["_historyNotes"] = lh.notes(history, x)

    if picked:
        messages = build_messages(picked, updated_at, peers=candidates)
    else:
        messages = [build_empty_message(len(pool), rejected)]
        print("[선정] 기준을 넘는 매물 0건 - '추천 없음'만 알린다")

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
    state["lastRunAt"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    state["lastSentIds"] = [x["id"] for x in picked]
    # 보낸 매물은 '본 것'으로 남겨 다음 실행에서 같은 물건을 다시 올리지 않는다.
    state["seenCommunityIds"] = sorted(
        set(state.get("seenCommunityIds") or []) | {x["id"] for x in picked})
    save_state(state)
    print(f"[전송 완료] 추천 {len(picked)}건")


if __name__ == "__main__":
    main()
