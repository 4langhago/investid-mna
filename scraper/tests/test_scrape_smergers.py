# -*- coding: utf-8 -*-
"""scrape_smergers 파서 계약 테스트.

실제 카드 HTML에서 확인한 함정을 그대로 고정한다.
  • 지표 값 칸에 <span class="currency-symbol">IDR</span> 태그가 섞여 있다
  • 같은 값을 카드에서는 "67214 million", 툴팁에서는 "IDR 13500 Mn" 으로 쓴다
  • 지분 매각은 금액 뒤에 "for 30%" 가 붙는다 — 이걸 금액으로 읽으면 가격이 조용히 틀린다
  • 목록에 매물이 아닌 유도 카드(/create-business-profile/)가 섞여 있다
  • 링크에 추적 쿼리(?source=list-business-card)가 붙어 id 가 깨진 적이 있다
"""
import deal_tier as dt
import scrape_smergers as sm


def card(url="/business/food-processing-company-for-sale-in-jakarta-indonesia/dt8t0/",
         title="Food Processing Company for Sale in Jakarta, Indonesia",
         location="Jakarta, Special Capital Region of Jakarta, Indonesia",
         deal_label="Business for Sale",
         price='IDR 500 Mn',
         revenue='<span class="currency-symbol">IDR</span> 6000 million',
         ebitda="0-10 %",
         activity="Active"):
    """실제 카드 구조를 줄인 픽스처. 파서가 보는 표식만 남긴다."""
    return f'''
    col-lg-4 col-md-6"  data-url="{url}"  target="_blank" rel="noopener" title="{title}">
    <h2 class="fs-2 sme-v3-single-line">
        <a href="{url}" target="_blank" rel="noopener" title="{title}">
        <i data-toggle="tooltip" data-title=" {activity}" class="icon-circle lastseen-active"></i>
        {title}</a>
    </h2>
    <li data-toggle="tooltip" data-title="Email Verified">Email</li>
    <li data-toggle="tooltip" data-title="Phone Unverified">Phone</li>
    <div class="fs-4" itemprop="name">Profitable central kitchen with food safety license.</div>
    <div class="description" itemprop="description">The business has been fully operational
    for 10-20 years with 10-50 employees and recurring clients.</div>
    <link itemprop="ratingCount" content="4">
    <link itemprop="ratingValue" content="8.6">
    <span class="icon-map-marker location" data-toggle="tooltip" data-title="{location}">Jakarta</span>
    <div class="bg-grey-100">
        <div class="col-xs-6 px-2 py-2 fs-2 sme-v3-single-line">Run Rate Sales</div>
        <div class="col-xs-6 px-2 py-2 text-right sme-v3-single-line fs-2"
                >{revenue}</div>
        <div class="col-xs-6 px-2 py-2 fs-2 sme-v3-single-line">EBITDA Margin</div>
        <div class="col-xs-6 px-2 py-2 text-right sme-v3-single-line fs-2"
                >{ebitda}</div>
    </div>
    <div class="sme-v3-smalltext" style="color:#999">{deal_label}</div>
    <div class="sme-v3-bolder"  data-toggle="tooltip" data-title="{price}"
         data-trigger="hover">IDR</div>
    '''


def test_full_sale_card_parses():
    item, why = sm.parse_card(card())
    assert item is not None, why
    assert item["id"] == "smg-dt8t0"
    assert item["priceNum"] == 500_000_000
    assert item["annualRevenueNum"] == 6_000_000_000
    assert item["ebitdaMargin"] == "0-10 %"
    assert item["dealStructure"] == dt.SHARE_DEAL
    assert item["locationKo"] == "자카르타"
    assert item["category"] == "식품 제조"
    assert item["listingActivity"] == "Active"
    assert item["platformVerified"] == ["Email"]   # Unverified 는 세지 않는다
    assert item["platformRating"] == 8.6
    assert item["postedAt"] is None                # 게시일을 지어내지 않는다


def test_currency_tag_inside_metric_is_stripped():
    """지표 칸의 <span>IDR</span> 때문에 매출을 못 읽어 전 매물이 탈락한 적이 있다."""
    item, why = sm.parse_card(card(revenue='<span class="currency-symbol">IDR</span> 156 Mn'))
    assert item is not None, why
    assert item["annualRevenueNum"] == 156_000_000


def test_stake_percentage_is_not_read_as_price():
    item, _ = sm.parse_card(card(deal_label="Stake Sale", price="IDR 13500 Mn for 30%"))
    assert item["priceNum"] == 13_500_000_000
    assert item["stakePercent"] == 30.0
    assert item["dealStructure"] == dt.MINORITY


def test_majority_stake_keeps_control():
    item, _ = sm.parse_card(card(deal_label="Stake Sale", price="IDR 9000 Mn for 51%"))
    assert item["dealStructure"] == dt.SHARE_DEAL


def test_tracking_query_does_not_break_id():
    item, _ = sm.parse_card(card(url="/business/advertising-agency-for-sale/fjfh1/"
                                     "?source=list-business-card"))
    assert item["id"] == "smg-fjfh1"
    assert "?" not in item["sourceUrl"]


def test_non_listing_cards_are_dropped():
    assert sm.parse_card(card(url="/create-business-profile/"))[0] is None
    assert sm.parse_card(card(url="/franchise/savage-gears/dij3o/"))[0] is None


def test_loan_and_dead_listings_are_dropped():
    assert sm.parse_card(card(deal_label="Business Seeking Loan"))[0] is None
    item, why = sm.parse_card(card(revenue="Nil"))
    assert item is None and "영업 중" in why


def test_outside_greater_jakarta_is_dropped():
    item, why = sm.parse_card(card(location="Medan, North Sumatra, Indonesia"))
    assert item is None and "수도권 밖" in why


def test_banten_is_kept_but_marked_uncertain():
    """반뜬주에는 땅그랑(수도권)과 세랑(수도권 밖)이 섞여 있다 - 버리지 말고 드러낸다."""
    item, why = sm.parse_card(card(location="Banten, Indonesia"))
    assert item is not None, why
    assert item["locationKo"] == "반뜬(도시 미표기)"
    assert item["regionPriority"] == 3


def test_parse_amount_rejects_bare_number():
    """통화도 단위도 없는 숫자는 금액이 아니다('for 30%' 의 30 을 가격으로 읽으면 안 된다)."""
    assert sm.parse_amount("30") is None
    assert sm.parse_amount("IDR 500 Mn") == 500_000_000
    assert sm.parse_amount("USD 600 K") == 600_000 * sm.USD_TO_IDR
