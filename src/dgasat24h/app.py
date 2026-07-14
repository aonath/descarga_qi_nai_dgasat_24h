#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Descarga DGASAT: ultimas 24 h, SOLO estaciones fluviometricas, SOLO Q.i y NA.i.
Dashboard EN VIVO del proceso de descarga + xlsx de riesgo de inundacion.

Flujo por defecto (comando `download_dgasat_24h`):
  1. levanta un servidor HTTP local sobre la carpeta de salida,
  2. abre el dashboard en el navegador (estado "descargando"),
  3. descarga en paralelo escribiendo progress.json tras cada lote,
  4. el dashboard hace polling y muestra barra + log en vivo,
  5. al terminar escribe data.json (el dashboard pasa a mostrar estaciones),
     el CSV consolidado y el xlsx de riesgo de inundacion.
El servidor queda vivo hasta Ctrl+C.

Datos de referencia (catalogo + umbrales) viajan dentro del paquete.
"""
import argparse
import csv
import json
import math
import threading
import time
import unicodedata
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from itertools import groupby
from pathlib import Path

import openpyxl

from .engine import worker

VARS = ("Q.i", "NA.i")            # caudal instantaneo, nivel de agua instantaneo

# ---- datos de referencia empaquetados ----
_PKG = Path(str(files("dgasat24h")))
CAT = _PKG / "data" / "catalogo_v0.4.xlsx"
UMB = _PKG / "data" / "umbrales_dga_R6_R7_R8_R16.xlsx"
DASH_TPL = _PKG / "dashboard.html"


def _norm(s):
    s = "" if s is None else str(s)
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn").lower()


def leer_fluvio(xlsx=CAT):
    """{codigo: {"estacion", "region", "vars": {var_origen: nomencl}}} solo para
    estaciones fluviometricas con var Q.i o NA.i y con registro de datos."""
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    ws = wb["dgasat_descargar"]
    rows = list(ws.iter_rows(values_only=True))
    hdr = {h: i for i, h in enumerate(rows[0])}
    tipo_cols = ["Tipo de estación 1", "Tipo de estación 2",
                 "Tipo de estación 3", "Tipo de estación 4"]
    sel = {}
    for r in rows[1:]:
        cod = r[hdr["Código de estación en origen"]]
        if not cod:
            continue
        if r[hdr["Nomenclatura de variable"]] not in VARS:
            continue
        if r[hdr["Tiene registro de datos?"]] != "si":
            continue
        tipos = _norm(" ".join(str(r[hdr[c]]) for c in tipo_cols if r[hdr[c]]))
        if "fluviometrica" not in tipos:
            continue
        cod = str(cod).strip()
        e = sel.setdefault(cod, {
            "estacion": r[hdr["Nombre de estación"]],
            "region": r[hdr["Región"]], "vars": {}})
        e["vars"][(r[hdr["Nombre de variable en origen"]] or "").strip()] = \
            r[hdr["Nomenclatura de variable"]]
    return sel


# ------------------------------------------------------------ umbrales / coords
def _coords_catalogo(xlsx=CAT):
    """{codigo: (nombre, este_h19, norte_h19)} desde la hoja 'Estaciones'."""
    import pandas as pd
    est = pd.read_excel(xlsx, sheet_name="Estaciones").drop_duplicates(
        "Código de estación")
    out = {}
    for _, r in est.iterrows():
        cod = str(r["Código de estación"]).strip()
        out[cod] = (r.get("Nombre de estación"),
                    r.get("Este (WGS84H19)"), r.get("Norte (WGS84H19)"))
    return out


def _leer_umbrales(path=UMB):
    """{codigo: (Rojo, Amarillo, Azul)} en m3/s."""
    if not Path(path).exists():
        return {}
    import pandas as pd
    um = pd.read_excel(path)
    return {str(r["Código"]).strip(): (r["Umbral Rojo (m³/s)"],
            r["Umbral Amarillo (m³/s)"], r["Umbral Azul (m³/s)"])
            for _, r in um.iterrows()}


def _ok(x):
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def clasifica(cod, qv, umb):
    if cod not in umb:
        return "Sin umbral"
    if qv is None:
        return "Sin dato"
    R, A, Z = umb[cod]
    if _ok(R) and qv >= R:
        return "Rojo"
    if _ok(A) and qv >= A:
        return "Amarillo"
    if _ok(Z) and qv >= Z:
        return "Azul"
    return "Sin riesgo"


# ------------------------------------------------------------ progreso en vivo
class Progreso:
    def __init__(self, out, meta):
        self.fp = out / "progress.json"
        self.lock = threading.Lock()
        self.t0 = time.time()
        self.state = dict(meta)
        self.state.update({"status": "descargando", "done": 0, "registros": 0,
                           "errores": 0, "elapsed": 0, "log": []})
        self._flush()

    def _flush(self):
        self.state["elapsed"] = round(time.time() - self.t0, 1)
        tmp = self.fp.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.fp)

    def lote(self, tag, n, err):
        with self.lock:
            self.state["done"] += 1
            self.state["registros"] += n
            if err:
                self.state["errores"] += 1
            self.state["log"].append({"tag": tag, "n": n, "err": err,
                                      "t": datetime.now().strftime("%H:%M:%S")})
            self._flush()

    def finalizar(self, registros, series, errores):
        with self.lock:
            self.state.update({"status": "listo", "registros": registros,
                               "series": series, "errores": errores})
            self._flush()


# ------------------------------------------------------------ descarga
def descargar(sel, desde, hasta, workers, prog):
    codes = sorted(sel)
    batches = [{c: sel[c] for c in codes[i:i + 3]}
               for i in range(0, len(codes), 3)]
    print(f"{len(sel)} estaciones fluviometricas, vars {list(VARS)}")
    print(f"{desde:%d/%m/%Y %H:%M} -> {hasta:%d/%m/%Y %H:%M}")
    print(f"{len(batches)} requests (3 est c/u), {workers} en paralelo\n")
    todos, errores = [], []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(worker, b, desde, hasta): b for b in batches}
        for f in as_completed(futs):
            b = futs[f]
            regs, err = f.result()
            tag = ",".join(b.keys())
            if err:
                errores.append((tag, err))
                print(f"  [!] {tag}: {err}")
            else:
                print(f"  {tag}: {len(regs)} registros")
            todos.extend(regs)
            if prog:
                prog.lote(tag, len(regs), err)
    todos = [r for r in todos
             if desde <= datetime.strptime(r["fecha_hora"], "%d/%m/%Y %H:%M") <= hasta]
    todos.sort(key=lambda r: (r["codigo"], r["variable"],
               datetime.strptime(r["fecha_hora"], "%d/%m/%Y %H:%M")))
    return todos, errores


def _to_float(v):
    try:
        return float(str(v).replace(",", "."))
    except (ValueError, AttributeError):
        return None


def escribir_csv(todos, sel, out, ts):
    nombres = {c: v["estacion"] for c, v in sel.items()}
    cons = out / f"consolidado_24h_{ts}.csv"
    with open(cons, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["codigo", "estacion", "variable", "fecha_hora", "valor"])
        for r in todos:
            w.writerow([r["codigo"], nombres[r["codigo"]], r["variable"],
                        r["fecha_hora"], r["valor"]])
    return cons


# ------------------------------------------------------------ payload dashboard
def construir_payload(todos, sel, desde, hasta, umb):
    nombres = {c: v["estacion"] for c, v in sel.items()}
    regiones = {c: v["region"] for c, v in sel.items()}
    est = {}
    key = lambda r: (r["codigo"], r["variable"])
    for (cod, var), grp in groupby(todos, key=key):
        serie = [{"t": r["fecha_hora"], "v": _to_float(r["valor"])} for r in grp]
        serie = [p for p in serie if p["v"] is not None]
        if not serie:
            continue
        e = est.setdefault(cod, {"codigo": cod, "estacion": nombres[cod],
                                 "region": regiones[cod], "series": {}})
        e["series"][var] = serie
    estaciones = []
    for cod, e in est.items():
        row = {"codigo": cod, "estacion": e["estacion"], "region": e["region"],
               "series": e["series"]}
        for var in VARS:
            s = e["series"].get(var)
            row[f"ultimo_{var}"] = s[-1] if s else None
        uq = row["ultimo_Q.i"]
        row["riesgo"] = clasifica(cod, uq["v"] if uq else None, umb)
        estaciones.append(row)
    estaciones.sort(key=lambda x: (x["region"] or 0, x["estacion"] or ""))
    return {"generado": datetime.now().strftime("%d/%m/%Y %H:%M"),
            "desde": desde.strftime("%d/%m/%Y %H:%M"),
            "hasta": hasta.strftime("%d/%m/%Y %H:%M"), "vars": list(VARS),
            "n_estaciones": len(estaciones), "estaciones": estaciones}


def escribir_dashboard(out, payload=None):
    tpl = DASH_TPL.read_text(encoding="utf-8")
    emb = json.dumps(payload, ensure_ascii=False) if payload else "null"
    fp = out / "dashboard.html"
    fp.write_text(tpl.replace("__EMBED__", emb), encoding="utf-8")
    return fp


# ------------------------------------------------------------ riesgo xlsx
def escribir_riesgo(todos, sel, hasta, out_base, umb):
    """Snapshot: por cada lectura de Q.i en la ventana, NA.i simultaneo + RIESGO.
    Columnas: NOMBRE ESTACION | CODIGO ESTACION | ESTE_H19S | NORTE_H19S |
              FECHA-HORA | Q.i | NA.i | RIESGO
    -> <out_base>/RIESGO_INUNDACION/qi_nai_24hrs_<YYYYMMDDHH>.xlsx"""
    import pandas as pd
    coords = _coords_catalogo()
    nombres = {c: v["estacion"] for c, v in sel.items()}
    piv = {}
    for r in todos:
        piv.setdefault((r["codigo"], r["fecha_hora"]), {})[r["variable"]] = \
            _to_float(r["valor"])
    rows = []
    for (cod, fh), vals in piv.items():
        q = vals.get("Q.i")
        if q is None or q < 0:        # ancla en Q.i valido (Q<0 = sensor en falla)
            continue
        na = vals.get("NA.i")
        nom, este, norte = coords.get(cod, (None, None, None))
        rows.append({
            "NOMBRE ESTACION": nom or nombres.get(cod, ""),
            "CODIGO ESTACION": cod, "ESTE_H19S": este, "NORTE_H19S": norte,
            "FECHA-HORA": fh, "Q.i": round(q, 3),
            "NA.i": round(na, 3) if na is not None else None,
            "RIESGO": clasifica(cod, q, umb)})
    df = pd.DataFrame(rows, columns=[
        "NOMBRE ESTACION", "CODIGO ESTACION", "ESTE_H19S", "NORTE_H19S",
        "FECHA-HORA", "Q.i", "NA.i", "RIESGO"])
    if not df.empty:
        df["_o"] = pd.to_datetime(df["FECHA-HORA"], format="%d/%m/%Y %H:%M")
        df = df.sort_values(["CODIGO ESTACION", "_o"]).drop(columns="_o")
    rdir = out_base / "RIESGO_INUNDACION"
    rdir.mkdir(parents=True, exist_ok=True)
    fp = rdir / f"qi_nai_24hrs_{hasta:%Y%m%d%H}.xlsx"
    df.to_excel(fp, index=False)
    return fp, df


def _servir(out, port):
    handler = partial(SimpleHTTPRequestHandler, directory=str(out))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


# ------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="download_dgasat_24h",
        description="DGASAT fluviometricas Q.i+NA.i ultimas 24h: dashboard en "
                    "vivo + xlsx de riesgo de inundacion.")
    ap.add_argument("--region", type=int, help="filtrar una sola region")
    ap.add_argument("--horas", type=int, default=24, help="ventana (default 24)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default=str(Path.cwd() / "salida_dgasat_24h"),
                    help="carpeta de salida (default ./salida_dgasat_24h)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-serve", action="store_true",
                    help="no abrir navegador (dashboard estatico embebido)")
    ap.add_argument("--no-riesgo", action="store_true",
                    help="no generar el xlsx de riesgo de inundacion")
    a = ap.parse_args(argv)

    sel = leer_fluvio()
    if a.region is not None:
        sel = {c: v for c, v in sel.items() if v["region"] == a.region}
    if not sel:
        print("Nada seleccionado.")
        return

    hasta = datetime.now()
    desde = hasta - timedelta(hours=a.horas)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ts = hasta.strftime("%Y%m%d_%H%M")
    umb = _leer_umbrales()
    n_batches = (len(sel) + 2) // 3
    meta = {"generado": hasta.strftime("%d/%m/%Y %H:%M"),
            "desde": desde.strftime("%d/%m/%Y %H:%M"),
            "hasta": hasta.strftime("%d/%m/%Y %H:%M"),
            "n_estaciones": len(sel), "total": n_batches}

    serve = not a.no_serve
    prog = None
    if serve:
        for f in ("progress.json", "data.json"):
            (out / f).unlink(missing_ok=True)
        escribir_dashboard(out)
        prog = Progreso(out, meta)
        _servir(out, a.port)
        url = f"http://127.0.0.1:{a.port}/dashboard.html"
        print(f"Dashboard en vivo: {url}\n")
        webbrowser.open(url)

    t0 = time.time()
    todos, errores = descargar(sel, desde, hasta, a.workers, prog)
    cons = escribir_csv(todos, sel, out, ts)
    payload = construir_payload(todos, sel, desde, hasta, umb)
    con_datos = len({(r["codigo"], r["variable"]) for r in todos})

    if serve:
        (out / "data.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        prog.finalizar(len(todos), con_datos, len(errores))
    else:
        escribir_dashboard(out, payload)

    print(f"\n{len(todos)} registros, {con_datos} series con datos, "
          f"{len(errores)} errores, {time.time()-t0:.0f}s")
    print(f"-> {cons}")
    print(f"-> {out / 'dashboard.html'}")

    if not a.no_riesgo:
        friesgo, dfr = escribir_riesgo(todos, sel, hasta, out, umb)
        vc = dfr["RIESGO"].value_counts().to_dict() if not dfr.empty else {}
        print(f"-> {friesgo}  ({len(dfr)} filas, {vc})")

    if errores:
        (out / f"errores_{ts}.txt").write_text(
            "\n".join(f"{t}: {e}" for t, e in errores), encoding="utf-8")

    if serve:
        print("\nServidor activo. Cierra esta ventana o pulsa Ctrl+C para salir.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nListo.")


if __name__ == "__main__":
    main()
