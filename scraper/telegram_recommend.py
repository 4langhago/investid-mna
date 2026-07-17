# -*- coding: utf-8 -*-
"""
'완전인수(akuisisi)' 매물 중 실제 운영 가능한 사업체만 골라 매일 5건을 텔레그램으로 추천.

- 대상: js/data.js LISTINGS 중 subtype === "akuisisi" && source 필드 존재(A그룹, 샘플 제외)
- 검증: 필수 필드(연락처/가격/월매출/수익률/설명) 누락 매물은 자동 제외 — 사람 개입 없음
- 선정: 수익률(profit) 내림차순 정렬 후, 이전 발송 위치(scraper/output/telegram_state.json)
        다음부터 5건씩 순환(로테이션). 끝까지 가면 처음부터 다시 순환.
- 전송: Telegram Bot API (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수 필요)

사용법:
  python scraper/telegram_recommend.py            # 실제 전송
  python scraper/telegram_recommend.py --dry-run   # 전송 없이 선정 결과만 출력
"""
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DATA_JS = ROOT / "js" / "data.js"
EXPORT_JS = Path(__file__).resolve().parent / "export_listings.js"
STATE_FILE = Path(__file__).resolve().parent / "telegram_state.json"

REQUIRED_FIELDS = ["title", "location", "price", "priceNum", "monthlyRevenue", "profit", "whatsapp"]
BATCH_SIZE = 5


def load_listings():
    out = subprocess.run(
        ["node", str(EXPORT_JS), str(DATA_JS), "LISTINGS"],
        capture_output=True, check=True,
    )
    return json.loads(out.stdout.decode("utf-8"))


def is_valid(item):
    for field in REQUIRED_FIELDS:
        v = item.get(field)
        if v is None or v == "":
            return False
    return True


def profit_value(item):
    try:
        return float(str(item.get("profit", "0")).replace("%", "").strip())
    except ValueError:
        return 0.0


def select_candidates(listings):
    akuisisi = [x for x in listings if x.get("subtype") == "akuisisi" and x.get("source")]
    valid, invalid = [], []
    for x in akuisisi:
        (valid if is_valid(x) else invalid).append(x)
    for x in invalid:
        print(f"  !! 검증 실패로 제외: id={x.get('id')} {x.get('title')}")
    valid.sort(key=profit_value, reverse=True)
    return valid


def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"cursor": 0}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def pick_batch(candidates, cursor):
    n = len(candidates)
    if n == 0:
        return [], cursor
    picked = [candidates[(cursor + i) % n] for i in range(min(BATCH_SIZE, n))]
    next_cursor = (cursor + len(picked)) % n
    return picked, next_cursor


def format_item(item, rank):
    wa = item.get("whatsapp", "")
    wa_digits = wa.replace("+", "").replace(" ", "")
    wa_link = f"https://wa.me/{wa_digits}" if wa_digits else ""
    desc = (item.get("description") or "").strip()
    if len(desc) > 150:
        desc = desc[:147] + "..."
    lines = [
        f"{rank}. *{item['title']}*",
        f"   📍 {item.get('locationKo') or item.get('location')}",
        f"   💰 인수가 {item['price']} · 월매출 {item['monthlyRevenue']} · 수익률 {item['profit']}",
    ]
    if desc:
        lines.append(f"   {desc}")
    if wa_link:
        lines.append(f"   📞 {wa_link}")
    return "\n".join(lines)


def build_message(picked):
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    header = f"*오늘의 인수 추천 매물* ({today})\n실제 운영 중인 사업체 완전인수 매물 {len(picked)}건\n"
    body = "\n\n".join(format_item(x, i + 1) for i, x in enumerate(picked))
    return header + "\n" + body


def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 설정되지 않음")

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as res:
        result = json.loads(res.read().decode("utf-8"))
    if not result.get("ok"):
        raise RuntimeError(f"텔레그램 전송 실패: {result}")


def main():
    dry_run = "--dry-run" in sys.argv

    listings = load_listings()
    candidates = select_candidates(listings)
    print(f"[검증] 완전인수(실사) 대상 {len(candidates)}건 (검증 통과)")

    if not candidates:
        print("!! 발송 가능한 매물이 없음 - 종료")
        sys.exit(1)

    state = load_state()
    picked, next_cursor = pick_batch(candidates, state.get("cursor", 0))
    message = build_message(picked)

    print("----- 발송 내용 미리보기 -----")
    print(message)
    print("-----------------------------")

    if dry_run:
        print("[dry-run] 전송 생략")
        return

    send_telegram(message)
    state["cursor"] = next_cursor
    state["lastRunAt"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    state["lastSentIds"] = [x["id"] for x in picked]
    save_state(state)
    print(f"[전송 완료] {len(picked)}건, 다음 커서 위치: {next_cursor}")


if __name__ == "__main__":
    main()
