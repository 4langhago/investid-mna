# -*- coding: utf-8 -*-
"""scrape_indoweb.py 의 순수 함수(파싱/판정) 계약 테스트. 네트워크 호출 없음."""
from datetime import datetime, timedelta, timezone

import pytest

import scrape_indoweb as si


# --- parse_price --------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "1 USD", "5 IDR", "999 IDR", "2,250,000 IDR",
])
def test_parse_price_placeholder_values_are_none(raw):
    # 게시판 필수 입력칸의 자리채움 값 - PLACEHOLDER_PRICE_MAX(1000만) 미만은 미표기로 간주
    shown, num = si.parse_price(raw)
    assert num is None


@pytest.mark.parametrize("raw", [
    "월간 - 2,600 USD", "월간 - 210,000,000 IDR",
])
def test_parse_price_rent_is_none(raw):
    shown, num = si.parse_price(raw)
    assert num is None
    assert "임대료" in shown


def test_parse_price_usd_converts_to_idr():
    shown, num = si.parse_price("28,000 USD")
    assert num == 28_000 * si.USD_TO_IDR
    assert num == 448_000_000


def test_parse_price_idr_plain_number():
    shown, num = si.parse_price("400,000,000 IDR")
    assert num == 400_000_000


def test_parse_price_idr_billions():
    shown, num = si.parse_price("12,000,000,000 IDR")
    assert num == 12_000_000_000


def test_parse_price_juta_unit():
    # 'jt'/juta = 백만. \bjt\b 는 숫자 바로 뒤(공백 없이 붙는 "150jt")에는 단어 경계가 없어
    # 매치되지 않는다 - 지원되는 표기는 공백을 둔 "150 jt" 뿐이다(버그 후보, 코드 수정은 안 함).
    shown, num = si.parse_price("150 jt")
    assert num == 150_000_000


def test_parse_price_juta_unit_no_space():
    # 공백 없는 '150jt' 도 백만 단위로 읽어야 한다. 예전엔 150 만 잡혀 자리채움 값으로 버려졌다.
    shown, num = si.parse_price("150jt")
    assert num == 150_000_000


def test_parse_price_korean_eok_unit():
    # '억' = 루피아 1억(1e8), 원화 단위가 아님
    shown, num = si.parse_price("15억")
    assert num == 1_500_000_000


def test_parse_price_miliar_unit_integer():
    shown, num = si.parse_price("4 miliar")
    assert num == 4_000_000_000


@pytest.mark.parametrize("raw, expected", [
    ("3,5 miliar", 3_500_000_000),   # 인도네시아식 소수 콤마 - 예전엔 35십억(10배)으로 읽었다
    ("3.5 M", 3_500_000_000),
    ("2,75 M", 2_750_000_000),
    ("1.500 jt", 1_500_000_000),     # 천단위 구분자
    ("4,5억", 450_000_000),
])
def test_parse_price_scaled_units_with_separators(raw, expected):
    shown, num = si.parse_price(raw)
    assert num == expected


def test_parse_price_none_input():
    shown, num = si.parse_price(None)
    assert shown is None and num is None


def test_parse_price_empty_string():
    shown, num = si.parse_price("")
    assert shown is None and num is None


# --- is_business_deal ----------------------------------------------------

def test_is_business_deal_rejects_rental_category():
    ok, reason = si.is_business_deal("아무 제목", cate="임대")
    assert ok is False
    ok, reason = si.is_business_deal("아무 제목", cate="재임대")
    assert ok is False


@pytest.mark.parametrize("title", [
    "토지 및 건물",
    "포차 매각",
])
def test_is_business_deal_accepts_maemae_category_regardless_of_title_keywords(title):
    ok, reason = si.is_business_deal(title, cate="매매")
    assert ok is True


def test_is_business_deal_maemae_category_still_rejects_recruiting_titles():
    ok, reason = si.is_business_deal("직원 구인합니다 매매", cate="매매")
    assert ok is False


def test_is_business_deal_no_category_falls_back_to_keyword_rules():
    # cate=None: 거래 표현 + 사업체 표현이 둘 다 있어야 채택
    ok, reason = si.is_business_deal("카페 매각합니다", cate=None)
    assert ok is True
    ok, reason = si.is_business_deal("카페 안내드립니다", cate=None)
    assert ok is False  # 거래 표현 없음
    ok, reason = si.is_business_deal("토지 매각합니다 임대 놓습니다", cate=None)
    assert ok is False  # NOISE_RE(임대) 매치


# --- to_model routing -----------------------------------------------------

def _row(title, cate=None, board="real_estate_mb", board_ko="부동산·업체 매매(주력)",
         wr_id="1", days_ago=1):
    date = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    return {
        "wr_id": wr_id, "title": title, "cate": cate, "date": date,
        "url": f"?bo_table={board}&wr_id={wr_id}", "board": board, "boardKo": board_ko,
    }


@pytest.mark.parametrize("title", [
    "Majalengka 공장 매매",
    "토지 및 건물",
    "NAVAPARK BSD",
])
def test_to_model_routes_to_properti(title):
    item, reason = si.to_model(_row(title, cate="매매"))
    assert item is not None, reason
    assert item["type"] == "properti"
    assert item["subtype"] == "jual"


@pytest.mark.parametrize("title", [
    "법인 매각 (찌까랑 자바베카 1공단)",
    "금형(몰드)제조공장 매각",
    "포차 매각",
])
def test_to_model_routes_to_bisnis(title):
    item, reason = si.to_model(_row(title, cate="매매"))
    assert item is not None, reason
    assert item["type"] == "bisnis"
    assert item["subtype"] == "akuisisi"


def test_to_model_market_board_cafe_sale_routes_to_bisnis():
    item, reason = si.to_model(_row("카페 양도합니다", cate=None, board="market", board_ko="벼룩시장"))
    assert item is not None, reason
    assert item["type"] == "bisnis"


def test_to_model_rejects_posts_older_than_365_days():
    item, reason = si.to_model(_row("법인 매각", cate="매매", days_ago=400))
    assert item is None
    assert "365일" in reason


def test_to_model_accepts_recent_post():
    item, reason = si.to_model(_row("법인 매각", cate="매매", days_ago=10))
    assert item is not None, reason


# --- parse_rows -------------------------------------------------------------

def test_parse_rows_extracts_cate_title_wr_id():
    # 실제 목록 마크업에서 발췌: td_subject 안에 bo_cate_link(매매) 앵커 뒤에 wr_id 앵커, 그 다음 td_date.
    html = """
    <tr>
      <td class="td_num">1</td>
      <td class="td_subject">
        <a href="#" class="bo_cate_link">매매</a> |
        <a href="./board.php?bo_table=real_estate_mb&wr_id=10386&page=1">카페 양도합니다</a>
      </td>
      <td class="td_date">26-09-07</td>
    </tr>
    """
    items = si.parse_rows(html, "real_estate_mb", "부동산·업체 매매(주력)")
    assert len(items) == 1
    row = items[0]
    assert row["cate"] == "매매"
    assert row["title"] == "카페 양도합니다"
    assert row["wr_id"] == "10386"
    assert row["date"] == "26-09-07"
    assert row["board"] == "real_estate_mb"
