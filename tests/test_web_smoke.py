"""
Smoke test de web.py con un Streamlit simulado (tests/fake_streamlit.py): ejecuta el script real de punta a punta
—elegir archivo, generar, aplicar, restaurar— y comprueba que no cambia el directorio del proceso (Fase 0.2).
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

RESPUESTA = "<<<<<<< SEARCH\nb = 2\n=======\nb = 20\n>>>>>>> REPLACE"


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


def _llm_falso(mensajes, on_fragmento=None, on_pensamiento=None, url=None, modelo=None, on_estadisticas=None):
    if on_fragmento:
        on_fragmento(RESPUESTA)
    if on_estadisticas:
        on_estadisticas({"tokens_entrada": 10, "tokens_salida": 5, "tps": 1.0, "seg_total": 0.1, "seg_primer_token": 0.1})
    return RESPUESTA


def test_flujo_completo_de_la_web():
    cwd = os.getcwd()
    original_llm = llm.stream_llm
    llm.stream_llm = _llm_falso
    try:
        with tempfile.TemporaryDirectory() as d:
            raiz = Path(d).resolve()
            archivo = raiz / "a.py"
            archivo.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
            ss = fs.SS(proyecto=str(raiz), campo_proyecto=str(raiz))
            control = fs.Control()

            # 1) sin archivo elegido: pide elegir uno y se detiene
            assert _ejecutar(ss, control) == "stop"
            assert any("Elegí un archivo" in t for _, t in control.log)

            # 2) generar: queda una propuesta en sesión y el disco sigue intacto
            ss["archivo_sel"] = "a.py"
            control.instruccion, control.clicks = "cambiá b", {"Generar"}
            assert _ejecutar(ss, control) == "fin"
            assert ss.resultado.cambios[0].nuevo == "a = 1\nb = 20\nc = 3\n"
            assert archivo.read_text(encoding="utf-8") == "a = 1\nb = 2\nc = 3\n"
            assert ss.ruta == archivo

            # 3) aplicar: escribe, deja backup y pide rerun
            control.clicks = {"Aplicar y guardar"}
            assert _ejecutar(ss, control) == "rerun"
            assert archivo.read_text(encoding="utf-8") == "a = 1\nb = 20\nc = 3\n"
            assert len(list((raiz / ".ai_backups").rglob("*.bak"))) == 1 and ss.guardado is True

            # 4) tras el rerun se muestra el mensaje de guardado
            control.clicks, control.log = set(), []
            _ejecutar(ss, control)
            assert any(t == "success" and "a.py guardado" in x for t, x in [(a, b) for a, b in control.log])

            # 5) restaurar el backup deja el archivo como estaba
            control.clicks = {"Restaurar este backup"}
            assert _ejecutar(ss, control) == "rerun"
            assert archivo.read_text(encoding="utf-8") == "a = 1\nb = 2\nc = 3\n"
        assert os.getcwd() == cwd                          # la web ya no cambia el directorio del proceso
    finally:
        llm.stream_llm = original_llm
        os.chdir(cwd)


def test_la_web_no_se_aplica_si_el_archivo_cambio_en_disco():
    cwd = os.getcwd()
    original_llm = llm.stream_llm
    llm.stream_llm = _llm_falso
    try:
        with tempfile.TemporaryDirectory() as d:
            raiz = Path(d).resolve()
            archivo = raiz / "a.py"
            archivo.write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
            ss = fs.SS(proyecto=str(raiz), campo_proyecto=str(raiz), archivo_sel="a.py")
            control = fs.Control()
            control.instruccion, control.clicks = "cambiá b", {"Generar"}
            _ejecutar(ss, control)
            archivo.write_text("EDITADO A MANO\n", encoding="utf-8")
            control.clicks = {"Aplicar y guardar"}
            assert _ejecutar(ss, control) == "fin"
            assert archivo.read_text(encoding="utf-8") == "EDITADO A MANO\n"
            assert not (raiz / ".ai_backups").exists()
    finally:
        llm.stream_llm = original_llm
        os.chdir(cwd)
