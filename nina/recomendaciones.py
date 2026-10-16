"""
Fases 5.4 y 5.7 – texto de apoyo para la UI, sin Streamlit (funciones puras, fáciles de probar).
"""
from typing import List


def recomendaciones(proyecto, mapa: dict, n_archivos: int, contexto_servidor: int = None, max_tokens_prompt: int = 12000) -> List[str]:
    """Consejos concretos según el estado del proyecto (5.7)."""
    r = []
    if not proyecto.cargar_convenciones().strip():
        r.append("No hay CONVENTIONS.md: crealo con tus reglas (estilo, librerías, estructura); es lo que más mejora la calidad.")
    if not mapa:
        r.append("No hay mapa del proyecto: generalo para que el modelo conozca firmas, imports y endpoints.")
    elif n_archivos and len(mapa) < n_archivos * 0.5:
        r.append(f"El mapa cubre {len(mapa)} de {n_archivos} archivos: regeneralo.")
    if contexto_servidor and max_tokens_prompt > contexto_servidor * 0.75:
        r.append(f"Tu tope de prompt ({max_tokens_prompt}) deja poco margen para la respuesta con un contexto de servidor de {contexto_servidor}.")
    return r


def resumen_impactos(cambios) -> List[str]:
    """Una línea por archivo del lote: ✏️ modificado / 🆕 nuevo / ⚠️ con errores / ⏸ sin cambios (5.4)."""
    lineas = []
    for c in cambios:
        if c.errores or c.nuevo is None:
            marca = "⚠️"
        elif c.nuevo == c.original:
            marca = "⏸"
        elif c.es_nuevo:
            marca = "🆕"
        else:
            marca = "✏️"
        lineas.append(f"{marca} {c.clave}")
    return lineas
