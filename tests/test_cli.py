"""
Fase 0.2 — Los comandos del CLI se pueden llamar como funciones normales: se simula el modelo y la confirmación del usuario.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nina import cli  # noqa: E402

RESPUESTA = "<<<<<<< SEARCH\nb = 2\n=======\nb = 20\n>>>>>>> REPLACE"
ORIGINAL = "a = 1\nb = 2\nc = 3\n"


class Simulacion:
    """Reemplaza al modelo y a `typer.confirm` mientras dura el test."""
    def __init__(self, confirma: bool):
        self.confirma, self.enviados = confirma, []

    def __enter__(self):
        self._llm, self._conf = cli.llamar_llm, cli.typer.confirm
        cli.llamar_llm = lambda mensajes: (self.enviados.append(mensajes), RESPUESTA)[1]
        cli.typer.confirm = lambda *a, **k: self.confirma
        return self

    def __exit__(self, *a):
        cli.llamar_llm, cli.typer.confirm = self._llm, self._conf
        return False


def _proyecto(d):
    raiz = Path(d).resolve()
    (raiz / "a.py").write_text(ORIGINAL, encoding="utf-8")
    return raiz


def test_refactor_aplica_con_backup_y_deshacer_lo_revierte():
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        raiz = _proyecto(d)
        with Simulacion(confirma=True):
            cli.refactor("a.py", "cambiá b", False, str(raiz))
            assert (raiz / "a.py").read_text(encoding="utf-8") == "a = 1\nb = 20\nc = 3\n"
            assert len(list((raiz / ".ai_backups").glob("*.bak"))) == 1
            cli.deshacer("a.py", str(raiz))
            assert (raiz / "a.py").read_text(encoding="utf-8") == ORIGINAL
    assert os.getcwd() == cwd


def test_refactor_rechazado_no_toca_nada():
    with tempfile.TemporaryDirectory() as d:
        raiz = _proyecto(d)
        with Simulacion(confirma=False):
            cli.refactor("a.py", "cambiá b", False, str(raiz))
        assert (raiz / "a.py").read_text(encoding="utf-8") == ORIGINAL
        assert not (raiz / ".ai_backups").exists()


def test_fix_detecta_el_archivo_y_la_linea_y_los_manda_al_modelo():
    with tempfile.TemporaryDirectory() as d:
        raiz = _proyecto(d)
        (raiz / ".ai_map.json").write_text(json.dumps(
            {"version": 2, "files": {"a.py": {"signatures": ["x"], "imports": []}}}), encoding="utf-8")
        traza = 'Traceback (most recent call last):\n  File "a.py", line 2, in <module>\n    b = 2 / 0\nZeroDivisionError'
        with Simulacion(confirma=True) as sim:
            cli.fix(traza, False, None, False, str(raiz))
        usuario = sim.enviados[0][1]["content"]
        assert '<archivo ruta="a.py">' in usuario and "línea 2" in usuario and "2: b = 2" in usuario
        assert (raiz / "a.py").read_text(encoding="utf-8") == "a = 1\nb = 20\nc = 3\n"


def test_proyecto_inexistente_sale_con_error_limpio():
    try:
        cli.refactor("a.py", "x", False, "/no/existe/seguro")
    except Exception as e:  # typer.Exit
        assert getattr(e, "code", getattr(e, "exit_code", 1)) == 1
    else:
        raise AssertionError("debió salir con error")
