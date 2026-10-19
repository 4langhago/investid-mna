# -*- coding: utf-8 -*-
"""발송 직전 최종 검증(confirm_listing)과 재발송 방지(sent_listing_ids)."""
import deal_tier as dt
import foreign_eligibility as fe
import telegram_recommend as tr

ITEM = {
    "id": "t-1",
    "title": "Yoga Studio for Sale in Jakarta",
    "source": "smergers.com",
    "sourceUrl": "https://example.com/biz/1",
}


def _stub(monkeypatch, fe_result, live=(True, "HTTP 200"), indoweb=("alive", "")):
    monkeypatch.setattr(fe, "classify", lambda item: fe_result)
    monkeypatch.setattr(tr, "url_is_live", lambda url: live)
    monkeypatch.setattr(tr, "check_indoweb_alive", lambda url: indoweb)


def test_share_deal_with_asset_verdict_is_unconfirmed(monkeypatch):
    _stub(monkeypatch, (fe.CONDITIONAL, "자산 양도 Rp 2.0십억 - PT PMA 설립 시 인수 가능", []))
    ok, why = tr.confirm_listing(dict(ITEM, _dealStructure=dt.SHARE_DEAL))
    assert not ok and "판정 불일치" in why


def test_consistent_share_deal_with_live_source_passes(monkeypatch):
    _stub(monkeypatch, (fe.ELIGIBLE, "기존 법인 지분 인수 - 신규 PT PMA 설립 없이 외국인 인수 가능한 구조", []))
    assert tr.confirm_listing(dict(ITEM, _dealStructure=dt.SHARE_DEAL)) == (True, "")


def test_dead_source_url_is_unconfirmed(monkeypatch):
    _stub(monkeypatch, (fe.CONDITIONAL, "자산 양도 Rp 2.0십억", []), live=(False, "HTTP 403"))
    ok, why = tr.confirm_listing(dict(ITEM, _dealStructure=dt.ASSET_DEAL))
    assert not ok and "HTTP 403" in why


def test_indoweb_unknown_liveness_is_unconfirmed(monkeypatch):
    # 접속 실패로 게시 여부를 모르면 확인된 것이 아니다.
    _stub(monkeypatch, (fe.CONDITIONAL, "자산 양도 Rp 2.0십억", []),
          indoweb=("unknown", "접속 실패(TimeoutError)"))
    item = dict(ITEM, source="indoweb.org", _dealStructure=dt.ASSET_DEAL)
    ok, why = tr.confirm_listing(item)
    assert not ok and "unknown" in why


def test_unclear_deal_structure_is_unconfirmed(monkeypatch):
    _stub(monkeypatch, (fe.ELIGIBLE, "기존 법인 지분 인수 - 신규 PT PMA 설립 없이 외국인 인수 가능한 구조", []))
    ok, why = tr.confirm_listing(dict(ITEM, _dealStructure=dt.UNKNOWN))
    assert not ok and "딜 구조 미확정" in why


def test_partial_send_failure_records_only_delivered(monkeypatch, tmp_path):
    # 두 번째 매물 메시지에서 전송이 실패하면 첫 매물만 '보냄'으로 남아야 한다.
    monkeypatch.setattr(tr, "STATE_FILE", tmp_path / "state.json")
    a, b = dict(ITEM, id="a", _msgIndex=1), dict(ITEM, id="b", _msgIndex=2)

    def fake_send(m):
        if m == "B":
            raise RuntimeError("boom")
    monkeypatch.setattr(tr, "send_telegram", fake_send)
    state = {}
    try:
        tr._send_and_record(["head", "A", "B"], [a, b], state, set())
    except RuntimeError:
        pass
    assert state["sentIds"] == ["a"]


def test_sent_ids_merge_legacy_state_keys():
    state = {"sentIds": ["a"], "lastSentIds": ["b"], "lastSentBusinessIds": ["c"],
             "lastSentPropertyIds": ["d"], "lastSentCommunityIds": ["e"],
             "seenCommunityIds": ["f"]}
    assert tr.sent_listing_ids(state) == {"a", "b", "c", "d", "e", "f"}
