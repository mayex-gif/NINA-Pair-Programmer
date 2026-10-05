"""
Interpretación de la respuesta del modelo: bloques SEARCH/REPLACE y su aplicación. Funciones puras, sin E/S.
"""
import difflib
import re
from typing import Optional


# FASE 4.1: El salto de línea antes de ======= es opcional para soportar SEARCH vacíos
PATRON_BLOQUE = re.compile(
    r"<<<<<<< SEARCH[ \t]*\r?\n(.*?)(?:\r?\n)?=======[ \t]*\r?\n(.*?)(?:\r?\n)?>>>>>>> REPLACE",
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
    # Reemplazamos los Non-Breaking Spaces por espacios normales
    texto = original.replace("\xa0", " ")
    errores = []
    
    for i, (buscar, reemplazar) in enumerate(bloques, 1):
        buscar = buscar.replace("\r\n", "\n").replace("\xa0", " ")
        reemplazar = reemplazar.replace("\r\n", "\n").replace("\xa0", " ")
        
        # FASE 4.1: SEARCH vacío equivale a archivo nuevo o reemplazo total
        if not buscar.strip():
            if original.strip():
                errores.append(f"Bloque {i}: enviaste un SEARCH vacío, pero el archivo ya existe y tiene contenido. Para modificarlo debés usar código real en SEARCH.")
            else:
                texto = reemplazar + "\n"
            continue

        n = texto.count(buscar)
        if n == 1:
            if reemplazar == "" and texto.count(buscar + "\n") == 1:
                texto = texto.replace(buscar + "\n", "", 1)
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


def _extraer_ruta(texto_previo: str, ruta_defecto: str) -> str:
    """
    Escanea de abajo hacia arriba buscando la primera línea que parezca una ruta de archivo.
    Ignora texto basura o explicaciones del LLM entre la ruta y el bloque.
    """
    for linea in reversed(texto_previo.splitlines()):
        linea = linea.strip(" `*:'\"")  # Limpiamos tildes invertidas o formato Markdown
        # Coincide con rutas como src/app.js, backend/Main.java, .env, etc.
        if re.match(r'^[\w@\-./\\]+\.[a-zA-Z0-9]+$', linea):
            return linea.replace("\\", "/")
    return ruta_defecto


def interpretar_respuesta_lote(respuesta: str, originales: dict, ruta_defecto: str):
    """
    FASE 4.1: Procesa una respuesta multi-archivo.
    Agrupa los bloques por ruta y los aplica a su archivo correspondiente.
    """
    nuevos, errores, avisos = {}, {}, {}
    matches = list(PATRON_BLOQUE.finditer(respuesta))
    
    # Si no hay bloques, devolvemos vacío para que el wrapper pueda hacer fallbacks
    if not matches:
        return {}, {}, {}

    # 1. Agrupar bloques asociándolos a la ruta encontrada arriba de cada uno
    agrupados = {}
    indice_previo = 0
    for m in matches:
        texto_intermedio = respuesta[indice_previo:m.start()]
        indice_previo = m.end()
        
        ruta = _extraer_ruta(texto_intermedio, ruta_defecto)
        if ruta not in agrupados:
            agrupados[ruta] = []
        agrupados[ruta].append((m.group(1), m.group(2)))

    # 2. Aplicar los bloques a cada archivo
    for ruta, bloques in agrupados.items():
        original = originales.get(ruta, "")
        texto_nuevo, errs = aplicar_bloques(original, bloques)
        
        if errs:
            errores[ruta] = errs
        else:
            nuevos[ruta] = texto_nuevo
            # Si el original estaba vacío y aplicamos código, avisamos que se creó.
            if not original.strip():
                avisos[ruta] = ["Archivo creado desde cero."]
            else:
                avisos[ruta] = []
                
    return nuevos, errores, avisos


# --- Wrapper de compatibilidad temporal para mantener tests actuales de la Fase 0 ---
PATRON_NUEVO = re.compile(
    r"<<<<<<< SEARCH[ \t]*\r?\n.*?(?:\r?\n)?=======[ \t]*\r?\n(.*?)(?:\r?\n)?>>>>>>> REPLACE", re.DOTALL
)

def interpretar_respuesta(respuesta: str, original: str):
    # 1. Fallback de creación directa
    if not original.strip():
        nuevos = PATRON_NUEVO.findall(respuesta)
        if nuevos:
            return "\n".join(nuevos).strip("\n") + "\n", [], ["Archivo creado desde cero."]
        m = re.search(r"```[\w+-]*\n(.*?)```", respuesta, re.DOTALL)
        if m:
            return m.group(1).rstrip("\n") + "\n", [], ["Archivo creado desde cero."]
        if respuesta.strip() and "<<<<<<<" not in respuesta:
            return respuesta.strip() + "\n", [], ["Archivo creado desde cero."]

    # 2. Llamar al nuevo motor por lote
    nuevos, errs, avisos = interpretar_respuesta_lote(respuesta, {"default": original}, "default")
    
    if "default" in errs:
        return None, errs["default"], []
    if "default" in nuevos:
        return nuevos["default"], [], avisos.get("default", [])

    # 3. Fallback bloque markdown (archivo completo)
    m = re.search(r"```[\w+-]*\n(.*?)```", respuesta, re.DOTALL)
    if m:
        return m.group(1).rstrip("\n") + "\n", [], ["El modelo devolvió el archivo completo (no usó SEARCH/REPLACE). Revisá bien el diff."]

    return None, ["No pude interpretar la respuesta: no hay bloques SEARCH/REPLACE ni un bloque de código."], []