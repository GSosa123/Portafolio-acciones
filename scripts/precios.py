#!/usr/bin/env python3
"""Baja precios de cierre/último de acciones chilenas desde Yahoo Finance (sufijo .SN)
y los escribe en precios.json para que index.html los lea.

Tickers: los de MAESTRO en index.html + los que agregues en tickers.txt (uno por línea).
Solo usa la biblioteca estándar.
"""
import json, re, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (precios-cartera)"}


def tickers():
    html = (RAIZ / "index.html").read_text(encoding="utf-8")
    tk = re.findall(r"\{\s*ticker:\s*'([A-Z0-9.\-]+)'", html)
    extra = RAIZ / "tickers.txt"
    if extra.exists():
        for l in extra.read_text(encoding="utf-8").splitlines():
            l = l.split("#")[0].strip().upper()
            if l:
                tk.append(l)
    return sorted(set(tk)) + ["^IPSA"]  # IPSA como referencia


def cotizar(tk):
    sym = tk if tk.startswith("^") else tk + ".SN"
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(sym) + "?range=1y&interval=1d")
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        d = json.load(r)
    res = d["chart"]["result"][0]
    m = res["meta"]
    precio = m.get("regularMarketPrice")
    if not precio:
        raise ValueError("sin precio")
    lows = [x for x in (res.get("indicators", {}).get("quote", [{}])[0].get("low") or []) if x]
    min52 = m.get("fiftyTwoWeekLow") or (min(lows) if lows else 0)
    ts = m.get("regularMarketTime") or int(time.time())
    fecha = datetime.fromtimestamp(ts, timezone(timedelta(hours=-3))).strftime("%Y-%m-%d")
    closes = [x for x in (res.get("indicators", {}).get("quote", [{}])[0].get("close") or []) if x]
    hoy_bar = res.get("timestamp") and datetime.fromtimestamp(res["timestamp"][-1], timezone(timedelta(hours=-3))).strftime("%Y-%m-%d") == fecha
    ant = closes[-2] if hoy_bar and len(closes) > 1 else (closes[-1] if closes else 0)
    r2 = lambda v: round(float(v or 0), 4)
    return {"precio": r2(precio), "min52": r2(min52), "fecha": fecha, "ts": int(ts),
            "ant": r2(ant), "max": r2(m.get("regularMarketDayHigh")), "min": r2(m.get("regularMarketDayLow")),
            "vol": int(m.get("regularMarketVolume") or 0), **historia(res)}


def historia(res):
    """Cierres diarios del último año: {"t": [fechas], "c": [cierres]} (para IPSA, Sharpe, caída máxima)."""
    ts = res.get("timestamp") or []
    cl = (res.get("indicators", {}).get("quote", [{}])[0].get("close") or [])
    t, c = [], []
    for x, v in zip(ts, cl):
        if v:
            t.append(datetime.fromtimestamp(x, timezone(timedelta(hours=-3))).strftime("%Y-%m-%d"))
            c.append(round(float(v), 4))
    return {"t": t, "c": c}


def main():
    out, err = {}, []
    for tk in tickers():
        try:
            out[tk] = cotizar(tk)
        except Exception as e:  # noqa: BLE001
            err.append(f"{tk}: {e}")
        time.sleep(1)
    if not out:
        print("No se obtuvo ningún precio:", *err, sep="\n", file=sys.stderr)
        return 1
    ruta = RAIZ / "precios.json"
    previo = {}
    if ruta.exists():
        try:
            previo = json.loads(ruta.read_text(encoding="utf-8")).get("precios", {})
        except ValueError:
            pass
    # conserva el último precio conocido de los que fallaron
    for tk, v in previo.items():
        out.setdefault(tk, v)
    if out == previo:
        print("Sin cambios.")
        return 0
    ruta.write_text(json.dumps({
        "actualizado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fuente": "Yahoo Finance",
        "precios": out,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(out)} precios escritos.", *err, sep="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
