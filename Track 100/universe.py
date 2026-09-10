"""Nasdaq-100 + liquid top-100 universe for Track 100 study."""
from __future__ import annotations

# Nasdaq-100 (as of mid-2026 approximate constituents; study universe, not live SoT)
NDX100: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "AVGO", "GOOGL", "GOOG", "TSLA", "COST",
    "NFLX", "AMD", "PEP", "ADBE", "CSCO", "TMUS", "LIN", "INTU", "CMCSA", "TXN",
    "QCOM", "AMGN", "INTC", "AMAT", "ISRG", "HON", "BKNG", "VRTX", "ADP", "SBUX",
    "PANW", "GILD", "ADI", "MU", "LRCX", "MDLZ", "REGN", "PYPL", "KLAC", "SNPS",
    "CDNS", "MELI", "CRWD", "MAR", "CTAS", "ORLY", "CSX", "ASML", "MRVL", "FTNT",
    "DASH", "ADSK", "PCAR", "NXPI", "ROP", "AEP", "WDAY", "CPRT", "MNST", "PAYX",
    "ROST", "FAST", "KDP", "AZN", "CTSH", "BKR", "GEHC", "EA", "VRSK", "EXC",
    "XEL", "CCEP", "FANG", "TTD", "CHTR", "IDXX", "MCHP", "CSGP", "ON", "DXCM",
    "KHC", "ANSS", "TEAM", "CDW", "ZS", "GFS", "TTWO", "ODFL", "BIIB", "ILMN",
    "WBD", "DDOG", "MDB", "ARM", "SMCI", "APP", "PLTR", "ABNB", "PDD", "SHOP",
]

# Extra liquid popular names often in "most traded" (not always in NDX)
POPULAR_EXTRA: list[str] = [
    "SPY", "QQQ", "IWM", "SOXL", "TQQQ", "NVDL", "HOOD", "COIN", "MSTR", "SOFI",
    "RIVN", "NIO", "LCID", "PLUG", "MARA", "RIOT", "AFRM", "UPST", "SNAP", "PINS",
    "UBER", "LYFT", "DKNG", "PENN", "BA", "DIS", "NKE", "JPM", "BAC", "GS",
    "XOM", "CVX", "OXY", "SLB", "CAT", "DE", "GE", "UNH", "JNJ", "PFE",
    "LLY", "MRK", "WMT", "TGT", "HD", "LOW", "CRM", "NOW", "SNOW", "NET",
    "U", "RBLX", "PATH", "AI", "SOUN", "IONQ", "RKLB", "ASTS", "SMR", "OKLO",
    "IREN", "CIFR", "HUT", "CLSK", "BTBT", "BITF", "OPEN", "CVNA", "CVS", "WBA",
    "F", "GM", "TSLL", "CONL", "MSFT", "AAPL", "NVDA", "AMD", "INTC", "MU",
]


def study_universe(*, include_popular_extra: bool = True, cap: int = 120) -> list[str]:
    """Deduped NDX100 (+ optional popular extras), capped."""
    out: list[str] = []
    seen: set[str] = set()
    for sym in NDX100 + (POPULAR_EXTRA if include_popular_extra else []):
        s = sym.upper().strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= cap:
            break
    return out
