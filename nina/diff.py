"""
Diff: funciones puras (sin Streamlit ni rich). Antes vivían dentro de web.py.
"""
import difflib


def diff_unificado(original: str, nuevo: str, nombre: str, contexto: int = 3) -> list:
    return list(difflib.unified_diff(
        original.splitlines(), nuevo.splitlines(),
        fromfile=f"a/{nombre}", tofile=f"b/{nombre}", lineterm="", n=contexto,
    ))


def contar_cambios(original: str, nuevo: str):
    """(líneas agregadas, líneas eliminadas)."""
    mas = menos = 0
    for linea in diff_unificado(original, nuevo, "x")[2:]:  # las 2 primeras son las cabeceras ---/+++
        if linea.startswith("+"):
            mas += 1
        elif linea.startswith("-"):
            menos += 1
    return mas, menos


def filas_alineadas(viejo: str, nuevo: str):
    """Alinea fila por fila: ambos lados tienen siempre las mismas filas."""
    a, b = viejo.split("\n"), nuevo.split("\n")
    if a and a[-1] == "":
        a.pop()
    if b and b[-1] == "":
        b.pop()
    filas = []  # (num_izq, texto_izq, clase_izq, num_der, texto_der, clase_der)
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            for k in range(i2 - i1):
                filas.append((i1 + k + 1, a[i1 + k], "", j1 + k + 1, b[j1 + k], ""))
            continue
        for k in range(max(i2 - i1, j2 - j1)):
            i, j = i1 + k, j1 + k
            ti, tj = i < i2, j < j2
            filas.append((i + 1 if ti else None, a[i] if ti else "", "del" if ti else "vacio",
                          j + 1 if tj else None, b[j] if tj else "", "add" if tj else "vacio"))
    return filas


def colapsar(filas: list, contexto: int = 3) -> list:
    """Deja solo las zonas con cambios (± contexto líneas) y resume el resto."""
    cambiadas = [i for i, f in enumerate(filas) if f[2] or f[5]]
    visibles = set()
    for i in cambiadas:
        visibles.update(range(max(0, i - contexto), min(len(filas), i + contexto + 1)))
    salida, omitidas = [], 0

    def separador(n):
        txt = f"⋯ {n} líneas sin cambios ⋯"
        return (None, txt, "sep", None, txt, "sep")

    for i, f in enumerate(filas):
        if i in visibles:
            if omitidas:
                salida.append(separador(omitidas))
                omitidas = 0
            salida.append(f)
        else:
            omitidas += 1
    if omitidas:
        salida.append(separador(omitidas))
    return salida
