#!/usr/bin/env python3
"""Extrae titulares del newsletter de El Mercurio Inversiones (texto plano del correo)
y los suma a mercurio.json para la pestaña Noticias de la app.

Solo guarda titular + enlace + fecha (el contenido es pagado y el repo es público).
Marca los que mencionan acciones de la cartera o son recomendaciones de corredoras.

Uso:  python3 scripts/mercurio.py --fecha 2026-11-27 correo1.txt [--fecha ... correo2.txt ...]
"""
import json, re, sys
from datetime import datetime, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "mercurio.json"

# Cómo aparece cada acción en la prensa (minúsculas). Agrega aquí si compras otra.
ALIAS = {
    "CHILE": ["banco de chile", "banchile"],
    "ANDINA-B": ["andina"],
    "COLBUN": ["colbún", "colbun"],
    "LTM": ["latam"],
    "SCHWAGER": ["schwager"],
    "AGUAS-A": ["aguas andinas"],
    "SQM-B": ["sqm"], "CMPC": ["cmpc"], "COPEC": ["copec"], "ENTEL": ["entel"], "CAP": ["cap "],
    "FALABELLA": ["falabella"], "CENCOSUD": ["cencosud"], "BCI": ["bci"], "ENELCHILE": ["enel chile"],
}
RECO = re.compile(r"recomien|recomendad|precio objetivo|llama a (vender|comprar)|\bvender\b|\bcomprar\b|"
                  r"sobreponder|subponder|apuesta (por|alcista|bajista)|mantiene apuesta|rebaja|eleva|"
                  r"upgrade|downgrade|neutral|outperform|underperform|bajistas|alcistas", re.I)
NO = re.compile(r"the economist|columnas|ingrese al portal|anularme|suscrib|twitter|instagram|linkedin", re.I)


def vigentes():
    html = (RAIZ / "index.html").read_text(encoding="utf-8")
    bloque = html.split("posiciones: [", 1)[1].split("]", 1)[0]
    return re.findall(r"\{\s*ticker:\s*'([A-Z0-9.\-]+)'", bloque)


def titulares(texto):
    vistos = set()
    for m in re.finditer(r"\*\*\s*(?:https?://\S+?\.aspx)?([^\n*]{12,220}?)\s*\((https?://www\.elmercurio\.com/Inversiones/[^)\s]+)\)", texto):
        t, u = m.group(1).strip(), m.group(2)
        if u in vistos or NO.search(t) or NO.search(u) or "/Columnas/" in u or "newsletter" in u:
            continue
        vistos.add(u)
        yield t, u


def main(args):
    mias = set(vigentes())
    prev = json.loads(SALIDA.read_text(encoding="utf-8")) if SALIDA.exists() else {"items": []}
    items = {i["url"]: i for i in prev["items"]}
    fecha, nuevos = datetime.now().strftime("%Y-%m-%d"), 0
    i = 0
    while i < len(args):
        if args[i] == "--fecha":
            fecha = args[i + 1]
            i += 2
            continue
        texto = Path(args[i]).read_text(encoding="utf-8", errors="replace")
        i += 1
        for t, u in titulares(texto):
            if u in items:
                continue
            low = " " + t.lower() + " "
            tks = [tk for tk, al in ALIAS.items() if any(a in low for a in al)]
            reco = bool(RECO.search(t))
            mf = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", u)
            items[u] = {"fecha": "-".join(mf.groups()) if mf else fecha, "titulo": t, "url": u, "tickers": tks,
                        "mia": any(tk in mias for tk in tks), "recomendacion": reco}
            nuevos += 1
    corte = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
    lista = sorted((x for x in items.values() if x["fecha"] >= corte),
                   key=lambda x: (x["fecha"], x["mia"], x["recomendacion"]), reverse=True)[:200]
    SALIDA.write_text(json.dumps({"actualizado": datetime.now().strftime("%Y-%m-%d %H:%M"), "items": lista},
                                 ensure_ascii=False, indent=1), encoding="utf-8")
    mias_n = [x for x in lista if x["fecha"] >= fecha and x["mia"]]
    print(f"{nuevos} titulares nuevos; {len(mias_n)} sobre tus acciones")
    for x in mias_n:
        print(" -", ", ".join(x["tickers"]), "|", x["titulo"])


if __name__ == "__main__":
    main(sys.argv[1:])
