# -*- coding: utf-8 -*-
"""
collect.py — Market Calendar CHECK (dynamic data collector)

Collects, for the next ~100 days:
  A. US earnings dates for S&P 500 / Nasdaq 100 members, incl. before-open vs
     after-close (Nasdaq earnings calendar API, Yahoo Finance fallback)
  B. Korean earnings dates for KOSPI 200 / KOSDAQ 150 members
     - confirmed : DART "결산실적공시예고" filings
     - estimated : same quarter of previous years, shifted to a business day
  C. Korean corporate actions with forward-looking dates from DART
     (유상·무상증자, 전환사채·신주인수권부사채·교환사채, 자기주식 취득/처분)

Outputs (both .json for reuse and .js so the page also works over file://):
  data/earnings.json / data/earnings.js   -> window.EARNINGS_DATA
  data/corp.json     / data/corp.js       -> window.CORP_DATA
  data/status.json   / data/status.js     -> window.STATUS_DATA
  data/cache_kr_earn.json                 (발표일 이력 캐시, 재실행 비용 절감)

Environment
  DART_API_KEY   required for the Korean parts (GitHub Secret)
  TIME_BUDGET    optional, seconds (default 2400) — 예산 소진 시 안전 종료
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
HORIZON = 100          # days forward
LOOKBACK_YEARS = 3     # KR earnings history for estimation

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8"}
STATUS = {"started_at": datetime.now().isoformat(timespec="seconds"), "steps": {}, "errors": []}


def left():
    return BUDGET - (time.time() - T0)


def log(msg):
    print(f"[{int(time.time() - T0):5d}s] {msg}", flush=True)


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
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
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


# ==========================================================================
# A. US universe
# ==========================================================================
def us_universe():
    uni = {}
    srcs = [
        ("SP500", "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"),
        ("NDX", "https://en.wikipedia.org/wiki/Nasdaq-100"),
    ]
    for tag, url in srcs:
        try:
            html = get(url).text
            # first wikitable rows -> ticker in a <td> that looks like a symbol
            rows = re.findall(r"<tr>(.*?)</tr>", html, re.S)
            found = 0
            for r in rows:
                cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)
                if len(cells) < 2:
                    continue
                for c in cells[:2]:
                    txt = re.sub(r"<[^>]+>", "", c).strip()
                    if re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", txt):
                        uni.setdefault(txt.replace(".", "-"), set()).add(tag)
                        found += 1
                        break
            note(f"universe.{tag}", tickers=found)
        except Exception as e:
            err(f"universe.{tag}", e)
    if len(uni) < 300:  # keep previous run if scrape degraded
        prev = load("us_universe", {})
        for k, v in prev.items():
            uni.setdefault(k, set()).update(v)
    out = {k: sorted(v) for k, v in uni.items()}
    save("us_universe", out)
    return out


# ==========================================================================
# B. US earnings dates (with before/after market)
# ==========================================================================
SESSION_MAP = {"time-pre-market": "pre", "time-after-hours": "post",
               "time-not-supplied": "unknown", "": "unknown"}


def us_earnings(uni):
    events, hit = [], 0
    d, end = date.today(), date.today() + timedelta(days=HORIZON)
    while d <= end:
        if d.weekday() > 4 or left() < 240:
            d += timedelta(days=1)
            continue
        try:
            r = get("https://api.nasdaq.com/api/calendar/earnings",
                    params={"date": d.isoformat()},
                    headers={**UA, "Accept": "application/json",
                             "Origin": "https://www.nasdaq.com",
                             "Referer": "https://www.nasdaq.com/"})
            rows = (r.json().get("data") or {}).get("rows") or []
        except Exception as e:
            err(f"nasdaq.{d}", e)
            rows = []
        for row in rows:
            sym = (row.get("symbol") or "").strip().upper()
            if sym not in uni:
                continue
            idx = uni[sym]
            sess = SESSION_MAP.get((row.get("time") or "").strip(), "unknown")
            mcap = re.sub(r"[^0-9]", "", str(row.get("marketCap") or "")) or "0"
            imp = 3 if int(mcap) > 200_000_000_000 else (2 if "NDX" in idx or int(mcap) > 50_000_000_000 else 1)
            events.append({
                "id": f"us-earn-{sym}-{d.isoformat()}",
                "date": d.isoformat(), "market": "US", "cat": "earnings",
                "title": f"{sym} — {row.get('name') or ''}".strip(" —"),
                "ticker": sym, "session": sess, "imp": imp,
                "status": "confirmed", "index": idx,
                "eps_forecast": row.get("epsForecast") or "",
                "last_year_eps": row.get("lastYearEPS") or "",
                "fiscal": row.get("fiscalQuarterEnding") or "",
                "source": "Nasdaq Earnings Calendar",
                "links": [{"label": "Nasdaq", "url": f"https://www.nasdaq.com/market-activity/stocks/{sym.lower()}/earnings"}],
            })
            hit += 1
        d += timedelta(days=1)
    note("us_earnings.nasdaq", events=hit)

    # Yahoo fallback for members with no date found
    have = {e["ticker"] for e in events}
    missing = [t for t in uni if t not in have]
    filled = 0
    for i in range(0, len(missing), 40):
        if left() < 180:
            break
        batch = missing[i:i + 40]
        try:
            r = get("https://query1.finance.yahoo.com/v7/finance/quote",
                    params={"symbols": ",".join(batch), "fields": "earningsTimestamp,shortName,marketCap"})
            for q in (r.json().get("quoteResponse") or {}).get("result") or []:
                ts = q.get("earningsTimestamp")
                if not ts:
                    continue
                dt = datetime.utcfromtimestamp(ts) - timedelta(hours=4)  # ~US/Eastern
                if not (date.today() <= dt.date() <= date.today() + timedelta(days=HORIZON)):
                    continue
                sym = q.get("symbol")
                events.append({
                    "id": f"us-earn-{sym}-{dt.date().isoformat()}",
                    "date": dt.date().isoformat(), "market": "US", "cat": "earnings",
                    "title": f"{sym} — {q.get('shortName') or ''}".strip(" —"),
                    "ticker": sym, "session": "pre" if dt.hour < 12 else "post",
                    "imp": 3 if (q.get("marketCap") or 0) > 200_000_000_000 else 1,
                    "status": "estimated", "index": uni.get(sym, []),
                    "source": "Yahoo Finance (추정 시각)",
                })
                filled += 1
        except Exception as e:
            err("us_earnings.yahoo", e)
            break
    note("us_earnings.yahoo", events=filled)
    return events


# ==========================================================================
# C. Korean universe (KOSPI 200 / KOSDAQ 150)
# ==========================================================================
KRX_IDX = [("코스피200", "1", "028"), ("코스닥150", "2", "203")]


def kr_universe():
    uni = {}
    for name, mkt, idx in KRX_IDX:
        try:
            otp = requests.post(
                "http://data.krx.co.kr/comm/fileDn/GenerateOTP/generate.cmd",
                data={"locale": "ko_KR", "tboxindIdx_finder_equidx0_0": name,
                      "indIdx": mkt, "indIdx2": idx, "codeNmindIdx_finder_equidx0_0": name,
                      "param1indIdx_finder_equidx0_0": "",
                      "trdDd": date.today().strftime("%Y%m%d"),
                      "money": "1", "csvxls_isNo": "false",
                      "name": "fileDown", "url": "dbms/MDC/STAT/standard/MDCSTAT00601"},
                headers={**UA, "Referer": "http://data.krx.co.kr/"}, timeout=20).text
            r = requests.post("http://data.krx.co.kr/comm/fileDn/download_csv/download.cmd",
                              data={"code": otp}, headers={**UA, "Referer": "http://data.krx.co.kr/"},
                              timeout=30)
            r.encoding = "cp949"
            lines = [l for l in r.text.splitlines() if l.strip()]
            head = lines[0].replace('"', "").split(",")
            ci = head.index("종목코드") if "종목코드" in head else 0
            ni = head.index("종목명") if "종목명" in head else 1
            cnt = 0
            for l in lines[1:]:
                p = [x.strip().strip('"') for x in l.split(",")]
                if len(p) > max(ci, ni) and re.fullmatch(r"\d{6}", p[ci]):
                    e = uni.setdefault(p[ci], {"name": p[ni], "idx": []})
                    e["idx"].append(name)
                    cnt += 1
            note(f"kr_universe.{name}", tickers=cnt)
        except Exception as e:
            err(f"kr_universe.{name}", e)
    if len(uni) < 200:
        prev = load("kr_universe", {})
        for k, v in prev.items():
            uni.setdefault(k, v)
    save("kr_universe", uni)
    return uni


# ==========================================================================
# D. DART helpers
# ==========================================================================
DART_LIST = "https://opendart.fss.or.kr/api/list.json"


def dart_list(bgn, end, pblntf_ty=None, page_count=100, max_pages=40):
    """공시 목록 조회 (기간·유형)"""
    out, page = [], 1
    while page <= max_pages and left() > 120:
        p = {"crtfc_key": DART_KEY, "bgn_de": bgn, "end_de": end,
             "page_no": page, "page_count": page_count}
        if pblntf_ty:
            p["pblntf_ty"] = pblntf_ty
        try:
            j = get(DART_LIST, params=p).json()
        except Exception as e:
            err("dart_list", e)
            break
        if j.get("status") not in ("000",):
            if j.get("status") != "013":  # 013 = 데이터 없음
                STATUS["errors"].append(f"dart_list status {j.get('status')} {j.get('message')}")
            break
        out += j.get("list") or []
        if page >= int(j.get("total_page") or 1):
            break
        page += 1
        time.sleep(0.12)
    return out


def corp_codes():
    """corp_code <-> stock_code 매핑 (1일 1회면 충분, 캐시)"""
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


DATE_PAT = re.compile(r"(20\d{2})[.\-\s년]*(\d{1,2})[.\-\s월]*(\d{1,2})")


def pick_dates(rec):
    """레코드의 모든 필드에서 미래 날짜를 뽑아 (필드명, 날짜) 리스트로 반환"""
    out = []
    today = date.today()
    for k, v in rec.items():
        if not isinstance(v, str) or k in ("rcept_no", "corp_code", "rcept_dt"):
            continue
        m = DATE_PAT.search(v)
        if not m:
            continue
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if today <= d <= today + timedelta(days=HORIZON + 260):
            out.append((k, d))
    return out


FIELD_LABEL = {
    "nstk_asstd": "신주배정기준일", "nstk_ascrt": "신주배정기준일",
    "sbd": "청약예정일", "sbd_bgd": "청약 시작일", "sbd_edd": "청약 종료일",
    "pymd": "납입일", "nstk_dlprd": "신주 교부 예정일", "nstk_lstprd": "신주 상장 예정일",
    "ex_sm_r": "권리락", "bd_ptn": "사채 종류",
    "cv_prc_cttrs": "전환가액 조정", "cvrqpd_bgd": "전환청구 가능 시작일",
    "cvrqpd_edd": "전환청구 가능 종료일", "act_mktprcfl_cvprc_lwtrsprc": "전환가 하한",
    "aq_pl_bgd": "취득 예정 시작일", "aq_pl_edd": "취득 예정 종료일",
    "dp_pl_bgd": "처분 예정 시작일", "dp_pl_edd": "처분 예정 종료일",
    "ctr_pd_bgd": "신탁계약 시작일", "ctr_pd_edd": "신탁계약 종료일",
    "bddd": "이사회 결의일", "bd_intr_ex_mth": "이자지급",
}
CORP_TYPES = [
    ("piicDecsn", "유상증자 결정", 3, "유상증자"),
    ("fricDecsn", "무상증자 결정", 2, "무상증자"),
    ("pifricDecsn", "유·무상증자 결정", 3, "유무상증자"),
    ("cvbdIsDecsn", "전환사채(CB) 발행 결정", 3, "CB"),
    ("bdwtIsDecsn", "신주인수권부사채(BW) 발행 결정", 3, "BW"),
    ("exbdIsDecsn", "교환사채(EB) 발행 결정", 2, "EB"),
    ("tsstkAqDecsn", "자기주식 취득 결정", 3, "자사주 취득"),
    ("tsstkDpDecsn", "자기주식 처분 결정", 2, "자사주 처분"),
]
CORP_DESC = {
    "유상증자": "발행주식수가 늘어나 기존 주주 지분이 희석됩니다. 신주배정기준일 전날이 권리락일이며, "
              "권리락 당일 주가가 기술적으로 조정됩니다. 제3자 배정인지 주주배정인지, 할인율이 얼마인지가 핵심입니다.",
    "무상증자": "자본 항목 간 이동으로 기업가치 변화는 없으나, 기준일 전 수급이 붙는 경우가 많습니다. "
              "권리락 후 주가 착시에 대한 사전 설명이 필요합니다.",
    "유무상증자": "유상증자와 무상증자가 함께 결정된 건입니다. 일정이 두 단계로 진행됩니다.",
    "CB": "전환가액과 전환청구 가능일이 핵심입니다. 전환청구 시작일부터 오버행이 실제 매도 물량으로 나옵니다. "
          "주가 하락 시 전환가 리픽싱 조항 유무를 확인해야 합니다.",
    "BW": "신주인수권 행사 가능일부터 희석이 발생합니다. 워런트 분리 여부를 확인해야 합니다.",
    "EB": "발행사가 보유한 다른 주식으로 교환되는 사채로, 교환 대상 주식에 오버행이 생깁니다.",
    "자사주 취득": "취득 기간 중 실제 매입 이행률이 중요합니다. 신탁이면 계약 기간, 직접 취득이면 일별 한도가 적용됩니다.",
    "자사주 처분": "일반적으로 수급상 부담 요인입니다. 처분 목적(임직원 상여·교환사채 등)을 확인해야 합니다.",
}


def kr_corp_actions(uni, cc_map):
    """주요사항보고서에서 향후 일정이 있는 기업행위를 추출"""
    bgn = (date.today() - timedelta(days=45)).strftime("%Y%m%d")
    end = date.today().strftime("%Y%m%d")
    filings = dart_list(bgn, end, pblntf_ty="B")
    note("corp.filings", count=len(filings))

    want = {}
    for f in filings:
        nm = f.get("report_nm") or ""
        for api, label, imp, kind in CORP_TYPES:
            key = {"유상증자": "유상증자", "무상증자": "무상증자", "유무상증자": "유무상증자",
                   "CB": "전환사채", "BW": "신주인수권부사채", "EB": "교환사채",
                   "자사주 취득": "주식취득", "자사주 처분": "주식처분"}[kind]
            if key in nm.replace(" ", ""):
                want.setdefault((f.get("corp_code"), api), (f, label, imp, kind))
    note("corp.targets", count=len(want))

    events, seen = [], set()
    for (corp, api), (f, label, imp, kind) in want.items():
        if left() < 150:
            STATUS["steps"].setdefault("corp.partial", {})["remaining"] = len(want) - len(seen)
            break
        seen.add((corp, api))
        try:
            j = get(f"https://opendart.fss.or.kr/api/{api}.json",
                    params={"crtfc_key": DART_KEY, "corp_code": corp,
                            "bgn_de": bgn, "end_de": end}).json()
            if j.get("status") != "000":
                continue
            for rec in j.get("list") or []:
                stock = next((s for s, c in cc_map.items() if c == corp), None)
                inuni = uni.get(stock or "")
                for fld, d in pick_dates(rec):
                    lab = FIELD_LABEL.get(fld)
                    if not lab:
                        continue
                    ev_id = f"corp-{corp}-{api}-{fld}-{d}"
                    events.append({
                        "id": ev_id, "date": d.isoformat(), "market": "KR", "cat": "corp",
                        "title": f"{rec.get('corp_name', '')} — {label} · {lab}",
                        "ticker": stock or "", "kind": kind,
                        "imp": imp if inuni else max(1, imp - 1),
                        "status": "confirmed",
                        "index": (inuni or {}).get("idx", []),
                        "desc": CORP_DESC.get(kind, ""),
                        "checklist": ["공시 원문에서 발행 규모·할인율(전환가)·배정 대상 확인",
                                      "보유 고객 통보 및 권리락 전후 대응 안내",
                                      "오버행 물량 = 발행주식수 대비 비율 산출"],
                        "links": [{"label": "DART 공시 원문",
                                   "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rec.get('rcept_no', '')}"}],
                        "source": f"DART 주요사항보고서 ({api})",
                    })
            time.sleep(0.1)
        except Exception as e:
            err(f"corp.{api}", e)
    # de-dup
    uniq = {e["id"]: e for e in events}
    note("corp.events", count=len(uniq))
    return list(uniq.values())


# ==========================================================================
# E. Korean earnings dates
# ==========================================================================
PRE_PAT = re.compile(r"(결산실적공시예고|실적공시\s*예고)")


def kr_earnings(uni, cc_map):
    events = []

    # E-1. 확정: 결산실적공시예고 (거래소공시)
    try:
        bgn = (date.today() - timedelta(days=60)).strftime("%Y%m%d")
        end = date.today().strftime("%Y%m%d")
        pre = [f for f in dart_list(bgn, end, pblntf_ty="I") if PRE_PAT.search(f.get("report_nm") or "")]
        note("kr_earnings.notice_filings", count=len(pre))
        for f in pre:
            if left() < 120:
                break
            try:
                doc = get("https://opendart.fss.or.kr/api/document.xml",
                          params={"crtfc_key": DART_KEY, "rcept_no": f["rcept_no"]}, timeout=30)
                with zipfile.ZipFile(io.BytesIO(doc.content)) as z:
                    raw = z.read(z.namelist()[0]).decode("utf-8", "ignore")
                txt = re.sub(r"<[^>]+>", " ", raw)
                m = re.search(r"(?:공시\s*예정일|예정\s*일자|발표\s*예정일)[^0-9]{0,20}"
                              r"(20\d{2})[^0-9]{1,3}(\d{1,2})[^0-9]{1,3}(\d{1,2})", txt)
                if not m:
                    continue
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if d < date.today():
                    continue
                sess = "post" if re.search(r"장\s*종료\s*후|장\s*마감\s*후|1[5-9]:", txt) else \
                       ("pre" if re.search(r"장\s*개시\s*전|0[7-8]:", txt) else "unknown")
                stock = f.get("stock_code") or ""
                events.append({
                    "id": f"kr-earn-{stock}-{d}", "date": d.isoformat(), "market": "KR",
                    "cat": "earnings", "title": f"{f.get('corp_name')} 실적발표 예정 (공시 예고)",
                    "ticker": stock, "session": sess, "imp": 3 if uni.get(stock) else 2,
                    "status": "confirmed", "index": (uni.get(stock) or {}).get("idx", []),
                    "source": "DART 결산실적공시예고",
                    "links": [{"label": "DART 공시 원문",
                               "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={f['rcept_no']}"}],
                })
                time.sleep(0.1)
            except Exception as e:
                err("kr_earnings.notice_doc", e)
        note("kr_earnings.confirmed", count=len(events))
    except Exception as e:
        err("kr_earnings.notice", e)

    # E-2. 추정: 과거 잠정실적 공시일 → 동일 분기 발표 시기 추정
    cache = load("cache_kr_earn", {})
    hist = defaultdict(list)
    for y in range(date.today().year - LOOKBACK_YEARS, date.today().year + 1):
        key = str(y)
        if key in cache:
            rows = cache[key]
        else:
            if left() < 300:
                break
            rows = []
            for ty in ("I",):
                for f in dart_list(f"{y}0101", f"{y}1231", pblntf_ty=ty, max_pages=60):
                    nm = (f.get("report_nm") or "").replace(" ", "")
                    if "잠정실적" in nm or "영업(잠정)" in nm:
                        rows.append({"c": f.get("stock_code") or "", "n": f.get("corp_name"),
                                     "d": f.get("rcept_dt")})
            cache[key] = rows
        for r in rows:
            if r["c"]:
                hist[r["c"]].append(r["d"])
    save("cache_kr_earn", cache)
    note("kr_earnings.history", companies=len(hist))

    have = {e["ticker"] for e in events}
    est = 0
    today = date.today()
    for code, meta in uni.items():
        if code in have or code not in hist:
            continue
        # 같은 달·일에 가까운 과거 발표일을 기준으로 다음 발표 시점 추정
        cand = []
        for s in hist[code]:
            try:
                d = datetime.strptime(s, "%Y%m%d").date()
            except Exception:
                continue
            for yr in (today.year, today.year + 1):
                try:
                    nd = d.replace(year=yr)
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
            "desc": "전년 동기 잠정실적 공시일을 기준으로 추정한 일정입니다. 확정 일정은 기업 IR 또는 "
                    "결산실적공시예고로 확인해야 합니다.",
            "source": "과거 잠정실적 공시일 기반 추정",
            "links": [{"label": "네이버 금융",
                       "url": f"https://finance.naver.com/item/main.naver?code={code}"}],
        })
        est += 1
    note("kr_earnings.estimated", count=est)
    return events


# ==========================================================================
# main
# ==========================================================================
def main():
    us_ev, kr_ev, corp_ev = [], [], []
    try:
        uni = us_universe()
        us_ev = us_earnings(uni)
    except Exception as e:
        err("us", e)

    if DART_KEY:
        try:
            kuni = kr_universe()
            cc = corp_codes()
            kr_ev = kr_earnings(kuni, cc)
            corp_ev = kr_corp_actions(kuni, cc)
        except Exception as e:
            err("kr", e)
    else:
        STATUS["errors"].append("DART_API_KEY 미설정 — 국내 실적/기업행위 수집 생략")

    earn = sorted(us_ev + kr_ev, key=lambda e: (e["date"], -e["imp"]))
    prev_e = load("earnings", {}).get("events", [])
    prev_c = load("corp", {}).get("events", [])
    if not earn and prev_e:              # 실패 시 직전 데이터 유지
        earn, STATUS["steps"]["earnings.kept_previous"] = prev_e, {"count": len(prev_e)}
    if not corp_ev and prev_c:
        corp_ev, STATUS["steps"]["corp.kept_previous"] = prev_c, {"count": len(prev_c)}

    stamp = datetime.now().isoformat(timespec="seconds")
    save("earnings", {"generated_at": stamp, "count": len(earn), "events": earn}, "EARNINGS_DATA")
    save("corp", {"generated_at": stamp, "count": len(corp_ev), "events": corp_ev}, "CORP_DATA")
    STATUS["finished_at"] = stamp
    STATUS["elapsed_sec"] = int(time.time() - T0)
    STATUS["counts"] = {"earnings": len(earn), "corp": len(corp_ev)}
    save("status", STATUS, "STATUS_DATA")
    log(f"done: earnings={len(earn)} corp={len(corp_ev)} errors={len(STATUS['errors'])}")


if __name__ == "__main__":
    main()
    sys.exit(0)
