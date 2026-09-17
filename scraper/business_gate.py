# -*- coding: utf-8 -*-
"""'한국인이 인수해서 실제로 사업을 굴릴 수 있는 매물인가'의 최종 관문.

foreign_eligibility 는 '명의를 가질 수 있는 구조인가'(제도), operability 는
'실체가 있는 매물인가'(사실)를 본다. 이 모듈은 그 둘을 통과한 매물 중에서
**추천해도 되는 것만** 남긴다. 기준을 못 넘기면 0건으로 끝내는 것이 정상이다
— 빈손으로 두는 편이, 인수해봐야 운영할 수 없는 물건을 권하는 것보다 낫다.

관문 (하나라도 걸리면 탈락, 사유를 남긴다)
  1. 사업체일 것          - 루코·아파트·토지 같은 순수 부동산은 '인수해서 운영할 사업'이 아니다
  2. 업종이 외국인에게 열려 있을 것 - Perpres 10/2021 부속서 확인분만 통과(확인 못한 업종은 탈락)
  3. 취득 구조가 성립할 것 - foreign_eligibility 가 '불가'로 본 매물 제외
  4. 영업 실체가 확인될 것 - operability '운영가능'만(‘확인필요’는 추천 대상 아님)
  5. 규모를 알 수 있을 것  - 인수가나 매출 중 하나는 있어야 검토가 시작된다
  6. 최근 글일 것          - 오래된 글은 이미 팔렸거나 조건이 바뀐다

⚠️ 법률 자문이 아니라 공개 데이터 기반 1차 스크리닝이다.
"""
import re
from datetime import datetime, timezone

import foreign_eligibility as fe
import operability as op

# 추천 대상에서 제외할 '순수 부동산' 신호. 사업체 인수가 아니라 부동산 매매다.
_PROPERTY_ONLY = re.compile(
    r"\btanah\b|\bkavling\b|\bapartemen\b|\bapartment\b|아파트|토지|나대지|"
    r"\bruko\b(?!.*\busaha\b)|레지던스|\bsoho\b", re.I)

# 게시 후 이 기간을 넘긴 글은 추천하지 않는다(operability 의 180/365일보다 엄격하게 본다).
MAX_POST_AGE_DAYS = 90

# 업종 판정: (업종명, 정규식, 상태, 근거)
# 상태 OPEN  = 외국인 지분 100% 가능으로 조사된 업종
#      CAPPED= 지분 상한이 있어 경영권을 못 가짐 → 추천하지 않는다
#      BLOCKED= 외국인 투자 불가(유보·폐쇄)
#
# ⚠️ 출처 한계(2026-09-17 조사): Perpres 10/2021 부속서 원문(PDF) 파싱에 두 번 실패해,
# 아래 상태는 KBLI 조회 사이트(kbli.co.id)와 로펌 해설을 교차 확인한 2차 출처 기준이다.
# 그래서 ① 목록에 없는 업종은 '미확인'으로 탈락시키고(모르면 추천하지 않는다),
# ② 통과한 매물의 안내문에도 OSS 에서 KBLI 를 직접 확인하라는 문구를 붙인다.
# 애매한 업종(기숙사 kos, 호텔, 소매 일반, 양계)은 일부러 넣지 않았다.
SECTORS = [
    # --- 외국인 투자 불가 ---
    ("세탁업(KBLI 96200)", r"\blaundry\b|\bbinatu\b|\bcuci\s*(?:kiloan|baju|setrika)\b|세탁소|런드리|빨래방",
     "BLOCKED", "UMKM·협동조합 유보"),
    ("미용실(KBLI 96112)", r"salon\s*kecantikan|beauty\s*salon|\bsalon\b|미용실|헤어샵|헤어샾",
     "BLOCKED", "소상공인 유보(2차 출처, 원문 미확인)"),
    ("소형 소매·편의점(KBLI 47111)",
     r"minimarket|toko\s*kelontong|sembako|alfamart|indomaret|alfamidi|구멍가게|편의점",
     "BLOCKED", "매장 400㎡ 미만 소형 소매는 UMKM 유보"),
    ("노점·소형식당", r"\bwarung\b|\bwarteg\b|kaki\s*lima|gerobak|angkringan|노점|포장마차",
     "BLOCKED", "UMKM 유보"),
    ("정규학교(KBLI 85xxx)", r"\bsekolah\b.{0,40}\b(?:sd|smp|sma|smk)\b|정규학교",
     "BLOCKED", "경제특구 밖 외국인 투자 불가"),
    ("연료·LPG 소매", r"pangkalan\s*gas|agen\s*lpg|pertamini", "BLOCKED", "외국인 투자 제한"),
    # --- 지분 상한이 있어 경영권 확보 불가 ---
    ("택배·운송대행(KBLI 53201)", r"\bekspedisi\b|\bkurir\b|\bcourier\b|택배",
     "CAPPED", "외국인 지분 49% 상한 - 단독 경영 불가"),
    ("육상 여객운송", r"\bojek\b|angkutan\s*(?:umum|orang)", "CAPPED", "외국인 지분 제한"),
    # --- 외국인 지분 100% 가능(투자계획 Rp 100억 요건은 별도) ---
    ("요식업(KBLI 56101·56303)",
     r"restoran|rumah\s*makan|\bresto\b|\bcafe\b|café|kedai\s*kopi|coffee\s*shop|"
     r"식당|음식점|카페|커피|레스토랑|한식당|분식|치킨집",
     "OPEN", "외국인 지분 100% 가능"),
    ("제과·베이커리(KBLI 10710)", r"bakery|patisserie|\broti\b|\bkue\b|베이커리|제과|디저트",
     "OPEN", "외국인 지분 100% 가능"),
    ("세차·자동차정비(KBLI 45201)",
     r"cuci\s*mobil|car\s*wash|carwash|\bbengkel\b|detailing|세차장|정비소|카센터",
     "OPEN", "외국인 지분 100% 가능"),
    ("스파·마사지(KBLI 96122)", r"\bspa\b|\bmassage\b|pijat|스파|마사지",
     "OPEN", "외국인 지분 100% 가능"),
    ("제조업(플라스틱·금형·인쇄 등)",
     r"\bpabrik\b|manufactur|\bmesin\b|injeksi|\bmolding\b|\bmoulding\b|percetakan|printing|"
     r"공장|금형|사출|제조|인쇄",
     "OPEN", "제조업은 외국인 지분 100% 가능(환경허가·산단 입지 별도)"),
    ("화장품 제조(KBLI 20232)", r"kosmetik|cosmetic|화장품", "OPEN", "BPOM 등록 별도"),
    ("의료기기 제조(KBLI 32501)", r"alat\s*kesehatan|medical\s*device|의료기기",
     "OPEN", "인허가 부담 큼"),
    ("창고·물류(KBLI 52101)", r"\bgudang\b|warehouse|pergudangan|물류창고|창고",
     "OPEN", "외국인 지분 100% 가능"),
    ("무역·도매(KBLI 46xxx)", r"\bekspor\b|\bimpor\b|export|import|\bgrosir\b|distribut|무역|도매",
     "OPEN", "대부분 코드 개방"),
    ("IT·소프트웨어(KBLI 62xxx)", r"\bsoftware\b|aplikasi|\bit\s*service|개발사|소프트웨어",
     "OPEN", "개방 업종"),
    ("스포츠시설(KBLI 93111)", r"\bpadel\b|\bgym\b|fitness|futsal|스포츠센터|헬스장",
     "OPEN", "외국인 지분 100% 가능"),
]


def _text(item):
    return " ".join(str(item.get(k) or "") for k in
                    ("title", "description", "category", "type", "badge"))


def detect_sector(item):
    """(업종명, 상태, 근거) 반환. 판정할 수 없으면 (None, None, None)."""
    text = _text(item)
    for name, pat, status, source in SECTORS:
        if re.search(pat, text, re.I):
            return name, status, source
    return None, None, None


def _age_days(item):
    raw = item.get("postedAt")
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 86400


def assess(item):
    """(통과 여부, 탈락 사유, 통과 근거 목록) 반환."""
    title = str(item.get("title") or "")
    reasons = []

    is_business = item.get("type") == "bisnis" or item.get("subtype") == "akuisisi"
    if not is_business or _PROPERTY_ONLY.search(title):
        return False, "사업체 인수 매물이 아님(부동산 매매)", []

    sector, sector_status, sector_source = detect_sector(item)
    if sector is None:
        return False, "업종을 특정할 수 없음 - 외국인 개방 여부를 확인할 수 없는 매물", []
    if sector_status == "BLOCKED":
        return False, f"{sector} - 외국인 투자 불가 업종", []
    if sector_status == "CAPPED":
        return False, f"{sector} - 지분 상한으로 단독 경영 불가", []
    reasons.append(f"업종 {sector}: {sector_source}")

    status, reason, _steps = fe.classify(item)
    if status == fe.BLOCKED:
        return False, f"외국인 취득 불가 - {reason}", []
    reasons.append(f"취득 구조 {status} - {reason}")

    op_status, _score, op_reasons, _todos = op.classify(item)
    if op_status != op.OPERABLE:
        return False, f"영업 실체 {op_status} - {'; '.join(op_reasons) or '근거 부족'}", []
    reasons.extend(op_reasons)

    # 규모를 알 수 있어야 검토가 시작된다. 다만 한인 커뮤니티 글은 매출을 '연매출 20억'처럼
    # 문장으로만 쓰고 숫자 필드에는 안 남는다. 수집기가 못 뽑았을 뿐 글에는 있는 정보라,
    # operability 가 매출 신호를 찾았으면 규모가 제시된 것으로 본다.
    revenue_stated = any("매출" in r for r in op_reasons)
    if not item.get("priceNum") and not item.get("monthlyRevenueNum") and not revenue_stated:
        return False, "인수가·매출이 모두 없어 규모를 검토할 수 없음", []

    age = _age_days(item)
    if age is None:
        return False, "게시일을 알 수 없음", []
    if age > MAX_POST_AGE_DAYS:
        return False, f"게시 후 {age:.0f}일 경과({MAX_POST_AGE_DAYS}일 초과)", []
    reasons.append(f"최근 게시({age:.0f}일 전)")

    return True, "", reasons


# 사용자 예산: 한국돈 1~2억. 이 구간의 인수가를 '최적(1차)'으로, 나머지(초과·미표기)는
# '예산 밖(2차)'으로 나눠 보낸다. 2차를 버리지 않는 이유는 예산 밖이라도 정말 좋은
# 매물(예: 다점포 브랜드)은 공동 투자·지분 일부 인수 같은 다른 구조로 검토할 가치가 있어서다.
BUDGET_MIN_KRW = 100_000_000
BUDGET_MAX_KRW = 200_000_000
TIER_BUDGET = "최적(₩1~2억)"
TIER_OVER = "예산 밖"


def budget_tier(item):
    """(등급, 한글 설명) 반환. 인수가 미표기는 예산 밖으로 둔다 - 모르는 값을 맞다고 볼 수 없다."""
    price = item.get("priceNum") or 0
    if not price:
        return TIER_OVER, "인수가 미표기"
    krw = price / fe.KRW_TO_IDR
    if BUDGET_MIN_KRW <= krw <= BUDGET_MAX_KRW:
        return TIER_BUDGET, f"인수가 ≈₩{krw / 1e8:.2f}억"
    if krw < BUDGET_MIN_KRW:
        return TIER_OVER, f"인수가 ≈₩{krw / 1e8:.2f}억 - 예산 하한 미만"
    return TIER_OVER, f"인수가 ≈₩{krw / 1e8:.1f}억 - 예산 초과"


def screen(items):
    """(통과 목록, 탈락 사유별 건수) 반환. 통과 매물에는 _tier(1차/2차)를 붙인다."""
    passed, rejected = [], {}
    for item in items:
        ok, why, reasons = assess(item)
        if ok:
            item["_gateReasons"] = reasons
            item["_tier"], item["_tierNote"] = budget_tier(item)
            passed.append(item)
        else:
            key = why.split(" - ")[0]
            rejected[key] = rejected.get(key, 0) + 1
    return passed, rejected
