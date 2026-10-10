"""
Prueba real del auto-healing contra TU servidor de modelo (usa el perfil activo de ~/.nina/config.json).

Qué hace: crea un proyecto temporal con un archivo chico (no toca tus archivos), le pide un cambio al modelo y
ESTROPEA a propósito la respuesta del primer intento para forzar el reintento. El segundo intento es una
llamada real al modelo con el mensaje de error que arma NINA. Al final imprime, por intento, lo que llegó:
razonamiento, respuesta, tiempos y las estadísticas del servidor.

Uso:   python probar_reintento.py
       python probar_reintento.py "otra instrucción"
Pegame la salida completa.
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
from nina.llm import ErrorLLM, stream_llm  # noqa: E402
from nina.proyecto import Proyecto  # noqa: E402

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
    respuesta = stream_llm(mensajes, on_fragmento=al_responder, on_pensamiento=al_pensar, on_estadisticas=al_stats)
    reg["seg"] = time.monotonic() - reg["t_ini"]
    reg["original"] = respuesta
    if n == 1:   # forzamos el fallo: el SEARCH apunta a algo que no existe
        respuesta = re.sub(r"(<<<<<<< SEARCH\n).*?(\n=======)", r"\1zzz_no_existe\2", respuesta, flags=re.S)
        reg["estropeada"] = True
        print("    (la respuesta del intento 1 se estropeó a propósito para forzar el reintento)", flush=True)
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
    for r in intentos:
        s = r["stats"] or {}
        print(f"Intento {r['n']}: mensajes enviados={r['mensajes']} · razonamiento={len(r['pens'])} car. "
              f"(primer razonamiento a los {r['t_pens'] if r['t_pens'] is None else round(r['t_pens'], 1)}s) · "
              f"respuesta={len(r['resp'])} car. (primera respuesta a los {r['t_resp'] if r['t_resp'] is None else round(r['t_resp'], 1)}s) · "
              f"total={round(r.get('seg', 0), 1)}s{' · ESTROPEADA' if r.get('estropeada') else ''}")
        print(f"    estadísticas: {json.dumps(s, default=str)}")
        if r["pens"]:
            print(f"    razonamiento (primeros 200 car.): {r['pens'][:200]!r}")
            print(f"    razonamiento (últimos 200 car.):  {r['pens'][-200:]!r}")
        print(f"    respuesta original del modelo: {r.get('original', r['resp'])[:400]!r}")
    if res is not None:
        print(f"\nResultado: es_valida={res.es_valida} · intentos={res.intentos}")
        for c in res.cambios:
            print(f"  {c.clave}: errores={c.errores} · cambió={c.nuevo != c.original if c.nuevo is not None else None}")


if __name__ == "__main__":
    main()
