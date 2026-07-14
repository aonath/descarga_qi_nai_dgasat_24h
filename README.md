# download_dgasat_24h

Descarga las **últimas 24 horas** de **caudal (Q.i)** y **nivel de agua (NA.i)**
de las **estaciones fluviométricas** de la DGA (fuente
[DGASAT](https://snia.mop.gob.cl/dgasat/), sin captcha ni login), abre un
**dashboard en vivo** que muestra el avance de la descarga y genera un
**reporte de riesgo de inundación** en Excel.

Al terminar, corre en un solo comando:

```
download_dgasat_24h
```

y se abre el navegador con el dashboard mientras descarga.

---

## Qué produce

Todo se guarda en la carpeta `salida_dgasat_24h/` (dentro de donde ejecutes el
comando):

| Archivo | Contenido |
|---|---|
| `dashboard.html` | Panel web: avance de descarga en vivo + una tarjeta por estación con último Q.i / NA.i, sparklines y semáforo de riesgo. |
| `consolidado_24h_<fecha>.csv` | Todos los datos crudos: `codigo, estacion, variable, fecha_hora, valor`. |
| `RIESGO_INUNDACION/qi_nai_24hrs_<fecha>.xlsx` | Una fila por lectura de Q.i en la ventana, con NA.i simultáneo, coordenadas (ESTE/NORTE H19S) y clasificación de **RIESGO**. Columnas: `NOMBRE ESTACION · CODIGO ESTACION · ESTE_H19S · NORTE_H19S · FECHA-HORA · Q.i · NA.i · RIESGO`. |

**Clasificación de riesgo** (según `umbrales_dga_R6_R7_R8_R16.xlsx`, incluido):
Q.i ≥ umbral Rojo → **Rojo**, ≥ Amarillo → **Amarillo**, ≥ Azul → **Azul**,
por debajo → **Sin riesgo**, estación sin umbral definido → **Sin umbral**.

---

## Requisitos

- **Python 3.9 o superior**. Verifica con `python --version`.
  Si no lo tienes, descárgalo de [python.org](https://www.python.org/downloads/)
  y en el instalador marca **"Add Python to PATH"**.
- Conexión a internet (los datos se bajan del sitio de la DGA).

Windows, macOS y Linux funcionan igual.

---

## Instalación (una sola vez)

En una terminal, dentro de la carpeta del proyecto:

```
pip install .
```

Eso instala el comando `download_dgasat_24h` en tu sistema. El catálogo de
estaciones y los umbrales de riesgo vienen incluidos en el paquete: no hay que
descargar ni configurar nada más.

> ¿No tienes el proyecto todavía? Mira **"Cómo lo usa la gente"** más abajo para
> clonarlo desde GitHub.

---

## Uso

```
download_dgasat_24h
```

- Levanta un servidor local, abre el dashboard en el navegador y empieza a
  descargar. Verás la barra de progreso, el contador de registros y el log lote
  a lote. Cuando termina, el dashboard cambia a la vista de estaciones.
- La terminal queda con el servidor activo. **Ciérrala o pulsa `Ctrl+C`** cuando
  termines de mirar el dashboard.

En **Windows** también puedes hacer **doble clic en `run.bat`**.

### Opciones

| Opción | Para qué |
|---|---|
| `--region 8` | Descargar solo una región (6, 7, 8 o 16). Más rápido para probar. |
| `--horas 12` | Cambiar la ventana (default 24 h). |
| `--workers 8` | Más descargas en paralelo (default 6). |
| `--out C:\ruta\carpeta` | Elegir dónde guardar la salida. |
| `--no-serve` | No abrir navegador; deja un `dashboard.html` autónomo. |
| `--no-riesgo` | No generar el Excel de riesgo. |
| `--port 8899` | Cambiar el puerto del servidor local. |
| `--full` | Ignorar la caché y descargar la ventana completa. |

**¿Más rápido con menos horas?** Sí. `--horas 12` o `--horas 6` bajan menos
datos (suelen caer en un solo día) y terminan antes. `--horas 24` es lo
completo. Si el sitio de la DGA está lento, una estación puede demorar un lote;
usa **Pausar/Cancelar** (abajo).

Ejemplos:

```
download_dgasat_24h --region 8
download_dgasat_24h --horas 48 --workers 8
download_dgasat_24h --out "D:\reportes\dga"
```

Toma unos **3–4 minutos** para todo Chile (~216 estaciones); segundos por región.

### Detener, pausar o reanudar

**Desde el dashboard** (mientras descarga): botones **⏸ Pausar**, **▶ Reanudar**
y **■ Cancelar** en el panel de progreso. Cancelar guarda lo bajado hasta ese
momento.

**Desde la terminal** — en **otra** ventana, apuntando a la misma carpeta de
salida:

```
download_dgasat_24h --pause     # pausa la descarga en curso
download_dgasat_24h --resume    # la reanuda
download_dgasat_24h --stop      # la cancela (guarda lo parcial)
```

**Atajo directo:** en la ventana de la descarga, **Ctrl+C** cancela y guarda lo
descargado. Si no responde (sitio DGA colgado), cierra la ventana.

### Descarga incremental (caché)

Cada corrida guarda lo bajado en `salida_dgasat_24h/cache_qi_nai.csv`. La
siguiente corrida **revisa la caché y descarga solo las horas que faltan** para
completar la ventana; el resto lo reusa. Así, correr seguido es rápido. Usa
`--full` para forzar la descarga completa e ignorar la caché.

---

## Cómo lo usa la gente (paso a paso con GitHub)

GitHub es solo un lugar en internet donde vive el código. "Clonar" = descargar
una copia a tu computador. No necesitas saber programar.

1. **Instala Python** (ver *Requisitos* arriba).
2. **Descarga el proyecto**. Dos formas:
   - **Sin instalar nada extra:** en la página del proyecto en GitHub, botón
     verde **`Code`** → **`Download ZIP`**. Descomprime el ZIP.
   - **Con Git** (si lo tienes): abre una terminal y ejecuta
     `git clone <URL-del-proyecto>`.
3. **Abre una terminal en esa carpeta.**
   - Windows: abre la carpeta en el Explorador, escribe `cmd` en la barra de
     dirección y Enter.
   - macOS: clic derecho en la carpeta → "Nuevo terminal en la carpeta".
4. **Instala:** `pip install .`
5. **Ejecuta:** `download_dgasat_24h`

Listo: se abre el dashboard y la carpeta `salida_dgasat_24h/` con los archivos.

---

## Notas

- Datos **provisorios en tiempo real** (advertencia de la propia DGA); pueden
  corregirse después.
- Los timestamps son irregulares (~cada 30 min según el sensor).
- Lecturas de caudal negativas (sensor en falla) se descartan del reporte.
- El catálogo cubre estaciones fluviométricas de las regiones 6, 7, 8 y 16.

## Licencia

MIT — ver [LICENSE](LICENSE). Los datos son de la Dirección General de Aguas (DGA), Chile.
