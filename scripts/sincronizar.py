#!/usr/bin/env python3
"""Aplica a la cartera (bloque MAESTRO de index.html) los PDF que Banchile manda por correo.

Uso:
    BANCHILE_PDF_CLAVE=xxxx python3 scripts/sincronizar.py --id <id_correo> archivo.pdf [--probar]

Reconoce tres tipos de PDF:
  * Factura (Documento Tributario Electrónico) de compra o venta de acciones: suma o resta
    acciones; en compras recalcula el PPC real (cargo total con comisión e IVA ÷ acciones);
    en ventas registra la ganancia realizada en "rotaciones".
  * Aviso de Pago de Dividendos: suma el monto a "divAcum" del papel.
  * Estado de Inversiones (cartola mensual): cuadra cantidades, precio costo del broker,
    precio de cierre y caja con lo que informa el banco (fuente de verdad).

Cada correo se aplica una sola vez (sync/procesados.json). Todo cambio queda anotado en
sync/registro.md. Al cambiar algo sube MAESTRO.version para que la app en el navegador
tome las cantidades nuevas sin borrar lo que el usuario escribió (criterio, beta, etc.).
La clave del PDF se lee de la variable BANCHILE_PDF_CLAVE; nunca se guarda en el repo.
Solo biblioteca estándar + pdftotext (poppler-utils).
"""
import argparse, json, os, re, subprocess, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
HTML = RAIZ / "index.html"
SYNC = RAIZ / "sync"
PROC = SYNC / "procesados.json"
REG = SYNC / "registro.md"
CL = timezone(timedelta(hours=-3))
MESES = {m: i + 1 for i, m in enumerate(
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split())}


# ---------------- utilidades ----------------
def num(s):
    """'32.497,000000' -> 32497.0 ; '$ -705.785' -> -705785.0"""
    s = str(s).replace("$", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def fmt(v, dec=4):
    v = round(float(v), dec)
    return str(int(v)) if v == int(v) else ("%.*f" % (dec, v)).rstrip("0").rstrip(".")


def pdf_a_texto(ruta):
    clave = os.environ.get("BANCHILE_PDF_CLAVE", "")
    cmd = ["pdftotext", "-layout"] + (["-upw", clave] if clave else []) + [str(ruta), "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("No pude abrir el PDF (¿clave BANCHILE_PDF_CLAVE correcta?): " + r.stderr.strip())
    return r.stdout


def fecha_dmy(s):
    m = re.search(r"(\d{2})[/-](\d{2})[/-](\d{4})", s)
    return "%s-%s-%s" % (m.group(3), m.group(2), m.group(1)) if m else ""


def fecha_larga(s):
    m = re.search(r"(\d{1,2})\s+de\s+([a-záéíóú]+)\s+(?:de\s+)?(\d{4})", s, re.I)
    if not m or m.group(2).lower() not in MESES:
        return ""
    return "%s-%02d-%02d" % (m.group(3), MESES[m.group(2).lower()], int(m.group(1)))


# ---------------- lectores de PDF ----------------
def leer_factura(t):
    """Devuelve lista de operaciones de acciones [{tipo, ticker, cantidad, monto, precio, fecha}]."""
    fecha = fecha_dmy(re.search(r"Fecha emisi[oó]n\s*:\s*(\S+)", t).group(1)) if re.search(r"Fecha emisi[oó]n", t) else ""
    m = re.search(r"Fecha de Adquisici[oó]n\s*=\s*(\d{2}/\d{2}/\d{4})", t)
    if m:
        fecha = fecha_dmy(m.group(1))
    obs = (re.search(r"Observaciones:\s*(.*)", t) or [None, ""])[1]
    ops = []
    for L in t.splitlines():
        # LTM   BC-CN   32.497,000000   21,590000   701.610
        mm = re.match(r"\s*([A-Z][A-Z0-9]+(?:-[A-Z0-9]+)?)\s{2,}(\S+)\s{2,}([\d.]+,\d+)\s+([\d.]+,\d+)\s+([\d.]+)", L)
        if not mm or mm.group(1) in ("COMISION", "DÓLAR", "DOLAR", "IVA"):
            continue
        ops.append({"ticker": mm.group(1), "cantidad": num(mm.group(3)), "precio": num(mm.group(4)),
                    "bruto": num(mm.group(5)), "obs": mm.group(2)})
    if not ops:
        return []
    venta = bool(re.search(r"Venta\s+De\s+Renta\s+Variable", obs, re.I)) or "VENTA" in ops[0]["obs"].upper()
    if venta:
        m = re.search(r"Valor a Recibir\s*\$?\s*([\d.]+)", t) or re.search(r"TOTAL DOCUMENTO\s*\$?\s*([\d.]+)", t)
    else:
        m = re.search(r"TOTAL DOCUMENTO\s*\$?\s*([\d.]+)", t)
    total = num(m.group(1)) if m else 0
    bruto_tot = sum(o["bruto"] for o in ops) or 1
    for o in ops:  # prorratea costos si hubiera varias líneas
        o["tipo"] = "venta" if venta else "compra"
        o["fecha"] = fecha
        o["monto"] = round(total * o["bruto"] / bruto_tot) if total else o["bruto"]
    return ops


def leer_dividendo(t):
    tk = re.search(r"[“\"]([A-Z0-9-]+)[”\"]", t)
    monto = re.search(r"Monto a Recibir:\s*\$?\s*([\d.,]+)", t)
    acc = re.search(r"Sus Acciones en Custodia:\s*([\d.,]+)", t)
    dpa = re.search(r"Dividendos por Acci[oó]n:\s*\$?\s*([\d.,]+)", t)
    pago = re.search(r"Fecha de Pago hasta el:\s*(.+)", t)
    if not (tk and monto):
        return None
    return {"tipo": "dividendo", "ticker": tk.group(1), "monto": num(monto.group(1)),
            "acciones": num(acc.group(1)) if acc else 0, "dpa": num(dpa.group(1)) if dpa else 0,
            "fecha": fecha_larga(pago.group(1)) if pago else ""}


def leer_cartola(t):
    m = re.search(r"Per[ií]odo del \d{2}/\d{2}/\d{4} al (\d{2}/\d{2}/\d{4})", t)
    corte = fecha_dmy(m.group(1)) if m else ""
    pos, en_acc = [], False
    for L in t.splitlines():
        if re.match(r"\s*Producto\s+Cantidad", L):
            en_acc = True
            continue
        if re.match(r"\s*Producto\s+N° Cuotas", L) or "Total posiciones" in L:
            en_acc = False
        if not en_acc:
            continue
        mm = re.match(r"\s*([A-Z][A-Z0-9]+(?:-[A-Z0-9]+)?)\s+([\d.]+,\d{4})\s+\$\s*([\d.,]+)\s+\$\s*([\d.,]+)\s+\$\s*([\d.,]+)", L)
        if mm:
            pos.append({"ticker": mm.group(1), "acciones": num(mm.group(2)), "pcb": num(mm.group(3)),
                        "precio": num(mm.group(5))})
    caja = re.search(r"Caja Internet\s+\$\s*[\d.,]+\s+\$\s*[\d.,]+\s+\$\s*([\d.,]+)", t)
    return {"tipo": "cartola", "corte": corte, "posiciones": pos, "caja": num(caja.group(1)) if caja else None}


def clasificar(t):
    if "ESTADO DE TUS INVERSIONES" in t and "DETALLE DE POSICIONES" in t:
        return leer_cartola(t)
    if re.search(r"Aviso de Pago de Dividendo|pago de dividendo de", t, re.I):
        return leer_dividendo(t)
    if "TOTAL DOCUMENTO" in t or "Valor a Recibir" in t or "BOLETA" in t.upper() or "FACTURA" in t.upper():
        return {"tipo": "factura", "ops": leer_factura(t)}
    return None


# ---------------- edición del bloque MAESTRO ----------------
class Maestro:
    def __init__(self, html):
        self.html = html
        i = html.index("var MAESTRO = {")
        self.ini = html.index("posiciones: [", i)
        self.fin = html.index("\n  ],", self.ini)

    def lineas(self):
        return self.html[self.ini:self.fin].split("\n")[1:]

    def _set_bloque(self, lineas):
        lineas = [l.rstrip().rstrip(",") for l in lineas if l.strip()]
        nuevo = "posiciones: [\n" + ",\n".join(lineas)
        self.html = self.html[:self.ini] + nuevo + self.html[self.fin:]
        self.fin = self.ini + len(nuevo)

    def buscar(self, tk):
        for l in self.lineas():
            if re.search(r"ticker:\s*'%s'" % re.escape(tk), l):
                return l
        return None

    @staticmethod
    def get(l, campo):
        m = re.search(r"\b%s:\s*(-?[\d.]+)" % campo, l)
        return float(m.group(1)) if m else 0.0

    @staticmethod
    def set(l, campo, v):
        return re.sub(r"(\b%s:\s*)(-?[\d.]+)" % campo, lambda m: m.group(1) + fmt(v), l, count=1)

    def reemplazar(self, tk, nueva):
        ls = self.lineas()
        out = []
        for l in ls:
            if re.search(r"ticker:\s*'%s'" % re.escape(tk), l):
                if nueva:
                    out.append(nueva)
            else:
                out.append(l)
        self._set_bloque(out)

    def agregar(self, linea):
        self._set_bloque(self.lineas() + [linea])

    def agregar_rotacion(self, tk, monto, fecha):
        i = self.html.index("rotaciones: [", self.html.index("var MAESTRO = {"))
        j = self.html.index("\n  ]", i)
        cuerpo = self.html[i + len("rotaciones: ["):j].rstrip()
        item = "    { ticker: '%s', monto: %s, fecha: '%s' }" % (tk, fmt(monto, 0), fecha)
        if "'%s'" % tk in cuerpo and "'%s'" % fecha in cuerpo:
            return
        cuerpo = (cuerpo.rstrip(",") + ",\n" if cuerpo.strip() else "\n") + item
        self.html = self.html[:i] + "rotaciones: [" + cuerpo + self.html[j:]

    def set_cfg(self, campo, valor, texto=False):
        i = self.html.index("cfg: {", self.html.index("var MAESTRO = {"))
        j = self.html.index("}", i)
        bloque = self.html[i:j]
        pat = r"(\b%s:\s*)('[^']*'|-?[\d.]+)" % campo
        nuevo = re.sub(pat, lambda m: m.group(1) + ("'%s'" % valor if texto else fmt(valor)), bloque, count=1)
        self.html = self.html[:i] + nuevo + self.html[j:]

    def get_cfg(self, campo):
        i = self.html.index("cfg: {", self.html.index("var MAESTRO = {"))
        m = re.search(r"\b%s:\s*'([^']*)'" % campo, self.html[i:i + 2000])
        return m.group(1) if m else ""

    def set_version(self, v):
        self.html = re.sub(r"(var MAESTRO = \{\s*version:\s*)'[^']*'", lambda m: m.group(1) + "'%s'" % v, self.html, count=1)


def linea_nueva(tk, acciones, ppc, pcb, precio, fecha):
    return ("    { ticker: '%s', nombre: '%s', acciones: %s, ppc: %s, pcb: %s, precio: %s, beta: 1, "
            "tipo: 'En revisión', desde: '%s', est: false, dy: 0, dyTipo: 'extraordinario', divAcum: 0, "
            "min52: 0, criterio: '', gpPer: 0 }") % (tk, tk, fmt(acciones), fmt(ppc), fmt(pcb), fmt(precio), fecha)


# ---------------- aplicar ----------------
def aplicar(M, dato, notas):
    if dato["tipo"] == "factura":
        for o in dato["ops"]:
            l = M.buscar(o["ticker"])
            if o["tipo"] == "compra":
                if l:
                    a0, p0 = M.get(l, "acciones"), M.get(l, "ppc")
                    a1 = a0 + o["cantidad"]
                    ppc = (a0 * p0 + o["monto"]) / a1
                    l = M.set(M.set(l, "acciones", a1), "ppc", ppc)
                    M.reemplazar(o["ticker"], l)
                    notas.append("Compra %s: +%s acciones (ahora %s), cargo %s, PPC real %s." % (
                        o["ticker"], fmt(o["cantidad"]), fmt(a1), fmt(o["monto"], 0), fmt(ppc)))
                else:
                    ppc = o["monto"] / o["cantidad"]
                    M.agregar(linea_nueva(o["ticker"], o["cantidad"], ppc, o["precio"], o["precio"], o["fecha"]))
                    notas.append("Compra %s (papel nuevo): %s acciones, cargo %s, PPC real %s." % (
                        o["ticker"], fmt(o["cantidad"]), fmt(o["monto"], 0), fmt(ppc)))
            else:
                if not l:
                    notas.append("AVISO venta de %s que no estaba en la cartera; la cartola mensual lo cuadrará." % o["ticker"])
                    continue
                a0, p0 = M.get(l, "acciones"), M.get(l, "ppc")
                a1 = max(0.0, a0 - o["cantidad"])
                gan = o["monto"] - o["cantidad"] * p0
                M.agregar_rotacion(o["ticker"], gan, o["fecha"])
                M.reemplazar(o["ticker"], M.set(l, "acciones", a1) if a1 > 0.5 else None)
                notas.append("Venta %s: -%s acciones (quedan %s), recibido %s, ganancia realizada %s." % (
                    o["ticker"], fmt(o["cantidad"]), fmt(a1), fmt(o["monto"], 0), fmt(gan, 0)))
        if not dato["ops"]:
            notas.append("Factura sin acciones (p. ej. dólares o fondos): no cambia la cartera.")
    elif dato["tipo"] == "dividendo":
        l = M.buscar(dato["ticker"])
        if l:
            M.reemplazar(dato["ticker"], M.set(l, "divAcum", M.get(l, "divAcum") + dato["monto"]))
            notas.append("Dividendo %s: %s (%s acciones × $%s)." % (
                dato["ticker"], fmt(dato["monto"], 0), fmt(dato["acciones"]), fmt(dato["dpa"])))
        else:
            notas.append("Dividendo %s por %s: papel fuera de la cartera, no se suma." % (dato["ticker"], fmt(dato["monto"], 0)))
    elif dato["tipo"] == "cartola" and dato["corte"] and dato["corte"] < M.get_cfg("hoy"):
        # Cartola con corte anterior a operaciones ya aplicadas: no pisa nada, solo avisa diferencias.
        banco = {q["ticker"]: q["acciones"] for q in dato["posiciones"]}
        app = {re.search(r"ticker:\s*'([^']+)'", l).group(1): M.get(l, "acciones") for l in M.lineas()}
        dif = ["%s banco %s / app %s" % (tk, fmt(banco.get(tk, 0)), fmt(app.get(tk, 0)))
               for tk in sorted(set(banco) | set(app)) if abs(banco.get(tk, 0) - app.get(tk, 0)) > 0.5]
        notas.append("Cartola al %s es anterior a la cartera (al %s): no se aplica. %s" % (
            dato["corte"], M.get_cfg("hoy"),
            ("Diferencias (pueden ser operaciones posteriores al corte): " + "; ".join(dif) + ".") if dif else "Cantidades coinciden."))
    elif dato["tipo"] == "cartola":
        vistos = set()
        for q in dato["posiciones"]:
            vistos.add(q["ticker"])
            l = M.buscar(q["ticker"])
            if not l:
                M.agregar(linea_nueva(q["ticker"], q["acciones"], q["pcb"], q["pcb"], q["precio"], dato["corte"]))
                notas.append("Cartola: %s aparece con %s acciones y no estaba; agregado (PPC = precio costo del broker, revisar)." % (
                    q["ticker"], fmt(q["acciones"])))
                continue
            if abs(M.get(l, "acciones") - q["acciones"]) > 0.5:
                notas.append("Cartola corrige %s: %s → %s acciones." % (q["ticker"], fmt(M.get(l, "acciones")), fmt(q["acciones"])))
                l = M.set(l, "acciones", q["acciones"])
            l = M.set(M.set(l, "pcb", q["pcb"]), "precio", q["precio"])
            M.reemplazar(q["ticker"], l)
        for l in M.lineas():
            tk = re.search(r"ticker:\s*'([^']+)'", l).group(1)
            if tk not in vistos and dato["posiciones"]:
                notas.append("Cartola: %s ya no aparece en el banco; se quita de la cartera." % tk)
                M.reemplazar(tk, None)
        if dato["caja"] is not None:
            M.set_cfg("caja", dato["caja"])
        notas.append("Cartola al %s cuadrada: %d papeles." % (dato["corte"], len(dato["posiciones"])))
    fecha = dato.get("corte") or dato.get("fecha") or (dato.get("ops") or [{}])[0].get("fecha", "")
    return fecha


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--id", required=True, help="id del correo de Gmail (evita aplicarlo dos veces)")
    ap.add_argument("--asunto", default="")
    ap.add_argument("--probar", action="store_true", help="muestra qué haría sin guardar")
    a = ap.parse_args()

    SYNC.mkdir(exist_ok=True)
    proc = json.loads(PROC.read_text(encoding="utf-8")) if PROC.exists() else {"procesados": []}
    if a.id in proc["procesados"]:
        print("Ya aplicado antes: %s" % a.id)
        return

    dato = clasificar(pdf_a_texto(a.pdf))
    if not dato:
        print("PDF no reconocido; no cambia nada.")
        notas = ["PDF no reconocido (%s)." % (a.asunto or Path(a.pdf).name)]
        fecha, cambio = "", False
        M = None
    else:
        M = Maestro(HTML.read_text(encoding="utf-8"))
        antes = M.html
        notas = []
        fecha = aplicar(M, dato, notas)
        cambio = M.html != antes
        if cambio:
            ahora = datetime.now(CL)
            M.set_version(ahora.strftime("%Y-%m-%dT%H:%M"))
            if fecha and fecha > M.get_cfg("hoy"):
                M.set_cfg("hoy", fecha, texto=True)
    for n in notas:
        print(n)
    if a.probar:
        return
    if cambio:
        HTML.write_text(M.html, encoding="utf-8")
    proc["procesados"].append(a.id)
    PROC.write_text(json.dumps(proc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    nuevo = not REG.exists()
    with REG.open("a", encoding="utf-8") as f:
        if nuevo:
            f.write("# Registro de sincronización con Banchile\n\n")
        f.write("- %s · %s: %s\n" % (datetime.now(CL).strftime("%Y-%m-%d %H:%M"), a.asunto or Path(a.pdf).name, " ".join(notas)))


if __name__ == "__main__":
    main()
