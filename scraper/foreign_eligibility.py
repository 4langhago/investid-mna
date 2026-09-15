# -*- coding: utf-8 -*-
"""외국인(한국인) 매수 가능 여부 판정.

근거가 되는 인도네시아 제도 (2026-08 기준, 일반 원칙):
  - Hak Milik(SHM, 소유권)은 인도네시아 국민만 보유할 수 있다. 외국인 개인도,
    외국인 지분이 있는 PT PMA 도 SHM 명의를 가질 수 없다.
  - 외국인 개인(체류허가 보유)은 Hak Pakai(사용권) 주택/토지와
    SHMSRS(집합건물 소유권, 아파트)를 취득할 수 있다. 지역별 최소 가격 규정이 있다.
  - PT PMA(외국인투자법인)는 HGB(건물사용권)/Hak Pakai 를 취득할 수 있다.
    SHM 매물은 매도인이 HGB 로 전환(pelepasan hak → HGB)해야 인수 가능하다.
  - PT PMA 는 업종(KBLI 5자리)·사업장당 투자계획 Rp 100억 초과(토지·건물 제외)와
    납입자본 Rp 25억 요건을 받는다(BKPM 규정 2025년 제5호, 2025-10-02 시행으로
    납입자본이 Rp 100억 → Rp 25억으로 인하). 소매업·소규모 요식업 등 UMKM 유보 업종은
    외국인 투자가 막혀 있다.
  - 기존 법인의 지분을 사는 거래(share deal)는 새 PT PMA 를 세우지 않고 법인을 그대로
    넘겨받는다. 사업자산만 넘기는 거래(asset deal)는 인수자가 PT PMA 를 새로 세워야 한다.
    한인 커뮤니티의 소형 매장 양도는 거의 전부 후자다.

⚠️ 이 모듈은 공개 수집 데이터(제목·설명·증서 표기)만으로 하는 1차 스크리닝이다.
   법률 자문이 아니며, 최종 판단은 공증인(notaris)·BKPM 확인이 필요하다.
   그래서 결과를 '가능/조건부/불가' 3단계로만 내고, 각 단계에 필요한 절차를 함께 적는다.
"""
import re

from korean_brief import detect_certificate

ELIGIBLE = "가능"       # 외국인 명의로 바로 취득 가능한 구조
CONDITIONAL = "조건부"  # PT PMA 설립·권리 전환 등 절차를 거치면 가능
BLOCKED = "불가"        # 현행 제도상 외국인이 취득할 수 없는 구조

# 외국인 투자가 막혀 있거나 사실상 불가능한 업종(UMKM 유보·소매 등).
BLOCKED_BUSINESS = [
    (r"\bwarung\b|\bwarteg\b|\bkaki lima\b|\bgerobak\b|\bangkringan\b",
     "노점·소형 식당은 UMKM 유보 업종으로 외국인 투자가 허용되지 않음"),
    (r"\btoko kelontong\b|\bsembako\b|\bminimarket\b|\bwarung sembako\b|"
     r"\balfamart\b|\bindomaret\b|\balfamidi\b",
     "생필품 소매업(편의점 가맹점 포함)은 외국인 투자 유보 업종"),
    (r"\bsekolah\b.{0,40}\b(?:sd|smp|sma|smk)\b|\b(?:sd|smp|sma|smk)\b.{0,40}\bsekolah\b",
     "초·중·고 정규학교 운영은 경제특구(KEK) 밖에서 외국인 투자 불가"),
    # KBLI 96200 은 Perpres 49/2021 Lampiran II 에서 UMKM·협동조합 유보로 확인됨(2026-09-15 조사).
    (r"\blaundry\b|\bbinatu\b|\bcuci\s*(?:kiloan|baju|setrika)\b|세탁소|런드리|빨래방",
     "세탁업(KBLI 96200)은 UMKM·협동조합 유보 업종으로 외국인 투자 불가"),
    (r"\bpangkalan gas\b|\bagen lpg\b|\bpertamini\b",
     "LPG·연료 소매 유통은 외국인 투자 제한 업종"),
    (r"\bojek\b|\btravel\b.*\bangkutan\b|\bangkutan (?:umum|orang)\b",
     "육상 여객운송은 외국인 투자 제한 업종"),
    (r"\bjudi\b|\bmiras\b|\bminuman keras\b",
     "주류·사행성 업종은 외국인 투자 금지 목록"),
]

# PT PMA 최소 투자액/납입자본 (토지·건물 제외, KBLI·지역당)
PT_PMA_MIN_INVESTMENT = 10_000_000_000

# 인수가가 이 금액에도 못 미치면 PT PMA 를 세워 인수하는 구조 자체가 성립하지 않는다.
# (법인 설립·인허가 비용만으로 인수가를 넘어서는 수준.)
# 예: 임차권 양도 수준의 Rp 1,150만짜리 세탁소는 제도상 외국인 인수 대상이 아니다.
#
# 2026-08-11 하향(Rp 10억 → Rp 1억): 기존 값은 한국인이 실제로 인수해 운영하는 규모의
# 매물을 통째로 잘라내고 있었다. 인도웹에 올라온 '끌라빠가딩 카페 양도(약 Rp 4.5억)',
# '땅그랑 세차장(약 Rp 8억)'이 전부 '불가'로 떨어져 추천이 0건이 됐다.
# PT PMA 최소 투자 Rp 100억은 '인수 대금'이 아니라 '투자 계획 총액(설비·운전자금 포함,
# 토지·건물 제외)' 기준이므로, 인수가가 그보다 작다는 사실만으로 불가라고 할 수 없다.
# 따라서 이 선 아래만 구조 불성립으로 보고, 그 위는 '조건부'로 두어 요건 충족 방법을 안내한다.
BUSINESS_REALISTIC_MIN = 100_000_000

# 신규 PT PMA 납입자본 하한(BKPM 규정 2025년 제5호).
# 2026-09-14 재조정: 위 하향 이후 인수가 Rp 4억짜리 카페·포차까지 '조건부'로 통과해,
# 추천 목록이 실제로는 한국인이 적법하게 인수할 수 없는 매물로 채워졌다.
# 자산 양도(asset deal)는 인수자가 PT PMA 를 새로 세워야 하는데, 인수가가 납입자본
# 하한에도 못 미치면 인수가의 몇 배를 법인에 묶고 사업장당 Rp 100억 투자계획까지 내야 한다.
# 이 가격대에서 실제로 쓰이는 방식은 현지인 명의 대여(명의신탁)이고, 이는 투자법상 무효라
# 분쟁 시 매장을 통째로 잃는다. 그래서 이 선 아래의 자산 양도는 '불가'로 내린다.
# 가격 미표기와 지분 인수(share deal)는 이 규칙을 적용하지 않는다.
PT_PMA_MIN_PAID_UP = 2_500_000_000

# 기존 법인을 통째로(지분으로) 넘기는 거래. 인수자가 새 법인을 세우지 않아도 된다.
_SHARE_DEAL = re.compile(
    r"법인\s*(?:매각|양도|매매|인수)|지분\s*(?:매각|양도|인수|100\s*%)|주식\s*양도|"
    r"take\s*over\s*(?:pt|perusahaan|saham)|jual\s*(?:pt|perusahaan)\b|"
    r"akuisisi\s*saham|saham\s*(?:dijual|100\s*%)|share\s*(?:sale|deal|transfer)", re.I)
# 매도인이 '법인·지분은 넘기지 않는다'고 못박은 글. 지분 인수 표현보다 우선한다.
_ASSET_ONLY = re.compile(
    r"(?:지분|법인)[^.\n]{0,30}(?:매각|양도)하는\s*거래가\s*아닙|사업자산\s*양도|"
    r"자산\s*양도\s*방식|별도(?:의)?\s*(?:법인|사업자)[^.\n]{0,10}설립", re.I)

# 외국인 개인 주거용 취득 최소 가격 (PP 18/2021, Kepmen ATR/BPN 1241/SK-HK.02/IX/2022).
# 예전에는 전국에 Rp 30억(자카르타 아파트 기준) 하나만 적용해, 반텐·서부자바의 Rp 20억대
# 아파트까지 '최소가 미달'로 낮췄고 단독주택에는 최소가 검사가 아예 없었다.
# 2026-09-15 조사에서 출처(Hukumonline 요약 vs detik.com)끼리 자카르타·반텐·발리 밖 수치가
# 엇갈렸다. 스크리닝이 '가능'을 잘못 내는 쪽이 더 위험하므로 엇갈리면 높은 값을 쓴다.
# (주, 단독주택 최소가, 아파트 최소가)
FOREIGN_MIN_PRICE_BY_PROVINCE = {
    "DKI Jakarta": (5_000_000_000, 3_000_000_000),
    "Banten": (5_000_000_000, 2_000_000_000),
    "Jawa Barat": (5_000_000_000, 2_000_000_000),
    "Jawa Tengah": (5_000_000_000, 2_000_000_000),
    "DI Yogyakarta": (5_000_000_000, 2_000_000_000),
    "Jawa Timur": (5_000_000_000, 2_000_000_000),
    "Bali": (5_000_000_000, 2_000_000_000),
    "Nusa Tenggara Barat": (3_000_000_000, 1_000_000_000),
    "Sumatera Utara": (2_000_000_000, 1_000_000_000),
    "Kalimantan Timur": (2_000_000_000, 1_000_000_000),
    "Sulawesi Selatan": (2_000_000_000, 1_000_000_000),
    "Kepulauan Riau": (2_000_000_000, 1_000_000_000),
}
# 지역을 못 찾으면 가장 높은 기준(자카르타)을 쓴다 - 모르는 지역을 싸게 보지 않기 위함.
_DEFAULT_MIN_PRICE = FOREIGN_MIN_PRICE_BY_PROVINCE["DKI Jakarta"]

# 매물 텍스트의 도시·지역명 → 주. 수집 소스(99.co/OLX/인도웹)에 실제로 나오는 표기 위주.
_PROVINCE_KEYWORDS = [
    ("DKI Jakarta", r"jakarta|자카르타|jkt|kelapa\s*gading|끌라빠\s*가딩|pluit|sunter|kemayoran|"
                    r"pantai\s*indah\s*kapuk|\bpik\b|scbd|kuningan|kebayoran|cibubur"),
    ("Banten", r"tangerang|탕그랑|땅그랑|\bbsd\b|serpong|세르퐁|alam\s*sutera|karawaci|"
               r"가라와찌|cipondoh|ciputat|serang|cilegon|찔레곤|tigaraksa|banten"),
    ("Jawa Barat", r"bekasi|브카시|cikarang|찌까랑|meikarta|maikarta|메이까르타|메이카르타|bogor|보고르|"
                   r"depok|데포|bandung|반둥|karawang|subang|수방|majalengka|purwakarta|"
                   r"cirebon|sukabumi|parung|cimahi|jawa\s*barat"),
    ("Jawa Tengah", r"semarang|스마랑|solo\b|surakarta|kartasura|purwokerto|sragen|jawa\s*tengah"),
    ("DI Yogyakarta", r"yogyakarta|jogja|sleman|condongcatur|족자"),
    ("Jawa Timur", r"surabaya|수라바야|malang|sidoarjo|pasuruan|pandaan|gresik|lamongan|kepanjen|"
                   r"\bbatu\b|jawa\s*timur"),
    ("Bali", r"\bbali\b|발리|denpasar|badung|canggu|ubud|seminyak|jimbaran"),
    ("Nusa Tenggara Barat", r"lombok|롬복|mataram"),
    ("Sumatera Utara", r"medan|메단"),
    ("Kalimantan Timur", r"balikpapan|samarinda"),
    ("Sulawesi Selatan", r"makassar"),
    ("Kepulauan Riau", r"batam|바탐|bintan"),
]

# SOHO·오피스 분양은 비주거 집합건물이라 외국인 개인 명의 대상(주거용 rumah susun)이 아니다.
# 여기 넣으면 'Boutique Soho BSD', 'Grand Soho Slipi 사무실'이 개인 취득 '가능'으로 잘못 나온다.
_APARTMENT = re.compile(r"\bapartemen\b|\bapartment\b|\bcondo|\bstrata\b|아파트|레지던스|"
                        r"펜트하우스|penthouse", re.I)
_NON_RESIDENTIAL_STRATA = re.compile(r"\bsoho\b|\boffice\b|\bkantor\b|사무실|오피스", re.I)
_LAND_ONLY = re.compile(r"\btanah\s+(?:kosong|kavling)\b|\bkavling\b", re.I)
# '토지 매매' 글이라도 제목이 집·건물이면 나대지가 아니다(단지 내 kavling 표기 오판 방지).
_BUILDING_TITLE = re.compile(r"\brumah\b|\bhouse\b|\bvilla\b|주택|빌라|건물|bangunan|gedung", re.I)
_BUILDING_DEAL_TITLE = re.compile(r"\bruko\b|\brukan\b|\bgedung\b|\bbangunan\b|\brumah\b(?!\s*makan)|"
                                  r"\bgudang\b|\bpabrik\b|건물|루코|공장", re.I)
_HOUSE = re.compile(r"\brumah\b|\bhouse\b|\bvilla\b|주택|빌라|townhouse|cluster", re.I)


def detect_province(item):
    """매물의 주(州)를 추정한다. 못 찾으면 None."""
    # 정형 필드 → 제목 → 본문 순으로 본다. 본문에는 '자카르타에서 1시간' 같은
    # 다른 도시 언급이 섞여 있어, 한 덩어리로 보면 엉뚱한 주가 먼저 잡힌다.
    for keys in (("location", "locationKo", "address"), ("title",), ("description",)):
        text = " ".join(str(item.get(k) or "") for k in keys)
        for province, pat in _PROVINCE_KEYWORDS:
            if re.search(pat, text, re.I):
                return province
    return None


def foreign_min_price(item, kind):
    """(최소가, 주 이름 또는 '지역 미상') 반환. kind 는 'house' / 'apartment'."""
    province = detect_province(item)
    house, apartment = FOREIGN_MIN_PRICE_BY_PROVINCE.get(province, _DEFAULT_MIN_PRICE)
    return (house if kind == "house" else apartment), (province or "지역 미상(자카르타 기준 적용)")


def rupiah_ko(amount):
    """루피아 금액을 한글 단위로. 단위를 억/십억으로 뭉개면 소액이 0으로 보인다."""
    if amount >= 1_000_000_000:
        return f"Rp {amount/1_000_000_000:,.1f}십억"
    if amount >= 1_000_000:
        return f"Rp {amount/1_000_000:,.0f}백만"
    return f"Rp {amount:,.0f}"


def _text(item):
    return " ".join(str(item.get(k) or "") for k in
                    ("title", "description", "category", "type", "badge"))


# 업종 키워드 바로 앞에 위치 표현이 오면 그 업종이 아니라 '주변 시설' 설명이다.
# 이걸 가리지 않던 동안 'sebelah Indomaret(인도마렛 옆) 세탁소', 'Dekat Pangkalan Angkutan
# Umum(버스 정류장 근처) 학교'가 편의점·여객운송 업종으로 오판돼 '불가'로 떨어졌다.
_NEARBY_PREFIX = re.compile(
    r"(?:sebelah|samping|dekat|deket|dkt|depan|seberang|belakang|di\s+area|area|"
    r"near|next\s+to|opposite|옆|근처|앞)\s*(?:\(|dengan|dg|ada)?\s*$", re.I)


def _blocked_business_reason(item):
    text = _text(item)
    for pat, reason in BLOCKED_BUSINESS:
        for m in re.finditer(pat, text, re.I):
            if not _NEARBY_PREFIX.search(text[max(0, m.start() - 20):m.start()]):
                return reason
    return None


def classify(item):
    """(상태, 사유 한 줄, 절차 안내 목록) 반환."""
    certs = detect_certificate(item)
    cert_ko = [ko for _, ko in certs]
    has_shm = any(ko.startswith("SHM(") for ko in cert_ko)
    has_shmsrs = any(ko.startswith("SHMSRS") for ko in cert_ko)
    has_hgb = any(ko.startswith("HGB") for ko in cert_ko)
    has_pakai = any(ko.startswith("Hak Pakai") for ko in cert_ko)
    has_girik = any(ko.startswith("Girik") for ko in cert_ko)
    text = _text(item)
    price = float(item.get("priceNum") or 0)

    steps = []

    # --- 1) 미등기 관습지: 국적과 무관하게 외국인 취득 경로가 없다 ---
    if has_girik and not (has_shm or has_hgb or has_pakai):
        return (BLOCKED, "Girik(미등기 관습지) 매물 - 외국인·PT PMA 모두 등기 취득 불가",
                ["국민 명의 SHM 등기 후 HGB 전환을 거쳐야만 거래 대상이 됨"])

    # --- 2) 사업체 인수 ---
    if item.get("subtype") == "akuisisi" or item.get("type") == "bisnis":
        blocked = _blocked_business_reason(item)
        if blocked:
            # 'RUKO 4 lantai + usaha laundry Rp 45억'처럼 가치의 대부분이 건물인 매물은
            # 영업은 못 넘겨받아도 건물은 PT PMA 명의(HGB)로 살 수 있다. 통째로 불가로 내리면
            # 실제로 거래 가능한 부동산까지 버린다.
            if (_BUILDING_DEAL_TITLE.search(str(item.get("title") or ""))
                    and price >= PT_PMA_MIN_PAID_UP):
                return (CONDITIONAL,
                        f"건물만 취득 검토 가능 - {blocked}",
                        ["해당 업종 영업·권리금은 인수 대상에서 제외하고 건물(부동산)만 매매",
                         "PT PMA 명의 HGB 로 취득 - SHM 이면 매도인의 HGB 전환 필요",
                         "건물 용도(PBG/SLF)가 인수 후 운영할 업종과 맞는지 확인"])
            return BLOCKED, blocked, []

        # 지분 인수: 법인과 그 인허가·임차계약을 그대로 넘겨받으므로 인수가와 무관하게
        # 외국인이 취득할 수 있는 구조다. 다만 법인이 PMDN(내국 법인)이면 지분 인수 순간
        # PMA 로 전환돼 투자 요건을 새로 받는다.
        if _SHARE_DEAL.search(text) and not _ASSET_ONLY.search(text):
            steps = ["AHU 법인 등기부로 현재 주주 구성과 PMA/PMDN 여부 확인",
                     "OSS 에서 법인의 KBLI 가 외국인 지분 100% 허용 업종인지 확인",
                     "PMDN 이면 외국인 지분이 1%만 들어와도 PMA 전환 의무 - "
                     "납입자본 Rp 25억(12개월 인출 불가)·투자계획 Rp 100억 요건 재충족 필요",
                     "세무(DJP)·임금·임차료 미납과 소송(SIPP) 등 법인에 딸린 채무 실사 필수"]
            if not price:
                steps.insert(0, "인수가 미표기 - 지분가와 법인 부채 인수 범위를 함께 확인")
            if item.get("propertyIncluded") or has_shm:
                steps.append("법인 명의 부동산이 SHM 이면 법인이 보유할 수 없는 권리 - HGB 여부 확인")
            return (ELIGIBLE, "기존 법인 지분 인수 - 신규 PT PMA 설립 없이 외국인 인수 가능한 구조",
                    steps)

        # 가격 미표기(한인 커뮤니티 글은 '연락 주세요'로 끝나는 경우가 흔하다)를
        # 0원으로 읽어 불가 처리하면, 정작 실제 인수 대상인 매물이 전부 사라진다.
        # 모르는 값은 모른다고 하고 확인 절차를 안내한다.
        if not price:
            steps.append("인수가 미표기 - 매도인에게 총액과 포함 범위(권리금·재고·설비) 확인")
        elif price < BUSINESS_REALISTIC_MIN:
            return (BLOCKED,
                    f"인수가 {rupiah_ko(price)} - 법인 설립·인허가 비용에도 못 미치는 규모로"
                    " 외국인 인수 구조가 성립하지 않음(임차권 양도 수준)",
                    [])
        elif price < PT_PMA_MIN_PAID_UP:
            return (BLOCKED,
                    f"자산 양도 {rupiah_ko(price)} - 신규 PT PMA 납입자본(Rp 25억)에도 못 미쳐"
                    " 적법한 인수 구조가 성립하지 않음(현지인 명의 대여는 무효)",
                    ["이미 같은 업종(KBLI)의 PT PMA 를 보유한 경우에만 그 법인 명의로 검토 가능"])

        steps.append("PT PMA(외국인투자법인) 설립 후 법인 명의로 인수 - 개인 명의 인수 불가")
        steps.append("해당 업종 KBLI 의 외국인 지분 상한을 OSS 에서 먼저 확인")
        if not price:
            status = CONDITIONAL
            reason = "PT PMA 설립 시 인수 가능 - 인수가가 공개되지 않아 규모 확인 필요"
            if item.get("propertyIncluded") or has_shm:
                steps.append("부동산이 포함된 경우 SHM 은 PT PMA 명의로 이전 불가 - "
                             "HGB 전환 또는 부동산 임차 구조로 분리 필요")
            return status, reason, steps
        if price < PT_PMA_MIN_INVESTMENT:
            steps.append(
                f"인수가 {rupiah_ko(price)} < PT PMA 최소 투자 요건 Rp 100억"
                " - 최소 투자액은 인수 대금이 아니라 3년 투자 계획 총액(설비·운전자금 포함,"
                " 토지·건물 제외) 기준이므로, 증설·운전자금 계획으로 요건을 설계할 것")
            status = CONDITIONAL
            reason = "PT PMA 설립 시 인수 가능하나 최소 투자 요건(Rp 100억) 미달"
        else:
            status = CONDITIONAL
            reason = "PT PMA 설립 시 인수 가능 - 투자 규모는 최소 요건 충족"
        if item.get("propertyIncluded") or has_shm:
            steps.append("부동산이 포함된 경우 SHM 은 PT PMA 명의로 이전 불가 - "
                         "HGB 전환 또는 부동산 임차 구조로 분리 필요")
        return status, reason, steps

    # --- 3) 아파트/집합건물: 외국인 개인 취득이 가장 명확한 유형 ---
    title = str(item.get("title") or "")
    if _NON_RESIDENTIAL_STRATA.search(title) and not _APARTMENT.search(title):
        return (CONDITIONAL, "SOHO·사무실 분양 - 비주거용이라 외국인 개인 명의 불가, PT PMA 명의로 취득",
                ["PT PMA 설립 후 법인 명의 취득(건물 토지가 HGB 인지 확인)",
                 "사업장 주소로 쓰려면 건물 용도가 사무실(PBG/SLF)로 등록돼 있는지 확인"])
    if _APARTMENT.search(title) or has_shmsrs or (
            _APARTMENT.search(text) and not _HOUSE.search(title)):
        min_price, where = foreign_min_price(item, "apartment")
        if not price:
            return (CONDITIONAL, "외국인 개인 취득 가능 유형 - 매매가 미표기로 최소가 충족 여부 미확인",
                    [f"{where} 외국인 아파트 최소가 {rupiah_ko(min_price)} 이상인지 매매가 확인",
                     "체류허가(KITAS/KITAP) 사본 필요"])
        if price < min_price:
            return (CONDITIONAL,
                    f"외국인 개인 취득 가능 유형이나 {where} 최소가 {rupiah_ko(min_price)} 미달",
                    [f"표시가 {rupiah_ko(price)} - 최소가 미만이면 외국인 명의 등기 불가",
                     "최소가 이상으로 매매하는 경우에만 진행(다운계약은 등기 무효 위험)"])
        return (ELIGIBLE, f"SHMSRS 구조로 외국인 개인 명의 취득 가능({where} 최소가 충족)",
                ["체류허가(KITAS/KITAP) 보유 시 개인 명의 등기 가능",
                 "건물이 HGB 토지 위 SHMSRS 인지, 분양 잔여 기간과 관리비 체납 여부 확인"])

    # --- 4) 단독주택: 외국인 개인은 Hak Pakai 로만, 지역별 최소가 이상일 때 ---
    if _HOUSE.search(title) and item.get("type") != "ruko":
        min_price, where = foreign_min_price(item, "house")
        steps = ["외국인 개인은 Hak Pakai 로만 취득(30년+20년 연장+30년 갱신) - "
                 "SHM/HGB 는 매도인 측 권리 전환 필요",
                 "체류허가(KITAS/KITAP) 필요, 가구당 1필지·2,000㎡ 이하"]
        if has_shm and not (has_hgb or has_pakai):
            steps.insert(0, "SHM - 외국인 명의 불가, Hak Pakai 로 전환 후 등기")
        if not price:
            return (CONDITIONAL, "단독주택 - 매매가 미표기로 외국인 최소가 충족 여부 미확인",
                    [f"{where} 외국인 단독주택 최소가 {rupiah_ko(min_price)} 이상인지 확인"] + steps)
        if price < min_price:
            return (CONDITIONAL,
                    f"단독주택 {rupiah_ko(price)} - {where} 외국인 최소가 {rupiah_ko(min_price)} 미달",
                    ["최소가 미만 주택은 외국인 개인 명의 취득 불가"] + steps)
        return (CONDITIONAL, f"단독주택 - Hak Pakai 전환 시 외국인 개인 취득 가능({where} 최소가 충족)",
                steps)

    # --- 5) 나대지 ---
    if _LAND_ONLY.search(text) and not _BUILDING_TITLE.search(title):
        return (CONDITIONAL, "나대지는 외국인 개인 취득 불가 - PT PMA 명의 HGB 로만 가능",
                ["PT PMA 설립 후 HGB 취득", "SHM 매물이면 매도인의 HGB 전환 절차 필요"])

    # --- 6) 루코·상가·창고 등 상업용 부동산 ---
    if has_hgb:
        return (ELIGIBLE, "HGB 매물 - PT PMA 명의로 직접 취득 가능",
                ["PT PMA 설립 및 KBLI 등록", "HGB 잔여 기간과 연장 이력 확인"])
    if has_pakai:
        return (ELIGIBLE, "Hak Pakai 매물 - 외국인 개인/PT PMA 취득 가능",
                ["잔여 사용기간 확인"])
    if has_shm:
        return (CONDITIONAL, "SHM 매물 - 외국인 개인 명의 불가, PT PMA + HGB 전환 필요",
                ["PT PMA 설립", "매도인의 권리 포기(pelepasan hak) 후 HGB 신규 발급 절차",
                 "전환 비용·기간을 매매 조건에 반영할 것"])

    return (CONDITIONAL, "권리 형태(증서) 미표기 - 외국인 취득 가능 여부 확인 필요",
            ["매도인에게 sertifikat(SHM/HGB/Hak Pakai) 종류 확인",
             "SHM 이면 PT PMA + HGB 전환이 전제됨"])


def rank_key(status):
    """가능 → 조건부 → 불가 순으로 정렬하기 위한 키."""
    return {ELIGIBLE: 0, CONDITIONAL: 1, BLOCKED: 2}.get(status, 3)
