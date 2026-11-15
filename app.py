"""International Cost of Capital: cost of equity and WACC in USD and a selected country's currency.

Model (documented in the app and README)
    Levered beta    = unlevered beta x (1 + (1 - t) x D/E)
    Cost of equity  = U.S. 10y yield + levered beta x equity risk premium            (USD)
    Cost of debt    = U.S. 10y yield + USD BBB spread                                 (USD)
    Local rate      = (1 + USD rate) x (1 + local 10y yield) / (1 + U.S. 10y yield) - 1
    WACC            = E/V x Ke + D/V x Kd x (1 - t)
The same project tax rate, debt share and equity risk premium are used in both currencies.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import html
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

st.set_page_config(
    page_title="Cost of Capital, Two Currencies",
    page_icon="🪙",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --------------------------------------------------------------------------------------
# Sources and defaults
# --------------------------------------------------------------------------------------
BETA_XLS_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
BETA_HTML_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/Betas.html"
RATINGS_URL = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
OECD_PAGE_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/"
    "statutory-corporate-income-tax-rates_440f9286.html"
)
OECD_API_URLS = [
    ("https://sdmx.oecd.org/archive/rest/data/OECD,DF_CTS_CIT/.COMB_CIT_RATE",
     {"lastNObservations": "1"}, "application/vnd.sdmx.data+csv; charset=utf-8"),
    ("https://sdmx.oecd.org/archive/rest/data/OECD,DF_CTS_CIT/.COMB_CIT_RATE",
     {"lastNObservations": "1", "format": "csvfilewithlabels"}, "*/*"),
]
TE_BONDS_URL = "https://tradingeconomics.com/bonds"
TE_COUNTRY_URL = "https://tradingeconomics.com/{slug}/government-bond-yield"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
CONNECT_TIMEOUT, READ_TIMEOUT = 6, 40
CACHE_TTL = 6 * 3600
ERROR_COOLDOWN = 45

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "USA"
DEFAULT_ERP = 6.50
DEFAULT_DEBT = 30.0

# (name, ISO3, Trading Economics slug, currency code)
COUNTRIES = [
    ("United States", "USA", "united-states", "USD"), ("United Kingdom", "GBR", "united-kingdom", "GBP"),
    ("Canada", "CAN", "canada", "CAD"), ("Australia", "AUS", "australia", "AUD"),
    ("New Zealand", "NZL", "new-zealand", "NZD"), ("Japan", "JPN", "japan", "JPY"),
    ("South Korea", "KOR", "south-korea", "KRW"), ("China", "CHN", "china", "CNY"),
    ("India", "IND", "india", "INR"), ("Indonesia", "IDN", "indonesia", "IDR"),
    ("Malaysia", "MYS", "malaysia", "MYR"), ("Thailand", "THA", "thailand", "THB"),
    ("Philippines", "PHL", "philippines", "PHP"), ("Vietnam", "VNM", "vietnam", "VND"),
    ("Singapore", "SGP", "singapore", "SGD"), ("Hong Kong", "HKG", "hong-kong", "HKD"),
    ("Pakistan", "PAK", "pakistan", "PKR"), ("Bangladesh", "BGD", "bangladesh", "BDT"),
    ("Sri Lanka", "LKA", "sri-lanka", "LKR"), ("Saudi Arabia", "SAU", "saudi-arabia", "SAR"),
    ("Israel", "ISR", "israel", "ILS"), ("Turkey", "TUR", "turkey", "TRY"),
    ("Egypt", "EGY", "egypt", "EGP"), ("South Africa", "ZAF", "south-africa", "ZAR"),
    ("Nigeria", "NGA", "nigeria", "NGN"), ("Kenya", "KEN", "kenya", "KES"),
    ("Morocco", "MAR", "morocco", "MAD"), ("Ghana", "GHA", "ghana", "GHS"),
    ("Brazil", "BRA", "brazil", "BRL"), ("Mexico", "MEX", "mexico", "MXN"),
    ("Argentina", "ARG", "argentina", "ARS"), ("Chile", "CHL", "chile", "CLP"),
    ("Colombia", "COL", "colombia", "COP"), ("Peru", "PER", "peru", "PEN"),
    ("Switzerland", "CHE", "switzerland", "CHF"), ("Sweden", "SWE", "sweden", "SEK"),
    ("Norway", "NOR", "norway", "NOK"), ("Denmark", "DNK", "denmark", "DKK"),
    ("Iceland", "ISL", "iceland", "ISK"), ("Poland", "POL", "poland", "PLN"),
    ("Czech Republic", "CZE", "czech-republic", "CZK"), ("Hungary", "HUN", "hungary", "HUF"),
    ("Romania", "ROU", "romania", "RON"), ("Russia", "RUS", "russia", "RUB"),
    ("Ukraine", "UKR", "ukraine", "UAH"), ("Kazakhstan", "KAZ", "kazakhstan", "KZT"),
    ("Germany", "DEU", "germany", "EUR"), ("France", "FRA", "france", "EUR"),
    ("Italy", "ITA", "italy", "EUR"), ("Spain", "ESP", "spain", "EUR"),
    ("Netherlands", "NLD", "netherlands", "EUR"), ("Belgium", "BEL", "belgium", "EUR"),
    ("Austria", "AUT", "austria", "EUR"), ("Ireland", "IRL", "ireland", "EUR"),
    ("Portugal", "PRT", "portugal", "EUR"), ("Greece", "GRC", "greece", "EUR"),
    ("Finland", "FIN", "finland", "EUR"), ("Luxembourg", "LUX", "luxembourg", "EUR"),
    ("Slovakia", "SVK", "slovakia", "EUR"), ("Slovenia", "SVN", "slovenia", "EUR"),
    ("Croatia", "HRV", "croatia", "EUR"),
]
COUNTRY_BY_ISO = {c[1]: c for c in COUNTRIES}
NAME_ALIASES = {
    "korea": "KOR", "republic of korea": "KOR", "türkiye": "TUR", "turkiye": "TUR", "czechia": "CZE",
    "china (people's republic of)": "CHN", "people's republic of china": "CHN", "slovak republic": "SVK",
    "russian federation": "RUS", "viet nam": "VNM", "hong kong (china)": "HKG", "hong kong, china": "HKG",
    "united states of america": "USA",
}
NAME_TO_ISO = {c[0].lower(): c[1] for c in COUNTRIES} | NAME_ALIASES

# Palette: forest green, parchment, brass and terracotta
FOREST = "#0E3B33"
FOREST_2 = "#1F5C4F"
PARCH = "#FAF5EA"
PAPER = "#FFFDF8"
BRASS = "#B98324"
TERRA = "#B4532F"
INK = "#22302C"
MUTED = "#6B746F"
LINE = "#E6DECB"
SERIF = "Fraunces, 'Iowan Old Style', Georgia, serif"
SANS = "'DM Sans', 'Segoe UI', system-ui, sans-serif"


class SourceError(Exception):
    """A data source could not be fetched or understood."""


# --------------------------------------------------------------------------------------
# Streamlit helpers
# --------------------------------------------------------------------------------------
def stretch(fn) -> dict:
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    if "width" in params:
        return {"width": "stretch"}
    if "use_container_width" in params:
        return {"use_container_width": True}
    return {}


def pct(x, signed: bool = False) -> str:
    if x is None or not np.isfinite(x):
        return "n/a"
    return f"{x * 100:+,.2f}%" if signed else f"{x * 100:,.2f}%"


def num(x) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{x:,.2f}"


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def get_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(total=2, connect=2, read=1, backoff_factor=0.8,
                  status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",),
                  raise_on_status=False)
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=6, pool_maxsize=6))
    return session


def http_get(url: str, params: dict | None = None, accept: str = "*/*") -> requests.Response:
    host = urlparse(url).netloc
    headers = {"User-Agent": UA, "Accept": accept, "Accept-Language": "en-US,en;q=0.9"}
    try:
        resp = get_session().get(url, params=params, headers=headers, timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    except requests.exceptions.Timeout as exc:
        raise SourceError(f"{host} timed out") from exc
    except requests.exceptions.ConnectionError as exc:
        raise SourceError(f"could not connect to {host}") from exc
    except requests.exceptions.RequestException as exc:
        raise SourceError(f"request to {host} failed ({type(exc).__name__})") from exc
    if resp.status_code != 200:
        raise SourceError(f"{host} answered HTTP {resp.status_code}")
    return resp


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# --------------------------------------------------------------------------------------
# Parsers (pure functions)
# --------------------------------------------------------------------------------------
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"


def to_number(value) -> float:
    """Parse numbers such as 1.23, '5.58%' (returned as 0.0558) or 0.0558."""
    if value is None:
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    text = str(value).strip().replace("\u2212", "-").replace(",", "")
    if not text or text.lower() in {"nan", "n/a", "na", "-"}:
        return np.nan
    pct_flag = text.endswith("%")
    text = text.rstrip("%").strip()
    try:
        out = float(text)
    except ValueError:
        return np.nan
    return out / 100.0 if pct_flag else out


def find_date_text(rows: list[str]) -> str:
    for text in rows:
        m = re.search(rf"\b({MONTHS})\s+(\d{{4}})\b", text)
        if m:
            return f"{m.group(1)} {m.group(2)}"
        m = re.search(r"\b(\d{1,2}-[A-Za-z]{3}-\d{2,4})\b", text)
        if m:
            return m.group(1)
    return "date not stated in file"


def normalize_beta_table(raw: pd.DataFrame) -> pd.DataFrame:
    """Turn a raw sheet or HTML table into one row per industry with numeric columns."""
    header_pos = None
    for i in range(min(len(raw), 40)):
        cells = [str(c).strip().lower() for c in raw.iloc[i].tolist()]
        if "industry name" in cells:
            header_pos = i
            break
    if header_pos is None:
        raise SourceError("no 'Industry Name' header found")
    cols = [re.sub(r"\s+", " ", str(c).strip().lower()) for c in raw.iloc[header_pos].tolist()]
    body = raw.iloc[header_pos + 1:].copy()
    body.columns = cols

    def pick(predicate):
        for c in cols:
            if predicate(c):
                return c
        return None

    c_ind = pick(lambda c: c == "industry name")
    c_unl = pick(lambda c: c.startswith("unlevered beta") and "cash" not in c)
    c_unl_cash = pick(lambda c: c.startswith("unlevered beta") and "cash" in c)
    c_beta = pick(lambda c: c in {"beta", "average beta"})
    c_de = pick(lambda c: c.startswith("d/e"))
    c_firms = pick(lambda c: c.startswith("number of firms"))
    if not c_unl:
        raise SourceError("no 'Unlevered beta' column found")

    out = pd.DataFrame({
        "industry": body[c_ind].astype(str).str.strip(),
        "firms": body[c_firms].map(to_number) if c_firms else np.nan,
        "levered_beta": body[c_beta].map(to_number) if c_beta else np.nan,
        "de_ratio": body[c_de].map(to_number) if c_de else np.nan,
        "unlevered_beta": body[c_unl].map(to_number),
        "unlevered_beta_cash": body[c_unl_cash].map(to_number) if c_unl_cash else np.nan,
    })
    out = out[out["industry"].ne("") & out["industry"].str.lower().ne("nan")]
    out = out[~out["industry"].str.lower().str.startswith("total market")]
    out = out[np.isfinite(out["unlevered_beta"]) & (out["unlevered_beta"] > 0)]
    out = out.drop_duplicates("industry").reset_index(drop=True)
    if len(out) < 30:
        raise SourceError(f"only {len(out)} industries parsed, expected about 95")
    return out


def parse_betas_excel(content: bytes) -> tuple[pd.DataFrame, str]:
    sheets = pd.read_excel(io.BytesIO(content), sheet_name=None, header=None)
    ordered = sorted(sheets.items(), key=lambda kv: (0 if "industry averages" in kv[0].lower() else 1))
    last_err = "no usable sheet"
    for name, raw in ordered:
        try:
            table = normalize_beta_table(raw)
        except SourceError as exc:
            last_err = f"{name}: {exc}"
            continue
        top = [" ".join(str(c) for c in raw.iloc[i].tolist() if str(c) != "nan") for i in range(min(len(raw), 12))]
        return table, find_date_text(top)
    raise SourceError(last_err)


def parse_betas_html(text: str) -> tuple[pd.DataFrame, str]:
    tables = pd.read_html(io.StringIO(text), header=None)
    last_err = "no table found"
    for raw in tables:
        try:
            table = normalize_beta_table(raw.astype(object))
        except SourceError as exc:
            last_err = str(exc)
            continue
        page_text = BeautifulSoup(text, "lxml").get_text(" ", strip=True)
        m = re.search(rf"[Dd]ata used is as of\s+({MONTHS})\s+(\d{{4}})", page_text)
        return table, (f"{m.group(1)} {m.group(2)}" if m else find_date_text([page_text[:2000]]))
    raise SourceError(last_err)


def parse_bbb_spread(text: str) -> tuple[float, str, str]:
    """Return (spread as decimal, rating label, as-of text) for Baa2/BBB, large non-financial firms."""
    soup = BeautifulSoup(text, "lxml")
    page_text = soup.get_text(" ", strip=True)
    m = re.search(rf"as of\s+({MONTHS})\s+(\d{{4}})", page_text, re.I)
    asof = f"{m.group(1).title()} {m.group(2)}" if m else "date not stated on page"
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        for i, cell in enumerate(cells):
            if re.fullmatch(r"baa2\s*/\s*bbb", cell, re.I):
                for nxt in cells[i + 1:]:
                    if "%" in nxt:
                        value = to_number(nxt)
                        if np.isfinite(value) and 0 < value < 0.10:
                            return value, cell, asof
                        raise SourceError(f"unexpected BBB spread value '{nxt}'")
    raise SourceError("Baa2/BBB row not found")


def parse_oecd_csv(text: str) -> tuple[dict, str]:
    """Return ({ISO3: rate as decimal}, latest year) from an SDMX-CSV response."""
    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    df.columns = [re.split(r"\s*:\s*", str(c).strip(), maxsplit=1)[0].upper() for c in df.columns]
    ctry = next((c for c in ("REF_AREA", "COUNTRY", "COU", "LOCATION", "JURISDICTION") if c in df.columns), None)
    if not ctry or "TIME_PERIOD" not in df.columns or "OBS_VALUE" not in df.columns:
        raise SourceError(f"unexpected OECD columns: {', '.join(df.columns[:8])}")
    df["val"] = df["OBS_VALUE"].map(to_number)
    df["year"] = pd.to_numeric(df["TIME_PERIOD"].str.extract(r"(\d{4})")[0], errors="coerce")
    df = df.dropna(subset=["val", "year"])
    if df.empty:
        raise SourceError("OECD response held no numeric observations")
    df = df.sort_values("year").drop_duplicates(ctry, keep="last")
    scale = 100.0 if df["val"].max() > 1.0 else 1.0
    rates = {str(k).strip().upper(): float(v) / scale for k, v in zip(df[ctry], df["val"])}
    return rates, str(int(df["year"].max()))


def parse_oecd_page(text: str) -> tuple[dict, str]:
    """Fallback: read a statutory-rate table from the OECD report page, if the page carries one."""
    best = None
    for raw in pd.read_html(io.StringIO(text)):
        raw = raw.copy()
        raw.columns = [" ".join(map(str, c)) if isinstance(c, tuple) else str(c) for c in raw.columns]
        years = [(int(m.group(0)), c) for c in raw.columns if (m := re.search(r"\b(19|20)\d{2}\b", c))]
        if len(raw) >= 20 and years:
            year, col = max(years)
            if best is None or len(raw) > len(best[0]):
                best = (raw, year, col)
    if best is None:
        raise SourceError("no statutory-rate table found on the OECD page")
    raw, year, col = best
    rates = {}
    for name, value in zip(raw.iloc[:, 0], raw[col]):
        iso = NAME_TO_ISO.get(str(name).strip().lower().replace("*", ""))
        number = to_number(value)
        if iso and np.isfinite(number):
            rates[iso] = number / 100.0 if number > 1 else number
    if len(rates) < 10:
        raise SourceError("OECD page table could not be matched to countries")
    return rates, str(year)


def parse_bond_table(text: str) -> dict:
    """Parse tradingeconomics.com/bonds into {slug: (yield in percent, date text)}."""
    soup = BeautifulSoup(text, "lxml")
    out = {}
    for tr in soup.find_all("tr"):
        link = tr.find("a", href=re.compile(r"/[a-z0-9\-]+/government-bond-yield"))
        if not link:
            continue
        slug = re.search(r"/([a-z0-9\-]+)/government-bond-yield", link["href"]).group(1)
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        link_cell = link.find_parent("td")
        start = cells.index(link_cell.get_text(" ", strip=True)) + 1 if link_cell else 0
        value = next((to_number(c) for c in cells[start:] if re.fullmatch(r"-?\d+(\.\d+)?", c)), np.nan)
        stamp = next((c for c in reversed(cells) if c), "")
        if np.isfinite(value):
            out[slug] = (float(value), stamp)
    return out


def parse_country_yield(text: str) -> tuple[float, str]:
    page = BeautifulSoup(text, "lxml").get_text(" ", strip=True)
    m = re.search(r"(\d+\.\d+)%\s+on\s+((?:" + MONTHS + r")\s+\d{1,2},\s+\d{4})", page)
    if not m:
        raise SourceError("yield sentence not found on country page")
    return float(m.group(1)), m.group(2)


# --------------------------------------------------------------------------------------
# Cached fetchers
# --------------------------------------------------------------------------------------
@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_betas() -> dict:
    errors = []
    try:
        table, asof = parse_betas_excel(http_get(BETA_XLS_URL).content)
        return {"table": table, "asof": asof, "url": BETA_XLS_URL, "retrieved": now_utc(), "via": "betas.xls"}
    except (SourceError, ValueError, ImportError) as exc:
        errors.append(f"betas.xls: {exc}")
    try:
        table, asof = parse_betas_html(http_get(BETA_HTML_URL).text)
        return {"table": table, "asof": asof, "url": BETA_HTML_URL, "retrieved": now_utc(),
                "via": "Betas.html (fallback)"}
    except (SourceError, ValueError, ImportError) as exc:
        errors.append(f"Betas.html: {exc}")
    raise SourceError("; ".join(errors))


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_bbb() -> dict:
    value, label, asof = parse_bbb_spread(http_get(RATINGS_URL).text)
    return {"value": value, "label": label, "asof": asof, "url": RATINGS_URL, "retrieved": now_utc()}


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_tax_rates() -> dict:
    errors = []
    for url, params, accept in OECD_API_URLS:
        try:
            rates, year = parse_oecd_csv(http_get(url, params, accept).text)
            return {"rates": rates, "year": year, "url": OECD_PAGE_URL, "retrieved": now_utc(),
                    "via": "OECD Corporate Tax Statistics database (SDMX)"}
        except (SourceError, ValueError, pd.errors.ParserError) as exc:
            errors.append(str(exc))
    try:
        rates, year = parse_oecd_page(http_get(OECD_PAGE_URL).text)
        return {"rates": rates, "year": year, "url": OECD_PAGE_URL, "retrieved": now_utc(),
                "via": "OECD report page (fallback)"}
    except (SourceError, ValueError) as exc:
        errors.append(f"report page: {exc}")
    raise SourceError("; ".join(errors))


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_bond_table() -> dict:
    table = parse_bond_table(http_get(TE_BONDS_URL).text)
    if not table:
        raise SourceError("no bond rows found on the Trading Economics page")
    return {"table": table, "retrieved": now_utc()}


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_yield(slug: str) -> dict:
    """10-year yield for one country: the bonds table first, the country page second."""
    notes = []
    try:
        bulk = fetch_bond_table()["table"]
        if slug in bulk:
            value, stamp = bulk[slug]
            return {"value": value / 100.0, "asof": stamp, "url": TE_BONDS_URL, "retrieved": now_utc()}
        notes.append("not in bonds table")
    except SourceError as exc:
        notes.append(f"bonds table: {exc}")
    try:
        value, stamp = parse_country_yield(http_get(TE_COUNTRY_URL.format(slug=slug)).text)
        return {"value": value / 100.0, "asof": stamp, "url": TE_COUNTRY_URL.format(slug=slug),
                "retrieved": now_utc()}
    except SourceError as exc:
        notes.append(f"country page: {exc}")
    raise SourceError("; ".join(notes))


def safe(name: str, fn, *args):
    errors = st.session_state.setdefault("_errors", {})
    key = (name, args)
    if key in errors and time.time() - errors[key][0] < ERROR_COOLDOWN:
        return None, errors[key][1]
    try:
        result = fn(*args)
        errors.pop(key, None)
        return result, None
    except SourceError as exc:
        message = str(exc)
    except Exception as exc:  # shown to the user rather than crashing the app
        message = f"unexpected error ({type(exc).__name__}: {exc})"
    errors[key] = (time.time(), message)
    return None, message


# --------------------------------------------------------------------------------------
# Finance (pure functions)
# --------------------------------------------------------------------------------------
def convert_rate(rate_usd: float, rf_usd: float, rf_local: float) -> float:
    """Re-express a USD rate in local currency using the 10-year yield differential."""
    return (1.0 + rate_usd) * (1.0 + rf_local) / (1.0 + rf_usd) - 1.0


def cost_of_capital(beta_u, tax, debt_share, erp, rf_us, spread_us, rf_local, spread_local=None) -> dict:
    de = debt_share / (1.0 - debt_share)
    beta_l = beta_u * (1.0 + (1.0 - tax) * de)
    ke_usd = rf_us + beta_l * erp
    kd_usd = rf_us + spread_us
    wacc_usd = (1.0 - debt_share) * ke_usd + debt_share * kd_usd * (1.0 - tax)

    ke_loc = convert_rate(ke_usd, rf_us, rf_local)
    kd_loc_auto = convert_rate(kd_usd, rf_us, rf_local)
    spread_loc_auto = kd_loc_auto - rf_local
    kd_loc = rf_local + spread_local if spread_local is not None else kd_loc_auto
    wacc_loc = (1.0 - debt_share) * ke_loc + debt_share * kd_loc * (1.0 - tax)
    return {
        "de": de, "beta_l": beta_l,
        "ke_usd": ke_usd, "kd_usd": kd_usd, "kd_usd_at": kd_usd * (1.0 - tax), "wacc_usd": wacc_usd,
        "ke_loc": ke_loc, "kd_loc": kd_loc, "kd_loc_at": kd_loc * (1.0 - tax), "wacc_loc": wacc_loc,
        "spread_loc_auto": spread_loc_auto, "spread_loc_used": kd_loc - rf_local,
        "yield_gap": rf_local - rf_us,
        "conv_factor": (1.0 + rf_local) / (1.0 + rf_us) - 1.0,
    }


# --------------------------------------------------------------------------------------
# Styling
# --------------------------------------------------------------------------------------
def inject_css() -> None:
    st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Fraunces:opsz,wght@9..144,500;9..144,700&display=swap');
:root {{ color-scheme: light; }}
.stApp {{ background: {PARCH}; color: {INK}; font-family: {SANS}; }}
.block-container {{ max-width: 1240px; padding-top: 1.4rem; padding-bottom: 3rem; }}
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stSidebar"] {{ background: #F1EAD8; border-right: 1px solid {LINE}; }}
[data-testid="stSidebar"] * {{ color: {INK}; }}
h1, h2, h3 {{ font-family: {SERIF}; color: {FOREST}; letter-spacing: -0.01em; }}
.hero {{ position: relative; overflow: hidden; background: {FOREST}; border-radius: 18px; padding: 28px 32px; margin-bottom: 18px; }}
.hero:before {{ content: ""; position: absolute; right: -60px; top: -60px; width: 260px; height: 260px; border-radius: 50%;
  border: 36px solid rgba(185,131,36,0.35); }}
.hero:after {{ content: ""; position: absolute; right: 90px; top: 40px; width: 140px; height: 140px; border-radius: 50%;
  border: 22px solid rgba(180,83,47,0.40); }}
.hero h1 {{ color: #FFF8E6; font-size: 2rem; margin: 0 0 6px 0; position: relative; }}
.hero p {{ color: #CFE0D8; margin: 0; max-width: 640px; position: relative; }}
.hero .chips {{ margin-top: 14px; position: relative; display: flex; gap: 8px; flex-wrap: wrap; }}
.chip {{ background: rgba(255,248,230,0.12); color: #FFF3D1; border: 1px solid rgba(255,248,230,0.25);
  border-radius: 999px; padding: 4px 12px; font-size: 0.82rem; font-weight: 500; }}
.ccard {{ background: {PAPER}; border: 1px solid {LINE}; border-radius: 16px; padding: 20px 24px 18px 24px; height: 100%;
  box-shadow: 0 1px 0 rgba(14,59,51,0.04), 0 8px 24px rgba(14,59,51,0.05); }}
.ccard.usd {{ border-top: 6px solid {FOREST_2}; }}
.ccard.loc {{ border-top: 6px solid {TERRA}; }}
.ccard-head {{ display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }}
.ccy {{ font-family: {SERIF}; font-weight: 700; font-size: 1.35rem; color: {FOREST}; }}
.ccard.loc .ccy {{ color: {TERRA}; }}
.ccard-sub {{ color: {MUTED}; font-size: 0.86rem; }}
.wacc-label {{ text-transform: uppercase; letter-spacing: 0.08em; font-size: 0.72rem; color: {MUTED}; font-weight: 600; }}
.wacc-value {{ font-family: {SERIF}; font-size: 3rem; font-weight: 700; color: {FOREST}; line-height: 1.1; font-variant-numeric: tabular-nums; }}
.ccard.loc .wacc-value {{ color: {TERRA}; }}
.delta {{ display: inline-block; margin: 6px 0 12px 0; padding: 2px 10px; border-radius: 999px; font-size: 0.8rem; font-weight: 600;
  background: #F6E3D8; color: {TERRA}; }}
.delta.zero {{ background: #E3EEE9; color: {FOREST_2}; }}
.rowline {{ display: flex; justify-content: space-between; padding: 8px 0; border-top: 1px dashed {LINE}; font-size: 0.95rem; }}
.rowline span:last-child {{ font-weight: 600; font-variant-numeric: tabular-nums; color: {FOREST}; }}
.ccard.loc .rowline span:last-child {{ color: {TERRA}; }}
.mini {{ background: {PAPER}; border: 1px solid {LINE}; border-radius: 12px; padding: 12px 16px; }}
.mini .k {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.07em; color: {MUTED}; font-weight: 600; }}
.mini .v {{ font-family: {SERIF}; font-size: 1.4rem; font-weight: 700; color: {FOREST}; font-variant-numeric: tabular-nums; }}
.note {{ background: #F4EBD3; border-left: 4px solid {BRASS}; border-radius: 8px; padding: 10px 14px; font-size: 0.9rem; }}
.assump li {{ margin-bottom: 4px; }}
button[data-baseweb="tab"] p {{ font-weight: 600; color: {FOREST}; }}
[data-testid="stExpander"] {{ background: {PAPER}; border: 1px solid {LINE}; border-radius: 12px; }}
[data-baseweb="select"] > div, [data-baseweb="input"] > div {{ background-color: #fff; color: {INK}; }}
.stButton > button, .stDownloadButton > button {{ border-radius: 10px; border: 1px solid {FOREST_2}; color: {FOREST_2};
  background: #fff; font-weight: 600; }}
.stButton > button:hover, .stDownloadButton > button:hover {{ background: {FOREST_2}; color: #fff; }}
</style>""", unsafe_allow_html=True)


def mini(label: str, value: str) -> str:
    return f'<div class="mini"><div class="k">{html.escape(label)}</div><div class="v">{html.escape(value)}</div></div>'


def result_card(kind: str, ccy: str, title: str, r: dict, ke: float, kd: float, kd_at: float, wacc: float,
                debt_share: float, delta_text: str | None) -> str:
    delta = ""
    if delta_text is not None:
        cls = "delta zero" if delta_text.startswith("+0.00") or delta_text.startswith("-0.00") else "delta"
        delta = f'<div class="{cls}">{html.escape(delta_text)} vs USD</div>'
    return f"""<div class="ccard {kind}">
<div class="ccard-head"><span class="ccy">{html.escape(ccy)}</span><span class="ccard-sub">{html.escape(title)}</span></div>
<div class="wacc-label">WACC</div><div class="wacc-value">{pct(wacc)}</div>{delta or '<div style="height:44px"></div>'}
<div class="rowline"><span>Cost of equity</span><span>{pct(ke)}</span></div>
<div class="rowline"><span>Pre-tax cost of debt</span><span>{pct(kd)}</span></div>
<div class="rowline"><span>After-tax cost of debt</span><span>{pct(kd_at)}</span></div>
<div class="rowline"><span>Equity / debt weights</span><span>{pct(1 - debt_share)} / {pct(debt_share)}</span></div>
</div>"""


# --------------------------------------------------------------------------------------
# Charts
# --------------------------------------------------------------------------------------
def style_fig(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(height=height, paper_bgcolor=PAPER, plot_bgcolor=PAPER,
                      font=dict(family=SANS, color=INK, size=13), margin=dict(l=10, r=10, t=40, b=10),
                      legend=dict(orientation="h", y=1.12, x=0), hoverlabel=dict(bgcolor="white"))
    fig.update_xaxes(gridcolor=LINE, zeroline=False, linecolor=LINE)
    fig.update_yaxes(gridcolor=LINE, zeroline=False, linecolor=LINE)
    return fig


def composition_chart(r: dict, debt_share: float, ccy_loc: str) -> go.Figure:
    labels = ["USD", ccy_loc]
    eq = [(1 - debt_share) * r["ke_usd"] * 100, (1 - debt_share) * r["ke_loc"] * 100]
    db = [debt_share * r["kd_usd_at"] * 100, debt_share * r["kd_loc_at"] * 100]
    fig = go.Figure()
    fig.add_bar(y=labels, x=eq, orientation="h", name="Equity share of WACC", marker_color=FOREST_2,
                text=[f"{v:.2f}%" for v in eq], textposition="inside", insidetextfont=dict(color="white"),
                hovertemplate="%{x:.2f}%<extra>Equity</extra>")
    fig.add_bar(y=labels, x=db, orientation="h", name="After-tax debt share of WACC", marker_color=BRASS,
                text=[f"{v:.2f}%" for v in db], textposition="inside", insidetextfont=dict(color="white"),
                hovertemplate="%{x:.2f}%<extra>Debt</extra>")
    fig.update_layout(barmode="stack")
    fig.update_xaxes(title_text="WACC, percentage points", ticksuffix="%", tickformat=".2f")
    return style_fig(fig, 230)


def industry_chart(df: pd.DataFrame, selected: str, ccy_loc: str) -> go.Figure:
    fig = go.Figure()
    fig.add_scatter(x=df["Unlevered beta"], y=df["WACC USD (%)"], mode="markers", name="WACC, USD",
                    text=df["Industry"], marker=dict(color=FOREST_2, size=8, opacity=0.8),
                    hovertemplate="%{text}<br>Unlevered beta %{x:.2f}<br>WACC %{y:.2f}%<extra>USD</extra>")
    fig.add_scatter(x=df["Unlevered beta"], y=df["WACC local (%)"], mode="markers", name=f"WACC, {ccy_loc}",
                    text=df["Industry"], marker=dict(color=TERRA, size=8, opacity=0.8, symbol="diamond"),
                    hovertemplate="%{text}<br>Unlevered beta %{x:.2f}<br>WACC %{y:.2f}%<extra>" + ccy_loc + "</extra>")
    sel = df[df["Industry"] == selected]
    if len(sel):
        fig.add_scatter(x=sel["Unlevered beta"], y=sel["WACC USD (%)"], mode="markers", name="Selected industry",
                        marker=dict(color=BRASS, size=16, symbol="star", line=dict(color=FOREST, width=1)),
                        hovertemplate=selected + "<extra>selected</extra>")
    fig.update_xaxes(title_text="Unlevered beta", tickformat=".2f")
    fig.update_yaxes(title_text="WACC", ticksuffix="%", tickformat=".2f")
    return style_fig(fig, 460)


# --------------------------------------------------------------------------------------
# Manual override widget
# --------------------------------------------------------------------------------------
def override(label: str, key: str, auto: float | None, *, is_pct: bool = True, step: float = 0.05,
             min_value: float = -10.0, max_value: float = 100.0) -> tuple[float | None, str]:
    """Show an auto value with an optional manual override. Returns (value in model units, origin)."""
    unit = " (%)" if is_pct else ""
    scale = 100.0 if is_pct else 1.0
    have_auto = auto is not None and np.isfinite(auto)
    use_manual = st.toggle(f"Override {label}", key=f"{key}_on")
    if not have_auto:
        st.caption(f"Automatic value unavailable for {label}. Enter it manually.")
        use_manual = True
    if use_manual:
        start = float(auto * scale) if have_auto else None
        entry = st.number_input(f"{label}{unit}", value=start, step=step, format="%.2f", key=f"{key}_manual",
                                min_value=min_value, max_value=max_value)
        return (None if entry is None else entry / scale), "Manual"
    st.number_input(f"{label}{unit}", value=float(auto * scale), format="%.2f", disabled=True,
                    key=f"{key}_auto_{auto * scale:.4f}")
    return float(auto), "Auto"


# --------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------
def main() -> None:
    inject_css()

    names = {c[1]: c[0] for c in COUNTRIES}
    iso_options = [c[1] for c in sorted(COUNTRIES, key=lambda c: c[0])]

    with st.sidebar:
        st.markdown("### Controls")
        if st.button("Refresh data", **stretch(st.button)):
            st.cache_data.clear()
            st.session_state["_errors"] = {}
            st.toast("Cache cleared. Fetching fresh data.")

    # ---- Load sources ----
    with st.status("Loading data sources", expanded=False) as status:
        status.update(label="Damodaran: industry betas")
        beta_pack, beta_err = safe("betas", fetch_betas)
        status.update(label="Damodaran: BBB spread")
        bbb_pack, bbb_err = safe("bbb", fetch_bbb)
        status.update(label="OECD: corporate tax rates")
        tax_pack, tax_err = safe("tax", fetch_tax_rates)
    industries = list(beta_pack["table"]["industry"]) if beta_pack else [DEFAULT_INDUSTRY]

    with st.sidebar:
        industry = st.selectbox("Industry", industries,
                                index=industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0)
        iso = st.selectbox("Target country", iso_options, index=iso_options.index(DEFAULT_COUNTRY),
                           format_func=lambda c: names[c])
        erp_pct = st.slider("Equity risk premium", 0.0, 15.0, DEFAULT_ERP, 0.05, format="%.2f%%")
        debt_pct = st.slider("Target debt share (D/V)", 0.0, 90.0, DEFAULT_DEBT, 1.0, format="%.2f%%")
        st.markdown("**Project tax rate**")
        tax_choice = st.segmented_control(
            "Tax jurisdiction", ["us", "foreign"], default="us", key="tax_choice", label_visibility="collapsed",
            format_func=lambda v: "United States" if v == "us" else "Selected country", disabled=iso == "USA")
        tax_choice = tax_choice or "us"
        if iso == "USA":
            tax_choice = "us"
        st.caption("Rate applied to both currencies: " + ("United States" if tax_choice == "us" else names[iso]))

    is_us = iso == "USA"
    country_name, _, slug, ccy = COUNTRY_BY_ISO[iso]

    with st.status("Loading bond yields", expanded=False) as status:
        status.update(label="Trading Economics: U.S. 10-year yield")
        us_yield_pack, us_yield_err = safe("yield", fetch_yield, "united-states")
        if is_us:
            fc_yield_pack, fc_yield_err = us_yield_pack, us_yield_err
        else:
            status.update(label=f"Trading Economics: {country_name} 10-year yield")
            fc_yield_pack, fc_yield_err = safe("yield", fetch_yield, slug)

    # ---- Auto values ----
    auto = {
        "beta": None, "tax_us": None, "tax_fc": None, "spread_us": bbb_pack["value"] if bbb_pack else None,
        "rf_us": us_yield_pack["value"] if us_yield_pack else None,
        "rf_fc": fc_yield_pack["value"] if fc_yield_pack else None,
    }
    if beta_pack:
        row = beta_pack["table"].loc[beta_pack["table"]["industry"] == industry, "unlevered_beta"]
        auto["beta"] = float(row.iloc[0]) if len(row) else None
    if tax_pack:
        auto["tax_us"] = tax_pack["rates"].get("USA")
        auto["tax_fc"] = tax_pack["rates"].get(iso)

    # ---- Overrides ----
    with st.sidebar:
        box = st.expander("Manual overrides", expanded=False)
    with box:
        st.caption("Switch an input to manual to replace the fetched value. All other inputs stay automatic.")
        beta_u, beta_o = override("Unlevered beta", "ov_beta", auto["beta"], is_pct=False, step=0.01,
                                  min_value=0.0, max_value=5.0)
        tax_us, tax_us_o = override("U.S. corporate tax rate", "ov_taxus", auto["tax_us"], min_value=0.0, max_value=60.0)
        spread_us, spread_us_o = override("USD BBB spread", "ov_spus", auto["spread_us"], min_value=0.0, max_value=20.0)
        rf_us, rf_us_o = override("U.S. 10-year yield", "ov_rfus", auto["rf_us"], min_value=-2.0, max_value=30.0)
        if is_us:
            tax_fc, tax_fc_o, rf_fc, rf_fc_o = tax_us, tax_us_o, rf_us, rf_us_o
            st.caption("Selected country is the United States, so country inputs mirror the U.S. inputs.")
        else:
            tax_fc, tax_fc_o = override(f"{country_name} corporate tax rate", "ov_taxfc", auto["tax_fc"],
                                        min_value=0.0, max_value=60.0)
            rf_fc, rf_fc_o = override(f"{country_name} 10-year yield", "ov_rffc", auto["rf_fc"],
                                      min_value=-2.0, max_value=100.0)
        spread_fc_auto = None
        if None not in (rf_us, rf_fc, spread_us):
            spread_fc_auto = convert_rate(rf_us + spread_us, rf_us, rf_fc) - rf_fc
        spread_fc, spread_fc_o = (None, "Auto")
        if is_us:
            spread_fc_o = "Auto"
        else:
            spread_fc, spread_fc_o = override(f"{country_name} BBB spread", "ov_spfc", spread_fc_auto,
                                              min_value=-5.0, max_value=50.0)
            if spread_fc_o == "Auto":
                spread_fc = None  # use the model's implied spread

    # ---- Hero ----
    chips = [industry, country_name, f"Tax: {'United States' if tax_choice == 'us' else country_name}",
             f"ERP {erp_pct:.2f}%", f"Debt {debt_pct:.2f}%"]
    chip_html = "".join(f'<span class="chip">{html.escape(c)}</span>' for c in chips)
    st.markdown(f"""<div class="hero"><h1>Cost of Capital, Two Currencies</h1>
<p>Cost of equity, cost of debt and WACC for one project, side by side in U.S. dollars and in the currency of {html.escape(country_name)}.</p>
<div class="chips">{chip_html}</div></div>""", unsafe_allow_html=True)

    # ---- Source problems ----
    problems = []
    for label, err in (("Damodaran industry betas", beta_err), ("Damodaran BBB spread", bbb_err),
                       ("OECD corporate tax rates", tax_err), ("Trading Economics U.S. yield", us_yield_err),
                       (f"Trading Economics {country_name} yield", None if is_us else fc_yield_err)):
        if err:
            problems.append(f"**{label}:** {err}")
    if problems:
        st.warning("Some automatic data could not be loaded. Use Manual overrides in the sidebar for those inputs, "
                   "or press Refresh data to retry.\n\n" + "\n\n".join(f"- {p}" for p in problems))

    tax_project = tax_us if tax_choice == "us" else tax_fc
    required = {"Unlevered beta": beta_u, "U.S. corporate tax rate": tax_us, "USD BBB spread": spread_us,
                "U.S. 10-year yield": rf_us, f"{country_name} corporate tax rate": tax_fc,
                f"{country_name} 10-year yield": rf_fc}
    needed = ["Unlevered beta", "USD BBB spread", "U.S. 10-year yield", f"{country_name} 10-year yield",
              "U.S. corporate tax rate" if tax_choice == "us" else f"{country_name} corporate tax rate"]
    missing = [k for k in needed if required[k] is None]
    if missing:
        st.error("Missing inputs: " + ", ".join(missing) + ". Enter them under Manual overrides to continue.")
        render_sources(beta_pack, bbb_pack, tax_pack, us_yield_pack, fc_yield_pack, country_name, industry, {}, is_us)
        st.stop()

    erp, debt = erp_pct / 100.0, debt_pct / 100.0
    r = cost_of_capital(beta_u, tax_project, debt, erp, rf_us, spread_us, rf_fc, spread_fc)

    # ---- Results ----
    st.markdown("## Results")
    left, right = st.columns(2, gap="large")
    left.markdown(result_card("usd", "USD", "United States dollar", r, r["ke_usd"], r["kd_usd"], r["kd_usd_at"],
                              r["wacc_usd"], debt, None), unsafe_allow_html=True)
    diff = (r["wacc_loc"] - r["wacc_usd"]) * 100
    right.markdown(result_card("loc", ccy, f"{country_name} currency", r, r["ke_loc"], r["kd_loc"], r["kd_loc_at"],
                               r["wacc_loc"], debt, f"{diff:+.2f} pp"), unsafe_allow_html=True)
    st.write("")
    m1, m2, m3, m4 = st.columns(4)
    m1.markdown(mini("Unlevered beta", num(beta_u)), unsafe_allow_html=True)
    m2.markdown(mini("Levered beta", num(r["beta_l"])), unsafe_allow_html=True)
    m3.markdown(mini("Debt / equity", pct(r["de"])), unsafe_allow_html=True)
    m4.markdown(mini("Project tax rate", pct(tax_project)), unsafe_allow_html=True)
    st.write("")
    st.plotly_chart(composition_chart(r, debt, ccy), config={"displaylogo": False}, **stretch(st.plotly_chart))

    # ---- Assumptions ----
    st.markdown("### Main assumptions")
    st.markdown(f"""<div class="note"><ul class="assump">
<li><b>One project, two currencies.</b> The same unlevered beta, project tax rate ({pct(tax_project)}, {'U.S.' if tax_choice == 'us' else country_name}), debt share and equity risk premium apply in both currencies. Only the currency of the cash flows changes.</li>
<li><b>Beta.</b> Industry unlevered beta from Damodaran's U.S. data, relevered at the target D/E with the project tax rate and a debt beta of zero.</li>
<li><b>USD rates.</b> Cost of equity = U.S. 10-year yield + levered beta x equity risk premium. Cost of debt = U.S. 10-year yield + USD BBB spread.</li>
<li><b>Local-currency rates.</b> Each USD rate is converted with the 10-year yield differential: (1 + USD rate) x (1 + {html.escape(country_name)} yield) / (1 + U.S. yield) - 1. The yield gap is {pct(r['yield_gap'], signed=True)}, so the conversion adds {pct(r['conv_factor'], signed=True)} before compounding.</li>
<li><b>Local BBB spread.</b> {'Overridden manually at ' + pct(r['spread_loc_used']) + '.' if spread_fc_o == 'Manual' and not is_us else 'Calculated automatically as the converted USD cost of debt minus the local yield, ' + pct(r['spread_loc_auto']) + '.'}</li>
<li><b>Rounding.</b> Figures are shown to two decimals. Calculations use unrounded inputs.</li>
</ul></div>""", unsafe_allow_html=True)

    # ---- Tabs ----
    t_ind, t_src, t_calc, t_dl = st.tabs(["All industries", "Inputs and sources", "Calculation steps", "Download"])

    ind_rows = []
    if beta_pack:
        for _, row in beta_pack["table"].iterrows():
            rr = cost_of_capital(float(row["unlevered_beta"]), tax_project, debt, erp, rf_us, spread_us, rf_fc, spread_fc)
            ind_rows.append({
                "Industry": row["industry"], "Unlevered beta": row["unlevered_beta"], "Levered beta": rr["beta_l"],
                "Cost of equity USD (%)": rr["ke_usd"] * 100, "Cost of equity local (%)": rr["ke_loc"] * 100,
                "WACC USD (%)": rr["wacc_usd"] * 100, "WACC local (%)": rr["wacc_loc"] * 100,
                "Difference (pp)": (rr["wacc_loc"] - rr["wacc_usd"]) * 100,
            })
    ind_df = pd.DataFrame(ind_rows)

    with t_ind:
        if ind_df.empty:
            st.info("Industry betas are unavailable, so the comparison cannot be built. Press Refresh data to retry.")
        else:
            st.caption(f"Every industry uses the same tax rate, debt share, equity risk premium and yields as above. "
                       f"{'Your manual beta applies only to the selected industry row in the results above.' if beta_o == 'Manual' else ''}")
            st.plotly_chart(industry_chart(ind_df, industry, ccy), config={"displaylogo": False},
                            **stretch(st.plotly_chart))
            fmt = {c: st.column_config.NumberColumn(format="%.2f") for c in ind_df.columns[1:]}
            st.dataframe(ind_df.sort_values("WACC USD (%)", ascending=False), hide_index=True, column_config=fmt,
                         height=420, **stretch(st.dataframe))

    sources_df = render_sources(beta_pack, bbb_pack, tax_pack, us_yield_pack, fc_yield_pack, country_name, industry,
                                {"beta": beta_o, "tax_us": tax_us_o, "tax_fc": tax_fc_o, "spread_us": spread_us_o,
                                 "rf_us": rf_us_o, "rf_fc": rf_fc_o, "spread_fc": spread_fc_o}, is_us,
                                container=t_src,
                                values={"beta": beta_u, "tax_us": tax_us, "tax_fc": tax_fc, "spread_us": spread_us,
                                        "rf_us": rf_us, "rf_fc": rf_fc, "spread_fc": r["spread_loc_used"]})

    with t_calc:
        st.markdown("**Step by step** (values shown to two decimals)")
        steps = [
            ("D/E", f"D/V / (1 - D/V) = {pct(debt)} / {pct(1 - debt)}", pct(r["de"])),
            ("Levered beta", f"{num(beta_u)} x (1 + (1 - {pct(tax_project)}) x {pct(r['de'])})", num(r["beta_l"])),
            ("Cost of equity, USD", f"{pct(rf_us)} + {num(r['beta_l'])} x {pct(erp)}", pct(r["ke_usd"])),
            ("Cost of debt, USD", f"{pct(rf_us)} + {pct(spread_us)}", pct(r["kd_usd"])),
            ("WACC, USD", f"{pct(1 - debt)} x {pct(r['ke_usd'])} + {pct(debt)} x {pct(r['kd_usd'])} x (1 - {pct(tax_project)})",
             pct(r["wacc_usd"])),
            (f"Cost of equity, {ccy}", f"(1 + {pct(r['ke_usd'])}) x (1 + {pct(rf_fc)}) / (1 + {pct(rf_us)}) - 1", pct(r["ke_loc"])),
            (f"Cost of debt, {ccy}", f"{pct(rf_fc)} + {pct(r['spread_loc_used'])}", pct(r["kd_loc"])),
            (f"WACC, {ccy}", f"{pct(1 - debt)} x {pct(r['ke_loc'])} + {pct(debt)} x {pct(r['kd_loc'])} x (1 - {pct(tax_project)})",
             pct(r["wacc_loc"])),
        ]
        st.dataframe(pd.DataFrame(steps, columns=["Quantity", "Calculation", "Result"]), hide_index=True,
                     **stretch(st.dataframe))
        st.caption("Rounded values may differ from a hand calculation by 0.01 because the app keeps full precision.")

    with t_dl:
        summary = pd.DataFrame([
            ("Industry", industry), ("Target country", country_name), ("Local currency", ccy),
            ("Project tax jurisdiction", "United States" if tax_choice == "us" else country_name),
            ("Project tax rate (%)", tax_project * 100), ("Equity risk premium (%)", erp * 100),
            ("Debt share D/V (%)", debt * 100), ("Debt / equity (%)", r["de"] * 100),
            ("Unlevered beta", beta_u), ("Levered beta", r["beta_l"]),
            ("U.S. 10-year yield (%)", rf_us * 100), (f"{country_name} 10-year yield (%)", rf_fc * 100),
            ("USD BBB spread (%)", spread_us * 100), (f"{ccy} BBB spread (%)", r["spread_loc_used"] * 100),
            ("Cost of equity USD (%)", r["ke_usd"] * 100), ("Cost of debt USD pre-tax (%)", r["kd_usd"] * 100),
            ("Cost of debt USD after-tax (%)", r["kd_usd_at"] * 100), ("WACC USD (%)", r["wacc_usd"] * 100),
            (f"Cost of equity {ccy} (%)", r["ke_loc"] * 100), (f"Cost of debt {ccy} pre-tax (%)", r["kd_loc"] * 100),
            (f"Cost of debt {ccy} after-tax (%)", r["kd_loc_at"] * 100), (f"WACC {ccy} (%)", r["wacc_loc"] * 100),
        ], columns=["Item", "Value"])
        summary["Value"] = summary["Value"].map(lambda v: f"{v:.2f}" if isinstance(v, (int, float, np.floating)) else v)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        c1, c2, c3 = st.columns(3)
        c1.download_button("Results (CSV)", summary.to_csv(index=False).encode("utf-8"),
                           file_name=f"cost_of_capital_{iso}_{stamp}.csv", mime="text/csv", key="dl_res",
                           **stretch(st.download_button))
        if not ind_df.empty:
            c2.download_button("All industries (CSV)", ind_df.round(2).to_csv(index=False).encode("utf-8"),
                               file_name=f"cost_of_capital_all_industries_{iso}_{stamp}.csv", mime="text/csv",
                               key="dl_ind", **stretch(st.download_button))
        c3.download_button("Inputs and sources (CSV)", sources_df.to_csv(index=False).encode("utf-8"),
                           file_name=f"cost_of_capital_sources_{stamp}.csv", mime="text/csv", key="dl_src",
                           **stretch(st.download_button))
        st.caption("All exported figures are rounded to two decimals. Percentages are in percentage points.")
        st.dataframe(summary, hide_index=True, **stretch(st.dataframe))


def render_sources(beta_pack, bbb_pack, tax_pack, us_pack, fc_pack, country_name, industry, origins, is_us,
                   container=None, values=None) -> pd.DataFrame:
    """Build the inputs-and-sources table and show it in the given container."""
    values = values or {}

    def row(item, key, value, source, asof, retrieved, url, is_pct=True):
        origin = origins.get(key, "Auto" if value is not None else "Missing")
        shown = None if value is None else (value * 100 if is_pct else value)
        return {"Input": item, "Value": None if shown is None else round(float(shown), 2), "Origin": origin,
                "Source": source, "As of": asof, "Retrieved": retrieved, "Link": url}

    rows = [
        row(f"Unlevered beta, {industry}", "beta", values.get("beta"), "Damodaran, U.S. industry betas",
            beta_pack["asof"] if beta_pack else "n/a", beta_pack["retrieved"] if beta_pack else "n/a",
            BETA_XLS_URL, is_pct=False),
        row("USD BBB spread (Baa2/BBB)", "spread_us", values.get("spread_us"),
            "Damodaran, ratings and default spreads",
            bbb_pack["asof"] if bbb_pack else "n/a", bbb_pack["retrieved"] if bbb_pack else "n/a", RATINGS_URL),
        row("U.S. corporate tax rate", "tax_us", values.get("tax_us"), "OECD, statutory corporate income tax rates",
            f"{tax_pack['year']} data" if tax_pack else "n/a", tax_pack["retrieved"] if tax_pack else "n/a",
            OECD_PAGE_URL),
        row("U.S. 10-year yield", "rf_us", values.get("rf_us"), "Trading Economics",
            us_pack["asof"] if us_pack else "n/a", us_pack["retrieved"] if us_pack else "n/a",
            us_pack["url"] if us_pack else TE_BONDS_URL),
    ]
    if not is_us:
        rows += [
            row(f"{country_name} corporate tax rate", "tax_fc", values.get("tax_fc"),
                "OECD, statutory corporate income tax rates", f"{tax_pack['year']} data" if tax_pack else "n/a",
                tax_pack["retrieved"] if tax_pack else "n/a", OECD_PAGE_URL),
            row(f"{country_name} 10-year yield", "rf_fc", values.get("rf_fc"), "Trading Economics",
                fc_pack["asof"] if fc_pack else "n/a", fc_pack["retrieved"] if fc_pack else "n/a",
                fc_pack["url"] if fc_pack else TE_BONDS_URL),
            row(f"{country_name} BBB spread", "spread_fc", values.get("spread_fc"),
                "Model: converted USD cost of debt less local yield (or manual)", "n/a", "n/a", ""),
        ]
    df = pd.DataFrame(rows)
    target = container if container is not None else contextlib.nullcontext()
    with target:
        st.caption("Yield dates are shown as published by Trading Economics. Tax rates are the latest year available "
                   "in the OECD data. Values are shown in percent except the beta.")
        st.dataframe(df, hide_index=True, **stretch(st.dataframe),
                     column_config={"Value": st.column_config.NumberColumn(format="%.2f"),
                                    "Link": st.column_config.LinkColumn("Link", display_text="open")})
    return df


main()