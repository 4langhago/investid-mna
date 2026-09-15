# -*- coding: utf-8 -*-
"""operability.classify() 계약 테스트: '실제로 인수해 운영할 수 있는 매물인가'."""
from datetime import datetime, timedelta, timezone

import operability as op


def item(**kwargs):
    base = {
        "title": "", "description": "", "category": None, "badge": None,
        "summaryKo": None, "dealType": None, "facilities": [], "koreanFacts": [],
        "type": None, "subtype": None, "priceNum": None, "area": None,
        "lat": None, "lng": None, "address": None, "floors": None,
        "whatsapp": None, "sourceUrl": "https://example.com/x", "postedAt": None,
    }
    base.update(kwargs)
    return base


def test_factory_sale_with_cleanroom_signal_is_not_unfit_for_lack_of_substance():
    # '클린룸' 언급은 매출/직원 대신 제조 영업 기반을 드러내는 신호(POSITIVE 목록에 명시)
    it = item(title="법인 매각", type="bisnis", subtype="akuisisi",
              description="24년8월에 클린룸과 인테리아 공사가 완공되고 현재까지 매우 깨끗하게 "
                          "관리가 잘 되어 있습니다. 법인 설립 : 2023년 10월",
              priceNum=None)
    status, score, reasons, todos = op.classify(it)
    assert status != op.UNFIT


def test_factory_sale_with_existing_clients_signal_is_not_unfit_for_lack_of_substance():
    it = item(title="공장 매각", type="bisnis", subtype="akuisisi",
              description="기존의 거래처 포함 양도, 현재 가동 중")
    status, score, reasons, todos = op.classify(it)
    assert status != op.UNFIT


def test_rental_listing_is_unfit():
    it = item(title="상가 임대 안내", type="bisnis", subtype="akuisisi",
              description="disewakan, hubungi untuk info lebih lanjut")
    status, score, reasons, todos = op.classify(it)
    assert status == op.UNFIT
    assert score == -99


def test_post_older_than_365_days_is_unfit():
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    it = item(title="카페 양도", type="bisnis", subtype="akuisisi",
              description="현재 영업 중, 매출 발생 중", postedAt=old)
    status, score, reasons, todos = op.classify(it)
    assert status == op.UNFIT
