#!/usr/bin/env python3
"""Revisa hechos esenciales (CMF) y noticias de prensa de las acciones de la cartera
y los deja en noticias.json para que index.html los muestre.

- Hechos esenciales: consulta pública de la CMF por RUT del emisor (últimos 30 días).
- Dividendos: si un hecho esencial habla de dividendo, lee su PDF (texto u OCR) y
  extrae monto por acción, fecha límite de registro y fecha de pago.
- Noticias: Google Noticias (RSS) por empresa, últimos 7 días, filtradas a lo relevante.
- Avisos: lo importante y nuevo se envía al celular por ntfy (variable NTFY_TOPIC).

Solo biblioteca estándar. pdftotext / tesseract se usan si están instalados.
"""
import html as H
import json, os, re, subprocess, sys, tempfile, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "noticias.json"
CL = timezone(timedelta(hours=-3))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}

# RUT (sin DV) y cómo buscarla en prensa. Agrega aquí nuevas acciones de la cartera.
EMPRESAS = {
    "CHILE":    {"rut": "97004000", "nombre": "Banco de Chile", "q": '"Banco de Chile"'},
    "ANDINA-B": {"rut": "91144000", "nombre": "Embotelladora Andina", "q": '"Embotelladora Andina" OR "Coca-Cola Andina"'},
    "COLBUN":   {"rut": "96505760", "nombre": "Colbún", "q": 'Colbún'},
    "LTM":      {"rut": "89862200", "nombre": "LATAM Airlines", "q": '"LATAM Airlines"'},
    "SCHWAGER": {"rut": "96766600", "nombre": "Schwager", "q": 'Schwager'},
    "AGUAS-A":  {"rut": "61808000", "nombre": "Aguas Andinas", "q": '"Aguas Andinas"'},
}

# Palabras que hacen "importante" una noticia de prensa (los hechos esenciales siempre lo son).
CLAVE = re.compile(
    r"dividend|utilidad|resultado|ganancia|p[ée]rdida|ebitda|fusi[óo]n|adquisici|compra de|venta de|"
    r"\bopa\b|aumento de capital|colocaci|emisi[óo]n de|bono|renuncia|gerente general|directorio|"
    r"junta de accionistas|clasificaci|rating|multa|sanci[óo]n|cmf|quiebra|reorganizaci|chapter|"
    r"huelga|licitaci|contrato|deterioro|provisi[óo]n|guidance|proyecci", re.I)
DESCARTE = re.compile(r"tradingview|investing\.com|precio objetivo|cotizaci[óo]n|gr[áa]fico", re.I)

MESES = {m: i + 1 for i, m in enumerate(
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split())}
MESES["setiembre"] = 9


def get(url, data=None, timeout=40, intentos=3):
    """Descarga con curl (la CMF rechaza clientes que no parecen navegador); reintenta si hay 429/5xx."""
    cmd = ["curl", "-sSL", "--compressed", "-m", str(timeout), "-A", UA["User-Agent"],
           "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
           "-H", "Accept-Language: es-CL,es;q=0.9", "-w", "\n%{http_code}", url]
    if data is not None:
        cmd[1:1] = ["--data-binary", data.decode("latin-1"), "-H", "Referer: " + url]
    for k in range(intentos):
        r = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
        cuerpo, _, cod = r.stdout.rpartition(b"\n")
        if r.returncode == 0 and cod.strip() == b"200":
            return cuerpo
        err = f"HTTP {cod.decode().strip() or r.stderr.decode()[:80]}"
        if cod.strip() not in (b"429", b"500", b"502", b"503", b"504"):
            break
        time.sleep(8 * (k + 1))
    raise RuntimeError(err)


def limpio(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", H.unescape(s)).strip()


def decodificar(b):
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("latin-1", "replace")


# ---------------------------------------------------------------- CMF
CMF_URL = ("https://www.cmfchile.cl/institucional/mercados/entidad.php?mercado=V&rut={rut}"
           "&grupo=&tipoentidad=RVEMI&row=&vig=VI&control=svs&pestania=25")


def hechos_cmf(tk, emp, dias=30):
    url = CMF_URL.format(rut=emp["rut"])
    if not emp.get("razon"):
        g = decodificar(get(url))
        m = re.search(r'name=\s*"entidad"\s+value="([^"]*)"', g)
        emp["razon"] = m.group(1) if m else ""
    datos = urllib.parse.urlencode({
        "dd": "###", "mm": "###", "aa": "###", "dd2": "###", "mm2": "###", "aa2": "###",
        "dias": str(dias), "entidad": emp.get("razon", ""), "rut": emp["rut"] + " ", "formulario": "1"}).encode()
    h = decodificar(get(url, datos))
    fin = h.find("end inc/hechos_e.php")
    ini = h.rfind("<table", 0, fin) if fin > 0 else -1
    if ini < 0:
        return []  # sin hechos en el período
    cuerpo = h[ini:fin]
    out = []
    for fila in re.findall(r"<tr[^>]*>(.*?)</tr>", cuerpo, re.S | re.I):
        celdas = re.findall(r"<td[^>]*>(.*?)</td>", fila, re.S | re.I)
        if len(celdas) < 2:
            continue
        txt = [limpio(c) for c in celdas]
        m = next((re.search(r"(\d{2})/(\d{2})/(\d{4})", t) for t in txt if re.search(r"\d{2}/\d{2}/\d{4}", t)), None)
        if not m:
            continue
        fecha = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        link = re.search(r'href="([^"]+)"', fila)
        href = H.unescape(link.group(1)) if link else url
        if href.startswith("/"):
            href = "https://www.cmfchile.cl" + href
        materia = txt[-1] if txt[-1] and not re.fullmatch(r"[\d/ :\-]+", txt[-1]) else "Hecho esencial"
        out.append({"ticker": tk, "fuente": "CMF", "tipo": "Hecho esencial", "fecha": fecha,
                    "titulo": materia, "url": href, "importante": True})
    return out


# ---------------------------------------------------------------- PDF -> texto
def texto_pdf(url):
    try:
        pdf = get(url, timeout=60)
    except Exception as e:
        print("  pdf no disponible:", e)
        return ""
    if not pdf.startswith(b"%PDF"):
        # La CMF a veces entrega una página intermedia con el link real al PDF
        m = re.search(rb'href="([^"]+\.pdf[^"]*)"', pdf, re.I) or re.search(rb'href="([^"]*ver_sgd[^"]*)"', pdf, re.I)
        if not m:
            return limpio(pdf.decode("latin-1", "replace"))
        u2 = urllib.parse.urljoin(url, H.unescape(m.group(1).decode("latin-1")))
        try:
            pdf = get(u2, timeout=60)
        except Exception:
            return ""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "doc.pdf"
        p.write_bytes(pdf)
        txt = ""
        try:
            txt = subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True, timeout=60).stdout
        except Exception:
            pass
        if len(txt.strip()) < 80:  # escaneado: OCR
            try:
                subprocess.run(["pdftoppm", "-r", "200", "-png", str(p), str(Path(d) / "pg")], timeout=120, check=True)
                for img in sorted(Path(d).glob("pg*.png")):
                    txt += subprocess.run(["tesseract", str(img), "-", "-l", "spa"], capture_output=True,
                                          text=True, timeout=120).stdout
            except Exception as e:
                print("  sin OCR:", e)
        return re.sub(r"-\s*\n\s*", "", txt)  # une palabras cortadas con guion


def fecha_es(s):
    m = re.search(r"(\d{1,2})\s+de\s+([a-záéíóú]+)\s+(?:de|del)\s+(\d{4})", s, re.I)
    if m and m.group(2).lower() in MESES:
        return f"{m.group(3)}-{MESES[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    return ""


def num_es(s):
    return float(s.replace(".", "").replace(",", "."))


FERIADOS_FIJOS = {"01-01", "05-01", "05-21", "06-29", "08-15", "09-18", "09-19", "10-12", "10-31", "11-01", "12-08", "12-25"}


def habiles_antes(fecha, n):
    d = datetime.strptime(fecha, "%Y-%m-%d")
    while n:
        d -= timedelta(days=1)
        if d.weekday() < 5 and d.strftime("%m-%d") not in FERIADOS_FIJOS:
            n -= 1
    return d.strftime("%Y-%m-%d")


def leer_dividendo(texto, serie=""):
    """Extrae monto por acción, moneda, fecha de registro y de pago desde el texto del hecho esencial."""
    t = re.sub(r"\s+", " ", texto)
    d = {}
    pat = r"(US\$|USD|\$)\s*([\d.]+,\d+|\d+)\s*(?:\([^)]{0,60}\)\s*)?(?:por|por cada)\s+acci[óo]n(?:es)?(?:\s+de\s+la\s+serie\s+([AB]))?"
    cands = list(re.finditer(pat, t, re.I))
    m = next((c for c in cands if serie and (c.group(3) or "").upper() == serie), cands[0] if cands else None)
    if m:
        d["moneda"] = "USD" if "US" in m.group(1).upper() else "CLP"
        d["por_accion"] = num_es(m.group(2))
    m = re.search(r"(provisorio|definitivo|eventual|adicional)", t, re.I)
    if m:
        d["clase"] = m.group(1).lower()
    m = re.search(r"a partir del?\s*(?:d[íi]a\s*)?(.{0,40}?\d{4})", t, re.I)
    if m:
        d["pago"] = fecha_es(m.group(1))
    m = re.search(r"(?:inscritos|registrad)[^.]{0,120}?(?:medianoche|cierre)[^.]{0,30}?(\d{1,2}\s+de\s+[a-záéíóú]+\s+de\s+\d{4})", t, re.I)
    if m:
        d["registro"] = fecha_es(m.group(1))
    m = re.search(r"(quinto|5[°º]?)\s+d[íi]a\s+h[áa]bil\s+anterior", t, re.I)
    if m and d.get("pago") and not d.get("registro"):
        d["registro"] = habiles_antes(d["pago"], 5)
    return d if d.get("por_accion") else {}


# ---------------------------------------------------------------- Prensa
def noticias_google(tk, emp, dias=7):
    q = f"{emp['q']} when:{dias}d"
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=es-419&gl=CL&ceid=CL:es-419")
    raiz = ET.fromstring(get(url, timeout=30))
    out = []
    for it in raiz.iter("item"):
        titulo = limpio(it.findtext("title"))
        if not titulo or DESCARTE.search(titulo):
            continue
        try:
            f = parsedate_to_datetime(it.findtext("pubDate")).astimezone(CL)
        except Exception:
            continue
        out.append({"ticker": tk, "fuente": limpio(it.findtext("source")) or "Prensa", "tipo": "Noticia",
                    "fecha": f.strftime("%Y-%m-%d"), "titulo": titulo, "url": it.findtext("link") or "",
                    "importante": bool(CLAVE.search(titulo))})
    return out[:12]


# ---------------------------------------------------------------- Aviso ntfy
def avisar(item, topic):
    titulo = f"{item['ticker']}: {item['tipo']}"
    cuerpo = item["titulo"]
    dv = item.get("dividendo")
    if dv:
        mon = "US$" if dv.get("moneda") == "USD" else "$"
        cuerpo = (f"Dividendo {dv.get('clase', '')} {mon} {dv['por_accion']:g} por acción. "
                  f"Fecha límite {dv.get('registro', '?')}, pago {dv.get('pago', '?')}.").replace("  ", " ")
    req = urllib.request.Request("https://ntfy.sh/" + topic, data=cuerpo.encode("utf-8"), headers={
        "Title": titulo.encode("utf-8").decode("latin-1", "replace"),
        "Click": item.get("url") or "https://gsosa123.github.io/Portafolio-acciones/",
        "Tags": "moneybag" if dv else ("loudspeaker" if item["fuente"] == "CMF" else "newspaper"),
        "Priority": "high" if (dv or item["fuente"] == "CMF") else "default"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        print("  aviso enviado:", titulo)
    except Exception as e:
        print("  aviso falló:", e)


def arreglar(t):
    """Corrige tildes rotas (texto UTF-8 leído como Latin-1)."""
    if "Ã" in t or "Â" in t:
        try:
            return t.encode("latin-1").decode("utf-8")
        except Exception:
            pass
    return t


def tickers_cartera():
    """Acciones que hoy están en la cartera (posiciones de MAESTRO en index.html)."""
    html = (RAIZ / "index.html").read_text(encoding="utf-8")
    bloque = html.split("posiciones: [", 1)[1].split("]", 1)[0]
    return re.findall(r"\{\s*ticker:\s*'([A-Z0-9.\-]+)'", bloque)


def util(it, emp):
    """Descarta avisos sin contenido (p. ej. la materia es solo el nombre de la empresa)."""
    t = re.sub(r"[\W_]+", " ", it["titulo"].lower()).strip()
    nom = re.sub(r"[\W_]+", " ", (emp.get("razon") or emp["nombre"]).lower()).strip()
    return len(t) > 8 and t not in (nom, nom.replace(" s a", "").strip(), "hecho esencial")


def clave(it):
    return it["ticker"] + "|" + it["fuente"] + "|" + re.sub(r"\W+", "", it["titulo"].lower())[:80]


def main():
    prev = json.loads(SALIDA.read_text(encoding="utf-8")) if SALIDA.exists() else {}
    vistos = {}
    for it in prev.get("items", []):
        it["titulo"] = arreglar(it["titulo"])
        it["id"] = clave(it)  # recalcula con el título corregido para no repetir avisos
        vistos[it["id"]] = it
    vigentes = tickers_cartera()
    print("Acciones vigentes:", ", ".join(vigentes))
    primera = not prev
    nuevos, errores = [], []

    for tk, emp in EMPRESAS.items():
        if tk not in vigentes:
            continue
        for fn in (hechos_cmf, noticias_google):
            try:
                lista = fn(tk, emp)
            except Exception as e:
                errores.append(f"{tk} {fn.__name__}: {e}")
                continue
            for it in lista:
                it["id"] = clave(it)
                if it["id"] in vistos:
                    continue
                if it["fuente"] == "CMF" and re.search(r"dividend", it["titulo"], re.I):
                    dv = leer_dividendo(texto_pdf(it["url"]), tk.split("-")[1] if "-" in tk else "")
                    if dv:
                        it["dividendo"] = dv
                vistos[it["id"]] = it
                nuevos.append(it)
            time.sleep(3)

    # Dividendos anunciados que se cargan a mano (respaldo si la CMF no responde)
    extra = RAIZ / "dividendos_anunciados.json"
    if extra.exists():
        for it in json.loads(extra.read_text(encoding="utf-8")):
            it.setdefault("importante", True)
            it["id"] = clave(it)
            if it["id"] not in vistos:
                vistos[it["id"]] = it
                nuevos.append(it)

    usdclp = prev.get("usdclp", 0)
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(
            "https://query1.finance.yahoo.com/v8/finance/chart/CLP=X?range=5d&interval=1d", headers=UA), timeout=20))
        usdclp = round(float(d["chart"]["result"][0]["meta"]["regularMarketPrice"]), 2)
    except Exception as e:
        errores.append(f"dólar: {e}")

    corte = (datetime.now(CL) - timedelta(days=120)).strftime("%Y-%m-%d")
    items = sorted((i for i in vistos.values()
                    if i["ticker"] in vigentes and (i["fecha"] >= corte or i.get("dividendo"))),
                   key=lambda i: (i["fecha"], i["fuente"] == "CMF"), reverse=True)[:300]
    SALIDA.write_text(json.dumps({
        "actualizado": datetime.now(CL).strftime("%Y-%m-%d %H:%M"), "usdclp": usdclp,
        "items": items, "errores": errores}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(nuevos)} nuevos, {len(items)} en total, {len(errores)} errores")
    for e in errores:
        print("  !", e)

    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if topic and not primera:
        enviados = set()
        for it in nuevos:
            emp = EMPRESAS.get(it["ticker"], {"nombre": ""})
            k = it["ticker"] + "|" + it["fecha"] + "|" + it["titulo"].lower()
            if it.get("importante") and it["ticker"] in vigentes and util(it, emp) and k not in enviados:
                enviados.add(k)
                avisar(it, topic)
    elif topic and primera:
        print("Primera corrida: se guardan sin avisar para no llenar el celular.")


if __name__ == "__main__":
    main()
