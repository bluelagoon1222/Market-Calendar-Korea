# -*- coding: utf-8 -*-
"""
collect.py  v2 — Market Calendar CHECK (dynamic data collector)

v1 실행(2026-09-09) 결과 반영
  · universe.SP500=0 / NDX=2      → <tr> 정규식이 속성 있는 태그를 놓쳤음. 소스 4단계 폴백으로 교체
  · kr_universe=0                 → KRX 다운로드 차단. 네이버 시가총액 + 필터 방식으로 교체
  · dart_list status 100 (4건)     → corp_code 없이 3개월 초과 조회 불가. 종목별 조회로 전환
  · corp 452건 중 54건만 이벤트화  → 날짜 필드 라벨 확장 + 신탁계약 서식 분리
  · 전환사채(CB) 건수 과다        → CB·BW·EB 추적 제외, 기업행위는 시총상위 220종목으로 한정 (요청)

v3 (v2 실행 결과 반영)
  · kr_universe 97종목            → 네이버 페이지 중복 판정을 HTML 앞부분으로 해 2·3페이지가 스킵됨.
                                    신규 종목코드 유입 여부로 판정, 코스피 150 / 코스닥 70 할당
  · universe.wiki.NDX=2           → 위키 표 구조가 S&P500 과 달라 정규식 실패.
                                    위키 API 원문 → 슬릭차트 → 고정 목록 3단계로 교체

국내 실적발표일은 사전 공표 제도가 없어 전 종목 '잠정(추정)'으로 산출한다.
확정값은 DART 결산실적공시예고가 있는 기업만 표시한다.
"""

import io
import json
import os
import re
import sys
import time
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DART_KEY = os.environ.get("DART_API_KEY", "").strip()
BUDGET = int(os.environ.get("TIME_BUDGET", "2400"))
T0 = time.time()

HORIZON = 100        # 향후 며칠까지 일정을 담을지
KR_MARKETS = ((0, "코스피 시총상위", 8, 150),   # (네이버 sosok, 라벨, 최대 페이지, 종목 상한)
              (1, "코스닥 시총상위", 6, 70))
KR_TOP = sum(m[3] for m in KR_MARKETS)
HIST_YEARS = 2       # 국내 실적발표일 추정에 쓸 과거 공시 이력

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}
STATUS = {"version": "v2", "started_at": datetime.now().isoformat(timespec="seconds"),
          "steps": {}, "errors": [], "samples": {}}


def left():
    return BUDGET - (time.time() - T0)


def log(m):
    print(f"[{int(time.time() - T0):5d}s] {m}", flush=True)


def note(step, **kw):
    STATUS["steps"][step] = kw
    log(f"{step}: {kw}")


def err(step, e):
    STATUS["errors"].append(f"{step}: {type(e).__name__} {e}")
    log(f"!! {step} failed: {e}")


def get(url, **kw):
    kw.setdefault("headers", UA)
    kw.setdefault("timeout", 20)
    return requests.get(url, **kw)


def load(name, default):
    try:
        with open(os.path.join(DATA, name + ".json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(name, obj, var=None):
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, name + ".json"), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    if var:
        with open(os.path.join(DATA, name + ".js"), "w", encoding="utf-8") as f:
            f.write(f"window.{var} = ")
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
            f.write(";\n")


def bday(d):
    while d.weekday() > 4:
        d += timedelta(days=1)
    return d


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s).replace("&amp;", "&").strip()


# ==========================================================================
# A. 미국 유니버스 — 4단계 폴백
# ==========================================================================
def _wiki_symbols(url, col_hint=(0, 1)):
    """위키피디아 표에서 티커 추출. <tr>/<td> 에 속성이 붙어도 잡히도록 수정(v1 버그)"""
    html = get(url).text
    syms = set()
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)
        for i in col_hint:
            if i < len(cells):
                t = strip_tags(cells[i])
                if re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", t) and t not in ("I", "A"):
                    syms.add(t.replace(".", "-"))
                    break
    return syms


NDX_FALLBACK = (
    "AAPL MSFT NVDA AMZN META AVGO GOOGL GOOG TSLA COST NFLX AMD PEP ADBE LIN CSCO TMUS "
    "QCOM INTU TXN AMAT ISRG BKNG AMGN HON VRTX PANW ADP MU ADI GILD LRCX MELI SBUX INTC "
    "MDLZ REGN KLAC CTAS SNPS CDNS MAR CRWD ORLY CSX ASML PYPL ABNB MRVL FTNT ADSK WDAY "
    "NXPI CHTR PCAR ROP MNST AEP PAYX TTD ODFL FAST KDP ROST VRSK CTSH DDOG EXC XEL GEHC "
    "CCEP KHC CSGP AZN IDXX ON BIIB CDW MDB GFS ARM APP PLTR AXON"
).split()


def us_universe():
    uni = defaultdict(set)

    # 1) 공개 CSV (가장 안정적)
    csvs = [
        ("SP500", "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"),
        ("SP500", "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"),
    ]
    for tag, url in csvs:
        if len(uni) >= 400:
            break
        try:
            txt = get(url, timeout=25).text
            n = 0
            for line in txt.splitlines()[1:]:
                s = line.split(",")[0].strip().strip('"').upper()
                if re.fullmatch(r"[A-Z]{1,5}([.\-][A-Z])?", s):
                    uni[s.replace(".", "-")].add(tag)
                    n += 1
            note("universe.csv", source=url.split("/")[-3], tickers=n)
        except Exception as e:
            err("universe.csv", e)

    # 2) 위키피디아 (S&P500 표는 이 방식이 안정적)
    try:
        sp = _wiki_symbols("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", (0, 1))
        for t in sp:
            uni[t].add("SP500")
        note("universe.wiki.SP500", tickers=len(sp))
    except Exception as e:
        err("universe.wiki.SP500", e)

    # 2-1) 나스닥100 — 위키 원문 → 슬릭차트 → 고정 목록
    ndx, ndx_src = set(), ""
    try:
        j = get("https://en.wikipedia.org/w/api.php",
                params={"action": "parse", "page": "Nasdaq-100", "prop": "wikitext",
                        "format": "json", "formatversion": "2"}, timeout=25).json()
        wt = (j.get("parse") or {}).get("wikitext") or ""
        cand = set(re.findall(r"\|\s*([A-Z]{1,5})\s*\|\|", wt))
        cand |= set(re.findall(r"\{\{\s*(?:NASDAQ|Nasdaq)\s*\|\s*([A-Z]{1,5})\s*\}\}", wt))
        if len(cand) >= 80:
            ndx, ndx_src = cand, "wikitext"
    except Exception as e:
        err("universe.ndx.wikitext", e)
    if not ndx:
        try:
            html = get("https://www.slickcharts.com/nasdaq100", timeout=25).text
            cand = set(re.findall(r'/symbol/([A-Z]{1,5})"', html))
            if len(cand) >= 80:
                ndx, ndx_src = cand, "slickcharts"
        except Exception as e:
            err("universe.ndx.slickcharts", e)
    if not ndx:
        ndx, ndx_src = set(NDX_FALLBACK), "fallback"
    tag = "NDX" if ndx_src != "fallback" else "NDX(추정)"
    for t in ndx:
        uni[t].add(tag)
    note("universe.ndx", tickers=len(ndx), source=ndx_src)

    # 3) 직전 실행 결과 유지
    prev = load("us_universe", {})
    if len(uni) < 400 and prev:
        for k, v in prev.items():
            uni[k].update(v)
        note("universe.prev_kept", tickers=len(prev))

    # 4) 최후 폴백 — 나스닥 스크리너에서 시가총액 100억달러 이상
    if len(uni) < 300:
        try:
            r = get("https://api.nasdaq.com/api/screener/stocks",
                    params={"tableonly": "true", "limit": "8000", "offset": "0"},
                    headers={**UA, "Accept": "application/json",
                             "Origin": "https://www.nasdaq.com",
                             "Referer": "https://www.nasdaq.com/"}, timeout=40)
            rows = ((r.json().get("data") or {}).get("table") or {}).get("rows") or []
            n = 0
            for row in rows:
                mc = re.sub(r"[^0-9]", "", str(row.get("marketCap") or "")) or "0"
                if int(mc) >= 10_000_000_000:
                    s = (row.get("symbol") or "").strip().upper()
                    if re.fullmatch(r"[A-Z]{1,5}", s):
                        uni[s].add("LARGE")
                        n += 1
            note("universe.screener", tickers=n)
        except Exception as e:
            err("universe.screener", e)

    out = {k: sorted(v) for k, v in uni.items()}
    note("universe.total", tickers=len(out))
    STATUS["samples"]["us_universe"] = sorted(out)[:12]
    if out:
        save("us_universe", out)
    return out


# ==========================================================================
# B. 미국 실적발표일 (장전/장후 포함)
# ==========================================================================
SESSION_MAP = {"time-pre-market": "pre", "time-after-hours": "post",
               "time-not-supplied": "unknown", "": "unknown"}


def us_earnings(uni):
    events, raw_rows, matched = [], 0, 0
    d, end = date.today(), date.today() + timedelta(days=HORIZON)
    while d <= end:
        if d.weekday() > 4:
            d += timedelta(days=1)
            continue
        if left() < 300:
            note("us_earnings.stopped_at", date=d.isoformat())
            break
        try:
            r = get("https://api.nasdaq.com/api/calendar/earnings",
                    params={"date": d.isoformat()},
                    headers={**UA, "Accept": "application/json",
                             "Origin": "https://www.nasdaq.com",
                             "Referer": "https://www.nasdaq.com/market-activity/earnings"})
            rows = (r.json().get("data") or {}).get("rows") or []
        except Exception as e:
            err(f"nasdaq.{d}", e)
            rows = []
        raw_rows += len(rows)
        for row in rows:
            sym = (row.get("symbol") or "").strip().upper()
            idx = uni.get(sym)
            if idx is None:
                continue
            matched += 1
            mc = int(re.sub(r"[^0-9]", "", str(row.get("marketCap") or "")) or 0)
            imp = 3 if mc > 200_000_000_000 else (2 if ("NDX" in idx or mc > 50_000_000_000) else 1)
            events.append({
                "id": f"us-earn-{sym}-{d.isoformat()}",
                "date": d.isoformat(), "market": "US", "cat": "earnings",
                "title": f"{sym} — {strip_tags(row.get('name') or '')}".strip(" —"),
                "ticker": sym, "session": SESSION_MAP.get((row.get("time") or "").strip(), "unknown"),
                "imp": imp, "status": "confirmed", "index": idx,
                "eps_forecast": row.get("epsForecast") or "",
                "last_year_eps": row.get("lastYearEPS") or "",
                "fiscal": row.get("fiscalQuarterEnding") or "",
                "source": "Nasdaq Earnings Calendar",
                "links": [{"label": "Nasdaq",
                           "url": f"https://www.nasdaq.com/market-activity/stocks/{sym.lower()}/earnings"}],
            })
        d += timedelta(days=1)
        time.sleep(0.05)
    note("us_earnings.nasdaq", raw_rows=raw_rows, matched=matched, events=len(events))
    return events


# ==========================================================================
# C. 국내 유니버스 — 네이버 시가총액 + 보통주 필터
# ==========================================================================
ETF_BRAND = ("KODEX", "TIGER", "KBSTAR", "ARIRANG", "HANARO", "KOSEF", "SOL ", "ACE ",
             "TIMEFOLIO", "PLUS ", "RISE ", "KIWOOM", "HK ", "마이다스", "파워",
             "TREX", "FOCUS", "히어로즈", "네비게이터", "UNICORN", "BNKN")
NAME_BAD = re.compile(r"(스팩|제\d+호|리츠$|리츠[0-9]*$|ETN|선물|레버리지|인버스|채권|MSCI|S&P|"
                      r"코스피\d|코스닥\d|국고채|단기통안|머니마켓)")


def is_common_stock(code, name):
    if not re.fullmatch(r"\d{6}", code):
        return False
    if not code.endswith("0"):          # 우선주·신주인수권 등 제외
        return False
    up = name.upper()
    if any(up.startswith(b) or (" " + b) in up for b in ETF_BRAND):
        return False
    if NAME_BAD.search(name):
        return False
    return True


def kr_universe():
    uni = {}
    for sosok, label, pages, cap in KR_MARKETS:
        got, pages_used = 0, 0
        for p in range(1, pages + 1):
            if got >= cap or left() < 200:
                break
            try:
                r = get("https://finance.naver.com/sise/sise_market_sum.naver",
                        params={"sosok": sosok, "page": p}, timeout=20)
                r.encoding = "euc-kr"
                found = re.findall(
                    r'/item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)</a>', r.text)
                fresh = 0                      # 새 종목코드가 없으면 마지막 페이지 반복으로 판단
                for code, name in found:
                    if code in uni:
                        continue
                    fresh += 1
                    name = name.strip()
                    if not is_common_stock(code, name) or got >= cap:
                        continue
                    uni[code] = {"name": name, "idx": [label]}
                    got += 1
                pages_used = p
                if fresh == 0:
                    break
            except Exception as e:
                err(f"kr_universe.naver.{sosok}.{p}", e)
                break
            time.sleep(0.2)
        note(f"kr_universe.{label}", tickers=got, pages=pages_used)
    if len(uni) < 50:
        prev = load("kr_universe", {})
        if prev:
            uni = prev
            note("kr_universe.prev_kept", tickers=len(prev))
    note("kr_universe.total", tickers=len(uni))
    STATUS["samples"]["kr_universe"] = [v["name"] for v in list(uni.values())[:12]]
    if uni:
        save("kr_universe", uni)
    return uni


# ==========================================================================
# D. DART 공통
# ==========================================================================
DART_LIST = "https://opendart.fss.or.kr/api/list.json"


def dart_list(bgn, end, corp_code=None, pblntf_ty=None, max_pages=30, quiet=False):
    """공시 목록. corp_code 가 없으면 DART 가 3개월까지만 허용하므로 호출부에서 분할해야 함."""
    out, page = [], 1
    while page <= max_pages and left() > 90:
        p = {"crtfc_key": DART_KEY, "bgn_de": bgn, "end_de": end,
             "page_no": page, "page_count": 100}
        if corp_code:
            p["corp_code"] = corp_code
        if pblntf_ty:
            p["pblntf_ty"] = pblntf_ty
        try:
            j = get(DART_LIST, params=p).json()
        except Exception as e:
            if not quiet:
                err("dart_list", e)
            break
        st = j.get("status")
        if st != "000":
            if st not in ("013",) and not quiet:
                STATUS["errors"].append(f"dart_list status {st} {j.get('message')}")
            break
        out += j.get("list") or []
        if page >= int(j.get("total_page") or 1):
            break
        page += 1
        time.sleep(0.1)
    return out


def dart_list_long(bgn, end, **kw):
    """corp_code 없이 3개월 초과 기간을 조회할 때 90일 단위로 분할 (v1 오류 원인)"""
    out = []
    b = datetime.strptime(bgn, "%Y%m%d").date()
    e = datetime.strptime(end, "%Y%m%d").date()
    while b <= e and left() > 120:
        chunk_end = min(b + timedelta(days=85), e)
        out += dart_list(b.strftime("%Y%m%d"), chunk_end.strftime("%Y%m%d"), **kw)
        b = chunk_end + timedelta(days=1)
    return out


def corp_codes():
    cache = load("corp_codes", None)
    if cache and cache.get("date") == date.today().isoformat():
        return cache["map"]
    m = {}
    try:
        r = get("https://opendart.fss.or.kr/api/corpCode.xml",
                params={"crtfc_key": DART_KEY}, timeout=60)
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            xml = z.read(z.namelist()[0]).decode("utf-8")
        for blk in re.findall(r"<list>(.*?)</list>", xml, re.S):
            cc = re.search(r"<corp_code>(.*?)</corp_code>", blk)
            sc = re.search(r"<stock_code>(.*?)</stock_code>", blk)
            if cc and sc and re.fullmatch(r"\d{6}", (sc.group(1) or "").strip()):
                m[sc.group(1).strip()] = cc.group(1).strip()
        note("corp_codes", listed=len(m))
        save("corp_codes", {"date": date.today().isoformat(), "map": m})
    except Exception as e:
        err("corp_codes", e)
        m = (load("corp_codes", {}) or {}).get("map", {})
    return m


# ==========================================================================
# E. 국내 기업행위 (증자·CB·BW·EB·자사주)
# ==========================================================================
CORP_RULES = [                      # (공시명 키워드, API, 표시명, 중요도, 유형)  — 앞쪽 우선
    ("신탁계약체결", "tsstkAqTrctCnsInhDecsn", "자기주식 취득 신탁계약 체결", 2, "자사주 신탁"),
    ("신탁계약해지", "tsstkAqTrctCnsCncDecsn", "자기주식 신탁계약 해지", 2, "자사주 신탁해지"),
    ("유무상증자", "pifricDecsn", "유·무상증자 결정", 3, "유무상증자"),
    ("유상증자", "piicDecsn", "유상증자 결정", 3, "유상증자"),
    ("무상증자", "fricDecsn", "무상증자 결정", 2, "무상증자"),
    ("자기주식취득", "tsstkAqDecsn", "자기주식 취득 결정", 3, "자사주 취득"),
    ("자기주식처분", "tsstkDpDecsn", "자기주식 처분 결정", 2, "자사주 처분"),
]
FIELD_LABEL = {
    "nstk_asstd": "신주배정기준일", "nstk_ascrt": "신주배정기준일", "nstk_asstd_dt": "신주배정기준일",
    "sbd": "청약예정일", "sbd_bgd": "청약 시작일", "sbd_edd": "청약 종료일",
    "sbscpd": "청약일", "pymd": "납입일", "pym_dt": "납입일",
    "nstk_dlprd": "신주 교부 예정일", "nstk_dlprd_bgd": "신주 교부 시작일",
    "nstk_lstprd": "신주 상장 예정일", "lstprd": "상장 예정일", "nstk_lstd": "신주 상장일",
    "aq_pl_bgd": "취득 예정 시작일", "aq_pl_edd": "취득 예정 종료일",
    "dp_pl_bgd": "처분 예정 시작일", "dp_pl_edd": "처분 예정 종료일",
    "ctr_pd_bgd": "신탁계약 시작일", "ctr_pd_edd": "신탁계약 종료일",
    "cs_iv_bgd": "신탁 취득 시작일", "cs_iv_edd": "신탁 취득 종료일",
    "bd_mtd": "사채 만기일", "mtd": "만기일", "bddd": "이사회 결의일",
}
GENERIC_DATE_KEY = re.compile(r"(_bgd$|_edd$|_dt$|prd|asstd|pymd$|^sbd|mtd$|lstd$)")
CORP_DESC = {
    "유상증자": "발행주식수 증가로 기존 주주 지분이 희석됩니다. 신주배정기준일 전날이 권리락일이며 그날 주가가 기술적으로 조정됩니다. 주주배정·제3자배정 여부와 할인율이 핵심입니다.",
    "무상증자": "기업가치 변화는 없으나 기준일 전 수급이 붙는 경우가 많습니다. 권리락 후 주가 착시에 대한 사전 설명이 필요합니다.",
    "유무상증자": "유상·무상증자가 함께 결정된 건으로 일정이 두 단계로 진행됩니다.",
    "자사주 취득": "취득 기간 중 실제 매입 이행률이 중요합니다. 직접 취득은 일별 한도가 적용됩니다.",
    "자사주 처분": "일반적으로 수급 부담 요인입니다. 처분 목적(임직원 상여·교환 등) 확인이 필요합니다.",
    "자사주 신탁": "신탁 계약 기간 중 증권사가 재량으로 매입합니다. 계약 종료일과 실제 집행률을 함께 봐야 합니다.",
    "자사주 신탁해지": "해지 후 보유 자사주 처분 가능성이 생기므로 수급상 확인이 필요합니다.",
}


def kr_corp_actions(uni, cc_map):
    """추적 대상은 시총상위 유니버스(KR_TOP 종목)로 제한한다 (2026-09-09 요청)."""
    inv = {v: k for k, v in cc_map.items()}
    uni_corps = {cc_map[c] for c in uni if c in cc_map}
    bgn = (date.today() - timedelta(days=60)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    filings = dart_list_long(bgn, end, pblntf_ty="B")
    note("corp.filings", count=len(filings))

    want, skipped = {}, 0
    for f in filings:
        nm = (f.get("report_nm") or "").replace(" ", "")
        for kw, api, label, imp, kind in CORP_RULES:
            if kw in nm:
                if f.get("corp_code") not in uni_corps:   # 시총상위 외 종목은 제외
                    skipped += 1
                else:
                    want.setdefault((f.get("corp_code"), api), (label, imp, kind))
                break
    note("corp.targets", count=len(want), skipped_outside_universe=skipped,
         universe=len(uni_corps))

    events, done = [], 0
    for (corp, api), (label, imp, kind) in want.items():
        if left() < 200:
            note("corp.partial", remaining=len(want) - done)
            break
        done += 1
        try:
            j = get(f"https://opendart.fss.or.kr/api/{api}.json",
                    params={"crtfc_key": DART_KEY, "corp_code": corp,
                            "bgn_de": bgn, "end_de": end}).json()
            if j.get("status") != "000":
                continue
            stock = inv.get(corp, "")
            meta = uni.get(stock)
            if not meta:
                continue
            for rec in j.get("list") or []:
                for fld, d, txt in _future_dates(rec):
                    lab = FIELD_LABEL.get(fld)
                    if not lab:
                        if not GENERIC_DATE_KEY.search(fld):
                            continue
                        lab = "예정일"
                    events.append({
                        "id": f"corp-{corp}-{api}-{fld}-{d}",
                        "date": d.isoformat(), "market": "KR", "cat": "corp",
                        "title": f"{rec.get('corp_name', '')} — {label} · {lab}",
                        "ticker": stock, "kind": kind,
                        "imp": imp,
                        "status": "confirmed",
                        "index": (meta or {}).get("idx", []),
                        "desc": CORP_DESC.get(kind, ""),
                        "checklist": ["공시 원문에서 발행 규모·가격(전환가)·배정 대상 확인",
                                      "보유 고객 리스트 추출 후 개별 안내",
                                      "발행주식수 대비 희석 비율과 권리락일 확인"],
                        "links": [{"label": "DART 공시 원문",
                                   "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
                                          + str(rec.get("rcept_no", ""))}],
                        "source": f"DART 주요사항보고서 ({api})",
                    })
            time.sleep(0.08)
        except Exception as e:
            err(f"corp.{api}", e)
    uniq = {e["id"]: e for e in events}
    note("corp.events", count=len(uniq), from_targets=done)
    return list(uniq.values())


DATE_PAT = re.compile(r"(20\d{2})\s*[.\-년]\s*(\d{1,2})\s*[.\-월]\s*(\d{1,2})")


def _future_dates(rec):
    out, today = [], date.today()
    for k, v in rec.items():
        if not isinstance(v, str) or k in ("rcept_no", "corp_code", "rcept_dt", "corp_name"):
            continue
        m = DATE_PAT.search(v)
        if not m:
            continue
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if today <= d <= today + timedelta(days=HORIZON + 300):
            out.append((k, d, v))
    return out


# ==========================================================================
# F. 국내 실적발표일 — 전 종목 '잠정(추정)', 예고 공시가 있으면 확정
# ==========================================================================
def kr_earnings(uni, cc_map):
    events = []

    # F-1. 확정 — 결산실적공시예고 (있는 기업만, 실무상 소수)
    try:
        pre = [f for f in dart_list_long((date.today() - timedelta(days=75)).strftime("%Y%m%d"),
                                         date.today().strftime("%Y%m%d"), pblntf_ty="I")
               if re.search(r"(결산실적공시예고|실적공시예고)", (f.get("report_nm") or "").replace(" ", ""))]
        note("kr_earnings.notice_filings", count=len(pre))
        for f in pre:
            if left() < 200:
                break
            try:
                doc = get("https://opendart.fss.or.kr/api/document.xml",
                          params={"crtfc_key": DART_KEY, "rcept_no": f["rcept_no"]}, timeout=30)
                with zipfile.ZipFile(io.BytesIO(doc.content)) as z:
                    raw = z.read(z.namelist()[0]).decode("utf-8", "ignore")
                txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))
                m = re.search(r"(?:공시\s*예정일|예정\s*일자|발표\s*예정일|공시예정일자)[^0-9]{0,25}"
                              r"(20\d{2})[^0-9]{1,4}(\d{1,2})[^0-9]{1,4}(\d{1,2})", txt)
                if not m:
                    continue
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if d < date.today() or d > date.today() + timedelta(days=HORIZON + 60):
                    continue
                sess = "post" if re.search(r"장\s*종료\s*후|장\s*마감\s*후|1[5-9]시|1[5-9]:", txt) else \
                       ("pre" if re.search(r"장\s*개시\s*전|0[7-8]시|0[7-8]:", txt) else "unknown")
                code = f.get("stock_code") or ""
                events.append({
                    "id": f"kr-earn-{code}-{d}", "date": d.isoformat(), "market": "KR",
                    "cat": "earnings", "title": f"{f.get('corp_name')} 실적발표 (공시 예고)",
                    "ticker": code, "session": sess,
                    "imp": 3 if uni.get(code) else 2, "status": "confirmed",
                    "index": (uni.get(code) or {}).get("idx", []),
                    "desc": "기업이 DART 결산실적공시예고로 직접 공표한 발표일입니다.",
                    "source": "DART 결산실적공시예고",
                    "links": [{"label": "DART 공시 원문",
                               "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + f["rcept_no"]}],
                })
                time.sleep(0.08)
            except Exception as e:
                err("kr_earnings.notice_doc", e)
        note("kr_earnings.confirmed", count=len(events))
    except Exception as e:
        err("kr_earnings.notice", e)

    # F-2. 잠정 — 종목별 과거 잠정실적 공시일 기반 추정 (corp_code 지정 시 기간 제한 없음)
    cache = load("cache_kr_earn", {})
    today = date.today()
    bgn = (today - timedelta(days=365 * HIST_YEARS + 30)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    fetched, est = 0, 0
    have = {e["ticker"] for e in events}

    for code, meta in uni.items():
        if left() < 240:
            note("kr_earnings.partial", fetched=fetched)
            break
        corp = cc_map.get(code)
        if not corp:
            continue
        c = cache.get(code)
        if not c or c.get("as_of") != today.isoformat():
            rows = dart_list(bgn, end, corp_code=corp, quiet=True)
            dates = sorted({r.get("rcept_dt") for r in rows
                            if re.search(r"(잠정실적|영업\(잠정\))",
                                         (r.get("report_nm") or "").replace(" ", ""))})
            cache[code] = c = {"as_of": today.isoformat(), "dates": dates}
            fetched += 1
            time.sleep(0.08)
        if code in have or not c["dates"]:
            continue
        cand = []
        for s in c["dates"]:
            try:
                d0 = datetime.strptime(s, "%Y%m%d").date()
            except Exception:
                continue
            for yr in (today.year, today.year + 1):
                try:
                    nd = d0.replace(year=yr)
                except ValueError:
                    continue
                if today <= nd <= today + timedelta(days=HORIZON):
                    cand.append(nd)
        if not cand:
            continue
        d = bday(min(cand))
        events.append({
            "id": f"kr-earn-est-{code}-{d}", "date": d.isoformat(), "market": "KR",
            "cat": "earnings", "title": f"{meta['name']} 실적발표 예상",
            "ticker": code, "session": "unknown", "imp": 2, "status": "estimated",
            "index": meta.get("idx", []),
            "desc": "국내는 실적발표일을 사전 공표하는 제도가 없어, 과거 2년간 잠정실적 공시일을 기준으로 "
                    "추정한 일정입니다. 발표 시각(장전·장마감 후)도 사전 확인이 불가하며 국내 잠정실적은 "
                    "통상 장중~장 마감 후에 공시됩니다. 고객 안내 전 기업 IR 확인이 필요합니다.",
            "checklist": ["기업 IR 또는 유선으로 실제 발표일 확인",
                          "컨센서스와 직전 분기 수치 비교표 준비",
                          "동일 업종 선행 발표 기업의 결과·가이던스 확인"],
            "source": "과거 잠정실적 공시일 기반 추정 (잠정)",
            "links": [{"label": "네이버 금융",
                       "url": f"https://finance.naver.com/item/main.naver?code={code}"},
                      {"label": "DART 기업 공시",
                       "url": f"https://dart.fss.or.kr/dsab007/main.do?textCrpNm={code}"}],
        })
        est += 1
    save("cache_kr_earn", cache)
    note("kr_earnings.estimated", count=est, newly_fetched=fetched, companies_with_history=len(cache))
    return events


# ==========================================================================
# main
# ==========================================================================
def main():
    us_ev, kr_ev, corp_ev = [], [], []
    try:
        us_ev = us_earnings(us_universe())
    except Exception as e:
        err("us", e)

    if DART_KEY:
        try:
            kuni = kr_universe()
            cc = corp_codes()
            corp_ev = kr_corp_actions(kuni, cc)
            kr_ev = kr_earnings(kuni, cc)
        except Exception as e:
            err("kr", e)
    else:
        STATUS["errors"].append("DART_API_KEY 미설정 — 국내 수집 생략")

    earn = sorted(us_ev + kr_ev, key=lambda e: (e["date"], -e["imp"]))
    if not earn:
        prev = load("earnings", {}).get("events", [])
        if prev:
            earn = prev
            note("earnings.kept_previous", count=len(prev))
    if not corp_ev:
        prev = load("corp", {}).get("events", [])
        if prev:
            corp_ev = prev
            note("corp.kept_previous", count=len(prev))

    stamp = datetime.now().isoformat(timespec="seconds")
    save("earnings", {"generated_at": stamp, "count": len(earn), "events": earn}, "EARNINGS_DATA")
    save("corp", {"generated_at": stamp, "count": len(corp_ev), "events": corp_ev}, "CORP_DATA")
    STATUS["finished_at"] = stamp
    STATUS["elapsed_sec"] = int(time.time() - T0)
    STATUS["counts"] = {"earnings": len(earn), "us": len(us_ev), "kr": len(kr_ev),
                        "corp": len(corp_ev)}
    save("status", STATUS, "STATUS_DATA")
    log(f"done: earnings={len(earn)} (us={len(us_ev)} kr={len(kr_ev)}) "
        f"corp={len(corp_ev)} errors={len(STATUS['errors'])}")


if __name__ == "__main__":
    main()
    sys.exit(0)
