"""
Interpretación de la respuesta del modelo: bloques SEARCH/REPLACE y su aplicación. Funciones puras, sin E/S.
"""
import difflib
import re
from typing import Optional


PATRON_BLOQUE = re.compile(
    r"<<<<<<< SEARCH[ \t]*\r?\n(.*?)\r?\n=======[ \t]*\r?\n(.*?)(?:\r?\n)?>>>>>>> REPLACE",
    re.DOTALL,
)


def _reemplazo_tolerante(texto: str, buscar: str, reemplazar: str) -> Optional[str]:
    """Reintento ignorando espacios al final de línea y líneas vacías en los bordes."""
    lineas = texto.split("\n")
    b = [l.rstrip() for l in buscar.strip("\n").split("\n")]
    if not any(b):
        return None
    n = len(b)
    hits = [i for i in range(len(lineas) - n + 1) if [l.rstrip() for l in lineas[i:i + n]] == b]
    if len(hits) != 1:
        return None
    i = hits[0]
    nuevas = reemplazar.strip("\n").split("\n") if reemplazar.strip("\n") else []
    return "\n".join(lineas[:i] + nuevas + lineas[i + n:])


def _lineas_parecidas(texto: str, buscar: str, max_lineas: int = 12) -> str:
    """Zona del archivo más parecida al SEARCH fallido (ayuda al modelo a corregirse)."""
    lineas = texto.split("\n")
    b = buscar.strip("\n").split("\n")
    n = min(len(b), len(lineas))
    if n == 0:
        return ""
    objetivo = "\n".join(l.strip() for l in b[:n])
    mejor, mejor_i = 0.0, -1
    for i in range(len(lineas) - n + 1):
        sm = difflib.SequenceMatcher(None, objetivo, "\n".join(l.strip() for l in lineas[i:i + n]))
        if sm.real_quick_ratio() > mejor and sm.quick_ratio() > mejor:
            r = sm.ratio()
            if r > mejor:
                mejor, mejor_i = r, i
    if mejor < 0.6:
        return ""
    return "\n".join(lineas[mejor_i:mejor_i + min(n, max_lineas)])


def aplicar_bloques(original: str, bloques: list):
    texto, errores = original, []
    for i, (buscar, reemplazar) in enumerate(bloques, 1):
        buscar, reemplazar = buscar.replace("\r\n", "\n"), reemplazar.replace("\r\n", "\n")
        n = texto.count(buscar)
        if n == 1:
            if reemplazar == "" and texto.count(buscar + "\n") == 1:
                texto = texto.replace(buscar + "\n", "", 1)  # borrado sin dejar línea vacía
            else:
                texto = texto.replace(buscar, reemplazar, 1)
        elif n > 1:
            errores.append(f"Bloque {i}: el fragmento SEARCH aparece {n} veces (ambiguo). Sumá más líneas de contexto para que sea único.")
        else:
            nuevo = _reemplazo_tolerante(texto, buscar, reemplazar)
            if nuevo is None:
                msg = f"Bloque {i}: el fragmento SEARCH no coincide con el archivo."
                pista = _lineas_parecidas(texto, buscar)
                if pista:
                    msg += f" Las líneas más parecidas del archivo real son:\n```\n{pista}\n```"
                errores.append(msg)
            else:
                texto = nuevo
    return texto, errores


PATRON_NUEVO = re.compile(
    r"<<<<<<< SEARCH[ \t]*\r?\n.*?=======[ \t]*\r?\n(.*?)(?:\r?\n)?>>>>>>> REPLACE", re.DOTALL
)


def interpretar_respuesta(respuesta: str, original: str):
    """Devuelve (texto_nuevo | None, errores, avisos). Todo o nada: ante un error no se aplica nada."""
    # 1. Archivo vacío: aceptamos el contenido completo
    if not original.strip():
        nuevos = PATRON_NUEVO.findall(respuesta)  # por si igual respondió con SEARCH vacío / REPLACE
        if nuevos:
            return "\n".join(nuevos).strip("\n") + "\n", [], ["Archivo creado desde cero."]
        m = re.search(r"```[\w+-]*\n(.*?)```", respuesta, re.DOTALL)
        if m:
            return m.group(1).rstrip("\n") + "\n", [], ["Archivo creado desde cero."]
        if respuesta.strip() and "<<<<<<<" not in respuesta:
            return respuesta.strip() + "\n", [], ["Archivo creado desde cero."]

    # 2. Bloques SEARCH/REPLACE para archivos existentes
    bloques = PATRON_BLOQUE.findall(respuesta)
    if bloques:
        nuevo, errores = aplicar_bloques(original, bloques)
        return (None if errores else nuevo), errores, []

    # 3. Fallback: el modelo devolvió el archivo completo dentro de un bloque ```
    m = re.search(r"```[\w+-]*\n(.*?)```", respuesta, re.DOTALL)
    if m:
        return m.group(1).rstrip("\n") + "\n", [], ["El modelo devolvió el archivo completo (no usó SEARCH/REPLACE). Revisá bien el diff."]

    return None, ["No pude interpretar la respuesta: no hay bloques SEARCH/REPLACE ni un bloque de código."], []
