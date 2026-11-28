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
    "FALABELLA": ["falabella"], "CENCOSUD": ["cencosud"], "BCI": ["bci "], "ENELCHILE": ["enel chile"],
    "ECL": ["engie"], "PARAUCO": ["parque arauco"], "MALLPLAZA": ["mall plaza", "mallplaza"],
    "RIPLEY": ["ripley"], "ILC": ["ilc"], "VAPORES": ["vapores"], "CCU": ["ccu"], "SMU": ["smu"],
    "QUINENCO": ["quiñenco"], "CONCHATORO": ["concha y toro"], "SALFACORP": ["salfacorp"],
    "BSANTANDER": ["banco santander"], "ITAUCL": ["itaú chile", "banco itaú"], "CREDICORP": ["credicorp ("],
}
# Corredoras / bancos de inversión (cómo aparecen en los titulares)
CORREDORAS = ["J.P. Morgan", "JPMorgan", "JP Morgan", "BTG Pactual", "BTG", "Credicorp Capital", "Credicorp", "Santander Corredores",
              "LarrainVial", "Larraín Vial", "Itaú BBA", "Itaú", "Bci Corredores", "Scotiabank", "Morgan Stanley",
              "Goldman Sachs", "Goldman", "Bank of America", "BofA", "Citi", "Jefferies", "UBS", "HSBC",
              "Deutsche Bank", "BMO", "Bradesco", "Renta4", "Renta 4", "MBI", "BICE", "Barclays", "Security",
              "EuroAmerica", "Banchile", "Safra", "Moody's", "S&P", "Fitch"]
RECO = re.compile(r"recomien|favorit|cartera (recomendada|de acciones)|top picks?|"
                  r"\bmejora\b|\bempeora\b|degrada|\bsube\b el precio|nueva visión|visión de|"
                  r"recomend|precio objetivo|llama a (vender|comprar)|\bvender\b|\bcomprar\b|"
                  r"sobreponder|subponder|apuesta (por|alcista|bajista)|mantiene apuesta|rebaja|eleva|"
                  r"upgrade|downgrade|neutral|outperform|underperform|bajistas|alcistas", re.I)
NO = re.compile(r"the economist|columnas|ingrese al portal|anularme|suscrib|twitter|instagram|linkedin", re.I)


def limpiar(t):
    t = re.sub(r"^[-\s]+", "", t).rstrip(", ")
    t = re.sub(r"\b(de|su|sus)([A-ZÁÉÍÓÚ]{2,}|cartera)", r"\1 \2", t)
    return t.replace("suscarteras", "sus carteras")


def es_reco(t, tks):
    if re.search(r"recomend|precio objetivo", t, re.I) and (tks or corredora(t)):
        return True
    if not RECO.search(t) or re.search(r"dividendo|utilidad|resultado|dólar|peso chileno|swaps", t, re.I):
        return False
    return bool(tks or corredora(t) or re.search(r"favorit|cartera|acciones chilenas", t, re.I))


def corredora(t):
    for c in CORREDORAS:
        if re.search(r"(?<![\w])" + re.escape(c) + r"(?![\w])", t):
            return {"JPMorgan": "J.P. Morgan", "JP Morgan": "J.P. Morgan", "Santander Corredores": "Santander", "Bci Corredores": "Bci", "Goldman": "Goldman Sachs", "BofA": "Bank of America",
                    "BTG": "BTG Pactual", "Credicorp": "Credicorp Capital", "Larraín Vial": "LarrainVial",
                    "Renta 4": "Renta4"}.get(c, c)
    return ""


def postura(t):
    l = t.lower()
    v = re.search(r"vender|subponder|rebaja|empeora|degrada|bajista|underperform|downgrade", l)
    c = re.search(r"comprar|sobreponder|eleva|mejora|alcista|outperform|upgrade", l)
    if v and c:
        return "Mixta"
    if re.search(r"cartera|favorit", l):
        return "Cartera recomendada"
    if re.search(r"vender|subponder|rebaja|recorta|empeora|degrada|bajista|underperform|downgrade|castiga", l):
        return "Vender / baja"
    if re.search(r"comprar|sobreponder|apuesta|eleva|sube el precio|subió|mejora|alcista|outperform|upgrade|a recomendar|top pick", l):
        return "Comprar / sube"
    if re.search(r"mantiene|neutral|mantener", l):
        return "Mantener"
    return "Cambio de visión"


def vigentes():
    html = (RAIZ / "index.html").read_text(encoding="utf-8")
    bloque = html.split("posiciones: [", 1)[1].split("]", 1)[0]
    return re.findall(r"\{\s*ticker:\s*'([A-Z0-9.\-]+)'", bloque)


def titulares(texto):
    vistos = set()
    for m in re.finditer(r"(?:\*\*|(?:^|\n)\s*-)\s*(?:https?://\S+?\.aspx)?([^\n*()]{12,220}?)\s*\((https?://www\.elmercurio\.com/Inversiones/[^)\s]+)\)", texto):
        t, u = m.group(1).strip(), m.group(2)
        if u in vistos or "buscador" in u or NO.search(t) or NO.search(u) or "/Columnas/" in u or "newsletter" in u:
            continue
        vistos.add(u)
        yield t, u


def main(args):
    mias = set(vigentes())
    recalcular = "--recalcular" in args
    args = [a for a in args if a != "--recalcular"]
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
            t = limpiar(t)
            reco = es_reco(t, tks)
            mf = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", u)
            items[u] = {"fecha": "-".join(mf.groups()) if mf else fecha, "titulo": t, "url": u, "tickers": tks,
                        "mia": any(tk in mias for tk in tks), "recomendacion": reco}
            if reco:
                items[u]["corredora"] = corredora(t)
                items[u]["postura"] = postura(t)
            nuevos += 1
    items = {u: x for u, x in items.items() if "buscador" not in u}
    if recalcular:  # vuelve a clasificar lo ya guardado (tras mejorar reglas)
        for x in items.values():
            low = " " + x["titulo"].lower() + " "
            x["tickers"] = [tk for tk, al in ALIAS.items() if any(a in low for a in al)]
            x["mia"] = any(tk in mias for tk in x["tickers"])
            x["titulo"] = limpiar(x["titulo"])
            x["recomendacion"] = es_reco(x["titulo"], x["tickers"])
            x.pop("corredora", None); x.pop("postura", None)
            if x["recomendacion"]:
                x["corredora"], x["postura"] = corredora(x["titulo"]), postura(x["titulo"])
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
