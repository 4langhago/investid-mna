# -*- coding: utf-8 -*-
"""--summary 출력 형식: 매물당 제목 + 링크 + 3줄."""
import telegram_recommend as tr

ITEM = {
    "id": "t-1",
    "title": "식당/식품 | 자카르타 한식당 양도",
    "category": "식당",
    "location": "Jakarta",
    "locationKo": "자카르타",
    "price": "Rp 1,5 M",
    "priceNum": 1_500_000_000,
    "sourceUrl": "https://example.com/post?wr_id=1",
    "description": "영업 중인 한식당 양도합니다. 직원 5명.",
    "_tierLabel": "1단계",
    "_dealStructure": "자산양수",
}


def test_summary_is_title_link_and_three_lines():
    lines = tr.build_summary(ITEM, 1, 2).split("\n")
    assert len(lines) == 5
    assert lines[0] == "[1/2] 자카르타 한식당 양도"  # 게시판 접두어 제거
    assert lines[1] == ITEM["sourceUrl"]
    assert all(x.startswith("- ") for x in lines[2:])
    assert "자카르타" in lines[2]
    assert "Rp 1,5 M" in lines[3] and "자산양수" in lines[3]
    assert "운영 가능성" in lines[4] and "외국인 취득" in lines[4]


def test_summary_without_price():
    item = dict(ITEM, price=None, priceNum=None)
    assert "가격 미표기" in tr.build_summary(item, 1, 1).split("\n")[3]


def test_report_says_so_when_nothing_passes():
    report = tr.build_summary_report([], 120)
    assert "기준을 넘는 매물 없음" in report and "120" in report


def test_report_lists_every_item():
    report = tr.build_summary_report([ITEM, dict(ITEM, id="t-2")], 120)
    assert "2건" in report and "[1/2]" in report and "[2/2]" in report
