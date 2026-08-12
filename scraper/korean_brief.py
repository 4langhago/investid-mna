# -*- coding: utf-8 -*-
"""수집된 인도네시아어 매물 설명에서 한글 요약을 만든다.

⚠️ 설계 원칙
  - 기계 번역을 하지 않는다. 문장 전체를 옮기면 원문에 없는 뜻이 섞여 들어갈 수 있다.
    대신 부동산·사업체 매물에서 반복되는 정형 표현만 정규식으로 뽑아 한글 항목으로 만든다.
  - 뽑지 못한 내용은 만들어내지 않는다. 원문(설명)은 메시지에 그대로 함께 남긴다.
  - 숫자는 원문 표기(인니식 1.000,5)를 한국식으로 변환해서 보여준다.
"""
import re

# 인도네시아 부동산·사업체 매물에서 반복되는 용어. 키워드가 본문에 있으면 한글 태그로 붙인다.
KEYWORD_KO = [
    (r"\bbebas banjir\b", "침수 이력 없음(매도인 주장)"),
    (r"\bsudah berjalan\b|\bberjalan lancar\b", "현재 영업 중"),
    (r"\bpelanggan tetap\b|\bcustomer tetap\b", "고정 고객 보유"),
    (r"\bsiap huni\b", "즉시 입주 가능"),
    (r"\bsiap pakai\b|\bsiap operasional\b", "즉시 운영 가능"),
    (r"\bfull furnish|\bfurnished\b", "가구·집기 포함"),
    (r"\bstrategis\b|\blokasi strategis\b", "요지 입지(매도인 주장)"),
    (r"\bpinggir jalan\b|\bjalan raya\b", "간선도로변"),
    (r"\bhook\b", "코너 필지"),
    (r"\bnego\b|\bnegotiable\b", "가격 협의 가능"),
    (r"\bbutuh uang\b|\bbu\b", "매도인 급매"),
    (r"\bturun harga\b", "가격 인하됨"),
    (r"\bbebas sengketa\b|\btidak sengketa\b", "분쟁 없음(매도인 주장)"),
    (r"\bkaryawan\b|\bpegawai\b", "직원 인계 대상 있음"),
    (r"\bsewa\b|\bkontrak\b", "임차 조건 포함 - 계약 잔여기간 확인 필요"),
    (r"\bkomplit\b|\blengkap\b", "설비 일체 포함"),
    (r"\bparkir\b|\bparkiran\b", "주차 공간 있음"),
    (r"\bdekat (?:kampus|sekolah|pasar|mall|stasiun|tol)\b",
     "주변에 대학·학교·시장·몰·역·톨게이트 등 집객 시설"),
    (r"\bramai\b|\bpadat penduduk\b", "유동인구·배후세대가 많은 곳(매도인 주장)"),
    (r"\bijin lengkap\b|\bizin lengkap\b|\blegalitas lengkap\b", "인허가 서류 완비(매도인 주장)"),
    (r"\bnib\b|\boss\b|\bsiup\b|\bnpwp\b", "사업자 등록번호(NIB/SIUP/NPWP) 언급 있음"),
    (r"\bpbg\b|\bimb\b", "건축허가(PBG/IMB) 언급 있음"),
    (r"\bstock\b|\bstok barang\b|\binventaris\b", "재고·비품 인계 포함"),
    (r"\bresep\b|\btraining\b|\bpelatihan\b", "레시피·운영 교육 인계 포함"),
    (r"\bsupplier\b|\bvendor\b", "거래처(공급업체) 인계 언급"),
    (r"\bmedsos\b|\binstagram\b|\bgofood\b|\bgrabfood\b|\bshopeefood\b",
     "배달 플랫폼·SNS 채널 운영 중"),
]

# 한인 커뮤니티(한국어) 매물에서 쓰이는 표현. 위 인니어 표를 한국어로 옮긴 대응표다.
KEYWORD_KO_SRC = [
    (r"영업\s*중|운영\s*중|정상\s*영업", "현재 영업 중"),
    (r"즉시\s*운영|바로\s*운영|인수\s*즉시", "인수 즉시 운영 가능"),
    (r"권리금", "권리금 조건 포함 - 총액에 무엇이 포함되는지 확인 필요"),
    (r"단골|고정\s*고객|고정\s*거래처", "고정 고객·거래처 보유"),
    (r"직원|종업원|스태프", "직원 인계 대상 있음"),
    (r"집기|설비|기계|시설\s*일체|풀\s*옵션", "설비·집기 인계 포함"),
    (r"임차|월세|렌트비|계약\s*기간", "임차 점포 - 임대인 승계 동의와 잔여 기간 확인 필요"),
    (r"법인|PT\b|사업자|인허가|허가증", "법인·인허가 관련 언급 있음"),
    (r"한인\s*(?:상권|밀집)|교민", "한인 상권·교민 수요 언급"),
    (r"네고|협의\s*가능|조정\s*가능", "가격 협의 가능"),
    (r"급매|급하게", "매도인 급매"),
]

# 업종/유형 키워드 → 한글. 제목·설명에서 찾아 '업종' 줄을 만든다.
BUSINESS_KO = [
    (r"\blaundry\b|\bbinatu\b", "세탁소"),
    (r"\bkos[- ]?kosan\b|\bkost\b|\bkosan\b", "하숙·원룸 임대"),
    (r"\bcaf[eé]\b|\bkedai kopi\b|\bcoffee\b", "카페"),
    (r"\brestoran\b|\brumah makan\b|\bresto\b|\bwarung\b", "음식점"),
    (r"\bminimarket\b|\btoko kelontong\b|\bsembako\b", "소매점·미니마트"),
    (r"\bbengkel\b", "정비소"),
    (r"\bpeternakan ayam\b|\bayam petelur\b", "양계장"),
    (r"\bpeternakan\b|\bternak\b", "축산"),
    (r"\bperkebunan\b|\bkebun\b", "농장"),
    (r"\bpabrik\b|\bindustri\b", "공장·제조"),
    (r"\bsalon\b|\bbarbershop\b", "미용실·이발소"),
    (r"\bapotek\b", "약국"),
    (r"\bklinik\b", "클리닉"),
    (r"\bhotel\b|\bpenginapan\b|\bvilla\b", "숙박업"),
    (r"\bgudang\b", "창고"),
    (r"\bbisnis online\b|\bonline shop\b|\bolshop\b", "온라인 판매업"),
    (r"\bdistributor\b|\bagen\b", "유통·대리점"),
    (r"\bpercetakan\b", "인쇄업"),
    (r"\bcuci mobil\b|\bcar wash\b", "세차장"),
    (r"\bkaryawan\b", None),  # 업종 아님. 아래 루프에서 None 은 건너뛴다.
    # 한국어 매물(한인 커뮤니티) 표현
    (r"세탁소|런드리", "세탁소"),
    (r"카페|커피숍|디저트", "카페"),
    (r"식당|레스토랑|음식점|한식당|고깃집|분식", "음식점"),
    (r"세차장", "세차장"),
    (r"미용실|이발소|살롱|네일", "미용실·살롱"),
    (r"마사지|스파", "마사지·스파"),
    (r"공장|제조", "공장·제조"),
    (r"창고|물류", "창고·물류"),
    (r"편의점|마트|슈퍼", "소매점·미니마트"),
    (r"학원|교습소|유치원", "학원·교육"),
    (r"호텔|게스트하우스|펜션|숙소", "숙박업"),
    (r"하숙|기숙|원룸\s*임대", "하숙·원룸 임대"),
    (r"농장|양계|축산", "농장·축산"),
    (r"루꼬|루코|상가|점포|매장", "상가·점포"),
    (r"사무실|오피스", "사무실"),
]

# 증서(권리) 종류 → 한글. 외국인 취득 가능 여부를 좌우하는 핵심 정보다.
CERT_KO = [
    (r"\bshm\s*sarusun\b|\bshmsrs\b|\bstrata\s*title\b", "SHMSRS(집합건물 소유권)"),
    (r"\bshm\b|\bhak milik\b|\bsertifikat hak milik\b", "SHM(소유권)"),
    (r"\bshgb\b|\bhgb\b|\bhak guna bangunan\b", "HGB(건물사용권)"),
    (r"\bhak pakai\b", "Hak Pakai(사용권)"),
    (r"\bhgu\b|\bhak guna usaha\b", "HGU(경작권)"),
    (r"\bgirik\b|\bletter c\b", "Girik(미등기 관습지)"),
    (r"\bppjb\b", "PPJB(매매예약증서)"),
    (r"\bajb\b", "AJB(매매증서)"),
    # 한인 커뮤니티 매물은 증서명을 한글로 적는 경우가 있다.
    (r"소유권\s*증서|하크\s*밀릭", "SHM(소유권)"),
    (r"건물\s*사용권", "HGB(건물사용권)"),
]

_MONTH_UNITS = {"bulan": "월", "bln": "월", "thn": "연", "tahun": "연"}


def _num_id_to_ko(text):
    """인니식 숫자 표기(1.000,5)를 한국식(1,000.5)으로."""
    t = text.strip().replace(".", "\x00").replace(",", ".").replace("\x00", ",")
    return t


def _rp(amount_text, unit_text):
    """'180jt', '16 M' 같은 금액 표기를 한글로."""
    num = _num_id_to_ko(amount_text)
    unit = (unit_text or "").lower()
    if unit.startswith("jt") or unit.startswith("juta"):
        return f"Rp {num}백만"
    if unit in ("m", "miliar", "milyar", "milliar"):
        return f"Rp {num}십억"
    if unit.startswith("rb") or unit.startswith("ribu"):
        return f"Rp {num}천"
    return f"Rp {num}"


def _find(patterns, text):
    hits = []
    for pat, ko in patterns:
        if ko and re.search(pat, text, re.I):
            hits.append(ko)
    # 중복 제거(입력 순서 유지)
    return list(dict.fromkeys(hits))


def detect_certificate(item):
    """매물에서 증서 종류를 찾아 (원문키, 한글) 목록으로 반환. 없으면 빈 목록."""
    text = " ".join(str(item.get(k) or "") for k in ("title", "description"))
    text += " " + " ".join(str(f) for f in (item.get("facilities") or []))
    found = []
    for pat, ko in CERT_KO:
        m = re.search(pat, text, re.I)
        if m:
            found.append((m.group(0).upper(), ko))
    # SHMSRS 가 잡히면 SHM 중복 항목은 버린다(같은 문자열에서 둘 다 매칭됨).
    if any(ko.startswith("SHMSRS") for _, ko in found):
        found = [x for x in found if not x[1].startswith("SHM(")]
    return list(dict.fromkeys(found))


def detect_business_kind(item):
    text = " ".join(str(item.get(k) or "") for k in ("title", "description", "category"))
    return _find(BUSINESS_KO, text)


def extract_facts(item):
    """설명문에서 수치·조건을 뽑아 한글 항목 리스트로 만든다."""
    desc = str(item.get("description") or "")
    title = str(item.get("title") or "")
    text = f"{title}\n{desc}"
    facts = []

    def add(line):
        if line not in facts:
            facts.append(line)

    # 면적 - LT(토지) / LB(건물)
    # '++' 는 '이상'을 뜻하는 매물 표기( 100++ m2 ). 숫자 뒤/앞 어디에나 붙는다.
    for pat, label in (
        (r"(?:luas\s*tanah|\blt\.?)\s*[:\-]?\s*([\d.,]+)\s*\+*\s*m[²2]?", "토지"),
        (r"(?:luas\s*bangunan|\blb\.?)\s*[:\-]?\s*([\d.,]+)\s*\+*\s*m[²2]?", "건물"),
    ):
        m = re.search(pat, text, re.I)
        if m:
            add(f"{label} 면적 {_num_id_to_ko(m.group(1))}m²")

    # 방 / 욕실
    # 'KM' 은 욕실 약어이자 거리 단위(km)다. '5 km dari tol' 을 욕실로 읽지 않도록
    # 약어형은 뒤에 거리 표현이 오지 않을 때만 인정한다.
    m = (re.search(r"(?:kamar tidur|\bkt\b)\s*[:\-]?\s*(\d+)", text, re.I)
         or re.search(r"(\d+)\s*(?:kamar tidur|\bkt\b)", text, re.I))
    if m:
        add(f"침실 {m.group(1)}개")
    m = (re.search(r"(?:kamar mandi|\bkm\b)\s*[:\-]?\s*(\d+)", text, re.I)
         or re.search(r"(\d+)\s*(?:kamar mandi|\bkm\b(?!\s*(?:dari|ke|jarak)))", text, re.I))
    if m:
        add(f"욕실 {m.group(1)}개")

    # 층수
    m = re.search(r"([\d.,]+)\s*(?:lantai|lt\.)\b", text, re.I)
    if m:
        add(f"{_num_id_to_ko(m.group(1))}층")

    # 전기 용량
    m = re.search(r"(?:listrik|pln|daya)[^\d]{0,12}([\d.,]+)\s*(?:watt|va|kva)", text, re.I)
    if m:
        add(f"전기 용량 {_num_id_to_ko(m.group(1))}W급")

    # 매출 / 순익 - 사업체 인수 판단의 핵심 수치
    for pat, label in (
        (r"(?:omset|omzet|pendapatan kotor|pemasukan kotor)[^\d]{0,15}([\d.,]+)\s*"
         r"(jt|juta|m\b|miliar|milyar|rb|ribu)?\s*/?\s*(bulan|bln|thn|tahun)?", "월매출"),
        (r"(?:net profit|laba bersih|keuntungan bersih|pendapatan bersih|profit)[^\d]{0,15}"
         r"([\d.,]+)\s*(jt|juta|m\b|miliar|milyar|rb|ribu)?\s*/?\s*(bulan|bln|thn|tahun)?", "순이익"),
    ):
        m = re.search(pat, text, re.I)
        if m:
            period = _MONTH_UNITS.get((m.group(3) or "").lower(), "")
            head = label if not period else (period + label[1:])
            add(f"{head} 약 {_rp(m.group(1), m.group(2))} (매도인 제시값 · 장부 확인 필요)")

    # 직원 수
    m = re.search(r"([\d]+(?:\s*[-–]\s*[\d]+)?)\s*(?:orang\s*)?(?:karyawan|pegawai)", text, re.I)
    if m:
        add(f"직원 {m.group(1).replace(' ', '')}명")

    # 매각 사유 - 원문을 그대로 인용하지 않고 유형만 분류한다
    if re.search(r"dijual karena|alasan (?:di)?jual", text, re.I):
        if re.search(r"tidak mau (?:menerus|lanjut)|tidak ada penerus|anak", text, re.I):
            add("매각 사유: 후계자 없음(원문 기재)")
        elif re.search(r"pindah|merantau|balik kampung", text, re.I):
            add("매각 사유: 매도인 이주(원문 기재)")
        elif re.search(r"butuh (?:uang|dana)|kebutuhan", text, re.I):
            add("매각 사유: 자금 필요(원문 기재)")
        else:
            add("매각 사유 기재 있음 - 원문 확인 필요")

    # --- 한국어 원문(한인 커뮤니티 매물)에서 뽑는 수치 ---
    m = re.search(r"(?:권리금)\s*[:\-]?\s*([\d,\.]+)\s*(억|천만|만|만불|USD|달러|jt|juta)?", text)
    if m:
        add(f"권리금 {m.group(1)}{m.group(2) or ''}")
    m = re.search(r"(?:보증금|디파짓)\s*[:\-]?\s*([\d,\.]+)\s*(억|천만|만|USD|달러|jt|juta)?", text)
    if m:
        add(f"보증금 {m.group(1)}{m.group(2) or ''}")
    m = re.search(r"(?:월\s*세|월세|임대료)\s*[:\-]?\s*([\d,\.]+)\s*(억|천만|만|USD|달러|jt|juta)?",
                  text)
    if m:
        add(f"월 임대료 {m.group(1)}{m.group(2) or ''}")
    m = re.search(r"(?:월\s*매출|연\s*매출|매출)\s*[:\-]?\s*(?:약\s*)?([\d,\.]+)\s*"
                  r"(억|천만|만|jt|juta|M)?", text)
    if m:
        add(f"매출 {m.group(1)}{m.group(2) or ''} (매도인 제시값 · 장부 확인 필요)")
    m = re.search(r"(?:직원|종업원)\s*(\d+)\s*(?:명|인)", text)
    if m:
        add(f"직원 {m.group(1)}명")
    m = re.search(r"(\d+)\s*년\s*(?:째|간)?\s*(?:운영|영업)", text)
    if m:
        add(f"영업 {m.group(1)}년차")
    m = re.search(r"(?:면적|평수)\s*[:\-]?\s*([\d,\.]+)\s*(?:㎡|m2|m²|평)", text)
    if m:
        add(f"면적 {m.group(1)}㎡")

    facts.extend(_find(KEYWORD_KO, text))
    facts.extend(_find(KEYWORD_KO_SRC, text))
    return facts


# --- 상세 한글 설명 ---------------------------------------------------------
# 텔레그램 메시지에서 '이 매물이 무엇이고, 무엇을 인수하며, 무엇을 더 확인해야 하는가'가
# 한 번에 읽히도록 항목을 섹션으로 묶는다. 모든 문장은 수집 필드에서 나온 것이며,
# 원문에 없는 평가(입지 좋음·수익성 양호 등)는 만들지 않는다.

_SECTION_RULES = [
    ("영업 현황", r"영업|운영|매출|순익|고객|거래처|직원|년차"),
    ("인수 범위", r"설비|집기|재고|비품|레시피|교육|공급업체|플랫폼|SNS|권리금"),
    ("계약·비용", r"보증금|임대료|임차|계약|권리금|협의|급매|인하"),
    ("건물·시설", r"면적|층|침실|욕실|전기|주차|입주"),
    ("권리·인허가", r"인허가|등록번호|건축허가|법인|사업자|분쟁"),
]


def _section_of(line):
    for name, pat in _SECTION_RULES:
        if re.search(pat, line):
            return name
    return "기타 정보"


def build_korean_detail(item):
    """섹션으로 묶은 상세 한글 설명. [(섹션명, [줄, ...]), ...] 형태로 반환."""
    grouped = {}
    order = []

    def put(section, line):
        if section not in grouped:
            grouped[section] = []
            order.append(section)
        if line not in grouped[section]:
            grouped[section].append(line)

    kinds = detect_business_kind(item)
    head = " · ".join(kinds) if kinds else (item.get("category") or "유형 미상")
    put("개요", f"업종/용도: {head}")

    where = item.get("locationKo") or item.get("location")
    if where:
        addr = item.get("address")
        put("개요", f"위치: {where}" + (f" ({addr})" if addr and addr != where else ""))
    if item.get("dealType"):
        put("개요", f"거래 형태: {item['dealType']}")
    if item.get("price"):
        put("계약·비용", f"표시가: {item['price']}")

    certs = detect_certificate(item)
    if certs:
        put("권리·인허가", "권리 형태: " + " · ".join(ko for _, ko in certs))

    for line in extract_facts(item):
        put(_section_of(line), line)

    return [(name, grouped[name]) for name in order if grouped[name]]


def build_korean_brief(item):
    """매물 1건의 한글 요약 줄 목록. 뽑을 내용이 없으면 빈 목록."""
    lines = []

    kinds = detect_business_kind(item)
    if kinds:
        lines.append("업종/용도: " + " · ".join(kinds))
    elif item.get("category"):
        lines.append(f"업종/용도: {item['category']}")

    certs = detect_certificate(item)
    if certs:
        lines.append("권리 형태: " + " · ".join(ko for _, ko in certs))

    lines.extend(extract_facts(item))
    return lines
