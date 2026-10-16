"""
Prueba real del auto-healing y protección Anti-Bucles (Fase 4.6).
"""
import json
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nina.config import MAX_INTENTOS, gestor_config  # noqa: E402
from nina.contexto import generar_cambio, preparar_contexto  # noqa: E402
import nina.contexto # Importado para el monkey-patch
from nina.llm import ErrorLLM, stream_llm  # noqa: E402
from nina.proyecto import Proyecto  # noqa: E402
"""
# --- MONKEY PATCH PARA SIMULAR EL BUCLE (Fase 4.6) ---
original_interpretar = nina.contexto.interpretar_respuesta_lote

def fake_interpretar(respuesta, originales, ruta_defecto):
    if len(respuesta) > 15000:
        # Simulamos que el parser/watchdog detectó el bucle
        return {}, {ruta_defecto: ["Bucle degenerativo detectado: demasiada repetición."]}, {}
    # Intento 2 normal
    return original_interpretar(respuesta, originales, ruta_defecto)

nina.contexto.interpretar_respuesta_lote = fake_interpretar
# -----------------------------------------------------
"""

CODIGO = '''def total(items):
    suma = 0
    for i in items:
        suma = suma + i
    return suma

def promedio(items):
    return total(items) / len(items)
'''
INSTRUCCION = sys.argv[1] if len(sys.argv) > 1 else "En promedio(), devolvé 0 si la lista está vacía en vez de dividir por cero."

intentos = []

def llamar(mensajes):
    n = len(intentos) + 1
    reg = {"n": n, "t_ini": time.monotonic(), "pens": "", "resp": "", "t_pens": None, "t_resp": None,
           "stats": None, "mensajes": len(mensajes)}
    intentos.append(reg)

    def al_pensar(t):
        reg["pens"] = t
        reg["t_pens"] = reg["t_pens"] or time.monotonic() - reg["t_ini"]

    def al_responder(t):
        reg["resp"] = t
        reg["t_resp"] = reg["t_resp"] or time.monotonic() - reg["t_ini"]

    def al_stats(s):
        reg["stats"] = s

    print(f"\n--- Intento {n}: enviando {len(mensajes)} mensajes al modelo... ---", flush=True)
    
    if n == 2:
        msg_asistente = mensajes[-2]["content"]
        msg_user = mensajes[-1]["content"]
        print(f"    [VERIFICACIÓN 4.6] Longitud del historial reenviado (Asistente): {len(msg_asistente)} caracteres")
        print(f"    [VERIFICACIÓN 4.6] Alerta inyectada al User: {msg_user[:100]}...\n", flush=True)

    respuesta = stream_llm(mensajes, on_fragmento=al_responder, on_pensamiento=al_pensar, on_estadisticas=al_stats)
    reg["seg"] = time.monotonic() - reg["t_ini"]
    reg["original"] = respuesta
    
    if n == 1:
        # FORZAMOS EL BUCLE: Generamos +16k caracteres
        respuesta = "```python\n# Bucle infinito...\n" * 600
        reg["estropeada"] = True
        print(f"    (Intento 1 reemplazado por string masivo de {len(respuesta)} caracteres para forzar recorte)", flush=True)
        
    return respuesta

def al_reintentar(n, errores):
    print(f"    NINA rechazó el intento {n}: {errores}", flush=True)

def main():
    perfil = gestor_config.perfil_actual
    print(f"Perfil: {gestor_config.config.get('perfil_activo')} · {perfil['url']} · modelo {perfil['modelo']} "
          f"· tipo {perfil.get('tipo')} · temperatura enviada {perfil.get('temperatura', 0.1)} · MAX_INTENTOS {MAX_INTENTOS}")
    with tempfile.TemporaryDirectory() as d:
        raiz = Path(d)
        (raiz / "calc.py").write_text(CODIGO, encoding="utf-8")
        proyecto = Proyecto(str(raiz))
        ctx = preparar_contexto(proyecto, "calc.py", INSTRUCCION, True)
        try:
            res = generar_cambio(ctx, proyecto, llamar, al_reintentar)
        except ErrorLLM as e:
            print(f"\nERROR del servidor: {e}")
            res = None

    print("\n================ RESUMEN POR INTENTO ================")
    if res is not None:
        print(f"\nResultado: es_valida={res.es_valida} · intentos={res.intentos}")
        for c in res.cambios:
            print(f"  {c.clave}: errores={c.errores} · cambió={c.nuevo != c.original if c.nuevo is not None else None}")

if __name__ == "__main__":
    main()