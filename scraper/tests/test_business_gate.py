# -*- coding: utf-8 -*-
"""business_gate 계약 테스트. '인수해서 운영할 수 있는 매물만 통과'가 지켜지는지 본다.

이 관문의 목적은 건수를 채우는 게 아니라 못 미치는 매물을 떨어뜨리는 것이라,
'통과해야 할 것'보다 '떨어져야 할 것'을 더 촘촘히 고정한다.
"""
from datetime import datetime, timedelta, timezone

import pytest

import business_gate as bg


def _recent(days=5):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def make(**kw):
    """영업 실체·규모·신선도를 모두 갖춘 기본 매물(요식업). 필요한 필드만 덮어쓴다."""
    item = {
        "type": "bisnis",
        "subtype": "akuisisi",
        "title": "자카르타 한식당 양도",
        "description": "현재 영업 중이며 월 매출 3억 루피아, 직원 8명 인계. 3년째 운영 중. NIB 보유.",
        "priceNum": 5_000_000_000,
        "postedAt": _recent(),
        "source": "indoweb.org",
        # 연락 경로가 없으면 operability 가 '부적합'으로 떨어뜨린다(인수 협상을 시작할 수 없어서).
        "sourceUrl": "https://indoweb.org/love/bbs/board.php?bo_table=biz_promo&wr_id=1",
    }
    item.update(kw)
    return item


def test_operating_restaurant_passes():
    ok, why, reasons = bg.assess(make())
    assert ok, why
    assert any("요식업" in r for r in reasons)


@pytest.mark.parametrize("title, expected", [
    ("자카르타 세탁소 양도", "세탁업"),          # KBLI 96200 UMKM 유보
    ("Take Over Salon Kecantikan Jakarta", "미용실"),
    ("Usaha aktif Alfamart di Sragen", "소형 소매"),
    ("Take Over Warteg 24 Jam", "노점"),
    ("TAKE OVER SEKOLAH AKTIF SMP SMK", "정규학교"),
])
def test_blocked_sectors_rejected(title, expected):
    ok, why, _ = bg.assess(make(title=title))
    assert not ok
    assert expected in why


def test_capped_sector_rejected():
    # 지분 49% 상한이면 경영권을 못 가지므로 추천 대상이 아니다.
    ok, why, _ = bg.assess(make(title="Take Over Usaha Ekspedisi Jakarta"))
    assert not ok
    assert "지분 상한" in why


def test_unknown_sector_rejected():
    # 업종을 특정 못 하면 외국인 개방 여부를 말할 수 없다 - 모르면 추천하지 않는다.
    ok, why, _ = bg.assess(make(title="사업체 양도합니다", description="자세한 내용은 연락 주세요"))
    assert not ok
    assert "업종" in why


def test_property_listing_rejected():
    ok, why, _ = bg.assess(make(type="properti", subtype="jual", title="Dijual Tanah 2000m2"))
    assert not ok
    assert "부동산" in why


def test_ruko_with_cafe_is_not_a_business_acquisition():
    # 루코 매매 글에 카페가 딸린 형태는 '부동산 매매'다. 사업체 인수로 보지 않는다.
    ok, why, _ = bg.assess(make(title="Dijual Ruko 3 Lantai, ada Cafe"))
    assert not ok


def test_blocked_by_foreign_eligibility():
    # 자산 양도가 ₩1억(ASSET_DEAL_MIN)에도 못 미치면 취득 구조가 성립하지 않는다.
    ok, why, _ = bg.assess(make(priceNum=400_000_000))
    assert not ok
    assert "외국인 취득 불가" in why


def test_weak_operating_evidence_rejected():
    # '영업 중'만 있고 매출·업력·직원이 없는 글은 확인필요 - 추천하지 않는다.
    ok, why, _ = bg.assess(make(description="Usaha masih berjalan, lokasi strategis"))
    assert not ok
    assert "영업 실체" in why


def test_scale_unknown_rejected():
    ok, why, _ = bg.assess(make(priceNum=None, monthlyRevenueNum=None,
                                description="현재 영업 중입니다. 직원 5명. 3년째 운영 중."))
    assert not ok
    assert "규모" in why


def test_revenue_in_text_counts_as_scale():
    # 한인 글은 '연매출 20억'처럼 문장으로만 쓴다. 숫자 필드가 비어도 규모가 제시된 것으로 본다.
    # 지역은 이 테스트의 관심사가 아니므로 대상 지역(자카르타) 매물로 둔다.
    ok, why, _ = bg.assess(make(priceNum=None, monthlyRevenueNum=None,
                                title="자카르타 디저트 카페 브랜드 매각",
                                description="현재 영업 중이며 연매출 20억 이상. 9개 매장 운영. 사업자 등록 완료."))
    assert ok, why


def test_stale_post_rejected():
    ok, why, _ = bg.assess(make(postedAt=_recent(days=bg.MAX_POST_AGE_DAYS + 1)))
    assert not ok
    assert "게시" in why


def test_screen_counts_rejections():
    items = [make(), make(title="세탁소 양도"), make(title="세탁소 인수")]
    passed, rejected = bg.screen(items)
    assert len(passed) == 1
    assert sum(rejected.values()) == 2


@pytest.mark.parametrize("price, tier", [
    (1_160_000_000, bg.TIER_BUDGET),   # ≈₩1.0억 - 하한
    (2_000_000_000, bg.TIER_BUDGET),   # ≈₩1.7억
    (2_320_000_000, bg.TIER_BUDGET),   # ≈₩2.0억 - 상한
    (5_000_000_000, bg.TIER_OVER),     # ≈₩4.3억 - 초과
    (None, bg.TIER_OVER),              # 미표기는 예산 안이라고 볼 수 없다
])
def test_budget_tier(price, tier):
    assert bg.budget_tier({"priceNum": price})[0] == tier


def test_screen_marks_tier_on_passed_items():
    passed, _ = bg.screen([make(priceNum=1_500_000_000), make(priceNum=5_000_000_000)])
    assert [x["_tier"] for x in passed] == [bg.TIER_BUDGET, bg.TIER_OVER]


# --- 지역·금액 범위 관문 (2026-09-19 추가) ---------------------------------
# 수집기는 전국을 긁어온다. 실제로 반둥 디저트 카페와 잠비 탄광(자카르타 본사 표기)이
# 추천 메시지에 올라왔다. 대상은 자카르타·브까시·찌카랑·보고르·땅그랑뿐이다.

@pytest.mark.parametrize("location", [
    "Jakarta Selatan", "Bekasi Kota", "Cikarang", "Bogor Kab.",
    "Tangerang Selatan", "Depok Kota", "Banten",
])
def test_target_regions_pass(location):
    ok, why, _ = bg.assess(make(location=location, title="한식당 양도"))
    assert ok, why


@pytest.mark.parametrize("location", ["Bandung Kota", "Jambi", "Surabaya Kota", "Bali"])
def test_outside_target_region_rejected(location):
    ok, why, _ = bg.assess(make(location=location, title="한식당 양도"))
    assert not ok
    assert "대상 지역 밖" in why


def test_region_unknown_is_rejected():
    """모르면 추천하지 않는다 - 업종 판정과 같은 원칙."""
    ok, why, _ = bg.assess(make(title="한식당 양도", description="현재 영업 중이며 월 매출 3억 루피아, 직원 8명 인계. 3년째 운영."))
    assert not ok
    assert "대상 지역 밖" in why


def test_over_budget_cap_rejected():
    """₩10억 초과는 사용자가 검토하는 구간 밖이다(잠비 탄광 Rp 1,050억 등)."""
    ok, why, _ = bg.assess(make(location="Jakarta", priceNum=105_000_000_000))
    assert not ok
    assert "상한" in why


def test_price_missing_still_passes():
    """가격 협의 매물을 버릴 이유는 없다 - 메시지에 '가격 미표기'로 표시된다."""
    ok, why, _ = bg.assess(make(location="Jakarta", priceNum=None))
    assert ok, why
