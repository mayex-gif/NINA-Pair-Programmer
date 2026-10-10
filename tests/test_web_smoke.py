"""
Smoke test de web.py con un Streamlit simulado (tests/fake_streamlit.py): ejecuta el script real de punta a punta
—Modo Global, generar, razonamiento persistente, reintentos, aplicar, restaurar— y comprueba que no cambia
el directorio del proceso (Fase 0.2).

Como en Streamlit, un `st.rerun()` corta la ejecución (acá: "rerun") y la siguiente ejecución arranca con
`session_state` intacto y sin clicks.
"""
import os
import runpy
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_streamlit as fs  # noqa: E402
from nina import llm  # noqa: E402

BUENA = "<<<<<<< SEARCH\nb = 2\n=======\nb = 20\n>>>>>>> REPLACE"
MALA = "<<<<<<< SEARCH\nzzz\n=======\nq\n>>>>>>> REPLACE"


def _ejecutar(ss, control):
    restaurar = fs.instalar(control, ss)
    try:
        try:
            runpy.run_path(str(RAIZ / "web.py"), run_name="__main__")
            return "fin"
        except fs.StopApp:
            return "stop"
        except fs.RerunApp:
            return "rerun"
    finally:
        restaurar()


def _llm_falso(respuestas):
    """Modelo simulado: devuelve las respuestas en orden (la última se repite) y razona un poco."""
    it = iter(respuestas)
    ultima = {"r": respuestas[-1]}

    def f(mensajes, on_fragmento=None, on_pensamiento=None, url=None, modelo=None, on_estadisticas=None):
        r = next(it, ultima["r"])
        if on_pensamiento:
            on_pensamiento("pienso que hay que cambiar b")
        if on_fragmento:
            on_fragmento(r)
        if on_estadisticas:
            on_estadisticas({"tokens_entrada": 10, "tokens_salida": 5, "tps": 1.0, "seg_total": 0.1, "seg_primer_token": 0.1})
        return r
    return f


class Escenario:
    """Proyecto temporal con a.py + modelo simulado; restaura todo al salir."""
    def __init__(self, respuestas):
        self.respuestas = respuestas

    def __enter__(self):
        self.cwd, self.original = os.getcwd(), llm.stream_llm
        llm.stream_llm = _llm_falso(self.respuestas)
        self.tmp = tempfile.TemporaryDirectory()
        self.raiz = Path(self.tmp.name).resolve()
        self.archivo = self.raiz / "a.py"
        self.archivo.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
        self.ss = fs.SS(proyecto=str(self.raiz), campo_proyecto=str(self.raiz))
        self.control = fs.Control()
        return self

    def generar(self, instruccion="cambiá b"):
        self.control.instruccion, self.control.clicks = instruccion, {"Generar"}
        r = _ejecutar(self.ss, self.control)
        self.control.clicks = set()
        return r

    def click(self, *botones):
        self.control.clicks = set(botones)
        r = _ejecutar(self.ss, self.control)
        self.control.clicks = set()
        return r

    def __exit__(self, *a):
        llm.stream_llm = self.original
        os.chdir(self.cwd)
        self.tmp.cleanup()
        return False


def test_flujo_completo_de_la_web():
    with Escenario([BUENA]) as e:
        cwd = os.getcwd()

        # 1) sin archivo elegido: Modo Global (no se detiene, avisa que se puede pedir sobre todo el proyecto)
        assert _ejecutar(e.ss, e.control) == "fin"
        assert any(t == "info" and "Modo Global" in x for t, x in e.control.log)

        # 2) generar: termina con un rerun y queda todo en sesión; el disco sigue intacto
        e.ss["archivo_sel"] = "a.py"
        assert e.generar() == "rerun"
        assert e.ss.resultado.cambios[0].nuevo == "a = 1\nb = 20\nc = 3\n"
        assert e.archivo.read_text(encoding="utf-8") == "a = 1\nb = 2\nc = 3\n"
        assert e.ss.ruta == e.archivo
        # el razonamiento y las métricas sobreviven al rerun (antes se perdían con los widgets en vivo)
        assert [r["pens"] for r in e.ss.traza] == ["pienso que hay que cambiar b"]
        assert e.ss.metricas["intentos"] == 1 and e.ss.metricas["exactas"] is True

        # 3) cualquier rerun posterior (sin clicks) conserva traza y propuesta
        assert _ejecutar(e.ss, e.control) == "fin"
        assert e.ss.traza and e.ss.resultado is not None

        # 4) aplicar: escribe, deja un lote de backup y pide rerun; el razonamiento sigue ahí
        assert e.click("Aplicar y guardar todo") == "rerun"
        assert e.archivo.read_text(encoding="utf-8") == "a = 1\nb = 20\nc = 3\n"
        assert len(list((e.raiz / ".ai_backups").rglob("*.bak"))) == 1 and e.ss.guardado is True
        assert e.ss.ultimo_lote and e.ss.traza

        # 5) tras el rerun se muestra el mensaje de guardado
        e.control.log = []
        _ejecutar(e.ss, e.control)
        assert any(t == "success" and "guardado" in x for t, x in e.control.log)

        # 6) restaurar el lote deja el archivo como estaba y limpia la vista
        assert e.click("Restaurar transacción") == "rerun"
        assert e.archivo.read_text(encoding="utf-8") == "a = 1\nb = 2\nc = 3\n"
        assert e.ss.resultado is None and e.ss.traza is None
        assert os.getcwd() == cwd                          # la web no cambia el directorio del proceso


def test_restaurar_funciona_en_modo_global():
    """Deuda técnica #2: sin archivo seleccionado también se puede deshacer el último lote."""
    with Escenario([BUENA]) as e:
        e.ss["archivo_sel"] = "a.py"
        e.generar()
        assert e.click("Aplicar y guardar todo") == "rerun"
        e.ss["archivo_sel"] = None                         # Modo Global
        assert e.click("Restaurar transacción") == "rerun"
        assert e.archivo.read_text(encoding="utf-8") == "a = 1\nb = 2\nc = 3\n"


def test_reintentos_quedan_registrados_por_intento():
    with Escenario([MALA, BUENA]) as e:
        e.ss["archivo_sel"] = "a.py"
        assert e.generar() == "rerun"
        assert len(e.ss.traza) == 2
        primero, segundo = e.ss.traza
        assert primero["errores"] and not segundo["errores"]
        # el error es el mensaje real, no la ruta del archivo (generar_cambio informa {ruta: [errores]})
        assert "SEARCH" in primero["errores"][0] and primero["errores"][0].startswith("a.py:")
        assert primero["resp"] == MALA and segundo["resp"] == BUENA
        assert all(r["stats"]["tokens_salida"] == 5 for r in e.ss.traza)
        assert e.ss.metricas["intentos"] == 2 and e.ss.metricas["entrada"] == 20
        assert e.ss.resultado.es_valida


def test_error_del_servidor_se_conserva_tras_el_rerun():
    with Escenario([BUENA]) as e:
        def caido(*a, **k):
            raise llm.ErrorLLM("No pude conectar")
        llm.stream_llm = caido
        e.ss["archivo_sel"] = "a.py"
        assert e.generar() == "rerun"
        assert e.ss.error_llm == "No pude conectar"
        e.control.log = []
        _ejecutar(e.ss, e.control)
        assert any(t == "error" and "No pude conectar" in x for t, x in e.control.log)


def test_la_web_no_se_aplica_si_el_archivo_cambio_en_disco():
    with Escenario([BUENA]) as e:
        e.ss["archivo_sel"] = "a.py"
        e.generar()
        e.archivo.write_text("EDITADO A MANO\n", encoding="utf-8")
        assert e.click("Aplicar y guardar todo") == "fin"
        assert e.archivo.read_text(encoding="utf-8") == "EDITADO A MANO\n"
        assert not (e.raiz / ".ai_backups").exists()
        assert any(t == "error" and "Conflicto" in x for t, x in e.control.log)
