"""
Fases 4.4 y 6: checkpoints Git, ejecutor con lista blanca, planificador y lazo del agente (con LLM falso y git real).
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nina import agente as ag  # noqa: E402
from nina import ejecutor, gitutil  # noqa: E402
from nina.proyecto import Proyecto  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="requiere git")


@pytest.fixture
def P():
    with tempfile.TemporaryDirectory() as t:
        p = Proyecto(Path(t).resolve())
        assert gitutil.inicializar(p.raiz)
        (p.raiz / "base.py").write_text("x = 1\n", encoding="utf-8")
        assert gitutil.checkpoint(p.raiz, "inicio")
        yield p


def nuevo_archivo(ruta, contenido):
    """Respuesta del modelo que crea un archivo (SEARCH vacío)."""
    return f"{ruta}\n<<<<<<< SEARCH\n=======\n{contenido}>>>>>>> REPLACE\n"


# ------------------------------------------------------------------ git
def test_checkpoint_y_revertir_deja_el_arbol_exacto(P):
    h = gitutil.checkpoint(P.raiz, "punto")
    (P.raiz / "base.py").write_text("x = 999\n", encoding="utf-8")
    (P.raiz / "basura.py").write_text("y\n", encoding="utf-8")
    assert gitutil.revertir_a(P.raiz, h)
    assert (P.raiz / "base.py").read_text(encoding="utf-8") == "x = 1\n"
    assert not (P.raiz / "basura.py").exists()


def test_sin_repo_el_checkpoint_no_hace_nada():
    with tempfile.TemporaryDirectory() as t:
        assert not gitutil.es_repo(t) or True      # un tmp podría estar dentro de otro repo: solo exigimos no romper
        if not gitutil.es_repo(t):
            assert gitutil.checkpoint(t, "x") is None


# ------------------------------------------------------------------ ejecutor
def test_ejecutor_corre_comandos_permitidos(P):
    r = ejecutor.ejecutar(P.raiz, f'{Path(sys.executable).name} -c "print(42)"', permitidos=[Path(sys.executable).name])
    assert r.ok and "42" in r.salida


@pytest.mark.parametrize("cmd", ["rm -rf /", "curl http://x", "git push origin main", "npm publish", "python a.py && rm x",
                                 "/usr/bin/python -c 1", "", "python 'sin cerrar"])
def test_ejecutor_rechaza_lo_peligroso(P, cmd):
    with pytest.raises(ejecutor.ComandoNoPermitido):
        ejecutor.ejecutar(P.raiz, cmd)


def test_ejecutor_timeout_y_codigo_de_error(P):
    py = Path(sys.executable).name
    assert not ejecutor.ejecutar(P.raiz, f'{py} -c "import sys; sys.exit(3)"', [py]).ok
    r = ejecutor.ejecutar(P.raiz, f'{py} -c "import time; time.sleep(5)"', [py], timeout=1)
    assert r.segundos_agotado and not r.ok


# ------------------------------------------------------------------ planificador
def test_extraer_json_tolera_texto_y_vallas():
    assert ag.extraer_json('Claro:\n```json\n{"a": {"b": "}"}}\n```\nlisto') == {"a": {"b": "}"}}
    assert ag.extraer_json("sin json") is None


def test_planificar_descarta_comandos_no_permitidos_y_tareas_vacias():
    resp = json.dumps({"tareas": [
        {"titulo": "A", "instruccion": "crear a.py", "verificacion": ["python -m pytest -q", "rm -rf /"]},
        {"titulo": "vacía", "instruccion": "  "}, "basura"]})
    t = ag.planificar("obj", lambda m: resp)
    assert len(t) == 1 and t[0].id == "T01" and t[0].verificacion == ["python -m pytest -q"]
    with pytest.raises(ValueError):
        ag.planificar("obj", lambda m: "no es json")


# ------------------------------------------------------------------ lazo del agente
PY = Path(sys.executable).name


def _tarea(contenido_check):
    return ag.Tarea(id="T01", titulo="crear mod", instruccion="creá mod.py",
                    verificacion=[f'{PY} -c "{contenido_check}"'])


def test_tarea_exitosa_escribe_verifica_y_commitea(P):
    t = _tarea("import mod; assert mod.v == 2")
    res = ag.ejecutar_plan(P, [t], lambda m: nuevo_archivo("mod.py", "v = 2\n"), permitidos=[PY], objetivo="o")
    assert res == "completo" and t.estado == ag.HECHA
    assert (P.raiz / "mod.py").read_text(encoding="utf-8") == "v = 2\n"
    plan = json.loads((P.raiz / ".nina" / "plan.json").read_text(encoding="utf-8"))
    assert plan["tareas"][0]["estado"] == "hecha"
    assert (P.raiz / ".nina" / "eventos.jsonl").is_file()


def test_verificacion_fallida_se_repara_con_la_salida_del_error(P):
    t = _tarea("import mod; assert mod.v == 2")
    vistos = []

    def llm(mensajes):
        vistos.append(mensajes[-1]["content"])
        if len(vistos) == 1:
            return nuevo_archivo("mod.py", "v = 1\n")
        return "mod.py\n<<<<<<< SEARCH\nv = 1\n=======\nv = 2\n>>>>>>> REPLACE\n"      # el archivo ya existe: edición real

    res = ag.ejecutar_plan(P, [t], llm, permitidos=[PY])
    assert res == "completo" and t.intentos == 2
    assert "FALLÓ" in vistos[1] and "AssertionError" in vistos[1]        # el segundo intento recibe el error real
    assert "v = 1" in vistos[1] or any("v = 1" in m for m in vistos[1:])   # y ve el archivo que él mismo escribió


def test_tarea_que_nunca_pasa_se_revierte_y_el_agente_se_detiene(P):
    malas = [_tarea("assert False") for _ in range(3)]
    for i, t in enumerate(malas):
        t.id = f"T0{i + 1}"
    res = ag.ejecutar_plan(P, malas, lambda m: nuevo_archivo("mod.py", "v = 1\n"),
                           ag.Presupuesto(reparaciones_por_tarea=1, fallidas_seguidas=2), permitidos=[PY])
    assert res == "detenido_por_fallos"
    assert [t.estado for t in malas] == [ag.FALLIDA, ag.FALLIDA, ag.PENDIENTE]
    assert not (P.raiz / "mod.py").exists()                       # revertido al checkpoint previo
    assert (P.raiz / "base.py").read_text(encoding="utf-8") == "x = 1\n"


def test_el_agente_no_escribe_fuera_del_proyecto(P):
    t = _tarea("pass")
    res = ag.ejecutar_plan(P, [t], lambda m: nuevo_archivo("../fuera.py", "x = 1\n"),
                           ag.Presupuesto(reparaciones_por_tarea=0, fallidas_seguidas=1), permitidos=[PY])
    assert res == "detenido_por_fallos" and not (P.raiz.parent / "fuera.py").exists()


def test_reanuda_saltando_las_tareas_hechas_y_respeta_el_tiempo(P):
    hecha = ag.Tarea(id="T01", titulo="h", instruccion="x", estado=ag.HECHA)
    pend = _tarea("pass")
    pend.id = "T02"
    llamadas = []
    reloj = iter([0, 99999])
    res = ag.ejecutar_plan(P, [hecha, pend], lambda m: llamadas.append(1) or "", ag.Presupuesto(segundos_totales=10),
                           permitidos=[PY], reloj=lambda: next(reloj))
    assert res == "sin_tiempo" and not llamadas


# ------------------------------------------------------------------ 5.2
def test_contexto_del_servidor_lee_props():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from nina import servidor

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            cuerpo = json.dumps({"default_generation_settings": {"n_ctx": 32768}}).encode()
            self.send_response(200 if self.path == "/props" else 404)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert servidor.contexto_del_servidor(f"http://127.0.0.1:{srv.server_port}/v1/chat/completions") == 32768
    finally:
        srv.shutdown()
    assert servidor.contexto_del_servidor("http://127.0.0.1:1/v1/chat/completions", timeout=0.5) is None


# ------------------------------------------------------------------ 5.4 / 5.7
def test_recomendaciones_y_resumen_de_impactos(P):
    from nina.contexto import CambioArchivo
    from nina.recomendaciones import recomendaciones, resumen_impactos
    r = recomendaciones(P, {}, 4, contexto_servidor=8000, max_tokens_prompt=7000)
    assert any("CONVENTIONS.md" in x for x in r) and any("mapa" in x for x in r) and any("poco margen" in x for x in r)
    (P.raiz / "CONVENTIONS.md").write_text("reglas", encoding="utf-8")
    assert recomendaciones(P, {"a": {}}, 1) == []
    lote = [CambioArchivo("a.py", "x=1\n", "\n", "x=2\n"), CambioArchivo("n.py", "", "\n", "x=1\n"),
            CambioArchivo("e.py", "x", "\n", None, errores=["mal"]), CambioArchivo("s.py", "x", "\n", "x")]
    assert resumen_impactos(lote) == ["✏️ a.py", "🆕 n.py", "⚠️ e.py", "⏸ s.py"]


def test_el_contexto_del_agente_incluye_los_archivos_de_la_tarea(P):
    """Regresión (prueba real): sin esto el modelo no veía calculadora.py y mandaba SEARCH vacío sobre un archivo existente."""
    from nina.contexto import preparar_contexto
    (P.raiz / "calc.py").write_text("def sumar(a, b):\n    return a + b\n", encoding="utf-8")
    sin = preparar_contexto(P, None, "agregá restar")
    con = preparar_contexto(P, None, "agregá restar", incluir=["calc.py", "no_existe.py"])
    assert "def sumar" not in "".join(m["content"] for m in sin.mensajes)
    assert "def sumar" in "".join(m["content"] for m in con.mensajes)


def test_python3_se_traduce_al_interprete_actual_y_acepta_permitidos_con_exe(P):
    r = ejecutor.ejecutar(P.raiz, 'python3 -c "print(7)"', permitidos=["python.exe"])
    assert r.ok and "7" in r.salida


def test_planificar_conserva_los_archivos_de_cada_tarea():
    resp = json.dumps({"tareas": [{"titulo": "A", "instruccion": "x", "archivos": ["calc.py", 3], "verificacion": []}]})
    assert ag.planificar("o", lambda m: resp)[0].archivos == ["calc.py"]


def test_en_windows_las_comillas_del_comando_no_llegan_a_python():
    """Regresión (prueba real en Windows): con las comillas literales `python -c "..."` no ejecutaba nada y daba éxito."""
    partes = ejecutor.dividir_comando('python -c "import sys; sys.exit(3)" tests\\x.py', windows=True)
    assert partes == ["python", "-c", "import sys; sys.exit(3)", "tests\\x.py"]
    assert ejecutor.dividir_comando("python -c 'print(1)'", windows=False) == ["python", "-c", "print(1)"]


def test_una_verificacion_que_realmente_falla_no_pasa_en_silencio(P):
    r = ejecutor.ejecutar(P.raiz, f'{PY} -c "import sys; sys.exit(3)"', [PY])
    assert not r.ok and r.codigo == 3


def test_verificacion_con_sintaxis_invalida_se_omite_y_se_avisa(P):
    t = ag.Tarea(id="T01", titulo="x", instruccion="creá mod.py",
                 verificacion=[f'{PY} -c "import mod; try: pass; except Exception: pass"', f'{PY} -c "import mod; assert mod.v == 2"'])
    res = ag.ejecutar_plan(P, [t], lambda m: nuevo_archivo("mod.py", "v = 2\n"), permitidos=[PY])
    assert res == "completo" and "omitida" in t.detalle
    # ...pero la verificación válida sigue mandando: si el código está mal, la tarea no pasa
    t2 = ag.Tarea(id="T02", titulo="y", instruccion="creá mod2.py", verificacion=[f'{PY} -c "import mod2; assert mod2.v == 2"'])
    res = ag.ejecutar_plan(P, [t2], lambda m: nuevo_archivo("mod2.py", "v = 1\n"),
                           ag.Presupuesto(reparaciones_por_tarea=0, fallidas_seguidas=1), permitidos=[PY])
    assert res == "detenido_por_fallos" and t2.estado == ag.FALLIDA
