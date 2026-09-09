# -*- coding: utf-8 -*-
"""
build_fixed.py  —  Market Calendar CHECK (fixed schedule builder)

Generates the "fixed" part of the calendar: exchange holidays, monetary policy
meetings, derivatives expiries, index review / rebalance dates, statistical
releases and Korean tax-deadline items.

No network access required. Run:
    python3 scripts/build_fixed.py

Outputs
    data/fixed.json   (machine readable)
    data/fixed.js     (window.FIXED_EVENTS = [...]  -> works over file:// too)

Status convention
    confirmed : official calendar already published by the source institution
    estimated : derived from a published rule (2nd Thursday, 3rd Friday,
                "first Friday of the month", "T+21 days") or from the usual
                pattern of previous years. Always verify before client use.
"""

import json
import os
from calendar import monthrange
from datetime import date, timedelta

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
START = date(2026, 9, 1)
END = date(2027, 6, 30)

EVENTS = []


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def add(d, market, cat, title, imp=2, status="confirmed", **kw):
    if isinstance(d, str):
        d = date.fromisoformat(d)
    if d < START or d > END:
        return
    ev = {
        "id": f"{cat}-{d.isoformat()}-{len(EVENTS)}",
        "date": d.isoformat(),
        "market": market,
        "cat": cat,
        "title": title,
        "imp": imp,
        "status": status,
    }
    ev.update({k: v for k, v in kw.items() if v not in (None, "", [], {})})
    EVENTS.append(ev)
    return ev


def nth_weekday(y, m, weekday, n):
    """n-th (1-based) weekday of a month. weekday: Mon=0 ... Sun=6"""
    d = date(y, m, 1)
    shift = (weekday - d.weekday()) % 7
    return d + timedelta(days=shift + 7 * (n - 1))


def last_weekday(y, m, weekday):
    d = date(y, m, monthrange(y, m)[1])
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def months(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


# --------------------------------------------------------------------------
# 1. Exchange holidays
# --------------------------------------------------------------------------
# KRX — 2026 is the officially announced calendar; 2027 is a rule-based estimate
KR_HOLIDAYS = [
    ("2026-09-24", "추석 연휴 (한국 휴장)", "confirmed"),
    ("2026-09-25", "추석 (한국 휴장)", "confirmed"),
    ("2026-10-05", "개천절 대체공휴일 (한국 휴장)", "confirmed"),
    ("2026-10-09", "한글날 (한국 휴장)", "confirmed"),
    ("2026-12-25", "성탄절 (한국 휴장)", "confirmed"),
    ("2026-12-31", "연말 휴장 — 2026년 마지막 거래일은 12/30", "confirmed"),
    ("2027-01-01", "신정 (한국 휴장)", "confirmed"),
    ("2027-02-05", "설날 연휴 (한국 휴장, 잠정)", "estimated"),
    ("2027-02-08", "설날 연휴·대체공휴일 (한국 휴장, 잠정)", "estimated"),
    ("2027-03-01", "삼일절 (한국 휴장, 잠정)", "estimated"),
    ("2027-05-05", "어린이날 (한국 휴장, 잠정)", "estimated"),
    ("2027-05-13", "부처님오신날 (한국 휴장, 잠정)", "estimated"),
]
KR_HOLIDAY_SET = {h[0] for h in KR_HOLIDAYS}

# NYSE / Nasdaq
US_HOLIDAYS = [
    ("2026-11-26", "Thanksgiving (미국 휴장)", "confirmed"),
    ("2026-12-25", "Christmas (미국 휴장)", "confirmed"),
    ("2027-01-01", "New Year's Day (미국 휴장)", "confirmed"),
    ("2027-01-18", "Martin Luther King Jr. Day (미국 휴장)", "confirmed"),
    ("2027-02-15", "Presidents' Day (미국 휴장)", "confirmed"),
    ("2027-03-26", "Good Friday (미국 휴장)", "confirmed"),
    ("2027-05-31", "Memorial Day (미국 휴장)", "confirmed"),
    ("2027-06-18", "Juneteenth 대체휴일 (미국 휴장)", "confirmed"),
]
US_EARLY_CLOSE = [
    ("2026-11-27", "미국 조기폐장 13:00 ET (KST 03:00) — 추수감사절 다음 거래일"),
    ("2026-12-24", "미국 조기폐장 13:00 ET (KST 03:00) — 크리스마스 이브"),
]

for d, t, st in KR_HOLIDAYS:
    add(d, "KR", "market", t, imp=3, status=st,
        desc="한국 증시 휴장. 결제일(T+2)이 밀리므로 배당·권리 기준일, 해외주식 환전·결제 일정과 함께 확인해야 합니다.",
        checklist=["휴장 전 마지막 거래일 기준 결제·환전 일정 안내",
                   "미국 시장은 정상 개장 여부 확인 (야간 대응 필요 고객 사전 안내)",
                   "연휴 중 해외 이벤트(FOMC·지표) 발생 시 갭 리스크 사전 고지"],
        source="KRX 휴장일 안내")

for d, t, st in US_HOLIDAYS:
    add(d, "US", "market", t, imp=3, status=st,
        desc="미국 증시(NYSE·Nasdaq) 휴장. 국내 야간 주문·환전 스케줄에 영향.",
        checklist=["해외주식 주문 접수 가능 여부 안내", "휴장 전후 유동성 축소 구간 주의"],
        source="NYSE Holiday Calendar")

for d, t in US_EARLY_CLOSE:
    add(d, "US", "market", t, imp=2, status="confirmed",
        desc="미국 정규장이 미 동부시간 13:00에 조기 종료됩니다. 거래량이 급감해 체결 슬리피지가 커집니다.",
        checklist=["대량 주문은 조기폐장 전 분할 체결", "종가 기준 리밸런싱 주문은 하루 앞당겨 처리"],
        source="NYSE Holiday Calendar")

# US daylight saving time -> Korean trading hours shift
add("2026-11-01", "US", "market", "미국 서머타임 해제 — 정규장 23:30~06:00 (KST)", imp=3,
    desc="미 동부시간이 EST로 전환되어 한국시간 기준 정규장이 1시간 늦어집니다. "
         "프리마켓·애프터마켓 시간, 실적 발표 시각(장전/장후) 환산 기준도 함께 바뀝니다.",
    checklist=["고객 안내 문구의 미국장 시간 표기 일괄 수정",
               "장전 실적 = 한국시간 22:30 이전 → 23:30 이전으로 기준 변경",
               "야간 자동주문·조건부 주문 시간 재설정"],
    source="US DST (첫째 일요일, 11월)")
add("2027-03-14", "US", "market", "미국 서머타임 시작 — 정규장 22:30~05:00 (KST)", imp=3,
    desc="미 동부시간이 EDT로 전환되어 한국시간 기준 정규장이 1시간 빨라집니다.",
    checklist=["고객 안내 문구의 미국장 시간 표기 일괄 수정", "야간 자동주문 시간 재설정"],
    source="US DST (둘째 일요일, 3월)")


def is_kr_bday(d):
    return d.weekday() < 5 and d.isoformat() not in KR_HOLIDAY_SET


def next_kr_bday(d):
    d += timedelta(days=1)
    while not is_kr_bday(d):
        d += timedelta(days=1)
    return d


# --------------------------------------------------------------------------
# 2. Monetary policy
# --------------------------------------------------------------------------
FOMC = [  # (day1, day2, has_SEP)
    ("2026-09-15", "2026-09-16", True),
    ("2026-10-27", "2026-10-28", False),
    ("2026-12-08", "2026-12-09", True),
]
for d1, d2, sep in FOMC:
    dd2 = date.fromisoformat(d2)
    add(d1, "US", "policy", "FOMC 1일차 (회의 시작)", imp=2,
        desc="정책 결정은 2일차 미 동부시간 14:00(한국시간 익일 새벽)에 공개됩니다.",
        source="Federal Reserve")
    add(d2, "US", "policy",
        "FOMC 금리 결정" + (" + 점도표·경제전망(SEP)" if sep else "") + " — 한국시간 익일 03:00/04:00",
        imp=3,
        desc=("연방기금금리 목표범위 결정. 성명서 14:00 ET, 의장 기자회견 14:30 ET. "
              + ("점도표(SEP)가 함께 공개되는 회의로, 금리 경로 재평가에 따른 변동성이 가장 큰 이벤트입니다."
                 if sep else "점도표는 공개되지 않습니다.")),
        checklist=["CME FedWatch 선물 반영 확률 확인 후 고객 기대치 조정",
                   "채권·달러·금 보유 고객별 시나리오 멘트 사전 준비",
                   "회의 전 2주간 연준 인사 발언(블랙아웃 진입 전) 정리",
                   "결과 발표가 한국시간 새벽인 점 감안, 다음 영업일 오전 안내 예약"],
        links=[{"label": "Fed 회의 일정", "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"},
               {"label": "CME FedWatch", "url": "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"}],
        source="Federal Reserve")
    add(dd2 + timedelta(days=21), "US", "policy", "FOMC 의사록 공개 (회의 3주 후)", imp=2,
        status="estimated",
        desc="직전 회의 의사록. 위원별 견해차와 대차대조표 논의가 드러나 중기 금리 전망 수정 요인이 됩니다.",
        source="Federal Reserve (규칙: 결정일 +21일)")
    # blackout period starts on the 2nd Saturday preceding the meeting's day 1
    dd1 = date.fromisoformat(d1)
    add(dd1 - timedelta(days=((dd1.weekday() - 5) % 7) + 7), "US", "policy",
        "연준 블랙아웃 기간 시작 (회의 전 발언 중단)", imp=1, status="estimated",
        desc="이 시점부터 연준 인사의 통화정책 관련 공개 발언이 중단됩니다. 발언 재료가 사라지므로 지표에 대한 시장 민감도가 높아집니다.",
        source="Fed 관행 (회의 2주 전 토요일)")

BOK_MPC = [("2026-10-22", "2026-11-10"), ("2026-11-26", "2026-12-15")]
for d, minute in BOK_MPC:
    add(d, "KR", "policy", "한국은행 금통위 — 기준금리 결정", imp=3,
        desc="통화정책방향 결정회의. 09:00 회의 후 통방문 발표, 이어서 총재 기자간담회. "
             "2026년 8월 회의에서 기준금리를 3.00%로 인상한 이후의 경로가 관건입니다.",
        checklist=["환율·가계부채·물가 흐름 기준 인상/동결 시나리오 정리",
                   "채권·예금 상품 보유 고객 대상 금리 재투자 안내 준비",
                   "결정 직후 총재 기자간담회 톤 확인 후 코멘트 발송"],
        links=[{"label": "한국은행 통화정책방향", "url": "https://www.bok.or.kr/portal/main/contents.do?menuNo=200755"}],
        source="한국은행 2026년 금통위 일정")
    add(minute, "KR", "policy", "금통위 의사록 공개", imp=1, status="estimated",
        desc="회의일로부터 2주 경과 후 첫 화요일 공개(관행). 소수의견 유무가 다음 회의 프라이싱에 직접 반영됩니다.",
        source="한국은행 (규칙 기반)")

add("2026-09-24", "KR", "policy", "한국은행 금융안정회의 (금리 결정 없음)", imp=1, status="estimated",
    desc="금융안정 상황 점검 회의로 기준금리는 결정하지 않습니다. 가계부채·부동산 금융 관련 메시지가 나옵니다.",
    source="한국은행 (3·6·9·12월)")

for d, t in [("2026-10-29", "BOJ 금융정책결정회의 (잠정)"), ("2026-12-18", "BOJ 금융정책결정회의 (잠정)"),
             ("2026-10-29", "ECB 통화정책회의 (잠정)"), ("2026-12-17", "ECB 통화정책회의 (잠정)")]:
    add(d, "GLOBAL", "policy", t, imp=2, status="estimated",
        desc="엔·유로 방향과 글로벌 유동성에 영향. 특히 BOJ 인상은 엔캐리 청산 경로로 국내 수급에 전이됩니다.",
        source="각 중앙은행 공표 일정 (확정 전 잠정)")


# --------------------------------------------------------------------------
# 3. Derivatives expiry / index events
# --------------------------------------------------------------------------
for y, m in months(START, END):
    # Korea: 2nd Thursday
    kr = nth_weekday(y, m, 3, 2)
    quarterly = m in (3, 6, 9, 12)
    add(kr, "KR", "market",
        ("코스피200 선물·옵션 동시만기 (분기)" if quarterly else "코스피200 옵션·월물 만기"),
        imp=3 if quarterly else 2,
        desc=("분기 동시만기일. 지수선물·옵션이 함께 만기되어 만기 전후 프로그램 매매와 베이시스 변동이 커집니다."
              if quarterly else "월물 옵션 만기. 만기 주 변동성 확대 구간."),
        checklist=["미결제약정·베이시스 확인 후 만기 수급 방향 점검",
                   "만기일 종가 부근 프로그램 매매 집중 구간 주문 회피"],
        source="KRX (매월 두 번째 목요일)")
    # US: 3rd Friday
    us = nth_weekday(y, m, 4, 3)
    add(us, "US", "market",
        ("쿼드러플 위칭 — 미국 지수·개별주 선물·옵션 동시만기" if quarterly else "미국 옵션 만기 (3rd Friday)"),
        imp=3 if quarterly else 2,
        desc=("지수선물·지수옵션·개별주선물·개별주옵션이 동시에 만기되는 날. 종가 거래량이 폭증하며 "
              "S&P 다우존스 분기 리밸런싱과 겹칩니다." if quarterly
              else "월간 옵션 만기. 대형 옵션 포지션(감마) 소멸로 만기 후 방향성이 바뀌는 경우가 많습니다."),
        checklist=["만기 주 종가 부근 대량주문 분할", "만기 후 첫 거래일 갭 대응 안내"],
        source="CBOE·CME (매월 세 번째 금요일)")
    if quarterly:
        add(us, "US", "index", "S&P 다우존스 지수 분기 리밸런싱 적용 (종가)", imp=3,
            desc="S&P500·400·600의 유동주식비율(float)·주식수 갱신과 편입·편출이 이 날 종가에 반영됩니다. "
                 "패시브 자금이 종가 단일가에 집중되므로 대상 종목의 종가 거래량이 평소의 수 배로 늘어납니다.",
            checklist=["편입·편출 종목 리스트 확인 (발표는 통상 리밸런싱 월 첫째 주 금요일)",
                       "보유 고객 종목이 대상인 경우 종가 이벤트 사전 안내"],
            links=[{"label": "S&P DJI 인덱스 뉴스", "url": "https://www.spglobal.com/spdji/en/index-announcements/"}],
            source="S&P Dow Jones Indices (규칙: 3월·6월·9월·12월 세 번째 금요일)")
        add(nth_weekday(y, m, 4, 1), "US", "index", "S&P 분기 리밸런싱 종목 발표 (잠정)", imp=2,
            status="estimated",
            desc="분기 리밸런싱 대상(편입·편출·주식수 변경)이 통상 이 시점 장 마감 후 발표됩니다. "
                 "발표 당일 애프터마켓에서 편입 종목이 급등하는 패턴이 반복됩니다.",
            source="S&P DJI (관행: 리밸런싱 월 첫째 금요일)")

# KRX regular index review / rebalance — 3·6·9·12월 선물 만기일 다음 거래일
for y, m in months(START, END):
    if m not in (3, 6, 9, 12):
        continue
    exp = nth_weekday(y, m, 3, 2)          # 선물·옵션 만기 (둘째 목요일)
    eff = next_kr_bday(exp)                # 정기변경 적용일
    review = m in (6, 12)                  # 구성종목 편입·편출은 6월·12월
    add(eff, "KR", "index",
        "KRX 정기변경 적용 — 코스피200·코스닥150 구성종목 변경" if review
        else "KRX 지수 정기변경 적용 — 비중·유동주식비율·종목 Cap 재적용", imp=3,
        desc=("코스피200·코스닥150 정기변경이 적용되는 날(선물 만기일 다음 거래일). 전일 종가에 패시브 "
              "리밸런싱이 집중되고, 편입 예상 종목은 발표일부터 적용일까지 선반영되는 경향이 있습니다."
              if review else
              "구성종목 변경은 없지만 유동주식비율과 종목별 비중 상한(Cap)이 재적용되는 분기 정기변경일입니다. "
              "삼성전자·SK하이닉스처럼 지수 내 비중이 큰 종목에서 Cap 재적용에 따른 패시브 매수·매도가 "
              "발생하므로, 구성종목 변경이 없는 분기에도 수급 이벤트로 확인해야 합니다."),
        checklist=(["편입·편출 확정 종목과 지수 추종자금 규모 확인",
                    "예상 수급(추종자금×비중변화) 대비 거래대금 배수 점검",
                    "선반영 여부 확인 후 적용일 차익 실현/신규 진입 판단"] if review else
                   ["비중 상한 적용 종목(삼성전자·SK하이닉스 등)의 비중 변화 확인",
                    "예상 수급(추종자금×비중변화) 대비 거래대금 배수 점검",
                    "적용 전일 종가 집중 매매 구간 주문 회피"]),
        links=[{"label": "지수 정기변경 트래커",
                "url": "https://bluelagoon1222.github.io/Index-Rebalance-Korea/"}],
        source="KRX 지수 정기변경 규정 (선물 만기일 다음 거래일)")
    add(exp, "KR", "index",
        "KRX 정기변경 리밸런싱 (만기일 종가 집행)", imp=3,
        desc="정기변경 반영을 위한 패시브 리밸런싱이 만기일 종가 단일가에 실행됩니다. "
             "대상 종목의 종가 거래량이 평소의 수 배로 늘어납니다.",
        source="KRX")

add("2026-11-20", "KR", "index", "KRX 정기변경 종목 발표 예상 (잠정)", imp=2, status="estimated",
    desc="심사기준일(10월 말) 기준 시가총액·거래대금 순위로 정기변경 종목이 결정되어 11월 중 발표됩니다. "
         "발표 시점이 실제 수급의 출발점입니다.",
    source="KRX (관행: 11월 중순~하순)")

add("2026-12-11", "US", "index", "나스닥100 연간 재구성 발표 예상 (잠정)", imp=3, status="estimated",
    desc="나스닥100은 매년 12월 시가총액 기준으로 연간 재구성됩니다. 통상 12월 둘째 금요일 장 마감 후 발표되고, "
         "세 번째 금요일 종가에 리밸런싱이 실행됩니다.",
    checklist=["편입 후보(시총 상위 비나스닥100 종목) 사전 점검", "QQQ 추종자금 규모 대비 예상 수급 산출"],
    source="Nasdaq (관행)")
add("2026-12-18", "US", "index", "나스닥100 연간 재구성 적용 (종가 리밸런싱)", imp=3, status="estimated",
    desc="재구성이 이 날 종가에 반영되어 익영업일 개장 전 확정됩니다.",
    source="Nasdaq (관행)")

add("2026-11-10", "GLOBAL", "index", "MSCI 11월 반기 리뷰 발표 예상 (잠정)", imp=3, status="estimated",
    desc="MSCI 지수 정기변경 발표. 한국물 편입·편출은 외국인 패시브 수급에 직결됩니다.",
    checklist=["편입·편출 후보 종목의 외국인 지분율·유동시총 점검"],
    source="MSCI (관행: 11월 중순 발표)")
add("2026-11-30", "GLOBAL", "index", "MSCI 11월 리뷰 적용 (종가 리밸런싱, 잠정)", imp=3, status="estimated",
    desc="발표된 정기변경이 이 날 종가 기준으로 적용됩니다. 외국인 종가 대량 매매가 집중됩니다.",
    source="MSCI (관행)")
add("2026-12-18", "GLOBAL", "index", "FTSE GEIS 12월 리뷰 적용 (잠정)", imp=2, status="estimated",
    desc="FTSE 글로벌 지수 정기변경 적용일. MSCI 대비 규모는 작지만 중소형주에서는 영향이 큽니다.",
    source="FTSE Russell (관행: 3월·6월·9월·12월 세 번째 금요일)")


# --------------------------------------------------------------------------
# 4. Macro releases
# --------------------------------------------------------------------------
for y, m in months(START, END):
    # US
    nfp = nth_weekday(y, m, 4, 1)
    add(nfp, "US", "macro", "미국 고용보고서 (비농업 취업자·실업률) 08:30 ET", imp=3, status="estimated",
        desc="연준 정책 판단의 1차 재료. 발표 시각은 한국시간 21:30(서머타임)/22:30(해제 후)입니다.",
        checklist=["예상치 대비 서프라이즈 시 금리·달러 반응 시나리오 준비"],
        source="BLS (관행: 매월 첫째 금요일)")
    add(date(y, m, 1) + timedelta(days=(1 - date(y, m, 1).weekday()) % 7 + 7) + timedelta(days=1),
        "US", "macro", "미국 CPI 소비자물가 08:30 ET (잠정)", imp=3, status="estimated",
        desc="발표일은 매월 BLS 공표 일정에 따라 10~15일 사이로 변동합니다. 실제 일정은 BLS 캘린더로 확인이 필요합니다.",
        source="BLS (잠정 추정)")
    add(last_weekday(y, m, 4), "US", "macro", "미국 PCE 물가·개인소비 (잠정)", imp=2, status="estimated",
        desc="연준이 목표로 삼는 물가지표. 월말 발표.",
        source="BEA (잠정 추정)")
    add(nth_weekday(y, m, 0, 1) if nth_weekday(y, m, 0, 1).day <= 3 else date(y, m, 1),
        "US", "macro", "미국 ISM 제조업 PMI (잠정)", imp=1, status="estimated",
        source="ISM (잠정 추정)")
    # Korea
    add(date(y, m, 1), "KR", "macro", "한국 수출입 동향 (관세청·산업부) 09:00", imp=3, status="estimated",
        desc="월초 발표되는 국내 최초 실물 지표. 반도체·자동차 수출 증감률이 코스피 이익 전망의 선행지표로 쓰입니다.",
        checklist=["반도체 수출 금액·단가 증감 확인 후 반도체 비중 고객 코멘트"],
        source="관세청 (매월 1일)")
    add(date(y, m, 2), "KR", "macro", "한국 소비자물가동향 (통계청) 08:00", imp=2, status="estimated",
        source="통계청 (관행: 매월 초)")
    add(date(y, m, 11), "KR", "macro", "관세청 수출입 10일 잠정치", imp=1, status="estimated",
        desc="월중 수출 흐름을 가장 먼저 확인할 수 있는 자료(1~10일 누계).",
        source="관세청 (매월 11일 전후)")
    add(last_weekday(y, m, 4) - timedelta(days=1), "KR", "macro", "산업활동동향 (통계청, 잠정)", imp=1,
        status="estimated", source="통계청 (월말)")


# --------------------------------------------------------------------------
# 5. Earnings-season markers
# --------------------------------------------------------------------------
SEASON = [
    ("2026-10-08", "삼성전자 3Q 잠정실적 발표 예상", "KR",
     "삼성전자는 분기 종료 후 약 1주일 시점에 매출·영업이익 잠정치를 발표합니다. 국내 실적 시즌의 시작점이며, "
     "반도체 업종 전반의 컨센서스 조정 트리거가 됩니다."),
    ("2026-10-13", "미국 대형은행 실적 시작 — 어닝 시즌 개막", "US",
     "JPM·GS 등 대형은행 실적으로 미국 3분기 어닝 시즌이 시작됩니다. 순이자마진·대출 연체율이 경기 판단 재료입니다."),
    ("2026-10-27", "미국 빅테크 실적 주간 시작 (잠정)", "US",
     "MS·알파벳·메타·애플·아마존이 한 주에 집중되는 구간입니다. AI 설비투자(CapEx) 가이던스가 반도체 밸류체인 전체에 파급됩니다."),
    ("2026-11-18", "엔비디아 실적 발표 예상 (장 마감 후)", "US",
     "AI 밸류체인 전체의 방향을 정하는 단일 이벤트. 발표 시각은 미국 장 마감 후로, 국내 반도체주는 다음 날 오전에 반응합니다."),
    ("2027-01-08", "삼성전자 4Q 잠정실적 발표 예상", "KR",
     "연간 실적 확정 전 잠정치. 배당 규모 추정과 연결됩니다."),
]
for d, t, mk, desc in SEASON:
    add(d, mk, "earnings", t, imp=3, status="estimated", desc=desc,
        checklist=["컨센서스와 직전 분기 실적 비교표 준비", "서프라이즈/쇼크 각각의 고객 안내 문구 사전 작성"],
        source="과거 발표 패턴 기반 추정")

add("2026-10-12", "US", "event", "TSMC 9월 매출 발표 (매월 10일 전후)", imp=2, status="estimated",
    desc="월간 매출을 가장 먼저 공개하는 반도체 대형주. AI 서버 수요의 실시간 지표로 국내 반도체·소재주에 즉시 반영됩니다.",
    source="TSMC (매월 10일 전후)")


# --------------------------------------------------------------------------
# 6. Tax / regulatory deadlines (PB 관점)
# --------------------------------------------------------------------------
TAX = [
    ("2026-12-23", 3, "해외주식 양도세 손실 상계 매도 마감 (권장)",
     "해외주식 양도차익은 결제일(T+2, 미국은 T+1) 기준 연도로 귀속됩니다. 손실 종목을 이익과 상계하려면 "
     "연말 마지막 결제 가능일을 역산해 매도해야 합니다. 환율 적용일까지 감안하면 12월 하순 이전 처리가 안전합니다.",
     ["고객별 해외주식 실현손익 집계 후 상계 여력 산출", "연 250만원 기본공제 활용 여부 점검",
      "재매수 계획이 있는 종목은 결제일·환율 변동 리스크 함께 설명"]),
    ("2026-12-29", 3, "국내주식 대주주 요건 판정 기준일 (연말 최종 보유 기준)",
     "연말 기준 보유 지분율·평가금액으로 다음 연도 대주주 여부가 결정됩니다. 판정되면 양도소득세 과세 대상이 됩니다.",
     ["가족 합산 지분 포함 보유금액 점검", "판정 회피가 필요한 고객은 결제일 기준으로 역산해 매도 일정 확정"]),
    ("2026-12-30", 3, "연금저축·IRP 세액공제 납입 마감 / 2026년 최종 거래일",
     "연금계좌 납입은 12월 31일까지지만, 영업일과 이체 처리 시간을 고려하면 최종 거래일까지 완료해야 안전합니다. "
     "동시에 국내주식 결산배당 기준일이 몰리는 날입니다.",
     ["연금계좌 납입 한도(연 1,800만원)·세액공제 한도(900만원) 대비 잔여 여력 안내",
      "ISA 연간 납입한도 소멸 안내", "배당 기준일 보유 여부 확인"]),
    ("2027-01-04", 2, "2027년 ISA·연금계좌 납입 한도 리셋",
     "연초 납입 여력이 새로 생깁니다. 연초 일시납 vs 분할납 전략을 고객 성향에 맞춰 제안할 시점입니다.", []),
    ("2027-02-26", 2, "금융소득 지급명세서 제출 마감 (원천징수 기준)",
     "전년도 금융소득이 확정되어 종합과세 대상 여부가 드러나는 시점입니다.",
     ["2천만원 초과 고객 사전 추출 후 건강보험료 영향 시뮬레이션"]),
    ("2027-05-31", 3, "종합소득세·해외주식 양도소득세 신고 마감",
     "금융소득종합과세 대상 고객과 해외주식 양도차익 발생 고객의 신고 기한입니다.",
     ["대상 고객 리스트업 후 4월 중 사전 안내", "손익 통산·이월 여부 확인"]),
    ("2027-06-01", 3, "재산세·종합부동산세 과세기준일",
     "이 날 소유자가 1년치 보유세를 부담합니다. 매도 예정 고객은 기준일 이전 잔금 처리가 유리합니다.",
     ["부동산 보유 고객의 잔금일 조정 필요 여부 확인"]),
]
for d, imp, t, desc, ck in TAX:
    add(d, "KR", "tax", t, imp=imp, desc=desc, checklist=ck, status="confirmed",
        source="세법·실무 관행")


# --------------------------------------------------------------------------
# 7. Recurring events / conferences
# --------------------------------------------------------------------------
EVT = [
    ("2026-11-05", "GLOBAL", "미국 재무부 국채 리펀딩 발표 (잠정)", 2,
     "발행 만기 구성(장기물 비중)이 장기금리에 직접 영향을 줍니다.", "US Treasury (분기)"),
    ("2027-01-06", "GLOBAL", "CES 2027 개막 (라스베이거스, 1/6~1/9)", 2,
     "AI·로봇·모빌리티 테마의 연초 재료가 집중됩니다. 국내 부품주 동반 반응 구간.", "CTA"),
    ("2026-12-01", "GLOBAL", "OPEC+ 정례회의 (잠정)", 1,
     "감산·증산 결정이 유가와 정유·화학 마진에 반영됩니다.", "OPEC"),
    ("2027-03-05", "GLOBAL", "중국 양회 개막 (잠정)", 2,
     "성장률 목표와 부양책 규모가 발표됩니다. 중국 소비·소재 관련 국내 종목에 직접 영향.", "관행"),
]
for d, mk, t, imp, desc, src in EVT:
    add(d, mk, "event", t, imp=imp, status="estimated", desc=desc, source=src)


# --------------------------------------------------------------------------
# write
# --------------------------------------------------------------------------
EVENTS.sort(key=lambda e: (e["date"], -e["imp"], e["title"]))
payload = {
    "generated_at": date.today().isoformat(),
    "range": [START.isoformat(), END.isoformat()],
    "count": len(EVENTS),
    "events": EVENTS,
}
os.makedirs(OUT_DIR, exist_ok=True)
with open(os.path.join(OUT_DIR, "fixed.json"), "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False, indent=1)
with open(os.path.join(OUT_DIR, "fixed.js"), "w", encoding="utf-8") as f:
    f.write("window.FIXED_DATA = ")
    json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    f.write(";\n")
print(f"fixed events: {len(EVENTS)}  ({START} ~ {END})")
