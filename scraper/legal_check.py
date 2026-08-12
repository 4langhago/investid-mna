# -*- coding: utf-8 -*-
"""한국인이 인도네시아 매물을 인수할 때 걸리는 법적 쟁점을 매물별로 붙여준다.

이 모듈이 하는 일과 하지 않는 일을 분명히 해 둔다.

  한다:
    - 업종(KBLI 계열)별 외국인 지분 상한·유보 여부를 매물 텍스트에서 추정해 경고
    - 권리(증서) 형태에서 오는 소유·명의 제약을 정리
    - 한국인 인수 건에서 반복적으로 사고가 난 '유형'을 사례로 제시
    - 계약 전에 직접 조회해야 하는 공적 장부와 그 조회 링크를 제시

  하지 않는다:
    - 특정 매물의 실제 소송·분쟁 이력 조회. 개별 사건 조회는 인도네시아 법원
      전자공시(SIPP)와 BPN·AHU 조회를 사람이 직접 해야 하며, 공개 API 가 없다.
      그래서 '조회 링크와 조회 방법'을 주고, 결과를 지어내지 않는다.

⚠️ 법률 자문이 아니다. 계약 전 인도네시아 변호사·공증인(notaris) 확인이 필요하다.
"""
import re
import urllib.parse

# --- 업종별 외국인 지분 규제 ------------------------------------------------
# 투자법(Perpres 10/2021 및 개정)의 일반 원칙을 업종 키워드로 매핑한 것이다.
# 실제 상한은 KBLI 5자리 코드로 결정되므로, 반드시 OSS 에서 코드 단위로 재확인해야 한다.
SECTOR_RULES = [
    (r"minimarket|toko kelontong|sembako|편의점|미니마트|소매",
     "소매업(KBLI 47xx)은 외국인 투자 유보 업종. PT PMA 명의로 직접 영업할 수 없다.",
     "현지인 명의(노미니) 구조를 권하는 매물이 많은데, 이는 투자법 제33조상 무효 사유다."),
    (r"warung|warteg|kaki lima|노점|포장마차",
     "노점·소형 식당은 UMKM 유보 업종으로 외국인 투자 불가.",
     None),
    (r"restoran|rumah makan|resto|cafe|café|kedai kopi|식당|음식점|카페|커피",
     "요식업은 외국인 투자 가능하나 KBLI·지역당 최소 투자 Rp 100억 요건을 받는다.",
     "소형 매장 1개만 인수하면 최소 투자 요건을 못 채워 PT PMA 인가가 반려될 수 있다. "
     "복수 매장·설비 투자로 요건을 설계하거나, 현지 파트너와의 합작 지분 구조를 검토."),
    (r"laundry|binatu|세탁소|런드리",
     "세탁업은 외국인 투자 자체는 열려 있으나 소규모는 UMKM 유보와 겹칠 수 있다.",
     "권리금 수준의 소액 인수는 PT PMA 최소 투자 요건과 격차가 커 구조가 성립하지 않는다."),
    (r"cuci mobil|car wash|세차장",
     "차량 정비·세차는 KBLI 452xx. 외국인 투자 가능하나 최소 투자 요건 적용.",
     None),
    (r"pabrik|industri|공장|제조",
     "제조업은 외국인 100% 지분이 가장 폭넓게 허용되는 분야다.",
     "다만 환경허가(AMDAL/UKL-UPL)와 산업단지 입주 조건, 폐수·소방 설비 적합성이 "
     "인수 후 비용으로 튀어나오는 대표 항목이다."),
    (r"gudang|물류|창고",
     "창고·물류는 외국인 투자 가능. 다만 화물운송(육상)은 지분 제한 업종이다.",
     None),
    (r"hotel|penginapan|villa|숙박|호텔|게스트하우스|펜션",
     "숙박업은 외국인 투자 가능(별 등급·객실 수에 따라 조건 상이).",
     "빌라·게스트하우스를 주거용 증서(SHM) 상태로 운영하던 매물은 관광사업 허가가 "
     "없는 경우가 많아, 인수 후 무허가 영업으로 폐쇄되는 사례가 반복된다."),
    (r"sekolah|kursus|학원|유치원|교육",
     "정규 교육기관은 외국인 지분 제한이 강하다(비정규 교습소는 조건부).",
     None),
    (r"klinik|apotek|약국|클리닉|병원",
     "의료·약국은 외국인 지분 상한과 인력 자격 요건이 별도로 붙는 규제 업종이다.",
     None),
    (r"tanah|kebun|perkebunan|농장|토지|땅|부지",
     "농지·플랜테이션은 HGU 대상이며 외국인 개인 취득은 불가하다.",
     None),
]

# --- 한국인 인수 건에서 반복되는 사고 유형 ----------------------------------
# 특정 매물의 이력이 아니라 '구조적으로 반복되는 유형'이다. 그래서 매물의 조건이
# 그 유형에 해당할 때만 붙인다.
RISK_PATTERNS = [
    (r"nominee|nomine|노미니|명의\s*(?:신탁|대여)|현지인\s*명의",
     "명의신탁(노미니) 구조",
     "현지인 명의로 SHM·지분을 두는 구조는 인도네시아 법상 무효로 판단될 수 있고, "
     "명의자 사망·이혼·채무 시 자산을 통째로 잃은 한국인 사례가 반복됐다. "
     "계약서상 '실소유 각서'는 법정에서 보호받지 못한다."),
    (r"\bsewa\b|\bkontrak\b|임차|월세|렌트",
     "임차 점포 승계",
     "권리금만 지급하고 임대차 승계 동의(persetujuan pengalihan sewa)를 받지 않아, "
     "인수 직후 임대인이 계약 갱신을 거절하거나 임대료를 대폭 올린 사례가 많다. "
     "임대인 동의서를 인수 계약의 선행조건으로 넣을 것."),
    (r"karyawan|pegawai|직원|종업원",
     "직원 승계와 퇴직금",
     "인도네시아 노동법상 사업 양도 시 근속이 승계되며, 누적 퇴직금(pesangon) 채무가 "
     "매수인에게 넘어온다. 인수가와 별도로 부채가 되므로 근속연수·미지급 급여·"
     "BPJS(4대보험) 체납을 실사에서 반드시 확인."),
    (r"\bpt\b|\bcv\b|법인|주식|지분",
     "법인 지분 인수(share deal)",
     "법인을 통째로 사면 숨은 채무·세금 추징·계류 소송까지 함께 인수된다. "
     "자산만 인수(asset deal)하는 구조가 안전하며, 지분 인수를 택한다면 "
     "AHU 등기부·최근 3년 세무신고(SPT)·차입 계약을 전수 확인할 것."),
    (r"\bshm\b|hak milik|소유권",
     "SHM 명의 제약",
     "SHM 은 외국인·PT PMA 명의로 이전되지 않는다. 매도인이 HGB 전환을 약속만 하고 "
     "잔금 후 전환이 반려돼 자금이 묶인 사례가 있다. 전환 완료를 잔금 조건으로 걸 것."),
    (r"girik|letter c|미등기",
     "미등기 관습지",
     "Girik 토지는 등기 자체가 없어 이중 매도·상속인 분쟁이 잦다. 외국인 취득 경로가 없다."),
    (r"ppjb|belum sertifikat|증서\s*미발급|분양",
     "증서 미발급 분양권",
     "PPJB(매매예약) 단계 매물은 준공·증서 발급 지연 시 회수 수단이 사실상 없다."),
]

# 계약 전 사람이 직접 조회해야 하는 공적 장부.
DUE_DILIGENCE_LINKS = [
    ("법인 등기·지분·이사 확인 (AHU 온라인)", "https://ahu.go.id/"),
    ("사업자 등록·인허가(NIB/KBLI) 확인 (OSS)", "https://oss.go.id/"),
    ("토지 증서 진위·저당 확인 (BPN Sentuh Tanahku)", "https://www.atrbpn.go.id/"),
    ("소송 계류·판결 검색 (대법원 SIPP)", "https://putusan3.mahkamahagung.go.id/"),
    ("세금 체납·NPWP 확인 (DJP)", "https://www.pajak.go.id/"),
]


def _text(item):
    parts = [str(item.get(k) or "") for k in
             ("title", "description", "category", "badge", "dealType")]
    parts += [str(f) for f in (item.get("facilities") or [])]
    return " ".join(parts)


def sector_notes(item):
    """(업종 규제 설명, 주의사항) 목록. 중복 없이 최대 2건."""
    text = _text(item)
    out = []
    for pat, rule, caution in SECTOR_RULES:
        if re.search(pat, text, re.I):
            out.append((rule, caution))
        if len(out) >= 2:
            break
    return out


def risk_cases(item):
    """해당되는 사고 유형 (제목, 설명) 목록."""
    text = _text(item)
    return [(name, detail) for pat, name, detail in RISK_PATTERNS
            if re.search(pat, text, re.I)]


def ownership_note(item):
    """지분·명의 구조에 대한 한 줄 결론."""
    text = _text(item)
    # 매도인이 구조를 명시한 경우에는 추측하지 않고 그대로 받아 적는다.
    if re.search(r"사업자산\s*양도|자산\s*양도|asset deal|지분.{0,6}(?:아닙|아닌)|"
                 r"법인.{0,10}매각(?:이|은)?\s*아닙", text):
        return ("매도인이 자산 양도(asset deal)라고 명시함 - 법인 부채는 따라오지 않는 구조. "
                "다만 인허가·임대차가 법인에 붙어 있으면 신규 발급이 필요하니 범위를 확인할 것.")
    if re.search(r"\bpt\b|법인|지분|saham", text, re.I):
        return ("법인이 낀 거래로 보임 - 자산 인수(asset deal)와 지분 인수(share deal) 중 "
                "어느 구조인지부터 확정할 것. 지분 인수는 숨은 부채까지 승계된다.")
    if re.search(r"\bshm\b|hak milik|소유권", text, re.I):
        return ("부동산이 SHM 명의 - 외국인·PT PMA 앞으로 이전 불가. "
                "HGB 전환 또는 임차 구조로 분리해야 한다.")
    return ("지분·명의 구조가 글에 드러나지 않음 - 매도인이 개인인지 법인인지, "
            "매각 대상이 자산인지 지분인지 먼저 확인할 것.")


def review_links(item):
    """실제 이용자 리뷰·평판을 직접 확인할 수 있는 검색 링크.

    리뷰 내용을 긁어와 요약하지 않는다. 매물명이 상호와 일치한다는 보장이 없어,
    엉뚱한 가게의 평점을 붙이면 잘못된 판단을 유도하기 때문이다.
    대신 바로 눌러 확인할 수 있는 검색 링크를 준다.
    """
    name = str(item.get("title") or "").strip()
    where = str(item.get("address") or item.get("locationKo") or item.get("location") or "")
    # 제목에서 거래 표현을 빼면 상호에 가까워진다.
    q = re.sub(r"매매|매각|양도|양수|인수|팝니다|합니다|dijual|take\s*over|over\b",
               " ", name, flags=re.I)
    q = re.sub(r"\s{2,}", " ", q).strip() or name
    query = urllib.parse.quote_plus(f"{q} {where}".strip())
    return [
        ("구글 지도·리뷰 검색", f"https://www.google.com/maps/search/?api=1&query={query}"),
        ("구글 웹 검색(평판·기사)", f"https://www.google.com/search?q={query}"),
        ("인스타그램 해시태그", f"https://www.instagram.com/explore/search/keyword/?q={query}"),
    ]


# --- 인수 후 '운영 개시'까지의 절차 -----------------------------------------
# 이 시스템의 목적은 매물 구경이 아니라 인수해서 직접 굴리는 것이다. 그래서 매물마다
# '사면 끝'이 아니라 '사고 나서 무엇을 해야 문을 여는가'를 붙인다.
# 기간·비용은 일반적인 실무 범위이며, 업종·지역·대행사에 따라 달라진다.
OPERATING_STEPS = [
    ("① 법인(PT PMA) 설립",
     "공증 정관 → AHU 법인 승인 → NPWP(세번) → NIB 발급. 통상 3~6주, "
     "대행 비용 Rp 2~4천만. 납입자본 Rp 100억 요건은 '납입 증명'을 요구받을 수 있다."),
    ("② 체류·취업 자격",
     "인수자가 직접 운영하려면 투자자 KITAS(이사·주주 등재 필요) 또는 근로 KITAS 가 있어야 "
     "한다. 관광비자로 영업에 관여하면 추방·재입국 금지 사유다."),
    ("③ 영업 인허가 이전",
     "인수한 사업장의 NIB·업종별 허가(식품이면 위생·할랄, 공장이면 환경허가)는 자동으로 "
     "넘어오지 않는다. 신규 발급 기간 동안 영업 공백이 생기는지 먼저 확인."),
    ("④ 임대차·자산 승계",
     "임대인 승계 동의서, 설비 목록(수량·상태·잔존 수명), 재고 실사, 브랜드·레시피·"
     "SNS 계정 이전까지 인수 계약서에 목록으로 명시."),
    ("⑤ 인력 승계",
     "근속·미지급 급여·BPJS 체납·퇴직금(pesangon) 채무를 인수가에서 정산. "
     "핵심 인력(주방장·정비 기술자) 잔류 조건을 계약에 넣지 않으면 인수 직후 이탈한다."),
    ("⑥ 자금·세무 운영",
     "법인 명의 은행 계좌 개설, POS·회계 장부 인수, 월 PPh(원천세)·PPN(부가세) 신고 체계 "
     "구축. 인도네시아는 월 단위 신고이며 지연 시 가산세가 붙는다."),
]

# 대금 지급 구조 - 한국인 인수 사고의 대부분이 여기서 난다.
PAYMENT_GUARD = [
    "계약금은 공증인(notaris) 에스크로에 예치하고, 인허가 이전·임대인 동의 완료를 "
    "잔금 지급 조건으로 걸 것.",
    "권리금·설비대금·재고대금을 항목별로 나눠 계약서에 적어야 분쟁 시 회수 범위가 생긴다.",
    "매도인 개인 계좌로 전액 선지급하는 구조는 회수 수단이 사실상 없다.",
]


def operating_playbook(item):
    """인수 후 운영 개시까지의 절차. 사업체 인수 매물에서 특히 중요하다."""
    is_business = item.get("subtype") == "akuisisi" or item.get("type") == "bisnis"
    lines = [f"{name} — {detail}" for name, detail in OPERATING_STEPS
             if is_business or not name.startswith(("③", "⑤", "⑥"))]
    lines.append("대금 구조: " + " / ".join(PAYMENT_GUARD))
    return lines


def build_legal_section(item):
    """텔레그램 메시지에 넣을 법률·리스크 섹션 줄 목록."""
    lines = []
    for rule, caution in sector_notes(item):
        lines.append(f"업종 규제: {rule}")
        if caution:
            lines.append(f"  └ {caution}")

    lines.append(f"지분·명의: {ownership_note(item)}")

    cases = risk_cases(item)
    if cases:
        lines.append("반복 사고 유형(이 매물 조건에 해당):")
        for name, detail in cases[:3]:
            lines.append(f"  • {name} — {detail}")
    return lines
