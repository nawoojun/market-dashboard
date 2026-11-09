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
import re
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


def _get(url, timeout=(15, 90), tries=3, **kw):
    """접속 실패·서버 오류 시 최대 3번 재시도"""
    for i in range(tries):
        try:
            r = S.get(url, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(4 * (i + 1))


def first_ok(*fns):
    """앞의 소스가 실패하면 다음 소스로 대체"""
    def run():
        err = None
        for fn in fns:
            try:
                s = fn()
                if s is not None and len(s):
                    return s
            except Exception as e:  # noqa
                err = e
                print(f"    → 대체 소스 시도 ({str(e)[:80]})")
        raise err or RuntimeError("모든 소스 실패")
    return run


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


def fetch_cnn_fg():
    """CNN Fear & Greed (비공식 공개 JSON). 시작일이 2020-07-14보다 이르면 CNN 서버가 500 오류를 냄"""
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept": "application/json, text/plain, */*", "Referer": "https://edition.cnn.com/"}
    base = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
    err = None
    for start in ("2020-07-14", (TODAY - dt.timedelta(days=730)).isoformat(), ""):
        try:
            js = _get(base + (f"/{start}" if start else ""), headers=hdr, tries=2).json()
            pts = js["fear_and_greed_historical"]["data"]
            s = pd.Series([float(x["y"]) for x in pts], index=pd.to_datetime([x["x"] for x in pts], unit="ms").normalize())
            cur = js.get("fear_and_greed", {})
            if cur.get("score") is not None and cur.get("timestamp"):
                s.loc[pd.to_datetime(cur["timestamp"]).tz_localize(None).normalize()] = float(cur["score"])
            return s.groupby(level=0).last().sort_index()
        except Exception as e:  # noqa
            err = e
    raise err


def fetch_btc_fg():
    """alternative.me Crypto Fear & Greed (2018년~)"""
    js = _get("https://api.alternative.me/fng/", params={"limit": 0, "format": "json"}).json()
    s = pd.Series([float(x["value"]) for x in js["data"]], index=pd.to_datetime([int(x["timestamp"]) for x in js["data"]], unit="s").normalize())
    return s.groupby(level=0).last().sort_index()


_SH_HIST = None
FG_COMP = {}  # 지수 구성 요소(최근값)


def fetch_stockhub_fg(market):
    """StockHub 공포탐욕지수(market='kr' 한국 · 'us' 미국/CNN) — 현재값 + 최근 30일"""
    global _SH_HIST
    cur = _get("https://stockhub.kr/api/fear-greed", params={"market": market},
               headers={"Referer": "https://stockhub.kr/fear-greed"}).json()
    if _SH_HIST is None:
        _SH_HIST = _get("https://stockhub.kr/api/fear-greed/history", headers={"Referer": "https://stockhub.kr/fear-greed"}).json().get("data", [])
    pts = [(x["date"], x.get(market)) for x in _SH_HIST if x.get(market) is not None]
    s = pd.Series([float(v) for _, v in pts], index=pd.to_datetime([dd for dd, _ in pts]))
    if cur.get("score") is not None:
        ts = pd.Timestamp(cur.get("timestamp", TODAY.isoformat())[:10])
        s.loc[ts] = float(cur["score"])
        FG_COMP[market] = cur.get("indicators") or {}
    return s.groupby(level=0).last().sort_index()


def load_prev_dashboard():
    """직전에 게시된 대시보드에서 차트 데이터를 읽어옴(출처가 짧은 기간만 주는 지표의 기록을 이어 붙이기 위함)"""
    url = os.getenv("DASHBOARD_URL", "")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    if not url and "/" in repo:
        owner, name = repo.split("/", 1)
        url = f"https://{owner.lower()}.github.io/{name}/"
    if not url:
        return {}
    try:
        html = _get(url, tries=2).text
        m = re.search(r"const _P=(\{.*?\});const CH=", html, re.S)
        return json.loads(m.group(1))["DATA"] if m else {}
    except Exception as e:  # noqa
        print("  [참고] 이전 대시보드 기록 없음:", str(e)[:60])
        return {}


def merge_prev(s, prev_data, key):
    """이번에 받은 값 + 이전 대시보드에 남아 있던 값(겹치면 새 값 우선)"""
    p = prev_data.get(key) or {}
    if not p.get("t"):
        return s
    old = pd.Series(p["v"], index=pd.to_datetime(p["t"], unit="D"))
    if s is None or len(s) == 0:
        return old
    return s.combine_first(old).sort_index()


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
        return pd.Series(dtype=float, index=pd.DatetimeIndex([])), "실패"


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
    "USDKRW": first_ok(lambda: fetch_ecos("731Y001", "D", "0000001", LONG_START), lambda: fetch_yahoo("KRW=X", LONG_START)),
    "JPYKRW": first_ok(lambda: fetch_ecos("731Y001", "D", "0000002", LONG_START), lambda: fetch_yahoo("JPYKRW=X", LONG_START) * 100),
    "USDBRL": lambda: fetch_bcb(1, dt.date(1994, 7, 1)),
    # 주식·원자재·코인
    "KOSPI": first_ok(lambda: fetch_ecos("802Y001", "D", "0001000", LONG_START), lambda: fetch_yahoo("^KS11", LONG_START)),
    "KOSDAQ": first_ok(lambda: fetch_ecos("802Y001", "D", "0089000", LONG_START), lambda: fetch_yahoo("^KQ11", LONG_START)),
    "SPX": lambda: fetch_yahoo("^GSPC", LONG_START),
    "NASDAQ": lambda: fetch_yahoo("^IXIC", LONG_START),
    "WTI": lambda: fetch_fred("DCOILWTICO", LONG_START),
    # 시장 심리
    "VIX": lambda: fetch_fred("VIXCLS", LONG_START),
    "FG_US": first_ok(fetch_cnn_fg, lambda: fetch_stockhub_fg("us")),
    "FG_BTC": fetch_btc_fg,
    "FG_KR": lambda: fetch_stockhub_fg("kr"),
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
    "KR_CPI": first_ok(lambda: fetch_ecos("901Y009", "M", "0", ECON_START), lambda: fetch_fred("KORCPIALLMINMEI", ECON_START)),
    "BR_IPCA12": lambda: fetch_bcb(13522, ECON_START),
    # 일정 상세용
    "ICSA": lambda: fetch_fred("ICSA", ECON_START),
    "GDPGR": lambda: fetch_fred("A191RL1Q225SBEA", ECON_START),
    "JTSJOL": lambda: fetch_fred("JTSJOL", ECON_START),
    "UMCSENT": lambda: fetch_fred("UMCSENT", ECON_START),
    "INDPRO": lambda: fetch_fred("INDPRO", ECON_START),
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
    # 실적 일정 · 컨센서스 · 주가
    try:
        us_earn = fetch_us_earnings()
    except Exception as e:
        print("  [실패] 미국 실적 일정:", e); us_earn = []
    try:
        kr_earn = [x for x in fetch_kr_earnings() if "실적" in x["title"] and x["code"]]
    except Exception as e:
        print("  [실패] 한국 실적 일정:", e); kr_earn = []
    kr_cons = {}
    p3 = TODAY - dt.timedelta(days=3 * 365)
    for sym in sorted({r["symbol"] for r in us_earn})[:40]:
        try:
            raw["PX_" + sym] = fetch_yahoo(sym, p3)
        except Exception:
            pass
    for code, mk in sorted({(x["code"] + "0" if len(x["code"]) == 5 else x["code"], x["mk"]) for x in kr_earn})[:60]:
        try:
            kr_cons[code] = fetch_naver_consensus(code)
            time.sleep(0.3)
        except Exception as e:
            print("  [실패] 네이버 컨센서스", code, str(e)[:60])
        try:
            raw["PX_" + code] = fetch_yahoo(code + (".KS" if mk == "1" else ".KQ"), p3)
        except Exception:
            pass
    prev = load_prev_dashboard()
    for k in ("FG_KR",):
        raw[k] = merge_prev(raw.get(k), prev, k)
    print(f"실적 일정: 미국 {len(us_earn)}건 · 한국 {len(kr_earn)}건 · 한국 컨센서스 {sum(1 for v in kr_cons.values() if v)}건")
    return raw, ff, fr, {k: v for k, v in status.items() if v}, us_earn, kr_earn, kr_cons


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
    if s is None or len(s) == 0 or not isinstance(s.index, pd.DatetimeIndex):
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    s = s.copy(); s.index = s.index.to_period("M").to_timestamp()
    p = s.copy(); p.index = p.index + pd.DateOffset(years=1)
    return ((s / p.reindex(s.index) - 1) * 100).dropna()


def mom(s):
    if s is None or len(s) == 0 or not isinstance(s.index, pd.DatetimeIndex):
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    s = s.copy(); s.index = s.index.to_period("M").to_timestamp()
    prev = s.copy(); prev.index = prev.index + pd.DateOffset(months=1)
    return ((s / prev.reindex(s.index) - 1) * 100).dropna()


def mdiff(s):
    if s is None or len(s) == 0 or not isinstance(s.index, pd.DatetimeIndex):
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    s = s.copy(); s.index = s.index.to_period("M").to_timestamp()
    prev = s.copy(); prev.index = prev.index + pd.DateOffset(months=1)
    return (s - prev.reindex(s.index)).dropna()


def kr_fear_greed(kospi, usdkrw):
    """한국 공포·탐욕 지수(자체 산출, 0~100)
    코스피 ① 125일 이평 대비 괴리 ② 52주 고저 내 위치 ③ RSI(14) ④ 20일 변동성(역) ⑤ 원/달러 20일 변화(역)
    각 요소를 최근 3년 백분위(0~100)로 바꿔 평균"""
    k = kospi.dropna()
    if len(k) < 900:
        return pd.Series(dtype=float)
    fx = usdkrw.reindex(k.index.union(usdkrw.index)).ffill().reindex(k.index)
    ret = k.pct_change()
    up, dn = ret.clip(lower=0).rolling(14).mean(), (-ret.clip(upper=0)).rolling(14).mean()
    comps = pd.DataFrame({
        "mom": k / k.rolling(125).mean() - 1,
        "pos": (k - k.rolling(250).min()) / (k.rolling(250).max() - k.rolling(250).min()),
        "rsi": 100 - 100 / (1 + up / dn.replace(0, np.nan)),
        "vol": -ret.rolling(20).std(),
        "fx": -fx.pct_change(20),
    })
    pct = comps.rolling(750, min_periods=500).rank(pct=True) * 100
    return pct.mean(axis=1, skipna=False).dropna().round(1)


def us_fear_greed(spx, vix):
    """CNN 접속이 막힐 때 쓰는 미국 공포·탐욕 대체 지수(자체 산출, 0~100)
    S&P500 ① 125일 이평 괴리 ② 52주 위치 ③ RSI(14) ④ VIX(역) ⑤ VIX의 50일 평균 대비(역)"""
    k = spx.dropna()
    if len(k) < 900:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    v = vix.reindex(k.index.union(vix.index)).ffill().reindex(k.index)
    ret = k.pct_change()
    up, dn = ret.clip(lower=0).rolling(14).mean(), (-ret.clip(upper=0)).rolling(14).mean()
    comps = pd.DataFrame({
        "mom": k / k.rolling(125).mean() - 1,
        "pos": (k - k.rolling(250).min()) / (k.rolling(250).max() - k.rolling(250).min()),
        "rsi": 100 - 100 / (1 + up / dn.replace(0, np.nan)),
        "vix": -v,
        "vixr": -(v / v.rolling(50).mean()),
    })
    pct = comps.rolling(750, min_periods=500).rank(pct=True) * 100
    return pct.mean(axis=1, skipna=False).dropna().round(1)


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
    d["ICSA_K"] = g("ICSA") / 1000; d["JOLTS_M"] = g("JTSJOL") / 1000; d["INDPRO_Y"] = yoy(g("INDPRO"))
    d["FG_US_PROXY"] = False
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
SENT_LINE = [("st_vix", "VIX 변동성 지수", "VIX", 2, "FRED(CBOE)")]
SENT_FG = [  # id, 제목, key, 출처
    ("st_fgus", "美 공포·탐욕 지수", "FG_US", "CNN Fear & Greed"),
    ("st_fgkr", "韓 공포·탐욕 지수", "FG_KR", "StockHub 한국 공포탐욕지수"),
    ("st_fgbtc", "비트코인 공포·탐욕 지수", "FG_BTC", "alternative.me"),
]
FG_ZONES = [(0, 25, "극단적 공포"), (25, 45, "공포"), (45, 55, "중립"), (55, 75, "탐욕"), (75, 100.1, "극단적 탐욕")]


def fg_label(v):
    return next(n for a, b, n in FG_ZONES if a <= v < b)


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
    for cid, title, key, dec, src in SENT_LINE:
        put(key)
        charts[cid] = {"title": title, "src": src, "series": [{"k": key, "n": "VIX", "c": DARK, "w": 2.2}], "dec": dec, "unit": "",
                       "bands": [[30, 200, "--dn", .10], [20, 30, "--dn", .05]], "refs": [[20, "20"], [30, "30"]]}
        stats[cid] = card_stats(d, key, "level", dec)
    for cid, title, key, src in SENT_FG:
        put(key, dec=1)
        charts[cid] = {"title": title, "src": src, "series": [{"k": key, "n": "지수", "c": RED, "w": 2.2}], "dec": 0, "unit": "",
                       "ymin": 0, "ymax": 100,
                       "bands": [[0, 25, "--dn", .16], [25, 45, "--dn", .07], [55, 75, "--up", .07], [75, 100, "--up", .16]],
                       "refs": [[25, "25"], [50, "50"], [75, "75"]]}
        x = d.get(key, pd.Series(dtype=float)).dropna()
        if len(x) >= 2:
            ago = lambda days: asof(x, x.index[-1] - pd.Timedelta(days=days))
            comp = FG_COMP.get({"FG_KR": "kr"}.get(key, ""), {})
            stats[cid] = {"val": float(x.iloc[-1]), "label": fg_label(x.iloc[-1]), "date": x.index[-1].strftime("%y.%m.%d"), "comp": comp,
                          "hist": [[n, None if pd.isna(v) else float(v)] for n, v in (("전일", x.iloc[-2]), ("1주 전", ago(7)), ("1개월 전", ago(30)), ("1년 전", ago(365)))]}
        else:
            stats[cid] = {"val": None, "label": "수집 실패", "date": "", "hist": []}
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


def _ffnum(x):
    """'0.3%' '194K' '3.86M' → float (단위 제외)"""
    if not x:
        return None
    try:
        return float(str(x).replace("%", "").replace("K", "").replace("M", "").replace("B", "").replace(",", "").strip())
    except ValueError:
        return None


def _m(s, months_back=0):
    """월별 시계열의 최근값(또는 n개월 전)과 기준월"""
    s = s.dropna()
    if len(s) <= months_back:
        return None, None
    return float(s.iloc[-1 - months_back]), s.index[-1 - months_back]


def _implied_yoy(idx, mm_fc):
    """전월비 컨센서스로 추정한 전년비 = (최근 지수 × (1+전월비)) / 12개월 전 지수 - 1"""
    idx = idx.dropna()
    if mm_fc is None or len(idx) < 13:
        return None
    last = idx.index[-1]
    nxt = (last + pd.DateOffset(months=1)).to_period("M").to_timestamp()
    bp = (nxt - pd.DateOffset(years=1)).to_period("M")
    per = idx.index.to_period("M")
    base = idx[per == bp]
    if base.empty:  # 기준월 공백(예: 2025년 10월 셧다운) → 앞뒤 달 기하평균으로 보간
        a, b = idx[per == bp - 1], idx[per == bp + 1]
        if a.empty or b.empty:
            return None
        bv = float(np.sqrt(a.iloc[0] * b.iloc[0]))
    else:
        bv = float(base.iloc[0])
    return (idx.iloc[-1] * (1 + mm_fc / 100) / bv - 1) * 100


def _fmt(v, dec=1, unit="%", sign=False):
    if v is None or pd.isna(v):
        return "-"
    return f"{v:+,.{dec}f}{unit}" if sign else f"{v:,.{dec}f}{unit}"


# 경제지표 그룹별: 표시 이름, 상세 행 정의, 차트 정의
#  행: (라벨, 이전값 함수, ForexFactory 제목(예상), 예상값 가공)
def macro_detail(grp, d, ffmap):
    g = lambda k: d.get(k, pd.Series(dtype=float, index=pd.DatetimeIndex([])))
    fc = lambda title: ffmap.get(title, {}).get("forecast")
    pv = lambda title: ffmap.get(title, {}).get("previous")
    rows, chart, main = [], None, None

    def mrow(label, series, dec=1, unit="%", ff_title=None, implied=None, wfmt="%y.%m"):
        v, when = _m(series)
        if v is not None:
            wl = f"{when.year % 100}.{(when.month - 1) // 3 + 1}Q" if wfmt == "Q" else when.strftime(wfmt)
            prev = f"{_fmt(v, dec, unit)} ({wl})"
        else:
            prev = pv(ff_title) or "-"
        f = fc(ff_title) if ff_title else None
        if implied is not None:
            f = f"≈{implied:.1f}% (추정)"
        rows.append([label, prev, f or "-"])
        return prev, (f or "")

    if grp == "CPI":
        yo = _implied_yoy(g("CPIAUCSL"), _ffnum(fc("CPI m/m")))
        cyo = _implied_yoy(g("CPILFESL"), _ffnum(fc("Core CPI m/m")))
        main = mrow("CPI 전년비", g("US_CPI"), implied=yo)
        mrow("CPI 전월비", mom(g("CPIAUCSL")), ff_title="CPI m/m")
        mrow("근원 CPI 전년비", g("US_CCPI"), implied=cyo)
        mrow("근원 CPI 전월비", mom(g("CPILFESL")), ff_title="Core CPI m/m")
        chart = [("US_CPI", "CPI 전년비", "--c1"), ("US_CCPI", "근원 CPI 전년비", "--c2")]
    elif grp == "PPI":
        yo = _implied_yoy(g("PPIFID"), _ffnum(fc("PPI m/m")))
        main = mrow("PPI 전년비", g("US_PPI"), implied=yo)
        mrow("PPI 전월비", mom(g("PPIFID")), ff_title="PPI m/m")
        rows.append(["근원 PPI 전월비", pv("Core PPI m/m") or "-", fc("Core PPI m/m") or "-"])
        chart = [("US_PPI", "PPI 전년비", "--c1")]
    elif grp == "PCE":
        yo = _implied_yoy(g("PCEPILFE"), _ffnum(fc("Core PCE Price Index m/m")))
        main = mrow("근원 PCE 전년비", g("US_CPCE"), implied=yo)
        mrow("근원 PCE 전월비", mom(g("PCEPILFE")), ff_title="Core PCE Price Index m/m")
        chart = [("US_CPCE", "근원 PCE 전년비", "--c1")]
    elif grp == "NFP":
        main = mrow("비농업 고용(천명)", g("US_NFP"), 0, "", "Non-Farm Employment Change")
        mrow("실업률", g("US_UR"), ff_title="Unemployment Rate")
        rows.append(["시간당 임금 전월비", pv("Average Hourly Earnings m/m") or "-", fc("Average Hourly Earnings m/m") or "-"])
        chart = [("US_NFP", "비농업 고용(천명)", "--c1", 1)]
    elif grp == "RETAIL":
        main = mrow("소매판매 전월비", g("US_RS"), ff_title="Retail Sales m/m")
        rows.append(["근원 소매판매 전월비", pv("Core Retail Sales m/m") or "-", fc("Core Retail Sales m/m") or "-"])
        chart = [("US_RS", "소매판매 전월비", "--c1", 1)]
    elif grp == "CLAIMS":
        main = mrow("신규 실업수당청구(천건)", g("ICSA_K"), 0, "K", "Unemployment Claims", wfmt="%m.%d")
        chart = [("ICSA_K", "신규 실업수당청구(천건)", "--c1")]
    elif grp == "GDP":
        main = mrow("실질 GDP 성장률(연율)", g("GDPGR"), ff_title="Advance GDP q/q", wfmt="Q")
        for t in ("Prelim GDP q/q", "Final GDP q/q"):
            if fc(t):
                rows[-1][2] = fc(t)
        chart = [("GDPGR", "GDP 성장률(연율)", "--c1", 1)]
    elif grp == "JOLTS":
        main = mrow("JOLTS 구인건수(백만)", g("JOLTS_M"), 2, "M", "JOLTS Job Openings")
        chart = [("JOLTS_M", "구인건수(백만)", "--c1")]
    elif grp == "UOM":
        main = mrow("미시간대 소비자심리", g("UMCSENT"), 1, "", "Prelim UoM Consumer Sentiment")
        chart = [("UMCSENT", "소비자심리", "--c1")]
    elif grp == "IP":
        main = mrow("산업생산 전년비", g("INDPRO_Y"))
        mrow("산업생산 전월비", mom(g("INDPRO")), ff_title="Industrial Production m/m")
        chart = [("INDPRO_Y", "산업생산 전년비", "--c1")]
    elif grp == "FOMC":
        v, _ = _m(g("USFF"))
        rows.append(["기준금리(상단)", _fmt(v, 2), fc("Federal Funds Rate") or "-"])
        main = (rows[-1][1], rows[-1][2] if rows[-1][2] != "-" else "")
        chart = [("USFF", "기준금리(상단)", "--c1", 0, 1)]
    elif grp == "BOK":
        v, _ = _m(g("KRBASE"))
        rows.append(["한은 기준금리", _fmt(v, 2), "-"])
        main = (rows[-1][1], "")
        chart = [("KRBASE", "한은 기준금리", "--c1", 0, 1)]
    elif grp == "KREXP":
        for nm, hs in EXPORT_ITEMS[:4]:
            s = g("EXP_" + hs)
            if len(s) >= 13:
                rows.append([f"{nm} 수출(최근월)", f"{s.iloc[-1]:,.1f}억$ ({s.index[-1]:%y.%m})", f"전년비 {(s.iloc[-1] / s.iloc[-13] - 1) * 100:+.1f}%"])
        main = ("", "")
        chart = [("EXP_8542", "반도체 월 수출(억$)", "--cb", 1, 0, 1), ("EXPMA_8542", "12개월 평균", "--c1")]
    return rows, ([chart] if chart else []), main or ("", "")


GRP_NAME = {"CPI": "소비자물가(CPI)", "PPI": "생산자물가(PPI)", "NFP": "고용보고서(비농업고용·실업률)", "PCE": "PCE 물가",
            "GDP": "GDP", "RETAIL": "소매판매", "CLAIMS": "신규 실업수당청구", "JOLTS": "JOLTS 구인건수",
            "UOM": "미시간대 소비자심리", "IP": "산업생산", "FOMC": "FOMC 기준금리 결정", "ISM": "ISM PMI",
            "ECB": "ECB 통화정책", "BOJ": "BOJ 통화정책"}
GRP_IMP = {"CPI": 3, "PPI": 3, "NFP": 3, "PCE": 3, "GDP": 3, "RETAIL": 3, "FOMC": 3, "CLAIMS": 2, "JOLTS": 2, "UOM": 2, "IP": 2, "ISM": 3}


def fetch_us_earnings(min_cap=5e10):
    """Nasdaq 실적발표 캘린더 — 시가총액 기준 대형주, EPS 컨센서스 포함"""
    out = []
    hdr = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
    for i in range(0, 22):
        day = TODAY + dt.timedelta(days=i)
        if day.weekday() >= 5:
            continue
        try:
            js = _get("https://api.nasdaq.com/api/calendar/earnings", params={"date": day.isoformat()}, headers=hdr, tries=2).json()
        except Exception as e:
            print("  [실패] Nasdaq 실적", day, str(e)[:60]); continue
        for r in ((js.get("data") or {}).get("rows") or []):
            cap = _ffnum((r.get("marketCap") or "").replace("$", "")) or 0
            if cap >= min_cap and "." not in r.get("symbol", ""):
                out.append({**r, "date": day.isoformat(), "cap": cap})
    return out


def fetch_kr_earnings():
    """KRX KIND IR 일정 중 '실적' 발표 (코스피·코스닥)"""
    out = []
    for mk in ("1", "2"):
        r = S.post("https://kind.krx.co.kr/corpgeneral/irschedule.do", timeout=60,
                   headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "X-Requested-With": "XMLHttpRequest",
                            "Referer": "https://kind.krx.co.kr/corpgeneral/irschedule.do?method=searchIRScheduleMain"},
                   data={"method": "searchIRScheduleSub", "forward": "searchirschedule_sub", "currentPageSize": "3000", "pageIndex": "1",
                         "orderMode": "4", "orderStat": "A", "fromDate": TODAY.isoformat(), "toDate": (TODAY + dt.timedelta(days=21)).isoformat(),
                         "searchFromDate": TODAY.isoformat(), "searchToDate": (TODAY + dt.timedelta(days=21)).isoformat(), "marketType": mk})
        html = r.content.decode("utf-8", errors="replace")
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
            tds = re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)
            if len(tds) < 6:
                continue
            txt = lambda h: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", h)).strip()
            code = re.search(r"companysummary_open\('(\w+)'\)", tds[1])
            out.append({"mk": mk, "corp": txt(tds[1]), "code": code.group(1) if code else "", "title": txt(tds[2]),
                        "date": txt(tds[4]), "time": txt(tds[5])})
    return out


def _decode_kr(content):
    """네이버·와이즈리포트 페이지 인코딩(UTF-8/EUC-KR) 자동 판별"""
    for enc in ("utf-8", "cp949"):
        try:
            t = content.decode(enc)
            if "영업이익" in t or "매출" in t:
                return t
        except UnicodeDecodeError:
            pass
    return content.decode("cp949", errors="replace")


def _parse_fin_table(tb):
    """재무표 HTML → {'q': [기간...], 'sales': [...], 'op': [...], 'np': [...]} (분기 마지막 6개)"""
    txt = lambda h: re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", h))
    per_re = re.compile(r"(20\d{2})[./](\d{2})")
    hdr = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tb, re.S):
        cells = [txt(x) for x in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.S)]
        ps = [c for c in cells if per_re.search(c)]
        if len(ps) >= 4:
            hdr = [per_re.search(c).group(1) + "." + per_re.search(c).group(2) + ("(E)" if "E" in c or "추정" in c else "") for c in ps]
            break
    rows = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", tb, re.S):
        th = re.search(r"<th[^>]*>(.*?)</th>", tr, re.S)
        if not th:
            continue
        lab = txt(th.group(1))
        for key, names in (("sales", ("매출액", "영업수익", "순영업수익", "매출액(수익)")), ("op", ("영업이익",)), ("np", ("당기순이익", "지배주주순이익"))):
            if key not in rows and any(lab.startswith(n) for n in names) and "률" not in lab and "발표" not in lab:
                rows[key] = [txt(x) for x in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
    if len(hdr) < 4 or "op" not in rows:
        return None
    n = min(len(hdr), len(rows["op"]))
    num = lambda v: _ffnum(v) if v not in ("", "-", "N/A") else None
    pick = lambda k: [num(x) for x in rows.get(k, [""] * n)[:n]][-6:]
    return {"q": hdr[:n][-6:], "sales": pick("sales"), "op": pick("op"), "np": pick("np")}


_NAVER_DIAG = []


def fetch_naver_consensus(code6):
    """분기 매출·영업이익·순이익 컨센서스(억원). ① 네이버 금융 기업실적분석 ② 와이즈리포트(네이버 기업정보) 분기 재무"""
    r = _get(f"https://finance.naver.com/item/main.naver?code={code6}", tries=2,
             headers={"Referer": "https://finance.naver.com/", "Accept-Language": "ko-KR,ko;q=0.9"})
    html = _decode_kr(r.content)
    i = html.find("cop_analysis")
    if i >= 0:
        tb = html[i: html.find("</table>", i)]
        # 네이버 표는 연간 4칸 + 분기 6칸 → 분기만 사용
        res = _parse_fin_table(tb)
        if res:
            return res
    if len(_NAVER_DIAG) < 3:
        title = re.search(r"<title>(.*?)</title>", html, re.S)
        _NAVER_DIAG.append(f"{code6}: HTTP {r.status_code}, {len(html)}자, title={title.group(1).strip()[:40] if title else '-'}, cop_analysis={'있음' if i >= 0 else '없음'}")
        print("    [진단] 네이버", _NAVER_DIAG[-1])
    try:
        r2 = _get("https://navercomp.wisereport.co.kr/v2/company/ajax/cF1001.aspx", tries=2,
                  params={"cmp_cd": code6, "fin_typ": "0", "freq_typ": "Q"},
                  headers={"Referer": f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={code6}"})
        res = _parse_fin_table(_decode_kr(r2.content))
        if res:
            return res
        if len(_NAVER_DIAG) < 6:
            _NAVER_DIAG.append(f"{code6}: 와이즈리포트 {r2.status_code}, {len(r2.content)}B")
            print("    [진단]", _NAVER_DIAG[-1])
    except Exception as e:  # noqa
        print("    [진단] 와이즈리포트 실패", code6, str(e)[:60])
    return None


def _kr_quarter(title):
    m = re.search(r"'?(\d{2,4})\s*(?:년|회계연도|년도)?\D{0,8}?([1-4])\s*분기", title) or re.search(r"([1-4])Q\s*'?(\d{2,4})", title)
    if not m:
        return None
    y, q = (m.group(1), m.group(2)) if "분기" in m.group(0) else (m.group(2), m.group(1))
    y = int(y) + (2000 if len(y) == 2 else 0)
    return f"{y}.{int(q) * 3:02d}"


def build_calendar(ff, fred_rel, d=None, us_earn=None, kr_earn=None, kr_cons=None):
    d = d or {}
    start, end = NOW - dt.timedelta(hours=6), NOW + dt.timedelta(days=21)
    ev = []
    # 1) 경제지표: (날짜, 그룹) 단위로 묶기
    groups = {}
    for x in ff:
        t = dt.datetime.fromisoformat(x["date"]).astimezone(KST)
        name, grp = FF_TITLES.get(x["title"], (x["title"], None))
        if grp is None:  # 그룹 없는 일정(연설 등)
            if x["impact"] == "High" or "Chair" in x["title"]:
                ev.append({"t": t, "cty": FF_COUNTRY[x["country"]], "name": name, "imp": 3 if x["impact"] == "High" else 2,
                           "fc": x.get("forecast") or "", "pv": x.get("previous") or "", "kind": "macro"})
            continue
        if x["country"] != "USD":
            ev.append({"t": t, "cty": FF_COUNTRY[x["country"]], "name": name, "imp": 3 if x["impact"] == "High" else 2,
                       "fc": x.get("forecast") or "", "pv": x.get("previous") or "", "kind": "macro"})
            continue
        gk = (t.date(), grp)
        groups.setdefault(gk, {"t": t, "ff": {}})["ff"][x["title"]] = x
        groups[gk]["t"] = min(groups[gk]["t"], t)
    for date_str, rname in fred_rel:
        for k, (name, et, imp, grp) in US_RELEASES.items():
            if rname.startswith(k):
                t = et_to_kst(date_str, et)
                groups.setdefault((t.date(), grp), {"t": t, "ff": {}})
    for ds in FOMC_DATES:
        t = et_to_kst(ds, "14:00")
        groups.setdefault((t.date(), "FOMC"), {"t": t, "ff": {}})
    for (day, grp), gi in groups.items():
        rows, chart, (pv, fc) = macro_detail(grp, d, gi["ff"])
        ev.append({"t": gi["t"], "cty": "미국", "name": GRP_NAME.get(grp, grp), "imp": GRP_IMP.get(grp, 2), "pv": pv, "fc": fc,
                   "kind": "macro", "rows": rows, "charts": chart, "rows_hdr": ["지표", "이전", "예상"], "per": "3Y"})
    for ds in BOK_DATES:
        day = dt.date.fromisoformat(ds)
        rows, chart, (pv, fc) = macro_detail("BOK", d, {})
        ev.append({"t": dt.datetime(day.year, day.month, day.day, 10, 0, tzinfo=KST), "cty": "한국", "name": "한은 금통위 기준금리 결정",
                   "imp": 3, "pv": pv, "fc": fc, "kind": "macro", "rows": rows, "charts": chart, "rows_hdr": ["지표", "현재", "예상"], "per": "5Y"})
    for k in range(0, 2):
        y, m = (TODAY.year, TODAY.month + k) if TODAY.month + k <= 12 else (TODAY.year + 1, 1)
        for dd, nm in ((1, "월간 수출입동향(산업부)"), (11, "1~10일 수출(관세청 잠정)"), (21, "1~20일 수출(관세청 잠정)")):
            day = _next_bday(dt.date(y, m, dd))
            rows, chart, _ = macro_detail("KREXP", d, {})
            ev.append({"t": dt.datetime(day.year, day.month, day.day, 9, 0, tzinfo=KST), "cty": "한국", "name": nm, "imp": 2, "pv": "", "fc": "",
                       "kind": "macro", "rows": rows, "charts": chart, "rows_hdr": ["품목", "최근", "전년비"], "per": "3Y"})
    # 2) 미국 실적
    for r in (us_earn or []):
        tm = r.get("time", "")
        if "pre" in tm:
            t, when = et_to_kst(r["date"], "07:00"), "장 시작 전"
        elif "after" in tm:
            t, when = et_to_kst(r["date"], "16:05"), "장 마감 후"
        else:
            t, when = et_to_kst(r["date"], "12:00"), "시간 미정"
        eps, ly = _ffnum((r.get("epsForecast") or "").replace("$", "")), _ffnum((r.get("lastYearEPS") or "").replace("$", ""))
        cap = r["cap"]
        growth = f"{(eps / ly - 1) * 100:+.0f}%" if eps is not None and ly not in (None, 0) and ly > 0 else "-"
        rows = [["회계분기", r.get("fiscalQuarterEnding", "-"), ""], ["EPS 컨센서스", r.get("epsForecast") or "-", f"애널리스트 {r.get('noOfEsts', '-')}명"],
                ["전년 동기 EPS", r.get("lastYearEPS") or "-", f"예상 증가율 {growth}"], ["시가총액", f"${cap / 1e9:,.0f}B", when]]
        sym = r["symbol"]
        ev.append({"t": t, "cty": "미국", "name": f'{re.sub(r",? (Inc\.|Incorporated|Corporation|Holdings? Inc\.|Ltd\.|plc|Limited|Company|Co\.)\b", "", r["name"]).strip()} ({sym})',
                   "imp": 3 if cap >= 5e11 else 2 if cap >= 1e11 else 1, "pv": r.get("lastYearEPS") or "", "fc": r.get("epsForecast") or "",
                   "kind": "earn_us", "tag": when, "rows": rows, "rows_hdr": ["항목", "값", "비고"],
                   "charts": [[("PX_" + sym, f"{sym} 주가($)", "--c1")]], "per": "1Y"})
    # 3) 한국 실적
    seen = set()
    for r in (kr_earn or []):
        if "실적" not in r["title"] or not r["code"]:
            continue
        key = (r["corp"], r["date"])
        if key in seen:
            continue
        seen.add(key)
        day = dt.date.fromisoformat(r["date"])
        hh, mm_ = (r["time"].split(":") + ["0"])[:2] if re.match(r"\d", r["time"]) else ("0", "0")
        t = dt.datetime(day.year, day.month, day.day, int(hh), int(mm_), tzinfo=KST)
        code6 = r["code"] + "0" if len(r["code"]) == 5 else r["code"]
        qlab = _kr_quarter(r["title"])
        cons = (kr_cons or {}).get(code6)
        pv = fc = ""
        rows = [["발표", r["title"], r["time"] if re.match(r"\d", r["time"]) else "시간 미정"]]
        charts = [[("PX_" + code6, "주가(원)", "--c1")]]
        if cons and qlab:
            idx = next((i for i, h in enumerate(cons["q"]) if h.startswith(qlab)), None)
            pidx = next((i for i, h in enumerate(cons["q"]) if h.startswith(f"{int(qlab[:4]) - 1}{qlab[4:]}")), None)
            if idx is not None:
                est = "(E)" in cons["q"][idx]
                for nm, kk in (("매출액", "sales"), ("영업이익", "op"), ("순이익", "np")):
                    cur = cons[kk][idx]; prv = cons[kk][pidx] if pidx is not None else None
                    yo = f" ({(cur / prv - 1) * 100:+.0f}%)" if cur is not None and prv not in (None, 0) and prv > 0 and cur > 0 else ""
                    rows.append([f"{nm} {'컨센서스' if est else '실적'}(억원)", _fmt(cur, 0, ""), f"전년 동기 {_fmt(prv, 0, '')}{yo}"])
                op, pop = cons["op"][idx], cons["op"][pidx] if pidx is not None else None
                pv, fc = (_fmt(pop, 0, "억") if pop is not None else ""), (_fmt(op, 0, "억") if (op is not None and est) else "")
            opq = [(h, v) for h, v in zip(cons["q"], cons["op"]) if v is not None]
            if opq:
                d["OPQ_" + code6] = pd.Series([v for h, v in opq], index=pd.to_datetime([h[:7].replace(".", "-") + "-01" for h, v in opq]))
                charts.insert(0, [("OPQ_" + code6, "분기 영업이익(억원) · 마지막 막대(E)=컨센서스", "--c2", 1, 0, 1)])
        ev.append({"t": t, "cty": "한국", "name": f'{r["corp"]} {qlab[2:4] + "년 " + str(int(qlab[5:]) // 3) + "Q " if qlab else ""}실적',
                   "imp": 2 if r["mk"] == "1" else 1, "pv": pv, "fc": fc, "kind": "earn_kr", "rows": rows, "rows_hdr": ["항목", "값", "비고"],
                   "charts": charts, "per": "1Y", "notime": not re.match(r"\d", r["time"])})
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
.qs span{display:block;font-size:10.5px;color:var(--mute)}.qs b{font-size:12.5px;font-weight:700;white-space:nowrap}
.gauge{padding:10px 14px 8px;border-bottom:1px solid var(--bd2)}
.gbar{position:relative;height:8px;background:linear-gradient(90deg,var(--dn) 0%,color-mix(in srgb,var(--dn) 45%,var(--panel)) 35%,var(--bd) 50%,color-mix(in srgb,var(--up) 45%,var(--panel)) 65%,var(--up) 100%);border-radius:1px}
.gbar i{position:absolute;top:-5px;width:3px;height:18px;background:var(--tx);transform:translateX(-50%);box-shadow:0 0 0 2px var(--panel)}
.fgc{display:flex;flex-wrap:wrap;gap:4px 12px;padding:7px 14px;border-bottom:1px solid var(--bd2);font-size:11px;color:var(--mute)}.fgc b{font-weight:700;margin-left:2px}
.glab{display:flex;justify-content:space-between;font-size:10px;color:var(--mute);margin-top:6px}
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
.calg{margin-bottom:18px}.calh{display:flex;align-items:baseline;gap:8px;margin:0 0 7px;flex-wrap:wrap}
.calh b{font-size:15px;font-weight:700}.calh .cnt{font-size:11.5px;font-weight:700;color:var(--acc)}.calh small{font-size:11.5px;color:var(--mute)}
.cal tr[data-more]{display:none}.cal tr[data-more].showmore{display:table-row}.cal tr.det[data-more]{display:none}
.calp .more{display:block;width:100%;border:0;border-top:1px solid var(--bd2);background:var(--panel);color:var(--sub);font:inherit;font-size:12.5px;font-weight:600;padding:10px;cursor:pointer}
.calp .more:hover{color:var(--tx);background:var(--bd2)}
.ctabs{display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap}
.ctabs button{border:1px solid var(--bd);background:var(--panel);color:var(--sub);font:inherit;font-size:12.5px;font-weight:600;padding:6px 12px;cursor:pointer;border-radius:2px}
.ctabs button.on{background:var(--hd);border-color:var(--hd);color:#fff}
.cal tr.ev.x{cursor:pointer}.cal tr.ev.x:hover td{background:var(--bd2)}.cal tr.ev.open td{background:var(--bd2)}
.cal .ar{width:28px;text-align:center;color:var(--mute);font-size:18px;transition:transform .15s}.cal tr.open .ar{transform:rotate(90deg);color:var(--acc)}
.cal .nm small{display:inline-block;margin-left:8px;font-size:11px;color:var(--mute)}
.kt{display:inline-block;font-size:10.5px;font-weight:700;padding:1px 6px;margin-right:8px;border-radius:2px;background:var(--bd2);color:var(--sub)}
.kt.earn_kr,.kt.earn_us{background:color-mix(in srgb,var(--c3) 18%,var(--panel));color:color-mix(in srgb,var(--c3) 70%,var(--tx))}
.cal tr.det td{padding:0;background:var(--bg);white-space:normal}
.detw{display:grid;grid-template-columns:minmax(280px,380px) 1fr;gap:12px;padding:12px}
.kv{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--bd);font-size:12.5px;align-self:start}
.kv th{background:var(--bd2);font-size:11px;color:var(--sub);text-align:left;padding:7px 10px}.kv td{padding:7px 10px;border-top:1px solid var(--bd2)}
.kv td:nth-child(2){font-weight:700;white-space:nowrap}.kv td:nth-child(3){color:var(--sub)}
.dcharts{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:10px}
.dph{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:8px 10px 0 12px;font-size:12px;font-weight:700;color:var(--sub)}
.per.sm button{padding:3px 8px;font-size:11px}
@media(max-width:700px){.detw{grid-template-columns:1fr}}
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
 const id=card.dataset.id,cfg=CH[id],per=card.dataset.p||card.closest('[data-p]').dataset.p,box=card.querySelector('.plot');
 const W=box.clientWidth,H=+box.dataset.h; if(!W)return;
 const K={grid:cv('--grid'),axis:cv('--axis'),pos:cv('--up'),neg:cv('--dn'),panel:cv('--panel'),badge:cv('--badge'),tx:cv('--tx')};
 const tmax=Math.max(...cfg.series.map(s=>{const t=DATA[s.k].t;return t.length?t[t.length-1]:-1e9}));
 const t0=tmax-PD[per];
 const S=cfg.series.map(s=>{const D=DATA[s.k];if(!D.t.length)return null;let i=D.t.findIndex(x=>x>=t0);if(i<0)return null;if(s.step&&i>0)i--;return{...s,col:cv(s.c),t:D.t.slice(i),v:D.v.slice(i)}}).filter(Boolean);
 box.innerHTML='';if(!S.length){box.innerHTML=`<div style="height:${H}px;display:grid;place-items:center;color:${K.axis};font-size:12px">데이터 없음</div>`;return}
 let x0=Math.min(...S.map(s=>Math.max(s.t[0],t0))),x1=tmax;if(x1-x0<20)x0=x1-20;
 let lo=Infinity,hi=-Infinity;for(const s of S)for(let i=0;i<s.t.length;i++){if(s.t[i]<x0&&!s.step)continue;lo=Math.min(lo,s.v[i]);hi=Math.max(hi,s.v[i])}
 if(cfg.zero||S.some(s=>s.bar)){lo=Math.min(lo,0);hi=Math.max(hi,0)}
 if(cfg.ymin!=null){lo=cfg.ymin;hi=cfg.ymax}else{const pad=(hi-lo)*.07||1;lo-=pad;hi+=pad}
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
 for(const [a,b,c,o] of (cfg.bands||[])){const ya=Y(Math.min(b,hi)),yb=Y(Math.max(a,lo));if(yb>ya)el('rect',{x:L,y:ya,width:pw,height:yb-ya,fill:cv(c),opacity:o},g)}
 for(const [v,lab] of (cfg.refs||[])){if(v<lo||v>hi)continue;el('line',{x1:L,x2:L+pw,y1:Y(v),y2:Y(v),stroke:K.axis,'stroke-dasharray':'4 3','stroke-width':.8,opacity:.8},g)}
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
function drawSec(sec){sec.querySelectorAll('.panel[data-id]:not(.dp)').forEach(draw)}
function drawAll(){CS=getComputedStyle(document.documentElement);document.querySelectorAll('section[data-p]').forEach(drawSec)}
try{const th=localStorage.getItem('theme');if(th)document.documentElement.dataset.theme=th}catch(e){}
document.getElementById('thm').onclick=()=>{const r=document.documentElement,n=r.dataset.theme==='dark'?'light':'dark';r.dataset.theme=n;try{localStorage.setItem('theme',n)}catch(e){}drawAll()};
document.querySelectorAll('section[data-p]').forEach(sec=>{
 let saved=null;try{saved=localStorage.getItem('p_'+sec.id)}catch(e){}
 if(saved&&PD[saved]&&sec.querySelector(`.per button[data-v="${saved}"]`))sec.dataset.p=saved;
 sec.querySelectorAll('.per button').forEach(b=>{b.classList.toggle('on',b.dataset.v===sec.dataset.p);
  b.onclick=()=>{sec.dataset.p=b.dataset.v;sec.querySelectorAll('.per button').forEach(x=>x.classList.toggle('on',x===b));try{localStorage.setItem('p_'+sec.id,b.dataset.v)}catch(e){}CS=getComputedStyle(document.documentElement);drawSec(sec)}})});
drawAll();
document.querySelectorAll('.cal tr.ev.x').forEach(tr=>tr.addEventListener('click',()=>{const det=tr.nextElementSibling;const open=det.hidden;det.hidden=!open;tr.classList.toggle('open',open);
 if(open){CS=getComputedStyle(document.documentElement);det.querySelectorAll('.panel[data-id]').forEach(draw)}}));
document.querySelectorAll('.dp .per button').forEach(b=>b.addEventListener('click',ev=>{ev.stopPropagation();const p=b.closest('.dp');p.dataset.p=b.dataset.v;p.querySelectorAll('.per button').forEach(x=>x.classList.toggle('on',x===b));CS=getComputedStyle(document.documentElement);draw(p)}));
document.querySelectorAll('.calp .more').forEach(b=>b.addEventListener('click',()=>{const tb=b.closest('.calp');const open=b.dataset.open!=='1';b.dataset.open=open?'1':'0';
 tb.querySelectorAll('tr[data-more]').forEach(tr=>tr.classList.toggle('showmore',open));b.textContent=open?'접기 ▴':`나머지 ${b.dataset.n}건 더 보기 ▾`}));
const navs=[...document.querySelectorAll('.nav a')];const io=new IntersectionObserver(es=>{es.forEach(e=>{if(e.isIntersecting)navs.forEach(a=>a.classList.toggle('on',a.getAttribute('href')==='#'+e.target.id))})},{rootMargin:'-40% 0px -55% 0px'});
document.querySelectorAll('section').forEach(x=>io.observe(x));
let rt;window.addEventListener('resize',()=>{clearTimeout(rt);rt=setTimeout(()=>{drawAll();document.querySelectorAll('.cal tr.det:not([hidden]) .panel[data-id]').forEach(draw)},150)});
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


def _fg_cls(v):
    return "dn" if v is not None and v < 45 else "up" if v is not None and v >= 55 else ""


def card_html(cid, cfg, st, h, kind):
    if kind == "gauge":
        v = st.get("val")
        pos = 0 if v is None else max(0, min(100, v))
        hist = "".join(f'<div><span>{n}</span><b class="{_fg_cls(x)}">{"-" if x is None else f"{x:.0f} " + fg_label(x).replace("극단적 ", "극")}</b></div>' for n, x in st["hist"])
        head = (f'<div class="ph"><div class="pt">{_esc(cfg["title"])}<small>{_esc(cfg["src"])} · {st["date"]}</small></div>'
                f'<div class="pv"><b class="{_fg_cls(v)}">{"-" if v is None else f"{v:.0f}"}</b><small class="{_fg_cls(v)}">{st["label"]}</small></div></div>'
                f'<div class="gauge"><div class="gbar"><i style="left:{pos}%"></i></div><div class="glab"><span>극단적 공포</span><span>공포</span><span>중립</span><span>탐욕</span><span>극단적 탐욕</span></div></div>'
                f'<div class="qs">{hist}</div>')
        cn = {"momentum": "모멘텀", "strength": "주가강도", "volume": "시장폭", "breadth": "시장폭", "vix": "변동성", "safehaven": "안전자산"}
        if st.get("comp"):
            head += '<div class="fgc">' + "".join(f'<span>{cn.get(k, k)} <b class="{_fg_cls(v)}">{v:.0f}</b></span>' for k, v in st["comp"].items() if isinstance(v, (int, float))) + "</div>"
        return f'<div class="panel" data-id="{cid}">{head}<div class="body"><div class="plot" data-h="{h}"></div></div></div>'
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


def _cal_table(items, cols_pv, cols_fc, folded=None):
    """items: [(전체 인덱스, 이벤트)] → 날짜별로 묶은 표. folded(e)=True인 일정은 '더 보기'로 접어 둠"""
    wd = "월화수목금토일"
    code = {"미국": "US", "한국": "KR", "중국": "CN", "일본": "JP", "유로": "EU"}
    rows, cur = [], None
    fold = {i: bool(folded and folded(e)) for i, e in items}
    day_all_folded = {}
    for i, e in items:
        day_all_folded[e["t"].date()] = day_all_folded.get(e["t"].date(), True) and fold[i]
    for i, e in items:
        hide = ' data-more="1"' if fold[i] else ""
        dd = e["t"].date()
        if dd != cur:
            cur = dd
            dhide = ' data-more="1"' if day_all_folded[dd] else ""
            tag = "<em>오늘</em>" if dd == TODAY else ("<em>내일</em>" if dd == TODAY + dt.timedelta(days=1) else "")
            rows.append(f'<tr class="day"{dhide}><td colspan="7">{dd:%m.%d} ({wd[dd.weekday()]}){tag}</td></tr>')
        cc = code.get(e["cty"], e["cty"])
        imp = "<i></i>" * e["imp"] + '<i class="o"></i>' * (3 - e["imp"])
        fc = f'<span class="fc">{_esc(e["fc"])}</span>' if e.get("fc") else '<span class="na">-</span>'
        pv = _esc(e["pv"]) if e.get("pv") else '<span class="na">-</span>'
        tm = "미정" if e.get("notime") else f'{e["t"]:%H:%M}'
        sub = f'<small>{_esc(e["tag"])}</small>' if e.get("tag") else ""
        has = bool(e.get("rows") or e.get("charts"))
        rows.append(f'<tr class="ev{" x" if has else ""}"{hide}><td class="tm">{tm}</td><td class="cc"><span class="{cc}">{cc}</span></td>'
                    f'<td class="nm">{_esc(e["name"])}{sub}</td><td class="imp">{imp}</td><td class="n pv2">{pv}</td><td class="n">{fc}</td>'
                    f'<td class="ar">{"›" if has else ""}</td></tr>')
        if has:
            kv = "".join(f"<tr><td>{_esc(a)}</td><td>{_esc(b)}</td><td>{_esc(c)}</td></tr>" for a, b, c in e.get("rows", []))
            h = e.get("rows_hdr", ["항목", "값", "비고"])
            per = e.get("per", "3Y")
            charts = "".join(f'<div class="panel dp" data-id="cal_{i}_{j}" data-p="{per}"><div class="dph"><span>{_esc(ch[0][1])}</span>'
                             f'<div class="per sm">' + "".join(f'<button data-v="{o}"{" class=on" if o == per else ""}>{o.replace("Y", "년")}</button>' for o in ("1Y", "3Y", "5Y")) +
                             f'</div></div><div class="plot" data-h="190"></div></div>' for j, ch in enumerate(e.get("charts", [])))
            rows.append(f'<tr class="det" hidden><td colspan="7"><div class="detw"><table class="kv"><thead><tr><th>{h[0]}</th><th>{h[1]}</th><th>{h[2]}</th></tr></thead>'
                        f'<tbody>{kv}</tbody></table><div class="dcharts">{charts}</div></div></td></tr>')
    nf = sum(fold.values())
    more = f'<button class="more" data-n="{nf}">나머지 {nf}건 더 보기 ▾</button>' if nf else ""
    return ('<div class="calp"><table class="cal"><thead><tr><th>시간(KST)</th><th>국가</th><th>일정</th><th class="imp">중요도</th>'
            f'<th class="n pv2">{cols_pv}</th><th class="n">{cols_fc}</th><th></th></tr></thead><tbody>' + "".join(rows) + f"</tbody></table>{more}</div>")


def calendar_html(ev):
    wk = TODAY + dt.timedelta(days=7)
    groups = [("macro", "경제지표", "美 주요 지표 · 한은 금통위 · 수출 / 줄을 누르면 전년비·전월비와 차트", "이전", "예상(컨센서스)", None),
              ("earn_kr", "국내 실적발표", "KRX KIND 공시 일정(코스피·코스닥) · 컨센서스 단위 억원(영업이익) · 코스피 1주일 우선 표시, 나머지는 더 보기",
               "전년 동기 영업이익", "영업이익 컨센서스", lambda e: e["t"].date() >= wk or e["imp"] < 2),
              ("earn_us", "해외 실적발표", "미국 상장 시가총액 500억 달러 이상 · EPS 기준 · 1,000억 달러 이상 우선 표시",
               "전년 동기 EPS", "EPS 컨센서스", lambda e: e["imp"] < 2)]
    out = []
    for kind, title, desc, pvh, fch, lim in groups:
        items = [(i, e) for i, e in enumerate(ev) if e.get("kind", "macro") == kind]
        body = _cal_table(items, pvh, fch, lim) if items else '<div class="calp" style="padding:16px 14px;color:var(--mute)">향후 3주 내 예정된 일정이 없습니다.</div>'
        out.append(f'<div class="calg"><div class="calh"><b>{title}</b><span class="cnt">{len(items)}건</span><small>{desc}</small></div>{body}</div>')
    return "".join(out)


def calendar_charts(ev, d, charts, data):
    for i, e in enumerate(ev):
        for j, ch in enumerate(e.get("charts", [])):
            ser, mags, monthly = [], [], True
            for tup in ch:
                k, n, c = tup[:3]
                bar, step, mono = (list(tup[3:]) + [0, 0, 0])[:3]
                s = d.get(k, pd.Series(dtype=float, index=pd.DatetimeIndex([]))).dropna()
                daily = k.startswith("PX_") or k in ("USFF", "KRBASE", "ICSA_K")
                monthly = monthly and not daily
                if k not in data:
                    data[k] = pack(s, daily=daily, dec=3)
                if len(s):
                    mags.append(float(s.abs().median()))
                ser.append({"k": k, "n": n, "c": c, "w": 2.2, "bar": bar, "step": step, "mono": mono})
            m = max(mags) if mags else 1
            charts[f"cal_{i}_{j}"] = {"title": "", "src": "", "series": ser, "dec": 0 if m >= 1000 else 1 if m >= 10 else 2,
                                      "unit": "", "zero": 1 if any(x["bar"] for x in ser) else 0, "monthly": 1 if monthly else 0}


TICKER = [("코스피", "KOSPI", 2, "level"), ("코스닥", "KOSDAQ", 2, "level"), ("S&P 500", "SPX", 2, "level"),
          ("나스닥", "NASDAQ", 2, "level"), ("원/달러", "USDKRW", 1, "level"), ("원/100엔", "JPYKRW", 2, "level"),
          ("국고 10년", "KR10Y", 3, "rate"), ("美국채 10년", "US10Y", 2, "rate"), ("VIX", "VIX", 2, "level"), ("WTI", "WTI", 2, "level"),
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
    calendar_charts(ev, d, charts, data)
    for cid in charts:
        if cid.startswith(("ec_", "ex_")):
            charts[cid]["monthly"] = 1
    rates = [card_html(c[0], charts[c[0]], stats[c[0]], 280, "market") for c in RATE_CARDS]
    fx = [card_html(c[0], charts[c[0]], stats[c[0]], 230, "market") for c in FX_CARDS]
    risk = [card_html(c[0], charts[c[0]], stats[c[0]], 220, "market") for c in RISK_CARDS]
    econ = [card_html(c[0], charts[c[0]], stats[c[0]], 200, "econ") for c in ECON_CARDS]
    sent = [card_html(c[0], charts[c[0]], stats[c[0]], 200, "market") for c in SENT_LINE] + \
           [card_html(c[0], charts[c[0]], stats[c[0]], 170, "gauge") for c in SENT_FG]
    exp = [card_html("ex_" + hs, charts["ex_" + hs], stats["ex_" + hs], 180, "econ") for _, hs in EXPORT_ITEMS]
    wd = "월화수목금토일"
    fails = ", ".join(f"{k}({v})" for k, v in (status or {}).items())
    L = ["1Y", "3Y", "5Y", "10Y", "20Y", "30Y"]
    body = f"""
<header class="hd"><div class="in"><div class="logo">PB</div><h1>Market Monitor<span>금리 · 환율 · 자산 · 경제지표 · 수출</span></h1>
<div class="rt"><span class="live">{TODAY:%Y.%m.%d}({wd[TODAY.weekday()]}) {NOW:%H:%M} 기준</span><button class="thm" id="thm">화면 전환</button></div></div></header>
<div class="tk"><div class="in">{ticker_html(d)}</div></div>
<div class="nav"><div class="in"><a href="#rates">금리</a><a href="#fx">환율</a><a href="#risk">주식·원자재·코인</a><a href="#sent">시장 심리</a><a href="#cal">주요 일정</a><a href="#econ">경제지표</a><a href="#exp">품목별 수출</a></div></div>
<main class="wrap">
{section("RATES", "rates", "금리", "국채 3·10·30년 / 점선 기준금리", L, "30Y", "g2", rates)}
{section("FX", "fx", "환율", "원화 기준 / 원·헤알 = 원·달러 ÷ 달러·헤알", L, "30Y", "g3", fx)}
{section("EQUITY · COMMODITY · CRYPTO", "risk", "주식 · 원자재 · 코인", "종가 기준", L, "30Y", "g3", risk)}
{section("SENTIMENT", "sent", "시장 심리", "VIX · 공포탐욕 지수(0 극단적 공포 ~ 100 극단적 탐욕)", ["1Y", "3Y", "5Y", "10Y", "30Y"], "3Y", "g4", sent)}
<section id="cal"><div class="sh"><div><div class="en">CALENDAR</div><h2>향후 3주 주요 일정<small>한국시간 기준 · 줄을 누르면 상세(이전값·컨센서스·차트)가 펼쳐집니다</small></h2></div></div>{calendar_html(ev)}</section>
{section("MACRO", "econ", "경제지표", "월간 발표치", ["1Y", "3Y", "5Y", "10Y"], "5Y", "g4", econ)}
{section("EXPORTS", "exp", "품목별 수출", "관세청 HS 4단위, 억 달러 / 막대 월별 · 선 12개월 평균", ["3Y", "5Y", "10Y"], "10Y", "g4", exp)}
<div class="note">출처: 한국은행 ECOS, FRED(세인트루이스 연준), Yahoo Finance, 일본 재무성, 브라질 중앙은행·국고청, 관세청, ForexFactory, Nasdaq(미국 실적·EPS 컨센서스), KRX KIND(국내 실적발표 일정), 네이버 금융(국내 실적 컨센서스, FnGuide 제공), CNN(Fear & Greed), StockHub(한국 공포탐욕), alternative.me(Crypto Fear & Greed).
韓 공포·탐욕 지수는 StockHub(stockhub.kr) 공개값(모멘텀·주가강도·시장폭·변동성·안전자산 5개 지표 가중합). 출처가 최근 30일만 제공해 매일 실행 시 기록을 누적함.
미국 30년물은 2002~2006년 발행 중단으로 해당 기간 공백. 브라질 국채는 Tesouro Direto 고정금리 이표채(NTN-F) 중 잔존 10년에 가장 가까운 종목(2000년대 초반은 단기물만 존재).
금은 COMEX 선물 근월물. 품목별 수출은 HS 4단위 근사치로 산업부 품목 분류와 다를 수 있음(디스플레이 HS 8524는 2022년 신설). ▲ 상승 / ▼ 하락. 美 지표 '≈x.x% (추정)'은 시장 전월비 예상치를 계절조정 지수에 적용해 계산한 전년비 추정치로, 공식 컨센서스가 아님.
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
    raw, ff, fr, status, us_earn, kr_earn, kr_cons = fetch_all()
    d = derive(raw)
    ev = build_calendar(ff, fr, d, us_earn, kr_earn, kr_cons)
    html = build_html(d, ev, status)
    (OUT_DIR / f"dashboard_{TODAY:%Y%m%d}.html").write_text(html, encoding="utf-8")
    (OUT_DIR / "dashboard_latest.html").write_text(html, encoding="utf-8")
    print("\n완료 →", OUT_DIR / "dashboard_latest.html")
    if status:
        print("수집 실패 항목:", ", ".join(status))


if __name__ == "__main__":
    main()
