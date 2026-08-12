# -*- coding: utf-8 -*-
"""매물의 시간 변화를 기록한다 — 최초 발견일, 가격 변동, 사라진 매물.

왜 필요한가:
  지금까지 수집 결과는 매번 통째로 교체돼서 '이 매물이 얼마나 오래 안 팔리고 있는지',
  '가격을 내렸는지'를 알 수 없었다. 그런데 인수 협상에서 실제로 쓰이는 카드가 바로 그것이다.
    - 3개월째 남아 있는 매물 = 매도인이 급하다 = 협상 여지
    - 가격을 두 번 내린 매물 = 시장이 그 값을 인정하지 않았다
    - 어제까지 있다가 사라진 매물 = 팔렸거나 내렸다

저장 형식(output/listing_history.json):
  { "<매물id>": {"firstSeen": iso, "lastSeen": iso, "title": str, "source": str,
                 "prices": [[iso, 숫자], ...], "gone": iso|null} }

가격 이력은 값이 '바뀔 때만' 덧붙인다. 매일 같은 값을 쌓으면 파일만 커진다.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

HISTORY_FILE = Path(__file__).resolve().parent / "output" / "listing_history.json"

# 이 비율 이상 내렸을 때만 '인하'로 본다. 환율 환산·표기 흔들림을 인하로 읽지 않기 위함.
MEANINGFUL_DROP = 0.03


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load():
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def save(history):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def update(history, listings):
    """이번 수집 결과를 이력에 반영한다. (신규 건수, 인하 건수, 사라진 건수) 반환."""
    now = _now()
    seen_ids = set()
    new_count = drop_count = 0

    for item in listings:
        item_id = item.get("id")
        if not item_id:
            continue
        seen_ids.add(item_id)
        price = item.get("priceNum")
        try:
            price = float(price) if price else None
        except (TypeError, ValueError):
            price = None

        rec = history.get(item_id)
        if rec is None:
            history[item_id] = {
                "firstSeen": now, "lastSeen": now,
                "title": item.get("title"), "source": item.get("source"),
                "prices": [[now, price]] if price else [],
                "gone": None,
            }
            new_count += 1
            continue

        rec["lastSeen"] = now
        rec["gone"] = None  # 사라졌다가 다시 올라온 경우
        if price:
            last = next((p for _, p in reversed(rec.get("prices") or []) if p), None)
            if last is None or abs(price - last) / last >= MEANINGFUL_DROP:
                rec.setdefault("prices", []).append([now, price])
                if last and price < last:
                    drop_count += 1

    gone_count = 0
    for item_id, rec in history.items():
        if item_id not in seen_ids and not rec.get("gone"):
            rec["gone"] = now
            gone_count += 1

    return new_count, drop_count, gone_count


def _days_between(a, b):
    try:
        return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 86400
    except (TypeError, ValueError):
        return None


def notes(history, item):
    """매물 1건의 이력 요약 줄. 이력이 없거나 하루치뿐이면 빈 목록."""
    rec = history.get(item.get("id"))
    if not rec:
        return []

    lines = []
    days = _days_between(rec.get("firstSeen"), rec.get("lastSeen"))
    if days is not None and days >= 1:
        line = f"추적 {days:.0f}일째 계속 게시 중"
        if days >= 90:
            line += " - 오래 안 팔린 매물. 가격 협상 여지가 크고, 안 팔리는 이유를 물을 것"
        elif days >= 30:
            line += " - 한 달 넘게 남아 있음"
        lines.append(line)

    prices = [p for p in (rec.get("prices") or []) if p[1]]
    if len(prices) >= 2:
        first_price, last_price = prices[0][1], prices[-1][1]
        change = (last_price / first_price - 1) * 100
        word = "인하" if change < 0 else "인상"
        lines.append(f"가격 {word} {abs(change):.0f}% "
                     f"(추적 시작 Rp {first_price:,.0f} → 현재 Rp {last_price:,.0f}, "
                     f"변동 {len(prices) - 1}회)")
        if change <= -10:
            lines.append("두드러진 인하 - 매도인이 처분을 서두르는 신호일 수 있음")
    return lines
