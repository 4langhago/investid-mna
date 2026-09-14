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
    (r"\bsekolah\b.*\b(?:sd|smp|sma|smk)\b|\b(?:sd|smp|sma|smk)\b.*\bsekolah\b",
     "초·중·고 정규학교 운영은 경제특구(KEK) 밖에서 외국인 투자 불가"),
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

# 외국인 개인 주거용 취득 최소 가격(지역별 상이, 자카르타권 기준을 보수적으로 사용).
FOREIGN_HOME_MIN_PRICE = 3_000_000_000

_APARTMENT = re.compile(r"\bapartemen\b|\bapartment\b|\bcondo|\bstrata\b|아파트", re.I)
_LAND_ONLY = re.compile(r"\btanah\s+(?:kosong|kavling)\b|\bkavling\b", re.I)


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


def _blocked_business_reason(item):
    text = _text(item)
    for pat, reason in BLOCKED_BUSINESS:
        if re.search(pat, text, re.I):
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
            return BLOCKED, blocked, []

        # 지분 인수: 법인과 그 인허가·임차계약을 그대로 넘겨받으므로 인수가와 무관하게
        # 외국인이 취득할 수 있는 구조다. 다만 법인이 PMDN(내국 법인)이면 지분 인수 순간
        # PMA 로 전환돼 투자 요건을 새로 받는다.
        if _SHARE_DEAL.search(text) and not _ASSET_ONLY.search(text):
            steps = ["AHU 법인 등기부로 현재 주주 구성과 PMA/PMDN 여부 확인",
                     "OSS 에서 법인의 KBLI 가 외국인 지분 100% 허용 업종인지 확인",
                     "PMDN 이면 지분 인수 시 PMA 전환 - 납입자본 Rp 25억·투자계획 요건 재충족 필요",
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
    if _APARTMENT.search(text) or has_shmsrs:
        if price < FOREIGN_HOME_MIN_PRICE:
            return (CONDITIONAL,
                    "외국인 개인 취득 가능 유형이나 지역별 최소 가격 요건 미달 가능",
                    [f"표시가 {rupiah_ko(price)} - 관할 주(州) 최소 가격 기준 확인 필요",
                     "체류허가(KITAS/KITAP) 사본 필요"])
        return (ELIGIBLE, "SHMSRS/Hak Pakai 구조로 외국인 개인 명의 취득 가능",
                ["체류허가(KITAS/KITAP) 보유 시 개인 명의 등기 가능",
                 "분양 잔여 사용기간과 관리비 체납 여부 확인"])

    # --- 4) 나대지 ---
    if _LAND_ONLY.search(text):
        return (CONDITIONAL, "나대지는 외국인 개인 취득 불가 - PT PMA 명의 HGB 로만 가능",
                ["PT PMA 설립 후 HGB 취득", "SHM 매물이면 매도인의 HGB 전환 절차 필요"])

    # --- 5) 루코·상가·창고 등 상업용 부동산 ---
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
