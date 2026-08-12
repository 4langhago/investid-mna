# -*- coding: utf-8 -*-
"""Google Places API (New) 로 매물이 '실제로 영업 중인 가게'인지 확인한다.

왜 필요한가:
  지금까지 '영업 중'은 전부 매도인 주장이었다. 매물 글에 "berjalan lancar"라고 적혀
  있어도 실제로는 몇 달 전에 닫은 가게일 수 있다. Places API 는 구글이 관리하는
  businessStatus(영업/임시휴업/폐업)와 평점·리뷰 수를 주므로, 매도인 주장과 독립된
  유일한 검증 수단이다.

⚠️ 이름 매칭의 한계 — 이 모듈이 조심하는 부분
  매물 제목은 상호가 아니다("MOI 끌라빠가딩 즉시 운영 가능한 카페 매장 양도").
  엉뚱한 가게의 평점을 붙이면 없는 것만 못하므로:
    - 검색해서 찾은 가게 '이름과 주소를 그대로 함께 표시'한다. 판단은 사람이 한다.
    - 이름 유사도가 낮으면 confidence 를 '낮음'으로 표시하고 참고용이라고 밝힌다.
    - 폐업(CLOSED_PERMANENTLY)은 confidence 가 높을 때만 탈락 근거로 쓴다.

비용 관리:
  호출 결과를 output/places_cache.json 에 캐시한다(기본 14일). 매일 같은 매물을
  다시 조회하면 요금만 나가고 결과는 같다.

환경변수:
  GOOGLE_PLACES_API_KEY  — 없으면 이 모듈은 조용히 비활성화된다(파이프라인 중단 없음).
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
CACHE_FILE = Path(__file__).resolve().parent / "output" / "places_cache.json"
CACHE_DAYS = 14
REQUEST_TIMEOUT = 15

# 요청할 필드만 지정한다. 필드를 넓게 잡으면 상위 요금 SKU 로 넘어가므로,
# 판단에 실제로 쓰는 것만 넣는다.
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.businessStatus",
    "places.rating",
    "places.userRatingCount",
    "places.googleMapsUri",
    "places.primaryTypeDisplayName",
    "places.currentOpeningHours.openNow",
])

# 매물 제목에서 상호가 아닌 부분(거래 표현·수식어)을 걷어내기 위한 패턴.
NOISE_WORDS = re.compile(
    r"매매|매각|양도|양수|인수|처분|팝니다|합니다|급매|즉시\s*운영\s*가능|운영\s*중|"
    r"dijual|take\s*over|takeover|over\b|oper\b|alih\b|usaha\b|bisnis\b|murah|"
    r"siap\s*pakai|aktif|사업체|매물|매장|점포", re.I)


def api_key():
    return os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()


def enabled():
    return bool(api_key())


def _load_cache():
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def _save_cache(cache):
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def build_query(item):
    """매물에서 상호에 가장 가까운 검색어를 만든다."""
    title = str(item.get("title") or "")
    # 따옴표·대문자 브랜드명이 있으면 그게 상호일 확률이 높다.
    quoted = re.findall(r"[\"'“”‘’]([^\"'“”‘’]{2,40})[\"'“”‘’]", title)
    base = quoted[0] if quoted else NOISE_WORDS.sub(" ", title)
    base = re.sub(r"[^\w가-힣\s\.\-&]", " ", base)
    base = re.sub(r"\s{2,}", " ", base).strip()
    where = str(item.get("address") or item.get("locationKo") or item.get("location") or "")
    where = re.sub(r"\s{2,}", " ", where).strip()
    query = f"{base} {where}".strip()
    return query[:200]


def _similarity(a, b):
    """검색어와 찾은 상호의 겹침 정도(0~1). 토큰 기반의 거친 척도다."""
    def toks(s):
        return {t for t in re.split(r"[\s\.\-_/]+", str(s).lower()) if len(t) >= 2}
    ta, tb = toks(a), toks(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(tb)


def search(item, cache=None, sleep=0.2):
    """매물 1건에 대응하는 구글 장소 정보. 못 찾거나 키가 없으면 None."""
    if not enabled():
        return None

    query = build_query(item)
    if len(query) < 4:
        return None

    own_cache = cache is None
    cache = _load_cache() if own_cache else cache
    key = query.lower()
    hit = cache.get(key)
    if hit:
        fetched = hit.get("fetchedAt")
        try:
            ts = datetime.fromisoformat(fetched)
        except (TypeError, ValueError):
            ts = None
        if ts and datetime.now(timezone.utc) - ts < timedelta(days=CACHE_DAYS):
            return hit.get("result")

    body = json.dumps({
        "textQuery": query,
        "languageCode": "ko",
        "regionCode": "ID",
        "maxResultCount": 1,
    }).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key(),
        "X-Goog-FieldMask": FIELD_MASK,
    })

    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as res:
            data = json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # 키 오류·할당량 초과를 조용히 삼키면 원인을 모른 채 결과만 비어 보인다.
        detail = e.read().decode("utf-8", errors="replace")[:300]
        print(f"  !! Places API 오류 HTTP {e.code}: {detail}")
        return None
    except Exception as e:
        print(f"  !! Places API 접속 실패({type(e).__name__})")
        return None

    places = data.get("places") or []
    result = None
    if places:
        p = places[0]
        name = (p.get("displayName") or {}).get("text") or ""
        sim = _similarity(query, name)
        result = {
            "name": name,
            "address": p.get("formattedAddress"),
            "status": p.get("businessStatus"),
            "rating": p.get("rating"),
            "reviews": p.get("userRatingCount"),
            "mapsUri": p.get("googleMapsUri"),
            "type": (p.get("primaryTypeDisplayName") or {}).get("text"),
            "openNow": (p.get("currentOpeningHours") or {}).get("openNow"),
            "match": "높음" if sim >= 0.5 else ("보통" if sim >= 0.25 else "낮음"),
        }

    cache[key] = {"fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "result": result}
    if own_cache:
        _save_cache(cache)
    time.sleep(sleep)  # 연속 호출 완화
    return result


STATUS_KO = {
    "OPERATIONAL": "영업 중(구글 확인)",
    "CLOSED_TEMPORARILY": "임시 휴업(구글 확인)",
    "CLOSED_PERMANENTLY": "영구 폐업(구글 확인)",
}


def format_lines(place):
    """텔레그램 메시지용 줄 목록."""
    if not place:
        return []
    lines = [f"구글 등록 상호: {place['name']}"
             f" (매물 제목과의 일치도 {place['match']})"]
    if place.get("address"):
        lines.append(f"구글 주소: {place['address']}")
    status = STATUS_KO.get(place.get("status"))
    if status:
        lines.append(f"영업 상태: {status}")
    if place.get("rating") and place.get("reviews"):
        lines.append(f"구글 평점 {place['rating']} · 리뷰 {place['reviews']}개"
                     + (" - 리뷰가 적어 대표성이 낮음" if place["reviews"] < 20 else ""))
    elif place.get("reviews") == 0 or place.get("rating") is None:
        lines.append("구글 리뷰 없음 - 신규 가게이거나 등록되지 않은 업소")
    if place.get("openNow") is not None:
        lines.append("지금 영업 중" if place["openNow"] else "현재 영업 시간 아님")
    if place.get("mapsUri"):
        lines.append(f"구글 지도: {place['mapsUri']}")
    if place["match"] != "높음":
        lines.append("※ 매물 제목이 상호가 아니어서 다른 가게일 수 있음 - 위 상호·주소를 "
                     "매도인에게 확인할 것")
    return lines


def verdict(place):
    """운영 가능성 판정에 넘길 (가점, 사유). 확신이 없으면 판정에 관여하지 않는다."""
    if not place:
        return 0, None
    if place.get("status") == "CLOSED_PERMANENTLY" and place["match"] == "높음":
        return -99, f"구글에 영구 폐업으로 등록된 업소({place['name']})"
    if place.get("status") == "OPERATIONAL" and place["match"] in ("높음", "보통"):
        pts = 2 if (place.get("reviews") or 0) >= 20 else 1
        return pts, f"구글에서 영업 중으로 확인됨({place['name']})"
    return 0, None
