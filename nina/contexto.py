"""
Contexto y generación del cambio: ranking del mapa por relevancia, armado del prompt y auto-healing.
Sin E/S propia ni UI: lee el disco solo a través de `Proyecto`.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .bloques import interpretar_respuesta
from .config import MAX_ARCHIVOS_MAPA, MAX_INTENTOS, PRESUPUESTO_MAPA_CHARS, SISTEMA_BASE
from .llm import stream_llm
from .mapa import tiene_errores_sintaxis
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


def filtrar_mapa(mapa: dict, clave_obj: str, instruccion: str, extra: str = ""):
    """Rankea los archivos del mapa por relevancia para el archivo objetivo y arma un texto compacto."""
    stem_obj = Path(clave_obj).stem
    dir_obj = Path(clave_obj).parent
    imports_obj = {_import_a_stem(i) for i in mapa.get(clave_obj, {}).get("imports", [])} - {None}
    palabras = _palabras(instruccion + " " + extra)

    puntuados = []
    for ruta, info in mapa.items():
        if ruta == clave_obj:
            continue
        stem = Path(ruta).stem
        p = 0
        if stem in imports_obj:
            p += 5
        if stem_obj in {_import_a_stem(i) for i in info.get("imports", [])}:
            p += 4
        if stem.lower() in palabras:
            p += 3
        p += min(len(palabras & _palabras(" ".join(info.get("signatures", [])))), 3)
        if Path(ruta).parent == dir_obj:
            p += 1
        if p > 0:
            puntuados.append((p, ruta))

    puntuados.sort(key=lambda x: (-x[0], x[1]))
    bloques, largo, usados = [], 0, 0
    for _, ruta in puntuados[:MAX_ARCHIVOS_MAPA]:
        bloque = f"# {ruta}\n" + "\n".join(mapa[ruta].get("signatures", []))
        if largo + len(bloque) > PRESUPUESTO_MAPA_CHARS:
            break
        bloques.append(bloque)
        largo += len(bloque)
        usados += 1
    return "\n\n".join(bloques), usados, len(mapa)


def construir_mensajes(clave: str, codigo: str, instruccion: str, convenciones: str, mapa_txt: str):
    sistema = SISTEMA_BASE  
    if convenciones:
        sistema += f"\n\n## Convenciones del proyecto (obligatorias)\n{convenciones}"
    if mapa_txt:
        sistema += (
            "\n\n## Mapa de archivos relacionados (solo firmas, para contexto de imports y dependencias)\n"
            f"{mapa_txt}"
        )
    usuario = (
        f'<archivo ruta="{clave}">\n{codigo}\n</archivo>\n\n'
        f"Instrucción: {instruccion}"
    )
    if not codigo.strip():
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

@dataclass
class Propuesta:
    """El lote completo devuelto por el LLM. Reemplaza al antiguo 'Resultado'."""
    cambios: list[CambioArchivo] = field(default_factory=list)
    intentos: int = 1
    respuesta: str = ""

    @property
    def es_valida(self) -> bool:
        """True si hay cambios y todos los archivos del lote se parsearon sin errores."""
        return bool(self.cambios) and all(c.nuevo is not None and not c.errores for c in self.cambios)
# ===============================================


def preparar_contexto(proyecto: Proyecto, ruta, instruccion: str, sin_mapa: bool = False, extra: str = "") -> Contexto:
    ruta = proyecto.abs(ruta)
    clave = proyecto.clave(ruta)
    original, eol = leer_archivo(ruta)
    convenciones = proyecto.cargar_convenciones()
    mapa_txt, usados, total = "", 0, 0
    if not sin_mapa:
        mapa_txt, usados, total = filtrar_mapa(proyecto.cargar_mapa(), clave, instruccion, extra)
    mensajes = construir_mensajes(clave, original, instruccion, convenciones, mapa_txt)
    tokens = estimar_tokens("".join(m["content"] for m in mensajes))
    return Contexto(ruta, clave, original, eol, mensajes, tokens, usados, total, bool(convenciones))


def mensaje_reintento(errores: list) -> str:
    return (
        "Tu respuesta anterior no se pudo aplicar:\n"
        + "\n".join(f"- {e}" for e in errores)
        + "\n\nCada SEARCH debe copiar EXACTAMENTE el archivo ORIGINAL que te envié (sin ningún cambio aplicado). "
        "Reenviá TODOS los bloques SEARCH/REPLACE de nuevo (los que estaban bien y los corregidos), "
        "usando solo ese formato y sin explicaciones."
    )


def generar_cambio(
    ctx: Contexto,
    llamar: Optional[Callable[[list], str]] = None,
    on_reintento: Optional[Callable[[int, list], None]] = None,
) -> Propuesta:
    """
    Genera una Propuesta (lote de cambios). 
    En esta etapa de transición, devuelve siempre un lote con 1 solo archivo (ctx.clave).
    """
    llamar = llamar or stream_llm
    mensajes = list(ctx.mensajes)  
    propuesta = Propuesta()
    
    for intento in range(1, MAX_INTENTOS + 1):
        respuesta = llamar(mensajes)
        propuesta.respuesta = respuesta
        propuesta.intentos = intento
        
        if not respuesta.strip():  
            cambio_vacio = CambioArchivo(
                clave=ctx.clave, original=ctx.original, eol=ctx.eol, 
                errores=["El modelo no devolvió ninguna respuesta (¿agotó el contexto o los tokens?)."]
            )
            propuesta.cambios = [cambio_vacio]
            return propuesta
            
        nuevo, errores, avisos = interpretar_respuesta(respuesta, ctx.original)

        # --- FASE 0.6: Validación de sintaxis ---
        if nuevo is not None:
            if not tiene_errores_sintaxis(ctx.original, ctx.clave) and tiene_errores_sintaxis(nuevo, ctx.clave):
                avisos.append("🚨 Advertencia de sintaxis: el código generado contiene errores estructurales (llaves sin cerrar, indentación rota, etc).")
        # ----------------------------------------

        cambio_actual = CambioArchivo(
            clave=ctx.clave, original=ctx.original, eol=ctx.eol, 
            nuevo=nuevo, errores=errores, avisos=avisos
        )
        propuesta.cambios = [cambio_actual]
        
        if propuesta.es_valida:
            return propuesta
            
        if intento < MAX_INTENTOS:
            if on_reintento:
                on_reintento(intento, errores)
            mensajes.append({"role": "assistant", "content": respuesta})
            mensajes.append({"role": "user", "content": mensaje_reintento(errores)})
            
    return propuesta


def aviso_recorte(original: str, nuevo: str) -> Optional[str]:
    lo, ln = len(original.splitlines()), len(nuevo.splitlines())
    if lo >= 20 and ln < lo * 0.6:
        return f"🚨 El archivo pasaría de {lo} a {ln} líneas. Posible recorte por alucinación."
    return None