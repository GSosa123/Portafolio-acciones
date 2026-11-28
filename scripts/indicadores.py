#!/usr/bin/env python3
"""Dólar, euro, UF, UTM y otras monedas -> indicadores.json (lo lee index.html).

Valores oficiales (Banco Central de Chile) vía mindicador.cl, contrastados con findic.cl.
Valores de mercado (cambian durante el día) vía Yahoo Finance.
Solo usa la biblioteca estándar.
"""
import json, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (indicadores-cartera)"}
OFICIALES = ["dolar", "euro", "uf", "utm", "libra_cobre"]
# código interno -> símbolo Yahoo
MERCADO = {"dolar": "CLP=X", "euro": "EURCLP=X", "libra": "GBPCLP=X", "real": "BRLCLP=X",
           "yuan": "CNYCLP=X", "yen": "JPYCLP=X", "cobre": "HG=F", "bitcoin": "BTC-USD"}


def leer(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
        return json.load(r)


def serie(base, cod):
    d = leer(f"{base}/{cod}")
    s = [(str(x["fecha"])[:10], float(x["valor"])) for x in d.get("serie", []) if x.get("valor") is not None]
    s.sort()
    return s


def oficial(cod):
    s = serie("https://mindicador.cl/api", cod)
    if not s:
        raise ValueError("sin datos")
    f, v = s[-1]
    out = {"valor": v, "fecha": f, "ant": s[-2][1] if len(s) > 1 else 0, "fechaAnt": s[-2][0] if len(s) > 1 else "",
           "fuente": "Banco Central de Chile (vía mindicador.cl)", "t": [x[0] for x in s], "c": [x[1] for x in s]}
    try:  # contraste con una segunda fuente independiente
        s2 = dict(serie("https://findic.cl/api", cod))
        if f in s2:
            out["contraste"] = {"fuente": "findic.cl", "valor": s2[f], "difPct": round((s2[f] / v - 1) * 100, 4)}
    except Exception as e:  # noqa: BLE001
        out["contraste"] = {"fuente": "findic.cl", "error": str(e)[:80]}
    return out


def mercado(sym):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/" + urllib.parse.quote(sym)
           + "?range=1d&interval=5m")
    res = leer(url)["chart"]["result"][0]
    m = res["meta"]
    p = m.get("regularMarketPrice")
    if not p:
        raise ValueError("sin precio")
    cl = [x for x in ((res.get("indicators", {}).get("quote") or [{}])[0].get("close") or []) if x]
    return {"valor": round(float(p), 6), "ant": round(float(m.get("chartPreviousClose") or m.get("previousClose") or 0), 6),
            "ts": int(m.get("regularMarketTime") or time.time()), "c": [round(x, 6) for x in cl[-80:]], "simbolo": sym}


def main():
    ruta = RAIZ / "indicadores.json"
    previo = {}
    if ruta.exists():
        try:
            previo = json.loads(ruta.read_text(encoding="utf-8"))
        except ValueError:
            pass
    of, me, err = {}, {}, []
    for c in OFICIALES:
        try:
            of[c] = oficial(c)
        except Exception as e:  # noqa: BLE001
            err.append(f"{c}: {e}")
            if c in previo.get("oficial", {}):
                of[c] = previo["oficial"][c]
        time.sleep(0.5)
    for c, sym in MERCADO.items():
        try:
            me[c] = mercado(sym)
        except Exception as e:  # noqa: BLE001
            err.append(f"{sym}: {e}")
            if c in previo.get("mercado", {}):
                me[c] = previo["mercado"][c]
        time.sleep(0.5)
    if not of and not me:
        print("Sin datos:", *err, sep="\n", file=sys.stderr)
        return 1
    if of == previo.get("oficial") and me == previo.get("mercado"):
        print("Sin cambios.")
        return 0
    ruta.write_text(json.dumps({
        "actualizado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "oficial": of, "mercado": me,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(of)} oficiales, {len(me)} de mercado.", *err, sep="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
