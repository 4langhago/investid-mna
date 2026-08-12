# -*- coding: utf-8 -*-
"""'실제로 인수해서 운영할 수 있는 매물인가' 를 판정한다.

foreign_eligibility 가 '외국인이 명의를 가질 수 있는 구조인가'(제도 문제)를 본다면,
이 모듈은 '그 매물이 실체가 있는 영업체/부동산인가'(사실 문제)를 본다.
두 관문을 모두 통과해야 추천 대상이 된다.

판정 근거는 수집된 텍스트에 실제로 있는 신호뿐이다. 없는 정보를 좋게 해석하지 않는다
— 인수 매물에서 '매출·영업기간·영업중 여부'가 하나도 안 적힌 글은 대개 실체가
없거나(브로커 낚시글) 이미 팔린 글이다. 그래서 그런 글은 '부적합'으로 떨어뜨린다.

한국어(한인 커뮤니티)와 인도네시아어(99.co/OLX) 매물을 같은 규칙으로 본다.
"""
import re
from datetime import datetime, timezone

OPERABLE = "운영가능"    # 영업 실체 신호가 충분함
UNCERTAIN = "확인필요"   # 실체 신호는 있으나 핵심 수치가 비어 있음
UNFIT = "부적합"         # 인수·운영 대상이 아니거나 실체를 확인할 수 없음

# 아예 인수 대상이 아닌 글. 점수와 무관하게 즉시 탈락시킨다.
HARD_REJECT = [
    (r"disewakan|dikontrakkan|for rent|\bsewa bulanan\b|월세\s*임대|임대\s*합니다|"
     r"임대\s*안내|렌트\s*합니다|임대\s*문의",
     "임대 매물 - 인수(양수) 대상이 아님"),
    (r"lowongan|dibutuhkan karyawan|구인|채용|모집합니다",
     "구인·모집 글 - 매물이 아님"),
    (r"waralaba|kemitraan|franchise partner|가맹점\s*모집|투자자\s*모집",
     "프랜차이즈 가맹·투자자 모집 - 기존 사업체 인수가 아님"),
    (r"sudah tutup|tidak beroperasi|berhenti operasi|폐업|휴업\s*중",
     "영업이 중단된 매물"),
    (r"\bsold\b|terjual|판매\s*완료|매각\s*완료|거래\s*완료",
     "이미 거래가 종료된 글"),
]

# 운영 실체를 뒷받침하는 신호. (정규식, 점수, 한글 설명)
POSITIVE = [
    (r"sudah berjalan|berjalan lancar|masih (?:aktif|jalan)|usaha aktif|siap operasional|"
     r"siap pakai|langsung (?:jalan|operasi)|영업\s*중|운영\s*중|즉시\s*운영|정상\s*영업",
     3, "현재 영업 중이라고 명시됨"),
    (r"omset|omzet|pendapatan|laba bersih|net profit|keuntungan|매출|순익|수익금|매상",
     3, "매출·수익 수치가 제시됨"),
    (r"sejak tahun|berdiri (?:sejak|tahun)|sudah \d+ tahun|beroperasi sejak|"
     r"\d+\s*년\s*(?:째|간)?\s*(?:운영|영업)|오픈\s*\d+\s*년",
     2, "영업 기간(업력)이 제시됨"),
    (r"karyawan|pegawai|staff|직원|종업원",
     1, "인계 대상 직원이 있음"),
    (r"peralatan|perlengkapan (?:lengkap|komplit)|mesin|inventaris|집기|설비|기계|시설\s*일체",
     1, "설비·집기 인계 범위가 언급됨"),
    (r"\bnib\b|\boss\b|\bsiup\b|\bnpwp\b|izin (?:lengkap|usaha)|legalitas|"
     r"\bpt\b|\bcv\b|사업자|법인|인허가|허가증",
     2, "사업자 등록·인허가 관련 언급이 있음"),
    (r"pelanggan tetap|customer tetap|langganan|고정\s*(?:고객|거래처)|단골",
     1, "고정 고객 기반이 언급됨"),
    (r"sisa kontrak|kontrak sampai|sewa sampai|masa sewa|임차\s*기간|계약\s*기간|잔여\s*기간",
     1, "임차 잔여 기간이 명시됨"),
]

# 감점 신호.
NEGATIVE = [
    (r"\bsewa\b|\bkontrak\b|임차|월세", -1,
     "임차 점포 - 임대인 승계 동의와 잔여 기간 확인 전에는 운영 지속을 보장할 수 없음"),
    (r"hubungi|wa saja|chat only|자세한\s*(?:내용|사항)은\s*(?:연락|전화)", -1,
     "상세 내용을 공개하지 않고 연락만 유도함"),
]

# 인수 매물에서 이 중 하나도 없으면 '영업 실체 미확인'으로 본다.
SUBSTANCE_RE = re.compile(
    r"sudah berjalan|berjalan lancar|usaha aktif|masih (?:aktif|jalan)|omset|omzet|"
    r"laba bersih|net profit|keuntungan|pendapatan|sejak tahun|berdiri|sudah \d+ tahun|"
    r"karyawan|pegawai|pelanggan tetap|siap operasional|"
    r"영업\s*중|운영\s*중|즉시\s*운영|매출|순익|수익|직원|년\s*(?:째|간)|단골|고정\s*고객",
    re.I)

STALE_DAYS = 180          # 이 기간을 넘긴 글은 거래 종료 가능성이 높다
VERY_STALE_DAYS = 365


def _text(item):
    parts = [str(item.get(k) or "") for k in
             ("title", "description", "category", "badge", "summaryKo", "dealType")]
    parts += [str(f) for f in (item.get("facilities") or [])]
    parts += [str(b) for b in (item.get("koreanFacts") or [])]
    return " ".join(parts)


def _age_days(item):
    raw = item.get("postedAt")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 86400


def _is_business(item):
    return item.get("subtype") == "akuisisi" or item.get("type") == "bisnis"


def classify(item):
    """(등급, 점수, 근거 목록, 확인 필요 항목 목록) 반환."""
    text = _text(item)
    reasons, todos = [], []

    for pat, why in HARD_REJECT:
        if re.search(pat, text, re.I):
            return UNFIT, -99, [why], []

    score = 0
    for pat, pts, why in POSITIVE:
        if re.search(pat, text, re.I):
            score += pts
            reasons.append(why)
    for pat, pts, why in NEGATIVE:
        if re.search(pat, text, re.I):
            score += pts
            todos.append(why)

    # 게시 신선도 - 오래된 글은 이미 팔렸을 확률이 높다.
    age = _age_days(item)
    if age is not None:
        if age > VERY_STALE_DAYS:
            return (UNFIT, score,
                    [f"게시 후 {age:.0f}일 경과 - 거래 종료 가능성이 높아 추천 대상에서 제외"], [])
        if age > STALE_DAYS:
            score -= 2
            todos.append(f"게시 후 {age:.0f}일 경과 - 매도인에게 매물 유효 여부 먼저 확인")
        elif age <= 30:
            score += 1
            reasons.append(f"최근 게시({age:.0f}일 전)")

    # 연락 경로가 전혀 없으면 인수 협상 자체를 시작할 수 없다.
    if not item.get("whatsapp") and not item.get("sourceUrl"):
        return UNFIT, score, ["연락 경로(원문 링크·연락처)가 없어 인수 절차를 시작할 수 없음"], []

    if _is_business(item):
        if not SUBSTANCE_RE.search(text):
            return (UNFIT, score,
                    ["영업 실체 신호(영업중 여부·매출·업력·직원)가 하나도 없음 - "
                     "실재 여부를 확인할 수 없는 글"], [])
        if not item.get("priceNum"):
            todos.append("인수가(권리금 포함) 미표기 - 매도인에게 총액과 포함 범위 확인")
        if not re.search(r"omset|omzet|laba|profit|pendapatan|매출|순익|수익", text, re.I):
            todos.append("매출·순익 미공개 - 최근 12개월 장부와 세금계산서 요구")
        todos.append("영업 인허가(NIB/OSS) 명의 이전 가능 여부와 임대인 승계 동의 확인")
    else:
        if not item.get("area"):
            todos.append("면적 미표기 - 실측 면적과 증서상 면적 대조 필요")

    if score >= 6:
        status = OPERABLE
    elif score >= 2:
        status = UNCERTAIN
    else:
        status = UNFIT
        reasons.append("운영 실체를 뒷받침하는 정보가 너무 적음")

    return status, score, reasons, todos


def rank_key(status):
    """운영가능 → 확인필요 → 부적합 순 정렬 키."""
    return {OPERABLE: 0, UNCERTAIN: 1, UNFIT: 2}.get(status, 3)
