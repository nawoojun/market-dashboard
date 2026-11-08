#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PB 마켓 대시보드 v2 — 무료 데이터만 사용
=========================================
실행하면 dashboard_latest.html(그리고 날짜별 사본)을 만듭니다. 파일 하나에 모든 차트가 들어 있고
기간 버튼(1년~30년)으로 장기 추이를 바로 바꿔 볼 수 있습니다.

데이터 소스 (전부 무료)
- 한국은행 ECOS : 국고채 3·10·30년, 기준금리, 원/달러, 원/100엔, 코스피, 코스닥, 한국 CPI   (키 필요)
- FRED          : 미국채 3·10·30년, 연준 기준금리, WTI, 미국 CPI·PPI·PCE·고용·실업률·소매판매 (키 없어도 됨)
- Yahoo Finance : S&P500, 나스닥종합, 금(선물), 비트코인                                      (키 불필요)
- 일본 재무성    : 일본국채 3·10·30년                                                        (키 불필요)
- 브라질 중앙은행 : 달러/헤알, Selic, IPCA                                                    (키 불필요)
- 브라질 국고청   : 고정금리 국채(NTN-F) 10년 근사 금리                                         (키 불필요)
- 관세청         : 품목(HS)별 월간 수출                                                       (공공데이터포털 키)
- ForexFactory  : 이번 주 주요 지표 일정·시장 예상치(컨센서스)                                   (키 불필요)
- FRED 발표일정  : 향후 3주 미국 지표 발표일                                                   (FRED 키 필요)

사용법
  pip install requests pandas numpy
  python market_dashboard.py
"""
import datetime as dt
import io
import json
import os
import time
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")

# =====================================================================
# 1. 설정
# =====================================================================
ECOS_KEY = os.getenv("ECOS_API_KEY", "")
FRED_KEY = os.getenv("FRED_API_KEY", "")
DATA_GO_KEY = os.getenv("DATA_GO_KR_KEY", "")

BASE_DIR = Path(__file__).resolve().parent
CACHE_DIR = BASE_DIR / "data"
OUT_DIR = BASE_DIR

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime.now(KST)
TODAY = NOW.date()
LONG_START = dt.date(1994, 1, 1)          # 장기 차트 시작(약 30년+)
ECON_START = dt.date(TODAY.year - 12, 1, 1)
EXPORT_YEARS = 10

EXPORT_ITEMS = [  # (표시 이름, HS 4단위) — 산업부 15대 품목과 정확히 같지는 않은 근사치
    ("반도체", "8542"), ("자동차", "8703"), ("석유제품", "2710"), ("선박", "8901"),
    ("자동차부품", "8708"), ("디스플레이", "8524"), ("무선통신기기", "8517"), ("이차전지", "8507"),
    ("합성수지", "3901"), ("화장품", "3304"), ("철강판", "7208"), ("컴퓨터·SSD", "8471"),
]

# 일정 (매년 초 갱신)
FOMC_DATES = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
              "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]
BOK_DATES = ["2026-01-15", "2026-02-26", "2026-04-10", "2026-05-28",
             "2026-07-16", "2026-08-27", "2026-10-22", "2026-11-26"]

# FRED 발표 이름 → (한글, 미국 동부시간, 중요도, 그룹)
US_RELEASES = {
    "Consumer Price Index": ("소비자물가(CPI)", "08:30", 3, "CPI"),
    "Producer Price Index": ("생산자물가(PPI)", "08:30", 3, "PPI"),
    "Employment Situation": ("고용보고서(비농업고용·실업률)", "08:30", 3, "NFP"),
    "Personal Income and Outlays": ("개인소득·PCE 물가", "08:30", 3, "PCE"),
    "Gross Domestic Product": ("GDP", "08:30", 3, "GDP"),
    "Advance Monthly Sales for Retail and Food Services": ("소매판매", "08:30", 3, "RETAIL"),
    "Unemployment Insurance Weekly Claims Report": ("신규 실업수당청구", "08:30", 2, "CLAIMS"),
    "Job Openings and Labor Turnover Survey": ("JOLTS 구인건수", "10:00", 2, "JOLTS"),
    "Industrial Production and Capacity Utilization": ("산업생산", "09:15", 2, "IP"),
    "Surveys of Consumers": ("미시간대 소비자심리", "10:00", 2, "UOM"),
}
# ForexFactory 제목 → (한글, 그룹)
FF_TITLES = {
    "CPI m/m": ("소비자물가(CPI) 전월비", "CPI"), "CPI y/y": ("소비자물가(CPI) 전년비", "CPI"),
    "Core CPI m/m": ("근원 CPI 전월비", "CPI"), "Core CPI y/y": ("근원 CPI 전년비", "CPI"),
    "PPI m/m": ("생산자물가(PPI) 전월비", "PPI"), "Core PPI m/m": ("근원 PPI 전월비", "PPI"),
    "Non-Farm Employment Change": ("비농업 고용 변화", "NFP"), "Unemployment Rate": ("실업률", "NFP"),
    "Average Hourly Earnings m/m": ("평균 시간당 임금 전월비", "NFP"),
    "Core PCE Price Index m/m": ("근원 PCE 물가 전월비", "PCE"), "Core PCE Price Index y/y": ("근원 PCE 물가 전년비", "PCE"),
    "Advance GDP q/q": ("GDP 속보치(연율)", "GDP"), "Prelim GDP q/q": ("GDP 잠정치(연율)", "GDP"),
    "Final GDP q/q": ("GDP 확정치(연율)", "GDP"),
    "Retail Sales m/m": ("소매판매 전월비", "RETAIL"), "Core Retail Sales m/m": ("근원 소매판매 전월비", "RETAIL"),
    "Unemployment Claims": ("신규 실업수당청구", "CLAIMS"), "JOLTS Job Openings": ("JOLTS 구인건수", "JOLTS"),
    "ISM Manufacturing PMI": ("ISM 제조업 PMI", "ISM"), "ISM Services PMI": ("ISM 서비스업 PMI", "ISM"),
    "Prelim UoM Consumer Sentiment": ("미시간대 소비자심리(예비)", "UOM"),
    "Revised UoM Consumer Sentiment": ("미시간대 소비자심리(확정)", "UOM"),
    "Federal Funds Rate": ("FOMC 기준금리 결정", "FOMC"), "FOMC Statement": ("FOMC 성명서", "FOMC"),
    "FOMC Press Conference": ("FOMC 기자회견", "FOMC"), "FOMC Meeting Minutes": ("FOMC 의사록", "FOMC"),
    "Main Refinancing Rate": ("ECB 기준금리 결정", "ECB"), "BOJ Policy Rate": ("BOJ 기준금리 결정", "BOJ"),
    "ECB Press Conference": ("ECB 기자회견", "ECB"),
}
FF_COUNTRY = {"USD": "미국", "CNY": "중국", "JPY": "일본", "EUR": "유로"}

# =====================================================================
# 2. 수집
# =====================================================================
S = requests.Session()
S.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PB-dashboard"


def _get(url, timeout=40, **kw):
    r = S.get(url, timeout=timeout, **kw)
    r.raise_for_status()
    return r


def fetch_ecos(stat, cycle, item, start):
    if not ECOS_KEY:
        raise RuntimeError("ECOS 키 없음")
    fmt = "%Y%m%d" if cycle == "D" else "%Y%m"
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_KEY}/json/kr/1/100000/"
           f"{stat}/{cycle}/{start.strftime(fmt)}/{TODAY.strftime(fmt)}/{item}")
    js = _get(url).json()
    if "StatisticSearch" not in js:
        raise RuntimeError(js.get("RESULT", {}).get("MESSAGE", "ECOS 오류"))
    rows = js["StatisticSearch"]["row"]
    s = pd.Series(pd.to_numeric([r["DATA_VALUE"] for r in rows], errors="coerce"),
                  index=pd.to_datetime([r["TIME"] for r in rows], format=fmt))
    return s.dropna().sort_index()


def fetch_fred(sid, start):
    if FRED_KEY:
        js = _get("https://api.stlouisfed.org/fred/series/observations",
                  params={"series_id": sid, "api_key": FRED_KEY, "file_type": "json",
                          "observation_start": start.isoformat()}).json()
        ob = js["observations"]
        s = pd.Series(pd.to_numeric([o["value"] for o in ob], errors="coerce"),
                      index=pd.to_datetime([o["date"] for o in ob]))
    else:
        df = pd.read_csv(io.StringIO(_get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}").text))
        s = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").values, index=pd.to_datetime(df.iloc[:, 0]))
    return s.dropna().sort_index()


def fetch_yahoo(ticker, start):
    p1 = int(dt.datetime(start.year, start.month, start.day).timestamp())
    js = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker)}",
              params={"period1": p1, "period2": int(time.time()), "interval": "1d"}).json()
    r = js["chart"]["result"][0]
    off = r["meta"].get("gmtoffset", 0)
    idx = pd.to_datetime([t + off for t in r["timestamp"]], unit="s").normalize()
    s = pd.Series(r["indicators"]["quote"][0]["close"], index=idx, dtype=float)
    return s.dropna().groupby(level=0).last().sort_index()


_MOF = None


def fetch_mof(col, start):
    global _MOF
    if _MOF is None:
        base = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/"
        frames = []
        for u in (base + "historical/jgbcme_all.csv", base + "jgbcme.csv"):
            raw = _get(u, timeout=90).content
            try:
                txt = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                txt = raw.decode("shift_jis", errors="replace")
            lines = txt.splitlines()
            hi = next(i for i, l in enumerate(lines) if l.strip().startswith("Date"))
            df = pd.read_csv(io.StringIO("\n".join(lines[hi:])), na_values=["-", "", " "])
            df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
            frames.append(df.dropna(subset=["Date"]).set_index("Date"))
        df = pd.concat(frames)
        _MOF = df[~df.index.duplicated(keep="last")].sort_index()
    s = pd.to_numeric(_MOF[col], errors="coerce").dropna()
    return s[s.index >= pd.Timestamp(start)]


def fetch_bcb(code, start):
    """브라질 중앙은행 SGS — 일별 시계열은 1회 10년까지라 나눠서 받음"""
    out, cur = [], start
    while cur <= TODAY:
        end = min(dt.date(cur.year + 9, 12, 31), TODAY)
        try:
            js = _get(f"https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados",
                      params={"formato": "json", "dataInicial": cur.strftime("%d/%m/%Y"),
                              "dataFinal": end.strftime("%d/%m/%Y")}).json()
            out += js
        except Exception:
            pass
        cur = end + dt.timedelta(days=1)
    if not out:
        raise RuntimeError("BCB 응답 없음")
    s = pd.Series([float(x["valor"]) for x in out], index=pd.to_datetime([x["data"] for x in out], dayfirst=True))
    return s[~s.index.duplicated()].sort_index()


def fetch_tesouro_10y(start):
    """Tesouro Direto 공시: 고정금리 이표채(NTN-F) 중 잔존 10년에 가장 가까운 종목의 금리"""
    url = ("https://www.tesourotransparente.gov.br/ckan/dataset/df56aa42-484a-4a59-8184-7676580c81e3/"
           "resource/796d2059-14e9-44e3-80c9-2d9e30b405c1/download/PrecoTaxaTesouroDireto.csv")
    df = pd.read_csv(io.BytesIO(_get(url, timeout=180).content), sep=";", decimal=",", encoding="latin-1")
    df = df[df["Tipo Titulo"].str.contains("Prefixado") & df["Tipo Titulo"].str.contains("Semestrais")].copy()
    df["base"] = pd.to_datetime(df["Data Base"], dayfirst=True)
    df["venc"] = pd.to_datetime(df["Data Vencimento"], dayfirst=True)
    df = df[df["base"] >= pd.Timestamp(start)]
    df["rate"] = df["Taxa Venda Manha"].replace(0, np.nan).fillna(df["Taxa Compra Manha"].replace(0, np.nan))
    df = df[df["rate"] > 1].dropna(subset=["rate"])
    df["gap"] = ((df["venc"] - df["base"]).dt.days / 365.25 - 10).abs()
    best = df.loc[df.groupby("base")["gap"].idxmin()]
    return best.set_index("base")["rate"].sort_index()


def fetch_customs(hs, years=EXPORT_YEARS):
    if not DATA_GO_KEY:
        raise RuntimeError("공공데이터포털 키 없음")
    rows = []
    for y in range(TODAY.year - years, TODAY.year + 1):
        for attempt in range(5):
            time.sleep(1.2)  # 초당 요청 제한
            r = _get("https://apis.data.go.kr/1220000/Itemtrade/getItemtradeList",
                     params={"serviceKey": DATA_GO_KEY, "strtYymm": f"{y}01", "endYymm": f"{y}12",
                             "hsSgn": hs, "numOfRows": 9999})
            if b"LIMITED_NUMBER" in r.content:
                time.sleep(3)
                continue
            root = ET.fromstring(r.content)
            rows += [{c.tag: (c.text or "").strip() for c in it} for it in root.iter("item")]
            break
    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("관세청 데이터 없음")
    df = df[df["year"].str.match(r"^\d{4}\.\d{2}$")]
    s = pd.to_numeric(df["expDlr"], errors="coerce").groupby(df["year"]).sum()
    s.index = pd.to_datetime(s.index, format="%Y.%m")
    return s.sort_index()


def fetch_ff_calendar():
    js = _get("https://nfs.faireconomy.media/ff_calendar_thisweek.json").json()
    return [x for x in js if x.get("country") in FF_COUNTRY and x.get("impact") in ("High", "Medium")]


def fetch_fred_releases():
    if not FRED_KEY:
        return []
    js = _get("https://api.stlouisfed.org/fred/releases/dates",
              params={"api_key": FRED_KEY, "file_type": "json", "limit": 1000,
                      "realtime_start": TODAY.isoformat(),
                      "realtime_end": (TODAY + dt.timedelta(days=22)).isoformat(),
                      "include_release_dates_with_no_data": "true", "sort_order": "asc"}).json()
    return [(x["date"], x["release_name"]) for x in js.get("release_dates", [])]


def with_cache(key, fn):
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{key}.csv"
    try:
        s = fn()
        if s is None or len(s) == 0:
            raise RuntimeError("빈 데이터")
        s.rename("v").to_csv(path, index_label="date")
        return s, None
    except Exception as e:  # noqa
        print(f"  [실패] {key}: {e}")
        if path.exists():
            return pd.read_csv(path, index_col=0, parse_dates=True)["v"], "캐시"
        return pd.Series(dtype=float), "실패"


SOURCES = {
    # 금리
    "KR3Y": lambda: fetch_ecos("817Y002", "D", "010200000", LONG_START),
    "KR10Y": lambda: fetch_ecos("817Y002", "D", "010210000", LONG_START),
    "KR30Y": lambda: fetch_ecos("817Y002", "D", "010230000", LONG_START),
    "KRBASE": lambda: fetch_ecos("722Y001", "D", "0101000", LONG_START),
    "US3Y": lambda: fetch_fred("DGS3", LONG_START),
    "US10Y": lambda: fetch_fred("DGS10", LONG_START),
    "US30Y": lambda: fetch_fred("DGS30", LONG_START),
    "USFF": lambda: pd.concat([fetch_fred("DFEDTAR", LONG_START).loc[:"2008-12-15"],
                               fetch_fred("DFEDTARU", dt.date(2008, 12, 16))]),
    "JP3Y": lambda: fetch_mof("3Y", LONG_START),
    "JP10Y": lambda: fetch_mof("10Y", LONG_START),
    "JP30Y": lambda: fetch_mof("30Y", LONG_START),
    "BR10Y": lambda: fetch_tesouro_10y(LONG_START),
    "SELIC": lambda: fetch_bcb(432, dt.date(1999, 3, 5)),
    # 환율
    "USDKRW": lambda: fetch_ecos("731Y001", "D", "0000001", LONG_START),
    "JPYKRW": lambda: fetch_ecos("731Y001", "D", "0000002", LONG_START),
    "USDBRL": lambda: fetch_bcb(1, dt.date(1994, 7, 1)),
    # 주식·원자재·코인
    "KOSPI": lambda: fetch_ecos("802Y001", "D", "0001000", LONG_START),
    "KOSDAQ": lambda: fetch_ecos("802Y001", "D", "0089000", LONG_START),
    "SPX": lambda: fetch_yahoo("^GSPC", LONG_START),
    "NASDAQ": lambda: fetch_yahoo("^IXIC", LONG_START),
    "WTI": lambda: fetch_fred("DCOILWTICO", LONG_START),
    "GOLD": lambda: fetch_yahoo("GC=F", LONG_START),
    "BTC": lambda: fetch_yahoo("BTC-USD", LONG_START),
    # 경제지표(원계열)
    "CPIAUCSL": lambda: fetch_fred("CPIAUCSL", ECON_START),
    "CPILFESL": lambda: fetch_fred("CPILFESL", ECON_START),
    "PPIFID": lambda: fetch_fred("PPIFID", ECON_START),
    "PCEPILFE": lambda: fetch_fred("PCEPILFE", ECON_START),
    "PAYEMS": lambda: fetch_fred("PAYEMS", ECON_START),
    "UNRATE": lambda: fetch_fred("UNRATE", ECON_START),
    "RSAFS": lambda: fetch_fred("RSAFS", ECON_START),
    "KR_CPI": lambda: fetch_ecos("901Y009", "M", "0", ECON_START),
    "BR_IPCA12": lambda: fetch_bcb(13522, ECON_START),
}


def fetch_all():
    raw, status = {}, {}
    for k, fn in SOURCES.items():
        print("수집:", k)
        raw[k], status[k] = with_cache(k, fn)
    for name, hs in EXPORT_ITEMS:
        print("수집: 수출", name)
        raw["EXP_" + hs], status["EXP_" + hs] = with_cache("EXP_" + hs, lambda hs=hs: fetch_customs(hs))
    try:
        ff = fetch_ff_calendar()
    except Exception as e:
        print("  [실패] ForexFactory 일정:", e); ff = []
    try:
        fr = fetch_fred_releases()
    except Exception as e:
        print("  [실패] FRED 일정:", e); fr = []
    return raw, ff, fr, {k: v for k, v in status.items() if v}


# =====================================================================
# 3. 가공
# =====================================================================
def _me():
    try:
        pd.date_range("2020-01-01", periods=2, freq="ME"); return "ME"
    except Exception:
        return "M"


ME = _me()


def yoy(s):
    s = s.copy(); s.index = s.index.to_period("M").to_timestamp()
    p = s.copy(); p.index = p.index + pd.DateOffset(years=1)
    return ((s / p.reindex(s.index) - 1) * 100).dropna()


def mom(s):
    s = s.copy(); s.index = s.index.to_period("M").to_timestamp()
    prev = s.copy(); prev.index = prev.index + pd.DateOffset(months=1)
    return ((s / prev.reindex(s.index) - 1) * 100).dropna()


def mdiff(s):
    s = s.copy(); s.index = s.index.to_period("M").to_timestamp()
    prev = s.copy(); prev.index = prev.index + pd.DateOffset(months=1)
    return (s - prev.reindex(s.index)).dropna()


def derive(raw):
    d = dict(raw)
    a, b = raw.get("USDKRW", pd.Series(dtype=float)), raw.get("USDBRL", pd.Series(dtype=float))
    if len(a) and len(b):
        idx = a.index
        d["BRLKRW"] = (a / b.reindex(b.index.union(idx)).ffill().reindex(idx)).dropna()
    g = lambda k: raw.get(k, pd.Series(dtype=float))
    d["US_CPI"] = yoy(g("CPIAUCSL")); d["US_CCPI"] = yoy(g("CPILFESL"))
    d["US_PPI"] = yoy(g("PPIFID")); d["US_CPCE"] = yoy(g("PCEPILFE"))
    d["US_NFP"] = mdiff(g("PAYEMS")); d["US_UR"] = g("UNRATE")
    d["US_RS"] = mom(g("RSAFS")); d["KR_CPI_Y"] = yoy(g("KR_CPI"))
    d["BR_IPCA"] = g("BR_IPCA12")
    if len(g("BR10Y")):
        d["BR10Y"] = g("BR10Y")[g("BR10Y") > 1]
    for name, hs in EXPORT_ITEMS:
        e = g("EXP_" + hs) / 1e8   # 억 달러
        d["EXP_" + hs] = e
        d["EXPMA_" + hs] = e.rolling(12).mean().dropna()
    return d


def pack(s, daily=True, dec=4):
    """차트용으로 줄이기: 최근 2년 일별 · 2~10년 주별 · 10년 이전 월별"""
    s = s.dropna().sort_index()
    if s.empty:
        return {"t": [], "v": []}
    if daily:
        end = s.index[-1]
        c2, c10 = end - pd.DateOffset(years=2), end - pd.DateOffset(years=10)
        a = s[s.index > c2]
        b = s[(s.index <= c2) & (s.index > c10)]
        b = b.groupby(b.index.to_period("W-FRI")).last()
        b.index = b.index.to_timestamp(how="end").normalize()
        c = s[s.index <= c10]
        c = c.groupby(c.index.to_period("M")).last()
        c.index = c.index.to_timestamp(how="end").normalize()
        s = pd.concat([c, b, a]).sort_index()
        s = s[~s.index.duplicated(keep="last")]
    t = ((s.index - pd.Timestamp("1970-01-01")) // pd.Timedelta("1D")).astype(int).tolist()
    return {"t": t, "v": [round(float(x), dec) for x in s.values]}


def asof(s, when):
    x = s[s.index <= pd.Timestamp(when)]
    return x.iloc[-1] if len(x) else np.nan


def changes(s, kind):
    """kind: rate(bp) / level(%)"""
    s = s.dropna()
    if len(s) < 2:
        return []
    ld, lv = s.index[-1], s.iloc[-1]
    refs = [("1일", s.iloc[-2]), ("1개월", asof(s, ld - pd.Timedelta(days=30))),
            ("1년", asof(s, ld - pd.Timedelta(days=365))), ("연초", asof(s, pd.Timestamp(ld.year - 1, 12, 31)))]
    out = []
    for lab, r in refs:
        if pd.isna(r) or r == 0:
            continue
        x = (lv - r) * 100 if kind == "rate" else (lv / r - 1) * 100
        txt = f"{x:+.0f}bp" if kind == "rate" else f"{x:+.1f}%"
        out.append([lab, txt, 1 if x > 0.0001 else -1 if x < -0.0001 else 0])
    return out


# =====================================================================
# 4. 차트 정의
# =====================================================================
RED, BLUE, DARK, GRAY, GREEN = "--c1", "--c2", "--c3", "--cp", "--c2"  # 테마 색상(CSS 변수): 네이비·틸·앰버·정책금리

RATE_CARDS = [
    ("rate_kr", "한국 국고채", "한국은행", [("KR3Y", "3년", BLUE), ("KR10Y", "10년", RED), ("KR30Y", "30년", DARK)], ("KRBASE", "기준금리"), "KR10Y"),
    ("rate_us", "미국 국채", "FRED", [("US3Y", "3년", BLUE), ("US10Y", "10년", RED), ("US30Y", "30년", DARK)], ("USFF", "기준금리(상단)"), "US10Y"),
    ("rate_jp", "일본 국채", "일본 재무성", [("JP3Y", "3년", BLUE), ("JP10Y", "10년", RED), ("JP30Y", "30년", DARK)], None, "JP10Y"),
    ("rate_br", "브라질 국채", "브라질 국고청·중앙은행", [("BR10Y", "10년 근사(NTN-F)", RED)], ("SELIC", "Selic 기준금리"), "BR10Y"),
]
FX_CARDS = [("fx_usd", "원/달러", "USDKRW", 1), ("fx_jpy", "원/100엔", "JPYKRW", 2), ("fx_brl", "원/헤알", "BRLKRW", 2)]
RISK_CARDS = [
    ("rk_kospi", "코스피", "KOSPI", 2, "한국은행"), ("rk_kosdaq", "코스닥", "KOSDAQ", 2, "한국은행"),
    ("rk_spx", "S&P 500", "SPX", 2, "Yahoo"), ("rk_ndq", "나스닥 종합", "NASDAQ", 2, "Yahoo"),
    ("rk_wti", "WTI 유가 ($/배럴)", "WTI", 2, "FRED"), ("rk_gold", "금 ($/온스, 선물)", "GOLD", 1, "Yahoo"),
    ("rk_btc", "비트코인 ($)", "BTC", 0, "Yahoo"),
]
ECON_CARDS = [  # id, 제목, [(key, 이름, 색)], 형태, 단위, 소수, 출처
    ("ec_uscpi", "美 소비자물가 (전년비)", [("US_CPI", "CPI", RED), ("US_CCPI", "근원 CPI", BLUE)], "line", "%", 1, "FRED"),
    ("ec_usppi", "美 생산자물가 (전년비)", [("US_PPI", "PPI 최종수요", RED)], "line", "%", 1, "FRED"),
    ("ec_uspce", "美 근원 PCE 물가 (전년비)", [("US_CPCE", "근원 PCE", RED)], "line", "%", 1, "FRED"),
    ("ec_usnfp", "美 비농업 고용 (전월대비)", [("US_NFP", "비농업 고용", RED)], "bar", "천명", 0, "FRED"),
    ("ec_usur", "美 실업률", [("US_UR", "실업률", BLUE)], "line", "%", 1, "FRED"),
    ("ec_usrs", "美 소매판매 (전월비)", [("US_RS", "소매판매", RED)], "bar", "%", 1, "FRED"),
    ("ec_krcpi", "韓 소비자물가 (전년비)", [("KR_CPI_Y", "CPI", RED)], "line", "%", 1, "한국은행"),
    ("ec_bripca", "브라질 IPCA (12개월 누적)", [("BR_IPCA", "IPCA", RED)], "line", "%", 1, "브라질 중앙은행"),
]


def card_stats(d, key, kind, dec, unit=""):
    s = d.get(key, pd.Series(dtype=float)).dropna()
    if s.empty:
        return {"last": "수집 실패", "date": "", "ch": []}
    val = f"{s.iloc[-1]:,.{dec}f}{unit}"
    return {"last": val, "date": s.index[-1].strftime("%y.%m.%d"), "ch": changes(s, kind)}


def build_charts(d):
    charts, data, stats = {}, {}, {}

    def put(key, daily=True, dec=4):
        if key not in data:
            data[key] = pack(d.get(key, pd.Series(dtype=float)), daily, dec)

    for cid, title, src, lines, pol, main in RATE_CARDS:
        ser = []
        if pol:
            put(pol[0]); ser.append({"k": pol[0], "n": pol[1], "c": GRAY, "w": 2, "dash": "6 4", "step": 1})
        for k, n, c in lines:
            put(k); ser.append({"k": k, "n": n, "c": c, "w": 2.6})
        charts[cid] = {"title": title, "src": src, "series": ser, "dec": 2, "unit": "%"}
        st = card_stats(d, main, "rate", 3 if main.startswith(("KR", "JP")) else 2, "%")
        st["main"] = [n for k, n, c in lines if k == main][0]
        stats[cid] = st
    for cid, title, key, dec in FX_CARDS:
        put(key)
        charts[cid] = {"title": title, "src": "한국은행·브라질 중앙은행" if key == "BRLKRW" else "한국은행",
                       "series": [{"k": key, "n": title, "c": RED, "w": 2.6}], "dec": dec, "unit": ""}
        stats[cid] = card_stats(d, key, "level", dec)
    for cid, title, key, dec, src in RISK_CARDS:
        put(key)
        charts[cid] = {"title": title, "src": src, "series": [{"k": key, "n": title, "c": {"SPX": BLUE, "NASDAQ": BLUE, "WTI": DARK, "GOLD": DARK, "BTC": "--c4"}.get(key, RED), "w": 2.2}],
                       "dec": dec, "unit": ""}
        stats[cid] = card_stats(d, key, "level", dec)
    for cid, title, lines, form, unit, dec, src in ECON_CARDS:
        ser = []
        for k, n, c in lines:
            put(k, daily=False, dec=3)
            ser.append({"k": k, "n": n, "c": c, "w": 2.6, "bar": 1 if form == "bar" else 0})
        charts[cid] = {"title": title, "src": src, "series": ser, "dec": dec, "unit": unit, "zero": 1}
        s = d.get(lines[0][0], pd.Series(dtype=float)).dropna()
        if len(s) >= 2:
            stats[cid] = {"last": f"{s.iloc[-1]:,.{dec}f}{unit}", "date": s.index[-1].strftime("%Y.%m"),
                          "prev": f"이전 {s.iloc[-2]:,.{dec}f}", "dir": int(np.sign(round(s.iloc[-1] - s.iloc[-2], 6)))}
        else:
            stats[cid] = {"last": "수집 실패", "date": "", "prev": "", "dir": 0}
    for name, hs in EXPORT_ITEMS:
        cid = "ex_" + hs
        put("EXP_" + hs, daily=False, dec=2); put("EXPMA_" + hs, daily=False, dec=2)
        charts[cid] = {"title": name, "src": f"HS {hs}", "dec": 1, "unit": "",
                       "series": [{"k": "EXP_" + hs, "n": "월 수출", "c": "--cb", "bar": 1, "mono": 1, "nolabel": 1},
                                  {"k": "EXPMA_" + hs, "n": "12개월 평균", "c": RED, "w": 2.6}]}
        s = d.get("EXP_" + hs, pd.Series(dtype=float)).dropna()
        if len(s) >= 13:
            y = s.iloc[-1] / s.iloc[-13] - 1 if s.iloc[-13] else np.nan
            stats[cid] = {"last": f"{s.iloc[-1]:,.1f}억$", "date": s.index[-1].strftime("%Y.%m"),
                          "prev": f"전년비 {y * 100:+.1f}%", "dir": 1 if y > 0 else -1}
        else:
            stats[cid] = {"last": "수집 실패", "date": "", "prev": "", "dir": 0}
    return charts, data, stats


# =====================================================================
# 5. 일정
# =====================================================================
def _us_dst(d):
    """미국 서머타임: 3월 둘째 일요일 ~ 11월 첫째 일요일"""
    mar = dt.date(d.year, 3, 8); mar += dt.timedelta(days=(6 - mar.weekday()) % 7)
    nov = dt.date(d.year, 11, 1); nov += dt.timedelta(days=(6 - nov.weekday()) % 7)
    return mar <= d < nov


def et_to_kst(date_str, hhmm):
    d = dt.date.fromisoformat(date_str)
    h, m = map(int, hhmm.split(":"))
    off = -4 if _us_dst(d) else -5
    return dt.datetime(d.year, d.month, d.day, h, m, tzinfo=dt.timezone(dt.timedelta(hours=off))).astimezone(KST)


def _next_bday(d):
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def build_calendar(ff, fred_rel):
    start, end = NOW - dt.timedelta(hours=6), NOW + dt.timedelta(days=21)
    ev, seen = [], set()
    for x in ff:
        t = dt.datetime.fromisoformat(x["date"]).astimezone(KST)
        name, grp = FF_TITLES.get(x["title"], (x["title"], x["title"]))
        if x["impact"] == "Medium" and not x.get("forecast") and "Chair" not in x["title"]:
            continue
        ev.append({"t": t, "cty": FF_COUNTRY[x["country"]], "name": name, "imp": 3 if x["impact"] == "High" else 2,
                   "fc": x.get("forecast") or "", "pv": x.get("previous") or ""})
        seen.add((t.date(), grp))
    for date_str, rname in fred_rel:
        for k, (name, et, imp, grp) in US_RELEASES.items():
            if rname.startswith(k):
                t = et_to_kst(date_str, et)
                if (t.date(), grp) in seen:
                    continue
                seen.add((t.date(), grp))
                ev.append({"t": t, "cty": "미국", "name": name, "imp": imp, "fc": "", "pv": ""})
    for ds in FOMC_DATES:
        t = et_to_kst(ds, "14:00")
        if (t.date(), "FOMC") not in seen:
            ev.append({"t": t, "cty": "미국", "name": "FOMC 기준금리 결정", "imp": 3, "fc": "", "pv": ""})
    for ds in BOK_DATES:
        d = dt.date.fromisoformat(ds)
        ev.append({"t": dt.datetime(d.year, d.month, d.day, 10, 0, tzinfo=KST), "cty": "한국", "name": "한은 금통위 기준금리 결정", "imp": 3, "fc": "", "pv": ""})
    # 한국 수출입 (관세청 1~10일·1~20일 잠정치, 산업부 월간)
    for k in range(0, 2):
        y, m = (TODAY.year, TODAY.month + k) if TODAY.month + k <= 12 else (TODAY.year + 1, 1)
        for day, nm in ((1, "월간 수출입동향"), (11, "1~10일 수출(관세청 잠정)"), (21, "1~20일 수출(관세청 잠정)")):
            d = _next_bday(dt.date(y, m, day))
            ev.append({"t": dt.datetime(d.year, d.month, d.day, 9, 0, tzinfo=KST), "cty": "한국", "name": nm, "imp": 2, "fc": "", "pv": ""})
    ev = [e for e in ev if start <= e["t"] <= end]
    ev.sort(key=lambda e: (e["t"], -e["imp"]))
    return ev


# =====================================================================
# 6. HTML
# =====================================================================
CSS = r"""
:root{--bg:#EEF1F5;--panel:#fff;--bd:#DCE1E8;--bd2:#E8ECF1;--tx:#0F1B2D;--sub:#5B6B80;--mute:#8A96A8;--grid:#EEF1F5;--axis:#8A96A8;
--hd:#0A1F44;--hd2:#0F2A57;--hdtx:#fff;--hdsub:#9FB2D4;--acc:#12A58C;
--c1:#0B2B5E;--c2:#12A58C;--c3:#E8A317;--c4:#7C5CBF;--cp:#9AA6B6;--cb:#C9D4E4;--up:#0E9F7A;--dn:#E0870A;--badge:#fff;--tipbg:#0A1F44;--fc:#FFF6E0}
[data-theme=dark]{--bg:#0A0E15;--panel:#111723;--bd:#1E2735;--bd2:#18202D;--tx:#E6EBF2;--sub:#93A1B5;--mute:#6B788C;--grid:#18202C;--axis:#6B788C;
--hd:#0D1422;--hd2:#111A2B;--hdtx:#E6EBF2;--hdsub:#7D8DA6;
--c1:#E6EBF2;--c2:#2DD4B4;--c3:#F6B73C;--c4:#A78BFA;--cp:#5E6B7E;--cb:#24324A;--up:#2DD4B4;--dn:#F6B73C;--badge:#0A0E15;--tipbg:#1E2A3F;--fc:#2A2413}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:104px}
body{margin:0;background:var(--bg);color:var(--tx);font-family:Pretendard,'Malgun Gothic','Apple SD Gothic Neo',system-ui,sans-serif;font-size:13.5px;-webkit-font-smoothing:antialiased;font-feature-settings:'tnum'}
.hd{background:var(--hd);color:var(--hdtx)}
.hd .in{max-width:1480px;margin:0 auto;padding:0 20px;height:54px;display:flex;align-items:center;gap:14px}
.logo{width:28px;height:28px;border:1.5px solid var(--acc);display:grid;place-items:center;font-weight:800;font-size:11px;color:var(--acc);letter-spacing:-.3px}
.hd h1{font-size:16px;font-weight:700;margin:0;letter-spacing:-.2px}.hd h1 span{font-weight:400;color:var(--hdsub);margin-left:10px;font-size:12.5px}
.hd .rt{margin-left:auto;display:flex;align-items:center;gap:14px;font-size:12px;color:var(--hdsub);white-space:nowrap}
.hd .live{display:inline-flex;align-items:center;gap:6px}.hd .live:before{content:"";width:6px;height:6px;border-radius:50%;background:var(--acc)}
.thm{background:none;border:1px solid #2B4170;color:var(--hdtx);font:inherit;font-size:11.5px;padding:4px 9px;cursor:pointer}
.tk{background:var(--hd2);border-top:1px solid rgba(255,255,255,.06);overflow-x:auto;scrollbar-width:none}
.tk .in{max-width:1480px;margin:0 auto;padding:0 20px;display:flex}
.tq{padding:8px 16px 8px 0;margin-right:16px;border-right:1px solid rgba(255,255,255,.08);white-space:nowrap;line-height:1.35}
.tq:last-child{border-right:0}.tq .n{font-size:11px;color:var(--hdsub)}.tq .p{font-size:13.5px;font-weight:700;color:var(--hdtx);margin-right:6px}.tq .c{font-size:11.5px;font-weight:600}
.nav{position:sticky;top:0;z-index:30;background:var(--panel);border-bottom:1px solid var(--bd)}
.nav .in{max-width:1480px;margin:0 auto;padding:0 20px;display:flex;overflow-x:auto;scrollbar-width:none}
.nav a{color:var(--sub);text-decoration:none;font-size:13px;font-weight:600;padding:13px 2px 11px;margin-right:26px;border-bottom:2px solid transparent;white-space:nowrap}
.nav a:hover,.nav a.on{color:var(--tx);border-bottom-color:var(--acc)}
.wrap{max-width:1480px;margin:0 auto;padding:6px 20px 56px}
section{margin-top:28px}
.sh{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;margin-bottom:10px;flex-wrap:wrap}.sh>div{min-width:0}
.sh .en{font-size:10.5px;font-weight:700;letter-spacing:.14em;color:var(--acc)}
.sh h2{margin:2px 0 0;font-size:19px;font-weight:700;letter-spacing:-.4px}.sh h2 small{font-size:12px;font-weight:400;color:var(--mute);margin-left:10px;letter-spacing:0}
.per{display:inline-flex;border:1px solid var(--bd);background:var(--panel);max-width:100%;overflow-x:auto}
.per button{border:0;border-right:1px solid var(--bd);background:none;font:inherit;font-size:12px;font-weight:600;color:var(--sub);padding:6px 12px;cursor:pointer;white-space:nowrap}
.per button:last-child{border-right:0}.per button.on{background:var(--hd);color:#fff}.per button:not(.on):hover{color:var(--tx);background:var(--bd2)}
.grid{display:grid;gap:10px}
.g2{grid-template-columns:repeat(auto-fill,minmax(560px,1fr))}.g3{grid-template-columns:repeat(auto-fill,minmax(420px,1fr))}.g4{grid-template-columns:repeat(auto-fill,minmax(330px,1fr))}
.panel{background:var(--panel);border:1px solid var(--bd);border-radius:3px;min-width:0}
.ph{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:11px 14px;border-bottom:1px solid var(--bd2)}
.pt{font-size:14px;font-weight:700;letter-spacing:-.2px}.pt small{display:block;font-size:11px;font-weight:400;color:var(--mute);margin-top:2px;letter-spacing:0}
.pv{text-align:right;white-space:nowrap}.pv b{font-size:20px;font-weight:700;letter-spacing:-.4px}.pv small{display:block;font-size:11px;color:var(--mute);margin-top:1px}
.qs{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:1px solid var(--bd2)}
.qs div{padding:6px 14px;border-right:1px solid var(--bd2)}.qs div:last-child{border-right:0}
.qs span{display:block;font-size:10.5px;color:var(--mute)}.qs b{font-size:12.5px;font-weight:700}
.up{color:var(--up)!important}.dn{color:var(--dn)!important}
.body{padding:8px 10px 6px 4px}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11.5px;color:var(--sub);padding:2px 10px 2px 10px}
.legend span{display:inline-flex;align-items:center;gap:6px}.legend em{display:inline-block;width:14px;height:0;border-top:2.5px solid}
.plot{position:relative;width:100%}.plot svg{display:block;width:100%;overflow:visible}
.tip{position:absolute;pointer-events:none;background:var(--tipbg);color:#E6EBF2;font-size:12px;padding:8px 10px;white-space:nowrap;display:none;z-index:5;line-height:1.6;border-radius:2px;box-shadow:0 6px 18px rgba(10,31,68,.25)}
.tip .d{font-size:11px;color:#9FB2D4;margin-bottom:2px}.tip b{font-weight:700;margin-left:4px}.tip i{display:inline-block;width:10px;height:0;border-top:2.5px solid;margin-right:6px;vertical-align:3px}
.calp{background:var(--panel);border:1px solid var(--bd);border-radius:3px;overflow-x:auto}
table.cal{width:100%;border-collapse:collapse;font-size:13px}
.cal th{background:var(--bd2);color:var(--sub);font-size:11.5px;font-weight:700;text-align:left;padding:9px 12px;border-bottom:1px solid var(--bd);white-space:nowrap}
.cal td{padding:9px 12px;border-bottom:1px solid var(--bd2);white-space:nowrap}
.cal tr.day td{background:var(--bg);font-size:12px;font-weight:700;color:var(--sub);padding:7px 12px}
.cal tr.day em{font-style:normal;background:var(--acc);color:#fff;font-size:10.5px;padding:1px 6px;margin-left:8px;border-radius:2px}
.cal .tm{font-weight:700;width:70px}.cal .cc{width:52px}
.cal .cc span{display:inline-block;font-size:10.5px;font-weight:700;border:1px solid var(--bd);padding:1px 5px;color:var(--sub);border-radius:2px}
.cal .cc span.KR{color:var(--acc);border-color:var(--acc)}
.cal .nm{white-space:normal;font-weight:500}
.cal .imp{width:70px}.cal .imp i{display:inline-block;width:5px;height:11px;background:var(--c3);margin-right:2px;vertical-align:-1px}.cal .imp i.o{background:var(--bd)}
.cal .n{text-align:right;width:120px}.cal .fc{font-weight:700;background:var(--fc);padding:2px 6px}.cal .na{color:var(--mute)}
.note{margin-top:30px;font-size:11.5px;color:var(--mute);line-height:1.7;border-top:1px solid var(--bd);padding-top:12px}
@media(max-width:700px){.g2,.g3,.g4{grid-template-columns:1fr}.hd h1 span,.hd .live{display:none}.wrap{padding:4px 12px 40px}.hd .in,.nav .in,.tk .in{padding:0 12px}
.cal .pv2,.cal .imp{display:none}.cal .n{width:auto}.qs div{padding:6px 8px}}
"""

JS = r"""
const PD={'1Y':365,'3Y':1096,'5Y':1826,'10Y':3653,'20Y':7305,'30Y':10958};
const fmt=(v,d)=>v.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const dstr=(t,full)=>{const d=new Date(t*864e5);const y=d.getUTCFullYear(),m=d.getUTCMonth()+1,dd=d.getUTCDate();return full?`${y}.${String(m).padStart(2,'0')}.${String(dd).padStart(2,'0')}`:`${y}.${String(m).padStart(2,'0')}`};
function ticks(lo,hi,n){const sp=hi-lo||Math.abs(hi)||1;let st=sp/n;const mg=10**Math.floor(Math.log10(st)),e=st/mg;st=(e>=7.5?10:e>=3.5?5:e>=1.5?2:1)*mg;const a=[];for(let v=Math.ceil(lo/st)*st;v<=hi+st*1e-6;v+=st)a.push(+v.toPrecision(12));return{a,st}}
function bs(a,x){let l=0,h=a.length-1;while(l<h){const m=(l+h+1)>>1;if(a[m]<=x)l=m;else h=m-1}return l}
const NS='http://www.w3.org/2000/svg';
function el(tag,at,par){const e=document.createElementNS(NS,tag);for(const k in at)e.setAttribute(k,at[k]);if(par)par.appendChild(e);return e}
let CS;const cv=c=>c&&c.startsWith('--')?CS.getPropertyValue(c).trim():c;
const FONT="Pretendard,'Malgun Gothic',sans-serif";
function draw(card){
 const id=card.dataset.id,cfg=CH[id],per=card.closest('section').dataset.p,box=card.querySelector('.plot');
 const W=box.clientWidth,H=+box.dataset.h; if(!W)return;
 const K={grid:cv('--grid'),axis:cv('--axis'),pos:cv('--up'),neg:cv('--dn'),panel:cv('--panel'),badge:cv('--badge'),tx:cv('--tx')};
 const tmax=Math.max(...cfg.series.map(s=>{const t=DATA[s.k].t;return t.length?t[t.length-1]:-1e9}));
 const t0=tmax-PD[per];
 const S=cfg.series.map(s=>{const D=DATA[s.k];if(!D.t.length)return null;let i=D.t.findIndex(x=>x>=t0);if(i<0)return null;if(s.step&&i>0)i--;return{...s,col:cv(s.c),t:D.t.slice(i),v:D.v.slice(i)}}).filter(Boolean);
 box.innerHTML='';if(!S.length){box.innerHTML=`<div style="height:${H}px;display:grid;place-items:center;color:${K.axis};font-size:12px">데이터 없음</div>`;return}
 let x0=Math.min(...S.map(s=>Math.max(s.t[0],t0))),x1=tmax;if(x1-x0<20)x0=x1-20;
 let lo=Infinity,hi=-Infinity;for(const s of S)for(let i=0;i<s.t.length;i++){if(s.t[i]<x0&&!s.step)continue;lo=Math.min(lo,s.v[i]);hi=Math.max(hi,s.v[i])}
 if(cfg.zero||S.some(s=>s.bar)){lo=Math.min(lo,0);hi=Math.max(hi,0)}
 const pad=(hi-lo)*.07||1;lo-=pad;hi+=pad;
 const labs=S.filter(s=>!s.nolabel);const lw=Math.max(...labs.map(s=>fmt(s.v[s.v.length-1],cfg.dec).length))*7.6+22;
 const L=10,R=Math.max(52,lw)+46,T=8,B=22,pw=W-L-R,ph=H-T-B;
 const X=t=>L+(t-x0)/(x1-x0)*pw,Y=v=>T+(hi-v)/(hi-lo)*ph;
 const svg=el('svg',{viewBox:`0 0 ${W} ${H}`,height:H},box);
 const yt=ticks(lo,hi,5),yd=Math.max(0,Math.min(3,-Math.floor(Math.log10(yt.st)+1e-9)));
 const YA=L+pw+lw+8;  // 오른쪽 축 라벨 위치
 for(const v of yt.a){if(v<lo||v>hi)continue;const y=Y(v);el('line',{x1:L,x2:L+pw,y1:y,y2:y,stroke:v===0&&(cfg.zero||S.some(s=>s.bar))?K.axis:K.grid,'stroke-width':1},svg);
  const tx=el('text',{x:W-2,y:y+4,'text-anchor':'end','font-size':10.5,fill:K.axis,'font-family':FONT},svg);tx.textContent=fmt(v,yd)}
 const yrs=(x1-x0)/365.25,dx0=new Date(x0*864e5),y0=dx0.getUTCFullYear(),y1=new Date(x1*864e5).getUTCFullYear();
 const xt=[],mx=Math.max(2,Math.floor(pw/60));if(yrs>2.5){const st=[1,2,5,10].find(k=>yrs/k<=mx)||10;for(let y=Math.ceil(y0/st)*st;y<=y1;y+=st){const t=Date.UTC(y,0,1)/864e5;if(t>=x0)xt.push([t,String(y)])}}
 else{const st=[1,2,3,6].find(k=>yrs*12/k<=mx)||6;let y=y0,m=dx0.getUTCMonth()+1;while(true){const t=Date.UTC(y,m,1)/864e5;if(t>x1)break;if((m)%st===0&&t>=x0)xt.push([t,String(y).slice(2)+'.'+String(m+1).padStart(2,'0')]);m++;if(m>11){m=0;y++}}}
 for(const [t,lab] of xt){const x=X(t);el('line',{x1:x,x2:x,y1:T,y2:T+ph,stroke:K.grid},svg);const tx=el('text',{x,y:H-5,'text-anchor':'middle','font-size':10.5,fill:K.axis,'font-family':FONT},svg);tx.textContent=lab}
 el('line',{x1:L,x2:L+pw,y1:T+ph,y2:T+ph,stroke:K.axis,'stroke-width':.8},svg);
 const clip='c'+id;const cp=el('clipPath',{id:clip},el('defs',{},svg));el('rect',{x:L,y:0,width:pw,height:H},cp);
 const g=el('g',{'clip-path':`url(#${clip})`},svg);
 for(const s of S){
  if(s.bar){const n=s.t.filter(t=>t>=x0).length||1,bw=Math.max(1,pw/n*.7),yz=Y(0);
   for(let i=0;i<s.t.length;i++){if(s.t[i]<x0)continue;const v=s.v[i],y=Y(v);el('rect',{x:X(s.t[i])-bw/2,y:Math.min(y,yz),width:bw,height:Math.max(1,Math.abs(yz-y)),fill:s.mono?s.col:(v>=0?K.pos:K.neg)},g)}continue}
  let d='';for(let i=0;i<s.t.length;i++){const x=X(Math.max(s.t[i],x0)),y=Y(s.v[i]);if(!i)d=`M${x},${y}`;else if(s.step)d+=`H${x}V${y}`;else d+=`L${x},${y}`}
  if(s.step)d+=`H${X(x1)}`;
  el('path',{d,fill:'none',stroke:s.col,'stroke-width':s.w||2.2,'stroke-linejoin':'round','stroke-dasharray':s.dash||''},g)}
 // 끝점 가격표(배지)
 const ends=labs.map(s=>{const v=s.v[s.v.length-1],t=s.t[s.t.length-1];return{v,y:Y(v),py:Y(v),x:X(s.step?x1:t),c:s.bar&&!s.mono?(v>=0?K.pos:K.neg):s.col,dash:s.dash}}).sort((a,b)=>a.y-b.y);
 for(let i=1;i<ends.length;i++)if(ends[i].y-ends[i-1].y<22)ends[i].y=ends[i-1].y+22;
 if(ends.length&&ends[ends.length-1].y>T+ph-8){const sh=ends[ends.length-1].y-(T+ph-8);ends.forEach(e=>e.y-=sh)}
 const bx=L+pw+10;
 for(const e of ends){
  el('path',{d:`M${e.x},${e.py}H${L+pw+3}L${bx},${e.y}`,stroke:e.c,'stroke-width':1,fill:'none','stroke-dasharray':'2 2',opacity:.7},svg);
  if(!e.dash)el('circle',{cx:e.x,cy:e.py,r:3.6,fill:e.c,stroke:K.panel,'stroke-width':1.6},svg);
  const txt=fmt(e.v,cfg.dec),w=txt.length*7.6+14;
  el('path',{d:`M${bx},${e.y}l6,-10h${w}v20h${-w}z`,fill:e.dash?K.panel:e.c,stroke:e.c,'stroke-width':e.dash?1:0},svg);
  const tx=el('text',{x:bx+6+w/2,y:e.y+4.5,'text-anchor':'middle','font-size':12.5,'font-weight':700,fill:e.dash?e.c:K.badge,'font-family':FONT},svg);tx.textContent=txt}
 const vl=el('line',{y1:T,y2:T+ph,stroke:K.axis,'stroke-dasharray':'3 3',visibility:'hidden'},svg);
 const tip=document.createElement('div');tip.className='tip';box.appendChild(tip);
 const hit=el('rect',{x:L,y:T,width:pw,height:ph,fill:'transparent'},svg);
 const mv=ev=>{const r=svg.getBoundingClientRect(),px=(ev.clientX-r.left)*W/r.width;if(px<L||px>L+pw){out();return}
  const t=x0+(px-L)/pw*(x1-x0);let rows='',tt=null;
  for(const s of S){const i=bs(s.t,t);if(s.t[i]>t+40&&!s.step)continue;tt=tt===null||(!s.step&&!s.bar)?s.t[i]:tt;rows+=`<div><i style="border-color:${s.bar&&!s.mono?K.pos:s.col}"></i>${s.n}<b>${fmt(s.v[i],cfg.dec)}</b></div>`}
  vl.setAttribute('x1',px);vl.setAttribute('x2',px);vl.setAttribute('visibility','visible');
  tip.innerHTML=`<div class="d">${dstr(tt??t,!cfg.monthly)}</div>`+rows;tip.style.display='block';
  const bw=box.clientWidth,tw=tip.offsetWidth;let lx=px/W*bw+12;if(lx+tw>bw)lx=px/W*bw-tw-12;tip.style.left=lx+'px';tip.style.top='4px'};
 const out=()=>{vl.setAttribute('visibility','hidden');tip.style.display='none'};
 hit.addEventListener('pointermove',mv);hit.addEventListener('pointerdown',mv);hit.addEventListener('pointerleave',out);
}
function drawSec(sec){sec.querySelectorAll('.panel[data-id]').forEach(draw)}
function drawAll(){CS=getComputedStyle(document.documentElement);document.querySelectorAll('section[data-p]').forEach(drawSec)}
try{const th=localStorage.getItem('theme');if(th)document.documentElement.dataset.theme=th}catch(e){}
document.getElementById('thm').onclick=()=>{const r=document.documentElement,n=r.dataset.theme==='dark'?'light':'dark';r.dataset.theme=n;try{localStorage.setItem('theme',n)}catch(e){}drawAll()};
document.querySelectorAll('section[data-p]').forEach(sec=>{
 let saved=null;try{saved=localStorage.getItem('p_'+sec.id)}catch(e){}
 if(saved&&PD[saved]&&sec.querySelector(`.per button[data-v="${saved}"]`))sec.dataset.p=saved;
 sec.querySelectorAll('.per button').forEach(b=>{b.classList.toggle('on',b.dataset.v===sec.dataset.p);
  b.onclick=()=>{sec.dataset.p=b.dataset.v;sec.querySelectorAll('.per button').forEach(x=>x.classList.toggle('on',x===b));try{localStorage.setItem('p_'+sec.id,b.dataset.v)}catch(e){}CS=getComputedStyle(document.documentElement);drawSec(sec)}})});
drawAll();
const navs=[...document.querySelectorAll('.nav a')];const io=new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting)navs.forEach(a=>a.classList.toggle('on',a.getAttribute('href')==='#'+e.target.id))})},{rootMargin:'-40% 0px -55% 0px'});
document.querySelectorAll('section').forEach(x=>io.observe(x));
let rt;window.addEventListener('resize',()=>{clearTimeout(rt);rt=setTimeout(drawAll,150)});
"""


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def seg(opts):
    return '<div class="per">' + "".join(f'<button data-v="{o}">{o.replace("Y", "년")}</button>' for o in opts) + "</div>"


def _cvar(c):
    return f"var({c})" if c.startswith("--") else c


def _legend(series):
    if len(series) < 2:
        return ""
    out = []
    for x in series:
        sty = f"border-color:{_cvar(x['c'])};" + ("border-top-style:dashed;" if x.get("dash") else "") + ("border-top-width:8px;width:9px;" if x.get("bar") else "")
        out.append(f'<span><em style="{sty}"></em>{_esc(x["n"])}</span>')
    return '<div class="legend">' + "".join(out) + "</div>"


def _arrow(c, txt):
    return f'<b class="{"up" if c > 0 else "dn" if c < 0 else ""}">{"▲" if c > 0 else "▼" if c < 0 else ""} {txt.lstrip("+-")}</b>'


def card_html(cid, cfg, st, h, kind):
    if kind == "market":
        qs = "".join(f"<div><span>{a}</span>{_arrow(c, b)}</div>" for a, b, c in st["ch"])
        main = f'{_esc(st["main"])} · ' if st.get("main") else ""
        head = (f'<div class="ph"><div class="pt">{_esc(cfg["title"])}<small>{_esc(cfg["src"])} · {st["date"]}</small></div>'
                f'<div class="pv"><b>{st["last"]}</b><small>{main}최근</small></div></div><div class="qs">{qs}</div>')
    else:
        cls = "up" if st.get("dir", 0) > 0 else "dn" if st.get("dir", 0) < 0 else ""
        head = (f'<div class="ph"><div class="pt">{_esc(cfg["title"])}<small>{_esc(cfg["src"])} · {st["date"]}</small></div>'
                f'<div class="pv"><b>{st["last"]}</b><small class="{cls}">{_esc(st.get("prev", ""))}</small></div></div>')
    return f'<div class="panel" data-id="{cid}">{head}<div class="body">{_legend(cfg["series"])}<div class="plot" data-h="{h}"></div></div></div>'


def section(en, sid, title, desc, opts, default, grid, cards):
    return (f'<section id="{sid}" data-p="{default}"><div class="sh"><div><div class="en">{en}</div><h2>{title}<small>{desc}</small></h2></div>{seg(opts)}</div>'
            f'<div class="grid {grid}">{"".join(cards)}</div></section>')


def calendar_html(ev):
    if not ev:
        return '<div class="calp" style="padding:20px;color:var(--mute)">예정된 일정이 없습니다.</div>'
    wd = "월화수목금토일"
    code = {"미국": "US", "한국": "KR", "중국": "CN", "일본": "JP", "유로": "EU"}
    rows, cur = [], None
    for e in ev:
        d = e["t"].date()
        if d != cur:
            cur = d
            tag = "<em>오늘</em>" if d == TODAY else ("<em>내일</em>" if d == TODAY + dt.timedelta(days=1) else "")
            rows.append(f'<tr class="day"><td colspan="6">{d:%m.%d} ({wd[d.weekday()]}){tag}</td></tr>')
        cc = code.get(e["cty"], e["cty"])
        imp = "<i></i>" * e["imp"] + '<i class="o"></i>' * (3 - e["imp"])
        fc = f'<span class="fc">{_esc(e["fc"])}</span>' if e["fc"] else '<span class="na">-</span>'
        pv = _esc(e["pv"]) if e["pv"] else '<span class="na">-</span>'
        rows.append(f'<tr><td class="tm">{e["t"]:%H:%M}</td><td class="cc"><span class="{cc}">{cc}</span></td><td class="nm">{_esc(e["name"])}</td>'
                    f'<td class="imp">{imp}</td><td class="n pv2">{pv}</td><td class="n">{fc}</td></tr>')
    return ('<div class="calp"><table class="cal"><thead><tr><th>시간(KST)</th><th>국가</th><th>지표</th><th class="imp">중요도</th>'
            '<th class="n pv2">이전</th><th class="n">예상(컨센서스)</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")


TICKER = [("코스피", "KOSPI", 2, "level"), ("코스닥", "KOSDAQ", 2, "level"), ("S&P 500", "SPX", 2, "level"),
          ("나스닥", "NASDAQ", 2, "level"), ("원/달러", "USDKRW", 1, "level"), ("원/100엔", "JPYKRW", 2, "level"),
          ("국고 10년", "KR10Y", 3, "rate"), ("美국채 10년", "US10Y", 2, "rate"), ("WTI", "WTI", 2, "level"),
          ("금", "GOLD", 1, "level"), ("비트코인", "BTC", 0, "level")]


def ticker_html(d):
    out = []
    for name, key, dec, kind in TICKER:
        s = d.get(key, pd.Series(dtype=float)).dropna()
        if len(s) < 2:
            continue
        a, b = s.iloc[-1], s.iloc[-2]
        if kind == "rate":
            ch, txt, val = a - b, f"{abs(a - b) * 100:.0f}bp", f"{a:.{dec}f}%"
        else:
            ch, txt, val = a / b - 1, f"{abs(a / b - 1) * 100:.2f}%", f"{a:,.{dec}f}"
        cls = "up" if ch > 0 else "dn" if ch < 0 else ""
        arw = "▲" if ch > 0 else "▼" if ch < 0 else ""
        out.append(f'<div class="tq"><div class="n">{name}</div><span class="p">{val}</span><span class="c {cls}">{arw}{txt}</span></div>')
    return "".join(out)


def build_html(d, ev, status=None):
    charts, data, stats = build_charts(d)
    for cid in charts:
        if cid.startswith(("ec_", "ex_")):
            charts[cid]["monthly"] = 1
    rates = [card_html(c[0], charts[c[0]], stats[c[0]], 280, "market") for c in RATE_CARDS]
    fx = [card_html(c[0], charts[c[0]], stats[c[0]], 230, "market") for c in FX_CARDS]
    risk = [card_html(c[0], charts[c[0]], stats[c[0]], 220, "market") for c in RISK_CARDS]
    econ = [card_html(c[0], charts[c[0]], stats[c[0]], 200, "econ") for c in ECON_CARDS]
    exp = [card_html("ex_" + hs, charts["ex_" + hs], stats["ex_" + hs], 180, "econ") for _, hs in EXPORT_ITEMS]
    wd = "월화수목금토일"
    fails = ", ".join(f"{k}({v})" for k, v in (status or {}).items())
    L = ["1Y", "3Y", "5Y", "10Y", "20Y", "30Y"]
    body = f"""
<header class="hd"><div class="in"><div class="logo">PB</div><h1>Market Monitor<span>금리 · 환율 · 자산 · 경제지표 · 수출</span></h1>
<div class="rt"><span class="live">{TODAY:%Y.%m.%d}({wd[TODAY.weekday()]}) {NOW:%H:%M} 기준</span><button class="thm" id="thm">화면 전환</button></div></div></header>
<div class="tk"><div class="in">{ticker_html(d)}</div></div>
<div class="nav"><div class="in"><a href="#rates">금리</a><a href="#fx">환율</a><a href="#risk">주식·원자재·코인</a><a href="#cal">주요 일정</a><a href="#econ">경제지표</a><a href="#exp">품목별 수출</a></div></div>
<main class="wrap">
{section("RATES", "rates", "금리", "국채 3·10·30년 / 점선 기준금리", L, "30Y", "g2", rates)}
{section("FX", "fx", "환율", "원화 기준 / 원·헤알 = 원·달러 ÷ 달러·헤알", L, "30Y", "g3", fx)}
{section("EQUITY · COMMODITY · CRYPTO", "risk", "주식 · 원자재 · 코인", "종가 기준", L, "30Y", "g3", risk)}
<section id="cal"><div class="sh"><div><div class="en">CALENDAR</div><h2>향후 3주 주요 일정<small>한국시간 기준 / 예상치는 시장 컨센서스(발표 주간 제공)</small></h2></div></div>{calendar_html(ev)}</section>
{section("MACRO", "econ", "경제지표", "월간 발표치", ["1Y", "3Y", "5Y", "10Y"], "5Y", "g4", econ)}
{section("EXPORTS", "exp", "품목별 수출", "관세청 HS 4단위, 억 달러 / 막대 월별 · 선 12개월 평균", ["3Y", "5Y", "10Y"], "10Y", "g4", exp)}
<div class="note">출처: 한국은행 ECOS, FRED(세인트루이스 연준), Yahoo Finance, 일본 재무성, 브라질 중앙은행·국고청, 관세청, ForexFactory.
미국 30년물은 2002~2006년 발행 중단으로 해당 기간 공백. 브라질 국채는 Tesouro Direto 고정금리 이표채(NTN-F) 중 잔존 10년에 가장 가까운 종목(2000년대 초반은 단기물만 존재).
금은 COMEX 선물 근월물. 품목별 수출은 HS 4단위 근사치로 산업부 품목 분류와 다를 수 있음(디스플레이 HS 8524는 2022년 신설). ▲ 상승 / ▼ 하락.
{("수집 실패·캐시 사용: " + fails) if fails else ""}</div>
</main>"""
    payload = json.dumps({"CH": charts, "DATA": data}, ensure_ascii=False, separators=(",", ":"))
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Market Monitor {TODAY:%m/%d}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css">
<style>{CSS}</style></head><body>{body}
<script>const _P={payload};const CH=_P.CH,DATA=_P.DATA;{JS}</script></body></html>"""


# =====================================================================
# 7. 실행
# =====================================================================
def main():
    raw, ff, fr, status = fetch_all()
    d = derive(raw)
    ev = build_calendar(ff, fr)
    html = build_html(d, ev, status)
    (OUT_DIR / f"dashboard_{TODAY:%Y%m%d}.html").write_text(html, encoding="utf-8")
    (OUT_DIR / "dashboard_latest.html").write_text(html, encoding="utf-8")
    print("\n완료 →", OUT_DIR / "dashboard_latest.html")
    if status:
        print("수집 실패 항목:", ", ".join(status))


if __name__ == "__main__":
    main()
