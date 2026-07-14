#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Motor de descarga DGASAT (snia.mop.gob.cl/dgasat, sin captcha ni login).

El formulario acepta 3 estaciones por request y N variables por estacion. La
tabla de respuesta viene ancha (1 columna por estacion-variable, en orden de
seleccion) y sparse. Limites del sitio: 3 estaciones y ~4400 filas por consulta.
Este modulo expone `worker()` que resuelve un lote de <=3 estaciones con
reintentos, particion adaptativa por tope de filas y aislamiento de dias
corruptos (backend 500).
"""
import re
import time
from datetime import timedelta

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://snia.mop.gob.cl/dgasat/pages/dgasat_param/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")
FMT = "%d/%m/%Y"
CAP = 4300
DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}")


class BackendError(Exception):
    pass


def nueva_sesion():
    s = requests.Session()
    s.verify = False
    s.headers.update({"User-Agent": UA, "Accept": "text/html,*/*",
                      "Accept-Language": "es-CL,es;q=0.9"})
    s.get(BASE + "dgasat_param.jsp?param=1", timeout=30)
    return s


def _form(codes, desde, hasta):
    c = list(codes) + ["-1"] * (3 - len(codes))
    return {"estacion1": c[0], "estacion2": c[1], "estacion3": c[2],
            "accion": "refresca", "param": "1", "tipo": "ANO",
            "fechaFinGrafico": hasta.strftime(FMT), "hora_fin": "0",
            "tiporep": "I", "period": "rango",
            "fechaInicioTabla": desde.strftime(FMT),
            "fechaFinTabla": hasta.strftime(FMT),
            "UserID": "nobody", "EsDL1": "0", "EsDL2": "0", "EsDL3": "0"}


def consulta_multi(s, batch, desde, hasta):
    """batch: {codigo: {"vars": {var_origen: nomencl}, ...}} (max 3 codigos).
    Devuelve (lista de {codigo, variable, fecha_hora, valor}, sel usada)."""
    codes = list(batch.keys())
    base = _form(codes, desde, hasta)
    r1 = s.post(BASE + "dgasat_param_1.jsp", data=base, timeout=40)
    if r1.status_code in (403, 429, 502, 503, 504):
        raise requests.RequestException(f"HTTP {r1.status_code} (param_1)")
    disponibles = re.findall(r'name="parametros"\s+[^>]*value="([^"]+)"', r1.text)
    sel = []          # (value, codigo, nomenclatura) -> orden de columnas
    for v in disponibles:
        m = re.match(r"([\dKkx\-]+)_(\d+)_(.+)$", v)
        if not m:
            continue
        code, _, nombre = m.group(1), m.group(2), m.group(3).strip()
        if code in batch and nombre in batch[code]["vars"]:
            sel.append((v, code, batch[code]["vars"][nombre]))
    if not sel:
        return [], []
    data = list(base.items()) + [("parametros", v) for v, _, _ in sel]
    r2 = s.post(BASE + "dgasat_param_tablas.jsp", data=data, timeout=180)
    rx = s.get(BASE + "dgasat_param_tablas_instantaneos.jsp?pag=0",
               timeout=300, headers={"Referer": r2.url})
    if rx.status_code in (403, 429, 502, 503, 504):
        raise requests.RequestException(f"HTTP {rx.status_code} (tablas)")
    if rx.status_code == 500 or "<table" not in rx.text.lower():
        raise BackendError(f"backend 500/sin tabla {desde:%d/%m}..{hasta:%d/%m}")
    regs = []
    nfilas = 0
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", rx.text, re.S | re.I):
        cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S | re.I)]
        for i, c in enumerate(cells):
            if DATE_RE.match(c):
                nfilas += 1
                fh = re.sub(r"\s+", " ", c)
                for k, (_, code, nom) in enumerate(sel):
                    val = cells[i + 1 + k] if i + 1 + k < len(cells) else ""
                    if val:
                        regs.append({"codigo": code, "variable": nom,
                                     "fecha_hora": fh, "valor": val})
                break
    if nfilas >= CAP and (hasta - desde) > timedelta(days=1):
        mid = desde + (hasta - desde) / 2
        a, _ = consulta_multi(s, batch, desde, mid)
        b, _ = consulta_multi(s, batch, mid + timedelta(days=1), hasta)
        return a + b, sel
    return regs, sel


def worker(batch, desde, hasta, reintentos=4):
    s = None
    err = ""
    for i in range(reintentos):
        try:
            if s is None:
                s = nueva_sesion()      # crear sesion DENTRO del try: si el GET
            regs, _ = consulta_multi(s, batch, desde, hasta)  # inicial hace
            return regs, None           # timeout, reintenta en vez de crashear
        except BackendError as e:
            # backend 500 determinista: una estacion del lote lo gatilla.
            # Aislar consultando de a 1 para rescatar las sanas.
            if len(batch) > 1:
                regs, malas = [], []
                for c in batch:
                    r1, e1 = worker({c: batch[c]}, desde, hasta, reintentos=2)
                    regs.extend(r1)
                    if e1:
                        malas.append(c)
                return regs, (f"backend en {','.join(malas)}" if malas else None)
            return [], str(e)
        except requests.RequestException as e:
            time.sleep(3 * (i + 1))
            s = None                    # recrear sesion en el proximo intento
            err = str(e)
    return [], f"red: {err}"
