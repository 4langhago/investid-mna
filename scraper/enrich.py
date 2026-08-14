# -*- coding: utf-8 -*-
"""수집한 매물에 '한인이 인수·운영할 수 있는가' 판정을 붙인다.

지금까지 이 판정은 텔레그램 추천(telegram_recommend.py)에서만 계산해 쓰고 버렸다.
그래서 사이트에서는 같은 매물을 봐도 외국인 취득 가능 여부를 알 수 없었다.
이 모듈은 각 스크래퍼가 js/*.js 를 쓰기 직전에 호출해서, 판정 결과를
매물 레코드 자체에 남긴다. 프론트(app.js)의 '한인 인수 가능' 필터가 이 필드를 쓴다.

붙는 필드
  summaryKo          : 한글 요약 줄 목록 (korean_brief)
  foreignStatus      : 가능 / 조건부 / 불가        (foreign_eligibility)
  foreignReason      : 판정 사유 한 줄
  foreignSteps       : 필요한 절차 목록
  operability        : 운영가능 / 확인필요 / 부적합 (operability)
  operabilityReasons : 운영 실체 근거 목록
  operabilityTodos   : 계약 전 확인 항목 목록
  koreanEligible     : 위 둘을 합친 최종 불리언 - 필터가 실제로 보는 값

⚠️ 법률 자문이 아니라 공개 데이터 기반 1차 스크리닝이다. 판정 근거는
   foreign_eligibility.py / operability.py 상단 주석에 있다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import foreign_eligibility as fe  # noqa: E402
import operability as op  # noqa: E402
from korean_brief import build_korean_brief  # noqa: E402


def annotate(item):
    """매물 1건에 판정 필드를 추가한다(입력 dict 를 그대로 수정하고 반환)."""
    # 순서 주의: operability 는 summaryKo 를 근거 텍스트에 포함해서 본다.
    # 요약을 먼저 붙여야 한글 매물의 매출·업력 신호가 판정에 반영된다.
    item["summaryKo"] = build_korean_brief(item)

    status, reason, steps = fe.classify(item)
    item["foreignStatus"] = status
    item["foreignReason"] = reason
    item["foreignSteps"] = steps

    op_status, _score, op_reasons, op_todos = op.classify(item)
    item["operability"] = op_status
    item["operabilityReasons"] = op_reasons
    item["operabilityTodos"] = op_todos

    # 최종 판정: 제도상 취득 불가하거나 운영 실체를 확인할 수 없으면 제외한다.
    # '조건부'와 '확인필요'는 남긴다 - 절차를 거치면 가능한 매물까지 버리면
    # 실제로 볼 만한 물건이 거의 남지 않는다. 대신 사유를 함께 노출한다.
    item["koreanEligible"] = (status != fe.BLOCKED and op_status != op.UNFIT)
    return item


def annotate_all(items):
    for item in items:
        annotate(item)
    return items


def summarize(items):
    """수집 로그에 찍을 판정 분포 한 줄씩."""
    lines = []
    for field, order in (("foreignStatus", (fe.ELIGIBLE, fe.CONDITIONAL, fe.BLOCKED)),
                         ("operability", (op.OPERABLE, op.UNCERTAIN, op.UNFIT))):
        counts = {}
        for item in items:
            counts[item.get(field)] = counts.get(item.get(field), 0) + 1
        lines.append(f"  {field}: " +
                     " / ".join(f"{k} {counts.get(k, 0)}건" for k in order))
    eligible = sum(1 for i in items if i.get("koreanEligible"))
    lines.append(f"  한인 인수 가능(koreanEligible): {eligible}건 / 전체 {len(items)}건")
    return lines
