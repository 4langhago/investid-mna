# -*- coding: utf-8 -*-
"""인수 금액 5단계 구간 분류와 '딜 구조'(자산 양수 / 지분 양수) 판정.

왜 두 가지를 한 모듈에 두는가:
  인도네시아에서는 이 둘이 한 몸이다. 인허가(NIB·업종허가)는 법인 간 이전 제도가
  없어서, 자산 양수(asset deal)로 사면 매도인의 허가는 따라오지 않고 인수자의
  신설 PT PMA 가 전부 새로 받아야 한다. 그래서 같은 금액·같은 업종이라도
  '주식을 사는 딜'과 '설비·영업권을 사는 딜'은 실행 난이도가 다르다.
  금액만 보고 추천 순서를 정하면 이 차이가 지워진다.

금액 5단계(KRW 기준, IDR 환산은 KRW_TO_IDR=11.6 적용):
  1단계  ~₩1억      (~IDR 1.1B)
  2단계  ₩1~3억     (~IDR 3.5B)
  3단계  ₩3~5억     (~IDR 5.8B)
  4단계  ₩5~7억     (~IDR 8.1B)
  5단계  ₩7~10억    (~IDR 11.6B)
  구간 밖 = ₩10억 초과 또는 인수가 미표기

⚠️ PT PMA 최소투자(투자계획 Rp 100억 ≈ ₩8.6억, 납입자본 Rp 25억 ≈ ₩2.2억)를 감안하면
  1~3단계는 단독으로 합법 구조가 성립하지 않는다. 그래서 각 구간에 그 사실을 적어
  메시지에 함께 내보낸다 — 금액이 맞는다고 인수가 가능한 것은 아니다.
"""
import re

import foreign_eligibility as fe

# --- 금액 5단계 ---------------------------------------------------------------
# (구간번호, 라벨, KRW 하한(초과), KRW 상한(이하))
TIERS = [
    (1, "1단계 ₩1억 이하",        0,              100_000_000),
    (2, "2단계 ₩1~3억",           100_000_000,    300_000_000),
    (3, "3단계 ₩3~5억",           300_000_000,    500_000_000),
    (4, "4단계 ₩5~7억",           500_000_000,    700_000_000),
    (5, "5단계 ₩7~10억",          700_000_000,  1_000_000_000),
]
TIER_OUT = (9, "구간 밖")

# PMA 최소투자 요건. 구간별로 '금액은 맞지만 제도가 막는다'를 설명하기 위한 값.
PMA_PAID_UP_IDR = 2_500_000_000      # 납입자본 Rp 25억
PMA_INVEST_PLAN_IDR = 10_000_000_000  # 투자계획 Rp 100억

# 구간별 제도 주의문. 1~3단계는 단독 PMA 가 성립하지 않는다는 사실을 숨기지 않는다.
_TIER_CAVEAT = {
    1: "PMA 투자계획 Rp 100억(≈₩8.6억) 미달 - 단독 인수로는 합법 구조 불성립. "
       "동일 KBLI PT PMA 보유자의 자산 인수 또는 복수 매물 묶음(롤업)만 가능",
    2: "PMA 투자계획 미달 + 납입자본 Rp 25억(≈₩2.2억)이 인수가를 넘거나 비슷함 "
       "- 총 소요 현금이 인수가보다 커진다",
    3: "PMA 투자계획 미달 - 복수 매물 묶음 또는 기존 PMA 활용 전제",
    4: "PMA 투자계획 Rp 100억에 근접 - 운전자금 합산으로 요건 충족 여지 있음",
    5: "PMA 투자계획 Rp 100억을 단독으로 충족할 수 있는 구간",
}


def krw(price_idr):
    """IDR 인수가를 원화로 환산. 값이 없으면 None."""
    if not price_idr:
        return None
    return price_idr / fe.KRW_TO_IDR


def classify_tier(item):
    """(구간번호, 라벨, 설명) 반환. 인수가 미표기는 '구간 밖'으로 둔다."""
    price = item.get("priceNum") or 0
    won = krw(price)
    if not won:
        return TIER_OUT[0], TIER_OUT[1], "인수가 미표기 - 구간 판정 불가"
    for idx, label, low, high in TIERS:
        if low < won <= high:
            return idx, label, f"인수가 ≈₩{won / 1e8:.2f}억 · {_TIER_CAVEAT[idx]}"
    return TIER_OUT[0], TIER_OUT[1], f"인수가 ≈₩{won / 1e8:.1f}억 - ₩10억 초과"


# --- 딜 구조 -----------------------------------------------------------------
SHARE_DEAL = "지분양수"      # 법인 주식을 산다 → NIB·업종허가·세무이력이 함께 넘어온다
ASSET_DEAL = "자산양수"      # 설비·영업권만 산다 → 인허가는 인수자가 새로 받아야 한다
MINORITY = "소수지분"        # 50% 미만 → 경영권 없음. 인수가 아니라 투자다
UNKNOWN = "구조미확인"

# 매물 글에 '법인 자체를 넘긴다'는 신호가 있는가.
_SHARE_RE = re.compile(
    r"\bshare\s*(?:sale|transfer|deal)\b|\bequity\b|\bshareholding\b|\b100%\s*shares?\b"
    r"|\bpt\s*pma\b|\bperseroan\b|\bsaham\b|법인\s*양도|지분\s*(?:양도|인수|매각)"
    r"|주식\s*(?:양도|인수)|\+\s*pt\b|\bpt\s+(?:siap|aktif)\b", re.I)
# 회사 실체(PT)가 딸려 있다는 약한 신호. 단독으로는 지분양수라고 단정하지 않는다.
_ENTITY_RE = re.compile(r"\bprivate\s+limited\b|\blimited\s+liability\b|\bllc\b|\bpt\.?\s+[A-Z]", re.I)

_NIB_WARNING = (
    "인허가 비승계 - 인도네시아는 NIB·업종허가의 법인 간 이전 제도가 없어, "
    "자산 양수 시 신설 PT PMA 가 전부 새로 발급받아야 한다(그 사이 영업 공백 발생). "
    "매도인에게 '주식 양수 구조가 가능한지' 먼저 확인할 것")
_SHARE_NOTE = (
    "법인 주식을 인수하면 NIB·업종허가·거래처 계약이 함께 넘어온다. "
    "대신 과거 세무·노무 우발부채도 함께 인수하므로 3개년 재무·세무 실사가 필수")
_MINORITY_NOTE = (
    "50% 미만 지분은 경영권이 없다 - 인수가 아니라 증자 참여다. "
    "경영권 있는 구조로 재협상하지 못하면 검토 가치 없음")


def classify_structure(item):
    """(구조, 설명) 반환.

    수집기가 dealStructure 를 직접 넣어준 소스(SMERGERS 등)는 그 값을 신뢰한다.
    나머지는 글 본문의 신호로 추정하되, 확신이 없으면 자산양수로 본다 —
    인도네시아 공개 게시판의 'take over usaha' 는 사실상 전부 자산 양수이고,
    이쪽으로 잘못 보는 것이 반대 방향 오판보다 안전하다.
    """
    given = item.get("dealStructure")
    if given == MINORITY:
        return MINORITY, _MINORITY_NOTE
    if given == SHARE_DEAL:
        return SHARE_DEAL, _SHARE_NOTE
    if given == ASSET_DEAL:
        return ASSET_DEAL, _NIB_WARNING

    text = " ".join(str(item.get(k) or "") for k in ("title", "description", "facilities"))
    if _SHARE_RE.search(text):
        return SHARE_DEAL, _SHARE_NOTE
    if _ENTITY_RE.search(text):
        return UNKNOWN, ("법인(PT) 실체는 언급되나 주식 양도 여부가 불분명 - " + _NIB_WARNING)
    return ASSET_DEAL, _NIB_WARNING


def annotate(item):
    """매물에 구간·구조 판정을 붙인다(제자리 수정). 붙인 값을 dict 로 반환."""
    idx, label, note = classify_tier(item)
    structure, s_note = classify_structure(item)
    marks = {
        "_tierIndex": idx,
        "_tierLabel": label,
        "_tierNote5": note,
        "_dealStructure": structure,
        "_dealStructureNote": s_note,
    }
    item.update(marks)
    return marks
