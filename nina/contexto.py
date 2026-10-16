"""
Contexto y generación del cambio: ranking del mapa por relevancia, armado del prompt y auto-healing.
Sin E/S propia ni UI: lee el disco solo a través de `Proyecto`.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .bloques import interpretar_respuesta_lote
from .config import MAX_ARCHIVOS_MAPA, MAX_INTENTOS, PRESUPUESTO_MAPA_CHARS, SISTEMA_BASE
from .llm import stream_llm
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


def filtrar_mapa(mapa: dict, clave_obj: str, instruccion: str, extra: str = ""):
    """Rankea los archivos usando el Grafo de Dependencias (imports_exactos) y coincidencia semántica."""
    
    # 1. Extraemos las dependencias exactas (Profundidad 1 - Hijos) del archivo pivote
    dependencias_directas = set(mapa.get(clave_obj, {}).get("imports_exactos", [])) if clave_obj else set()
    
    # 2. Encontramos dependencias inversas (Profundidad 1 - Padres que usan al pivote)
    dependencias_inversas = set()
    if clave_obj:
        for ruta, info in mapa.items():
            if clave_obj in info.get("imports_exactos", []):
                dependencias_inversas.add(ruta)

    palabras = _palabras(instruccion + " " + extra)
    puntuados = []
    
    dir_obj = Path(clave_obj).parent if clave_obj else None

    for ruta, info in mapa.items():
        if clave_obj and ruta == clave_obj:
            continue
            
        p = 0
        
        # --- Puntuación Relacional Bidireccional (Fase 4.5) ---
        if ruta in dependencias_directas:
            p += 10  # Es un archivo que nuestro archivo pivote importa
        if ruta in dependencias_inversas:
            p += 8   # Es un archivo que importa a nuestro archivo pivote
            
        if clave_obj and Path(ruta).parent == dir_obj:
            p += 2   # Son vecinos en la misma carpeta
            
        # --- Puntuación Semántica (Fallback para Modo Global o menciones explícitas) ---
        stem = Path(ruta).stem
        if stem.lower() in palabras:
            p += 3
        p += min(len(palabras & _palabras(" ".join(info.get("signatures", [])))), 3)
        
        if p > 0:
            puntuados.append((p, ruta))

    # Ordenamos por mayor puntaje, y luego alfabéticamente para desempatar
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


def construir_mensajes(clave: str, codigo: str, instruccion: str, convenciones: str, mapa_txt: str, adicionales: dict = None):
    sistema = SISTEMA_BASE  
    if convenciones:
        sistema += f"\n\n## Convenciones del proyecto (obligatorias)\n{convenciones}"
    if mapa_txt:
        sistema += (
            "\n\n## Mapa de archivos relacionados (solo firmas, para contexto de imports y dependencias)\n"
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


def preparar_contexto(proyecto: Proyecto, ruta, instruccion: str, sin_mapa: bool = False, extra: str = "") -> Contexto:
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
        mapa_txt, usados, total = filtrar_mapa(mapa, clave, instruccion, extra)
        
    adicionales = {}
    for k in mapa.keys():
        nombre = k.split('/')[-1]
        if k != clave and (k in instruccion or nombre in instruccion):
            try:
                txt, _ = leer_archivo(proyecto.abs(k))
                adicionales[k] = txt
            except OSError:
                pass
    
    mensajes = construir_mensajes(clave, original, instruccion, convenciones, mapa_txt, adicionales)
    tokens = estimar_tokens("".join(m["content"] for m in mensajes))
    return Contexto(ruta_abs, clave, original, eol, mensajes, tokens, usados, total, bool(convenciones))


def mensaje_reintento(errores_dict: dict) -> str:
    es_bucle = any("Bucle degenerativo" in str(e) for lista in errores_dict.values() for e in lista)
    
    if es_bucle:
        return (
            "⚠️ ALERTA CRÍTICA: Tu respuesta anterior fue interrumpida porque entraste en un bucle degenerativo "
            "(empezaste a repetir el mismo texto sin parar).\n"
            "Por favor, REINICIA tu razonamiento. Toma un enfoque diferente, más directo y conciso. "
            "Genera los bloques SEARCH/REPLACE de inmediato sin sobrepensar."
        )

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

    for intento in range(1, MAX_INTENTOS + 1):
        respuesta = llamar(mensajes)
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
        ruta_defecto = ctx.clave or "proyecto"
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
            
            # Solo recortamos si falló específicamente por un bucle (basura repetitiva)
            es_bucle = any("Bucle degenerativo" in str(e) for lista in errores_por_ruta.values() for e in lista)
            
            if es_bucle and len(respuesta) > 15000:
                respuesta_recortada = respuesta[:1500] + f"\n\n... [TEXTO RECORTADO: {len(respuesta)} caracteres. Bucle detectado, texto descartado] ...\n\n" + respuesta[-1500:]
                mensajes.append({"role": "assistant", "content": respuesta_recortada})
            else:
                mensajes.append({"role": "assistant", "content": respuesta})
                
            mensajes.append({"role": "user", "content": mensaje_reintento(errores_por_ruta)})
            
    return propuesta


def aviso_recorte(original: str, nuevo: str) -> Optional[str]:
    lo, ln = len(original.splitlines()), len(nuevo.splitlines())
    if lo >= 20 and ln < lo * 0.6:
        return f"🚨 El archivo pasaría de {lo} a {ln} líneas. Posible recorte por alucinación."
    return None
