# -*- coding: utf-8 -*-
"""deal_tier 계약 테스트.

여기서 고정하려는 것은 두 가지다.
  1) 금액 구간 경계 — 보고서가 5단계로 나뉘는 근거이므로 경계값이 흔들리면 안 된다.
  2) 딜 구조 판정의 '안전한 기본값' — 애매하면 자산양수(인허가 비승계)로 본다.
     인도네시아 공개 게시판의 take over 는 사실상 전부 자산 양수라, 이쪽으로
     잘못 보는 것이 반대 방향 오판보다 덜 위험하다.
"""
import pytest

import deal_tier as dt

# IDR 11.6 = ₩1. 경계값은 원화 기준으로 계산해서 넣는다.
IDR = dt.fe.KRW_TO_IDR


def at_krw(eok):
    """₩<eok>억에 해당하는 IDR 금액."""
    return int(eok * 1e8 * IDR)


@pytest.mark.parametrize("eok, expected", [
    (0.5, 1),    # ₩0.5억
    (1.0, 1),    # 1단계 상한 - 이하이므로 1단계
    (1.01, 2),
    (3.0, 2),    # 2단계 상한
    (3.01, 3),
    (5.0, 3),    # 3단계 상한
    (5.01, 4),
    (7.0, 4),    # 4단계 상한
    (7.01, 5),
    (10.0, 5),   # 5단계 상한
    (10.01, 9),  # 구간 밖
])
def test_tier_boundaries(eok, expected):
    assert dt.classify_tier({"priceNum": at_krw(eok)})[0] == expected


def test_price_missing_is_out_of_range():
    """인수가를 모르는 매물을 예산 안이라고 볼 수는 없다."""
    idx, label, note = dt.classify_tier({"priceNum": None})
    assert idx == dt.TIER_OUT[0]
    assert "미표기" in note


def test_tier_note_carries_pma_caveat():
    """1~3단계는 '금액은 맞지만 PMA 요건이 막는다'는 사실이 설명에 남아야 한다."""
    for eok in (0.5, 2.0, 4.0):
        note = dt.classify_tier({"priceNum": at_krw(eok)})[2]
        assert "Rp 100억" in note or "투자계획" in note


def test_asset_deal_is_the_default_for_c2c_takeover():
    structure, note = dt.classify_structure(
        {"description": "Take over usaha laundry lengkap dengan peralatan"})
    assert structure == dt.ASSET_DEAL
    assert "NIB" in note


def test_share_signal_detected():
    assert dt.classify_structure(
        {"description": "Dijual Usaha Multi Ekspedisi + PT Siap jalan"})[0] == dt.SHARE_DEAL
    assert dt.classify_structure(
        {"title": "Company Equity Stake For Sale", "description": "100% shares"}
    )[0] == dt.SHARE_DEAL


def test_collector_supplied_structure_wins():
    """수집기가 플랫폼 표기로 구조를 알아낸 경우 본문 추정보다 우선한다."""
    item = {"dealStructure": dt.MINORITY, "description": "100% shares tersedia"}
    assert dt.classify_structure(item)[0] == dt.MINORITY


def test_entity_mentioned_but_unclear_is_not_share_deal():
    """PT 가 언급됐다는 것만으로 주식 양수라고 단정하면 인허가 리스크를 놓친다."""
    structure, note = dt.classify_structure(
        {"description": "The business is a private limited entity with trade licenses"})
    assert structure == dt.UNKNOWN
    assert "NIB" in note


def test_annotate_writes_all_marks():
    item = {"priceNum": at_krw(2.0), "description": "take over usaha cafe"}
    marks = dt.annotate(item)
    assert marks["_tierIndex"] == 2
    assert item["_tierLabel"].startswith("2단계")
    assert item["_dealStructure"] == dt.ASSET_DEAL
    assert item["_dealStructureNote"]
