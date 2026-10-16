"""
Contexto y generación del cambio: ranking del mapa por relevancia, armado del prompt y auto-healing.
Sin E/S propia ni UI: lee el disco solo a través de `Proyecto`.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .bloques import interpretar_respuesta_lote
from .config import (MAX_ARCHIVOS_MAPA, MAX_INTENTOS, PRESUPUESTO_ARBOL_CHARS, PRESUPUESTO_MAPA_CHARS, SISTEMA_BASE,
                     gestor_config)
from .grafo import resolver_imports, vecindad
from .llm import CorteLLM, stream_llm
from .mapa import primer_error_sintaxis, tiene_errores_sintaxis
from .proyecto import Proyecto, leer_archivo


def estimar_tokens(texto: str) -> int:
    return len(texto) // 3


def _palabras(texto: str) -> set:
    return {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", texto)}


def _import_a_stem(imp: str) -> Optional[str]:
    """'./components/UserCard.jsx' -> 'UserCard' | 'com.x.service.UserService' -> 'UserService'."""
    imp = imp.strip().rstrip(";")
    if imp.endswith(".*"):
        return None
    if "/" in imp:  
        seg = imp.rstrip("/").split("/")[-1]
        return re.sub(r"\.(jsx?|tsx?|mjs)$", "", seg) or None
    return imp.lstrip(".").split(".")[-1] or None


# Qué datos extra del mapa v3 se muestran, con su título y cuántos elementos como máximo
CAMPOS_EXTRA = (
    ("fields", "atributos", 14),
    ("endpoints", "endpoints", 10),
    ("exports", "exporta", 8),
    ("components", "componentes", 6),
    ("hooks", "hooks", 8),
    ("renders", "renderiza", 10),
    ("api", "llama a", 6),
)


def bloque_mapa(ruta: str, info: dict, etiqueta: str = "") -> str:
    """Texto de un archivo del mapa: sus firmas y, si el mapa es v3, atributos, endpoints, hooks, etc."""
    lineas = [f"# {ruta}{etiqueta}"] + list(info.get("signatures", []))
    for clave, titulo, tope in CAMPOS_EXTRA:
        items = info.get(clave) or []
        if items:
            mas = f" (+{len(items) - tope} más)" if len(items) > tope else ""
            lineas.append(f"  · {titulo}: {', '.join(items[:tope])}{mas}")
    return "\n".join(lineas)


def _profundidad_configurada() -> int:
    return int((gestor_config.perfil_actual.get("limites") or {}).get("profundidad_mapa", 2))


def filtrar_mapa(mapa: dict, clave_obj: str, instruccion: str, extra: str = "",
                 profundidad: Optional[int] = None, completo: bool = False):
    """
    Arma el mapa que se le manda a la IA. Devuelve (texto, archivos_con_detalle, total).

    · Con archivo objetivo: solo su vecindad, o sea los archivos a hasta `profundidad` saltos de import (los que importa
      y los que lo importan, cada vez más lejos), más lo que la instrucción nombra explícitamente. `profundidad=0` = sin
      límite (rankea todo el mapa).
    · `completo=True` (archivo nuevo o Modo Global): el árbol entero. Los más relevantes van con detalle y el resto
      como lista de rutas, para que la IA vea dónde encaja lo nuevo.
    """
    profundidad = _profundidad_configurada() if profundidad is None else profundidad
    palabras = _palabras(instruccion + " " + extra)
    en_clave = clave_obj if clave_obj in mapa else ""
    aristas = resolver_imports(mapa) if (en_clave and profundidad > 0) else []
    dist = vecindad(mapa, en_clave, profundidad, aristas) if en_clave else {}
    importa = {b for a, b in aristas if a == en_clave}      # lo que el objetivo importa
    stem_obj = Path(clave_obj).stem if clave_obj else ""
    imports_obj = {_import_a_stem(i) for i in mapa.get(clave_obj, {}).get("imports", [])} - {None} if clave_obj else set()

    puntuados = []
    for ruta, info in mapa.items():
        if clave_obj and ruta == clave_obj:
            continue
        stem = Path(ruta).stem
        menciona = stem.lower() in palabras
        comunes = len(palabras & _palabras(" ".join(info.get("signatures", []))))
        p = (3 if menciona else 0) + min(comunes, 3)
        d = dist.get(ruta)
        if clave_obj and not completo:
            if profundidad > 0:
                # vecindad: lo cercano entra siempre; lo lejano solo si la instrucción lo nombra o comparte varios identificadores
                if d is None and not (menciona or comunes >= 2):
                    continue
                p += max(0, 12 - 4 * d) if d is not None else 0
            else:
                if stem in imports_obj:
                    p += 5
                if stem_obj in {_import_a_stem(i) for i in info.get("imports", [])}:
                    p += 4
                if Path(ruta).parent == Path(clave_obj).parent:
                    p += 1
                if p == 0:
                    continue
        elif p == 0 and not completo:
            continue
        puntuados.append((d if d is not None else 99, -p, ruta))

    puntuados.sort()
    bloques, largo, usados, detallados = [], 0, 0, set()
    for d, _, ruta in puntuados[:MAX_ARCHIVOS_MAPA]:
        etiqueta = ""
        if d == 1:
            etiqueta = "  [lo importa]" if ruta in importa else "  [lo usa]"
        elif d != 99:
            etiqueta = f"  [a {d} saltos]"
        bloque = bloque_mapa(ruta, mapa[ruta], etiqueta)
        if largo + len(bloque) > PRESUPUESTO_MAPA_CHARS:
            break
        bloques.append(bloque)
        detallados.add(ruta)
        largo += len(bloque)
        usados += 1

    texto = "\n\n".join(bloques)
    if completo:
        # el resto del árbol, solo con la ruta (barato), para que la IA sepa qué existe y dónde ubicar lo nuevo
        restantes, largo_arbol = [], 0
        for ruta in sorted(r for r in mapa if r not in detallados and r != clave_obj):
            if largo_arbol + len(ruta) + 3 > PRESUPUESTO_ARBOL_CHARS:
                restantes.append("…")
                break
            restantes.append(f"- {ruta}")
            largo_arbol += len(ruta) + 3
        if restantes:
            texto += ("\n\n" if texto else "") + "## Resto del árbol del proyecto (solo rutas)\n" + "\n".join(restantes)
    return texto, usados, len(mapa)


def construir_mensajes(clave: str, codigo: str, instruccion: str, convenciones: str, mapa_txt: str, adicionales: dict = None):
    sistema = SISTEMA_BASE  
    if convenciones:
        sistema += f"\n\n## Convenciones del proyecto (obligatorias)\n{convenciones}"
    if mapa_txt:
        sistema += (
            "\n\n## Mapa de archivos relacionados (firmas, atributos y dependencias; solo lo cercano al archivo pedido)\n"
            f"{mapa_txt}"
        )
    
    usuario = ""
    # Si hay un archivo pivote, lo inyectamos
    if clave:
        usuario += f'ESTE ES EL ARCHIVO SELECCIONADO (ruta exacta: {clave}):\n'
        usuario += f'<archivo ruta="{clave}">\n{codigo}\n</archivo>\n\n'
    
    # Archivos adicionales detectados en el prompt
    if adicionales:
        usuario += "OTROS ARCHIVOS EN CONTEXTO:\n"
        for k, v in adicionales.items():
            usuario += f'<archivo ruta="{k}">\n{v}\n</archivo>\n\n'
            
    usuario += f"Instrucción: {instruccion}"
    
    if clave and not codigo.strip() and not adicionales:
        usuario += "\n\nEl archivo está VACÍO: devolvé el código completo del archivo dentro de un único bloque ```, sin SEARCH/REPLACE."
        
    return [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}]


@dataclass
class Contexto:
    ruta: Path          
    clave: str          
    original: str
    eol: str
    mensajes: list
    tokens: int
    mapa_usados: int
    mapa_total: int
    hay_convenciones: bool


# === NUEVOS MODELOS MULTI-ARCHIVO (FASE 0.3) ===
@dataclass
class CambioArchivo:
    """Representa la propuesta de modificación para un único archivo del lote."""
    clave: str
    original: str
    eol: str
    nuevo: Optional[str] = None
    errores: list = field(default_factory=list)
    avisos: list = field(default_factory=list)

    @property
    def es_nuevo(self) -> bool:
        """Archivo que no existía (o estaba vacío): no hay nada que comparar, así que no se muestra diff."""
        return not self.original.strip()


@dataclass
class Propuesta:
    """El lote completo devuelto por el LLM."""
    cambios: list[CambioArchivo] = field(default_factory=list)
    intentos: int = 1
    respuesta: str = ""

    @property
    def es_valida(self) -> bool:
        """True si hay cambios y todos los archivos del lote se parsearon sin errores."""
        return bool(self.cambios) and all(c.nuevo is not None and not c.errores for c in self.cambios)
# ===============================================


def preparar_contexto(proyecto: Proyecto, ruta, instruccion: str, sin_mapa: bool = False, extra: str = "",
                      incluir: Optional[list] = None) -> Contexto:
    """`incluir`: rutas que van siempre con su contenido completo (el agente las usa para los archivos de la tarea)."""
    # FASE 4.2: Soporte para Modo Global (ruta = None)
    if ruta:
        ruta_abs = proyecto.abs(ruta)
        clave = proyecto.clave(ruta_abs)
        original, eol = leer_archivo(ruta_abs)
    else:
        ruta_abs = None
        clave = ""
        original, eol = "", "\n"
        
    convenciones = proyecto.cargar_convenciones()
    mapa = proyecto.cargar_mapa()
    
    mapa_txt, usados, total = "", 0, 0
    if not sin_mapa:
        # archivo nuevo/vacío o Modo Global: se manda el árbol entero; si no, solo la vecindad del archivo
        mapa_txt, usados, total = filtrar_mapa(mapa, clave, instruccion, extra, completo=not original.strip())
        
    adicionales = {}
    for k in mapa.keys():
        nombre = k.split('/')[-1]
        if k != clave and (k in instruccion or nombre in instruccion):
            try:
                txt, _ = leer_archivo(proyecto.abs(k))
                adicionales[k] = txt
            except OSError:
                pass
    
    for k in incluir or []:
        if k != clave and k not in adicionales:
            try:
                adicionales[k], _ = leer_archivo(proyecto.abs(k))
            except OSError:
                pass          # todavía no existe: la tarea lo va a crear
    mensajes = construir_mensajes(clave, original, instruccion, convenciones, mapa_txt, adicionales)
    tokens = estimar_tokens("".join(m["content"] for m in mensajes))
    return Contexto(ruta_abs, clave, original, eol, mensajes, tokens, usados, total, bool(convenciones))


def mensaje_reintento(errores_dict: dict) -> str:
    msg = "Tu respuesta anterior no se pudo aplicar en los siguientes archivos:\n\n"
    for ruta, errs in errores_dict.items():
        msg += f"Archivo: {ruta}\n"
        for e in errs:
            msg += f"- {e}\n"
        msg += "\n"
    msg += (
        "Cada SEARCH debe copiar EXACTAMENTE el código original. "
        "Si el archivo es nuevo, dejá SEARCH vacío. "
        "Reenviá TODOS los bloques corregidos, asegurándote de incluir la ruta del archivo justo arriba de cada bloque."
    )
    return msg


def debe_reenviar(respuesta: str, mensajes: list, perfil: Optional[dict] = None) -> bool:
    """
    ¿Vale la pena reenviar la respuesta rechazada en el reintento? Solo si pesa menos que
    `limites.reenviar_respuesta_hasta_tokens` Y el reintento entra en el contexto del servidor
    (`servidor.contexto`, si está configurado). Si no, se la reemplaza por un aviso corto.
    """
    perfil = perfil or gestor_config.perfil_actual
    limites = perfil.get("limites") or {}
    tokens_resp = estimar_tokens(respuesta)
    tope = limites.get("reenviar_respuesta_hasta_tokens")
    if tope is not None and tokens_resp > tope:
        return False
    contexto = (perfil.get("servidor") or {}).get("contexto")
    if contexto:
        prompt = sum(estimar_tokens(m["content"]) for m in mensajes)
        reserva = limites.get("max_tokens_salida") or 4096
        if prompt + tokens_resp + 600 + reserva > contexto:    # 600 ≈ mensaje de reintento
            return False
    return True


def mensaje_corte(corte: CorteLLM) -> str:
    return (
        f"AVISO: el intento anterior se cortó ({corte.motivo}): {corte}\n"
        "Respondé ahora directamente con los bloques SEARCH/REPLACE, sin razonar de nuevo ni repetir el análisis."
    )


def generar_cambio(
    ctx: Contexto,
    proyecto: Optional[Proyecto] = None,
    llamar: Optional[Callable[[list], str]] = None,
    on_reintento: Optional[Callable[[int, dict], None]] = None,
) -> Propuesta:
    """Genera una Propuesta (lote de cambios) manejando múltiples archivos."""
    llamar = llamar or stream_llm
    mensajes = list(ctx.mensajes)  
    propuesta = Propuesta()

    class LectorOriginales(dict):
        def get(self, ruta, default=""):
            if ruta in self:
                return self[ruta]
            if proyecto is None:
                return default
            try:
                texto, _ = leer_archivo(proyecto.abs(ruta))
                self[ruta] = texto
                return texto
            except OSError:
                return default

# -----------------------------------------------------------------------------

    ruta_defecto = ctx.clave or "proyecto"
    opciones = {}      # tras un corte del intento anterior, el siguiente se pide sin razonamiento

    for intento in range(1, MAX_INTENTOS + 1):
        try:
            respuesta = llamar(mensajes, **opciones) if opciones else llamar(mensajes)
        except CorteLLM as corte:
            # Bucle / razonamiento interminable / tope de tokens: es un intento fallido reintentable, no un error fatal.
            propuesta.respuesta, propuesta.intentos = corte.respuesta, intento
            msg = f"El intento se cortó ({corte.motivo}): {corte}"
            propuesta.cambios = [CambioArchivo(clave=ruta_defecto, original=ctx.original, eol=ctx.eol, errores=[msg])]
            if intento < MAX_INTENTOS:
                if on_reintento:
                    on_reintento(intento, {ruta_defecto: [msg]})
                opciones = {"razonar": False}
                # La respuesta cortada no se reenvía: la nota se suma al último mensaje del usuario (los roles siguen alternando).
                mensajes[-1] = {"role": "user", "content": mensajes[-1]["content"] + "\n\n" + mensaje_corte(corte)}
                continue
            return propuesta
        propuesta.respuesta = respuesta
        propuesta.intentos = intento
        
        if not respuesta.strip():  
            propuesta.cambios = [CambioArchivo(
                clave=ctx.clave or "proyecto", original=ctx.original, eol=ctx.eol, 
                errores=["El modelo no devolvió ninguna respuesta (¿agotó el contexto o los tokens?)."]
            )]
            return propuesta
            
        # Si hay archivo principal, lo precargamos. Si no, arranca vacío.
        dict_inicial = {ctx.clave: ctx.original} if ctx.clave else {}
        originales = LectorOriginales(dict_inicial)
            
        # En modo global, si falla, usamos "proyecto" como nombre de referencia
        nuevos, errores_por_ruta, avisos_por_ruta = interpretar_respuesta_lote(respuesta, originales, ruta_defecto)

        # Si el modelo no generó bloques, devolvemos un error en el archivo principal/proyecto
        if not nuevos and not errores_por_ruta:
            errores_por_ruta = {ruta_defecto: ["No pude interpretar la respuesta: no hay bloques SEARCH/REPLACE."]}

        cambios = []
        rutas_procesadas = set(nuevos.keys()) | set(errores_por_ruta.keys())
        
        for ruta in rutas_procesadas:
            orig = originales.get(ruta, "")
            nuevo = nuevos.get(ruta)
            errs = errores_por_ruta.get(ruta, [])
            avisos = avisos_por_ruta.get(ruta, [])

            if nuevo is not None:
                if not tiene_errores_sintaxis(orig, ruta) and tiene_errores_sintaxis(nuevo, ruta):
                    linea = primer_error_sintaxis(nuevo, ruta)
                    donde = f" cerca de la línea {linea}" if linea else ""
                    avisos.append(
                        f"🚨 Posible error de sintaxis{donde} (según tree-sitter). Puede ser un falso positivo "
                        "(p. ej. un `&` suelto en JSX): confirmalo con tu compilador antes de guardar.")

            cambios.append(CambioArchivo(
                clave=ruta, original=orig, eol=ctx.eol, 
                nuevo=nuevo, errores=errs, avisos=avisos
            ))
            
        propuesta.cambios = cambios
        
        if propuesta.es_valida:
            return propuesta
            
        if intento < MAX_INTENTOS:
            if on_reintento:
                on_reintento(intento, errores_por_ruta)
            if debe_reenviar(respuesta, mensajes):
                mensajes.append({"role": "assistant", "content": respuesta})
                nota = ""
            else:
                mensajes.append({"role": "assistant", "content": "(respuesta anterior omitida por su tamaño)"})
                nota = ("\n\n(Tu respuesta anterior se omitió por su tamaño; el contenido original de los archivos "
                        "está en el primer mensaje.)")
            mensajes.append({"role": "user", "content": mensaje_reintento(errores_por_ruta) + nota})
            
    return propuesta


def aviso_recorte(original: str, nuevo: str) -> Optional[str]:
    lo, ln = len(original.splitlines()), len(nuevo.splitlines())
    if lo >= 20 and ln < lo * 0.6:
        return f"🚨 El archivo pasaría de {lo} a {ln} líneas. Posible recorte por alucinación."
    return None