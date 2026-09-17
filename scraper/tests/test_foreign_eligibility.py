# -*- coding: utf-8 -*-
"""foreign_eligibility.classify() 계약 테스트.

실데이터(js/community_data.js, js/olx_data.js)에서 가져온 제목/설명을 기반으로 한
픽스처를 쓴다. 판정 로직이 바뀌면 이 테스트가 먼저 깨져야 한다.
"""
import pytest

import foreign_eligibility as fe


def item(**kwargs):
    """classify() 가 읽는 필드를 기본값과 함께 채운 dict."""
    base = {
        "title": "",
        "description": "",
        "category": None,
        "type": None,
        "subtype": None,
        "badge": None,
        "priceNum": None,
        "facilities": [],
        "propertyIncluded": False,
        "location": None,
        "locationKo": None,
        "address": None,
    }
    base.update(kwargs)
    return base


# --- 지분(share deal) 인수 -------------------------------------------------

def test_share_deal_without_price_is_eligible():
    # js/community_data.js 실제 매물: 가격 미표기 법인 매각
    it = item(title="법인 매각 (찌까랑 자바베카 1공단)", type="bisnis", subtype="akuisisi",
              description="법인을 매각하려고 합니다. 클린룸과 공사가 완공되고 현재까지 관리가 잘 되어 있습니다.")
    status, reason, steps = fe.classify(it)
    assert status == fe.ELIGIBLE
    assert "지분" in reason or "법인" in reason


def test_asset_only_disclaimer_blocks_share_deal_path():
    # 같은 게시물이라도 '법인 자체를 매각하는 거래가 아닙니다 ... 사업자산 양도 방식'이라고
    # 명시하면 지분 인수 경로를 타면 안 된다(가격 미표기 → 조건부로 빠져야 함).
    it = item(title="자카르타·탕그랑 운영 사업체 2건 양도", type="bisnis", subtype="akuisisi",
              description="이번 양도는 회사 지분이나 기존 법인 자체를 매각하는 거래가 아닙니다. "
                          "각 사업장의 임대권, 시설, 장비, 운영 시스템 및 합의된 영업자산을 인수하는 "
                          "사업자산 양도 방식 입니다.")
    status, reason, steps = fe.classify(it)
    assert status != fe.ELIGIBLE
    assert "지분 인수" not in reason


# --- 사업체 인수(asset deal), type bisnis -----------------------------------

def test_asset_deal_no_price_is_conditional():
    it = item(title="카페 양도", type="bisnis", subtype="akuisisi", priceNum=None,
              description="카페 양도합니다. 현재 운영 중입니다.")
    status, reason, steps = fe.classify(it)
    assert status == fe.CONDITIONAL


def test_asset_deal_below_realistic_min_is_blocked():
    # Rp 50jt < BUSINESS_REALISTIC_MIN(1억) - 인수 구조 자체가 성립하지 않음
    it = item(title="세탁소 양도", type="bisnis", subtype="akuisisi", priceNum=50_000_000)
    status, reason, steps = fe.classify(it)
    assert status == fe.BLOCKED


def test_asset_deal_cafe_448m_is_blocked():
    # 실사례: 448,000,000 (28,000 USD 환산 ≈ ₩3,900만) 카페 - ASSET_DEAL_MIN(≈₩1억) 미달.
    # 이 규모에 납입자본 Rp 25억·투자계획 Rp 100억을 얹는 구조는 성립하지 않는다.
    it = item(title="카페 양도 (즉시 운영 가능)", type="bisnis", subtype="akuisisi",
              priceNum=448_000_000)
    status, reason, steps = fe.classify(it)
    assert status == fe.BLOCKED
    assert "납입자본" in reason


def test_asset_deal_1_5b_is_conditional_with_cash_requirement():
    # ₩1억~2.2억 구간(2026-09-17 완화): 납입자본을 입금한 뒤 그 안에서 인수하는 구조가
    # 성립하므로 '조건부'. 다만 총 현금 ₩2.2억과 투자계획 요건이 안내에 반드시 붙어야 한다.
    it = item(title="식당 양도", type="bisnis", subtype="akuisisi", priceNum=1_500_000_000)
    status, reason, steps = fe.classify(it)
    assert status == fe.CONDITIONAL
    assert "총 현금" in reason
    assert any("투자계획" in s for s in steps)


def test_asset_deal_just_below_krw_100m_is_blocked():
    it = item(title="식당 양도", type="bisnis", subtype="akuisisi",
              priceNum=fe.ASSET_DEAL_MIN - 1)
    status, reason, steps = fe.classify(it)
    assert status == fe.BLOCKED


def test_asset_deal_2_4b_is_conditional():
    # PT_PMA_MIN_PAID_UP(25억) 바로 아래 - 예전엔 불가였으나 ₩1억 이상은 조건부로 연다
    it = item(title="식당 양도", type="bisnis", subtype="akuisisi", priceNum=2_400_000_000)
    status, reason, steps = fe.classify(it)
    assert status == fe.CONDITIONAL


def test_asset_deal_4_5b_is_conditional():
    it = item(title="식당 양도", type="bisnis", subtype="akuisisi", priceNum=4_500_000_000)
    status, reason, steps = fe.classify(it)
    assert status == fe.CONDITIONAL


def test_asset_deal_16b_is_conditional():
    it = item(title="공장 매각", type="bisnis", subtype="akuisisi", priceNum=16_000_000_000)
    status, reason, steps = fe.classify(it)
    assert status == fe.CONDITIONAL


# --- 외국인 투자 유보 업종(블록) --------------------------------------------

@pytest.mark.parametrize("title", [
    "Take Over Warteg 24 Jam di Graha Raya Tangerang Lokasi Ramai",
    "Take over usaha laundry tinggal jalanin",
    "Usaha aktif Alfamart, Perijinan Aktif di Sragen Investasi menguntungkan",
    "JUAL CEPAT BISNIS SEKOLAH AKTIF 3LT SMA SMK SMP PARUNG KEMANG BOGOR",
])
def test_blocked_business_sectors(title):
    it = item(title=title, type="bisnis", subtype="akuisisi", priceNum=97_000_000)
    status, reason, steps = fe.classify(it)
    assert status == fe.BLOCKED


def test_landmark_false_positive_laundry_not_blocked():
    # 'depan minimarket besar' 는 세탁업 옆의 편의점 언급이지, 편의점 업종이 아니다.
    # 실데이터(js/olx_data.js): laundry take-over, "depan minimarket besar" 문구 포함.
    it = item(
        title="TAKE OVER USAHA LAUNDRY BEJI DEPOK",
        type="bisnis", subtype="akuisisi", priceNum=97_000_000,
        description="Take over usaha laundry di Jl. Nusantara Raya, Beji, Depok, "
                    "lokasi strategis di kawasan padat penduduk dan tepat di depan minimarket besar. "
                    "Ruko 2 lantai Peralatan laundry lengkap.")
    status, reason, _ = fe.classify(it)
    # laundry 자체는 blocked 업종이므로 결국 불가지만, 사유가 '생필품 소매업'(minimarket)이면 안 된다.
    assert "세탁업" in reason
    assert "소매업" not in reason


def test_landmark_false_positive_school_not_transport():
    it = item(
        title="세탁소 near Dekat Pangkalan Angkutan Umum",  # 세탁업+운송 근처 표현 동시 존재 상황 재현
        type="bisnis", subtype="akuisisi", priceNum=97_000_000,
        description="세탁소 매물, Dekat Pangkalan Angkutan Umum 위치")
    status, reason, _ = fe.classify(it)
    # 육상 여객운송 업종으로 오판되면 안 된다(근처 표현 필터)
    assert "여객운송" not in reason


def test_school_landmark_not_flagged_as_transport_when_no_school_itself():
    # 학교 매물 자체가 아니라 근처에 '버스 정류장'만 있는 경우, 여객운송 업종 자체가 아니므로 통과해야 함.
    it = item(
        title="루코 매매 Dekat Pangkalan Angkutan Umum",
        type="bisnis", subtype="akuisisi", priceNum=3_000_000_000,
        description="위치가 Dekat Pangkalan Angkutan Umum 이라 접근성이 좋습니다.")
    status, reason, _ = fe.classify(it)
    assert status != fe.BLOCKED


def test_building_heavy_blocked_sector_over_min_is_conditional_building_only():
    # 'Gedung+usaha laundry aktif ... 3,5milyard' - 건물만 취득 검토 가능 경로
    it = item(title="Gedung 4 lantai + usaha laundry aktif dijual cepat 3,5milyard",
              type="bisnis", subtype="akuisisi", priceNum=3_500_000_000)
    status, reason, steps = fe.classify(it)
    assert status == fe.CONDITIONAL
    assert reason.startswith("건물만 취득 검토 가능")


# --- 부동산(properti/ruko) ---------------------------------------------------

def test_property_hgb_is_eligible():
    it = item(title="Ruko dijual", type="properti", facilities=["증서: HGB"])
    status, reason, _ = fe.classify(it)
    assert status == fe.ELIGIBLE


def test_property_shm_is_conditional():
    it = item(title="Ruko dijual", type="properti", facilities=["증서: SHM"])
    status, reason, _ = fe.classify(it)
    assert status == fe.CONDITIONAL


def test_property_girik_only_is_blocked():
    it = item(title="Tanah kavling dijual", type="properti", facilities=["증서: Girik"])
    status, reason, _ = fe.classify(it)
    assert status == fe.BLOCKED


def test_property_no_certificate_is_conditional():
    it = item(title="Ruko dijual", type="properti")
    status, reason, _ = fe.classify(it)
    assert status == fe.CONDITIONAL


# --- 아파트: 지역별 최소가 --------------------------------------------------

def test_apartment_jakarta_below_min_is_conditional():
    it = item(title="Apartemen dijual Jakarta", location="Jakarta", priceNum=2_500_000_000)
    status, reason, _ = fe.classify(it)
    assert status == fe.CONDITIONAL


def test_apartment_jakarta_above_min_is_eligible():
    it = item(title="Apartemen dijual Jakarta", location="Jakarta", priceNum=3_500_000_000)
    status, reason, _ = fe.classify(it)
    assert status == fe.ELIGIBLE


def test_apartment_banten_below_jakarta_min_but_above_banten_min_is_eligible():
    # Tangerang/BSD(Banten) 아파트 최소 2,000,000,000 - Jakarta 최소(3B)보다 낮음
    it = item(title="Apartemen BSD dijual", location="Tangerang BSD", priceNum=2_500_000_000)
    status, reason, _ = fe.classify(it)
    assert status == fe.ELIGIBLE


def test_apartment_cikarang_jawa_barat_is_conditional():
    it = item(title="Apartemen Cikarang dijual", location="Cikarang", priceNum=1_200_000_000)
    status, reason, _ = fe.classify(it)
    assert status == fe.CONDITIONAL


def test_apartment_price_none_is_conditional():
    it = item(title="Apartemen dijual Jakarta", location="Jakarta", priceNum=None)
    status, reason, _ = fe.classify(it)
    assert status == fe.CONDITIONAL


def test_apartment_unknown_region_uses_jakarta_minimum():
    it = item(title="Apartemen dijual", location=None, priceNum=2_500_000_000)
    status, reason, _ = fe.classify(it)
    # Jakarta 최소(3B) 적용 - 2.5B 는 미달이므로 조건부
    assert status == fe.CONDITIONAL
    assert "지역 미상" in reason or "자카르타" in reason


# --- SOHO/오피스 ------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "Boutique Soho at Latinos Business District, BSD City",
    "사무실 매각 (양도)",
])
def test_soho_office_never_eligible_for_individual(title):
    it = item(title=title, priceNum=3_000_000_000)
    status, reason, _ = fe.classify(it)
    assert status == fe.CONDITIONAL
    assert status != fe.ELIGIBLE
    assert "PT PMA" in reason


# --- 단독주택 ----------------------------------------------------------------

def test_landed_house_below_min_is_conditional():
    # 실사례: 주택 asana resident cibubur, Rp 1.3B - 단독주택 최소가(자카르타 5B) 미달
    it = item(title="주택 asana resident cibubur", location=None,
              address="asana resident cibubur blok F-51, cikeas",
              priceNum=1_300_000_000)
    status, reason, _ = fe.classify(it)
    assert status == fe.CONDITIONAL


def test_detect_province_prefers_address_over_description_mentions():
    # 설명에 '자카르타에서 1시간' 같은 다른 지역 언급이 섞여 있어도 address/location 필드를 우선한다.
    it = item(title="주택 매매", address="cibubur, gunung putri",
              description="자카르타에서 1시간 거리, jakarta 접근성 좋음",
              priceNum=1_300_000_000)
    province = fe.detect_province(it)
    assert province == "DKI Jakarta"  # cibubur 는 Jakarta 키워드에 포함됨


# --- rank_key ----------------------------------------------------------------

def test_rank_key_orders_eligible_before_conditional_before_blocked():
    assert fe.rank_key(fe.ELIGIBLE) < fe.rank_key(fe.CONDITIONAL) < fe.rank_key(fe.BLOCKED)
