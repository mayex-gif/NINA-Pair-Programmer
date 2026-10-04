"""
Stack traces: detectar qué archivo del proyecto falla y en qué línea. Funciones puras (testeables).
Antes esta lógica vivía dentro del comando `fix` del CLI.
"""
import re
from pathlib import Path
from typing import Optional

from .proyecto import Proyecto

# FASE 0.8: Se agregó (?:\?[^:]*)? antes de los dos puntos para ignorar queries como ?t=1234 de Vite
PATRON_TRAZA = re.compile(r"([\w@\-./\\:]*?[\w\-]+\.(?:java|jsx?|tsx?|mjs))(?:\?[^:]*)?:(\d+)(?::\d+)?")

PATRON_TRAZA_PY = re.compile(r'File "([^"]+\.py)", line (\d+)')


def resolver_en_proyecto(ruta_traza: str, mapa: dict, proyecto: Optional[Proyecto] = None) -> Optional[str]:
    norm = ruta_traza.replace("\\", "/")
    if "node_modules/" in norm:
        return None
    nombre = norm.split("/")[-1]
    candidatos = [k for k in mapa if k == nombre or k.endswith("/" + nombre)]
    if not candidatos and proyecto is not None:
        p = proyecto.abs(norm)
        if p.is_file():
            return proyecto.clave(p)
    if not candidatos:
        return None

    def sufijo_comun(k: str) -> int:
        a, b = k.split("/")[::-1], norm.split("/")[::-1]
        n = 0
        for x, y in zip(a, b):
            if x != y:
                break
            n += 1
        return n

    candidatos.sort(key=sufijo_comun, reverse=True)
    return candidatos[0]


def extraer_frames(traza: str) -> list:
    """[(ruta, línea), ...] en orden de prioridad. Python lista el frame culpable ÚLTIMO (al revés que Java/JS)."""
    frames = PATRON_TRAZA.findall(traza) + list(reversed(PATRON_TRAZA_PY.findall(traza)))
    return [(ruta, int(num)) for ruta, num in frames]


def analizar_traza(traza: str, mapa: dict, proyecto: Optional[Proyecto] = None):
    """Devuelve (archivo_objetivo | None, línea | None, [otros archivos del proyecto en la traza])."""
    objetivo, linea, otros = None, None, []
    for ruta_t, num in extraer_frames(traza):
        resuelto = resolver_en_proyecto(ruta_t, mapa, proyecto)
        if not resuelto:
            continue
        if objetivo is None:
            objetivo, linea = resuelto, num
        elif resuelto != objetivo and resuelto not in otros:
            otros.append(resuelto)
    return objetivo, linea, otros


def fragmento_alrededor(texto: str, linea: int, antes: int = 5, despues: int = 5) -> str:
    """Líneas alrededor de `linea` (1-based), con su número delante."""
    lineas = texto.split("\n")
    ini, fin = max(0, linea - antes - 1), min(len(lineas), linea + despues)
    return "\n".join(f"{i + 1}: {lineas[i]}" for i in range(ini, fin))