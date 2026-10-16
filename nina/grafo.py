"""
Grafo de dependencias del proyecto a partir del mapa: resuelve cada import al archivo real (Fase 4.5) y calcula
la vecindad de un archivo (los que importa, los que lo importan, y así hasta N saltos).
Es el equivalente en Python de `resolverImports` de RepomapGraph.jsx; sin E/S ni UI, solo funciones puras.
"""
import re
from collections import deque
from typing import Dict, List, Optional, Tuple

EXT_JS = (".jsx", ".js", ".tsx", ".ts", ".mjs")
_RE_EXT = re.compile(r"\.(jsx?|tsx?|mjs|java|py)$")


def _quitar_ext(p: str) -> str:
    return _RE_EXT.sub("", p)


def _dirname(p: str) -> str:
    return "/".join(p.split("/")[:-1])


def _normalizar(ruta: str) -> str:
    """'a/b/../c/./d' -> 'a/c/d' (los '..' que se pasan de la raíz se descartan)."""
    salida: List[str] = []
    for seg in ruta.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if salida:
                salida.pop()
        else:
            salida.append(seg)
    return "/".join(salida)


def _indexar_sufijos(paths, transformar) -> Dict[str, List[str]]:
    """'a/b/C.java' -> 'a/b/C', 'b/C', 'C' (cada sufijo apunta a los paths que lo cumplen)."""
    idx: Dict[str, List[str]] = {}
    for p in paths:
        partes = transformar(p).split("/")
        for i in range(len(partes)):
            idx.setdefault("/".join(partes[i:]), []).append(p)
    return idx


def _elegir(candidatos: Optional[List[str]], desde: str) -> Optional[str]:
    """Ante varios candidatos: el que comparte más carpetas con el importador; si empatan, el más corto."""
    if not candidatos:
        return None
    if len(candidatos) == 1:
        return candidatos[0]
    a = desde.split("/")

    def comunes(p: str) -> int:
        n = 0
        for x, y in zip(a, p.split("/")):
            if x != y:
                break
            n += 1
        return n

    return sorted(candidatos, key=lambda p: (-comunes(p), len(p)))[0]


def resolver_imports(mapa: dict) -> List[Tuple[str, str]]:
    """Aristas (origen, destino) entre archivos del mapa. Los paquetes externos (react, java.util...) se ignoran."""
    paths = list(mapa)
    existentes = set(paths)
    idx_java = _indexar_sufijos([p for p in paths if p.endswith(".java")], _quitar_ext)
    idx_py = _indexar_sufijos([p for p in paths if p.endswith(".py")],
                              lambda p: re.sub(r"/__init__$", "", _quitar_ext(p)))
    idx_js = _indexar_sufijos([p for p in paths if re.search(r"\.(jsx?|tsx?|mjs)$", p)],
                              lambda p: re.sub(r"/index$", "", _quitar_ext(p)))

    def probar(base: str) -> Optional[str]:
        if base in existentes:
            return base
        for e in EXT_JS:
            if base + e in existentes:
                return base + e
        for e in EXT_JS:
            if f"{base}/index{e}" in existentes:
                return f"{base}/index{e}"
        return None

    def por_sufijos(imp: str, idx: dict, desde: str) -> Optional[str]:
        partes = imp.split(".")          # com.x.UserService (o import static ...UserService.metodo)
        while partes:
            hit = _elegir(idx.get("/".join(partes)), desde)
            if hit:
                return hit
            partes.pop()
        return None

    def resolver_py(imp: str, desde: str) -> Optional[str]:
        m = re.match(r"^(\.+)(.*)$", imp)
        if not m:
            return por_sufijos(imp, idx_py, desde)
        d = _dirname(desde)
        for _ in range(len(m.group(1)) - 1):
            d = _dirname(d)
        partes = m.group(2).split(".") if m.group(2) else []
        while True:
            base = _normalizar("/".join([d, *partes]))
            for c in (f"{base}.py", f"{base}/__init__.py"):
                if c in existentes:
                    return c
            if not partes:
                return None
            partes.pop()

    def resolver_js(imp: str, desde: str) -> Optional[str]:
        if imp.startswith("."):
            return probar(_normalizar(f"{_dirname(desde)}/{imp}"))
        if re.match(r"^[@~]/", imp):
            rel = re.sub(r"^[@~]/", "", imp)           # alias tipo '@/components/Foo'
        elif imp.startswith("@") or "/" not in imp:
            return None                                 # paquete npm (react, @mui/material)
        else:
            rel = imp                                   # 'components/Foo' (baseUrl)
        return _elegir(idx_js.get(_quitar_ext(rel)), desde)

    def resolver_html(imp: str, desde: str) -> Optional[str]:
        if re.match(r"^(https?:)?//", imp):
            return None
        return probar(_normalizar(f"{_dirname(desde)}/{imp}"))

    vistos, aristas = set(), []
    for ruta, datos in mapa.items():
        for imp in (datos or {}).get("imports", []):
            if ruta.endswith(".java"):
                destino = por_sufijos(imp, idx_java, ruta)
            elif ruta.endswith(".py"):
                destino = resolver_py(imp, ruta)
            elif ruta.endswith(".html"):
                destino = resolver_html(imp, ruta)
            else:
                destino = resolver_js(imp, ruta)
            if not destino or destino == ruta or (ruta, destino) in vistos:
                continue
            vistos.add((ruta, destino))
            aristas.append((ruta, destino))
    return aristas


def vecindad(mapa: dict, clave: str, profundidad: int, aristas: Optional[list] = None) -> Dict[str, int]:
    """
    Archivos a hasta `profundidad` saltos de `clave` (siguiendo imports en ambos sentidos): {ruta: distancia}.
    No incluye a `clave`. Con profundidad <= 0 devuelve {} (el llamador decide qué hacer: sin límite o sin mapa).
    """
    if not clave or profundidad <= 0 or clave not in mapa:
        return {}
    aristas = resolver_imports(mapa) if aristas is None else aristas
    vecinos: Dict[str, set] = {}
    for a, b in aristas:
        vecinos.setdefault(a, set()).add(b)
        vecinos.setdefault(b, set()).add(a)
    dist = {clave: 0}
    cola = deque([clave])
    while cola:
        actual = cola.popleft()
        if dist[actual] >= profundidad:
            continue
        for v in sorted(vecinos.get(actual, ())):
            if v not in dist:
                dist[v] = dist[actual] + 1
                cola.append(v)
    dist.pop(clave)
    return dist
