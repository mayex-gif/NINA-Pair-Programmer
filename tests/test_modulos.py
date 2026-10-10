"""
Fase 0.2 — Tests de lo que antes NO se podía testear: Proyecto sin cwd, análisis de trazas, diff de la web,
preparación del contexto y la importación de las interfaces (CLI y shims de compatibilidad).
(Incluye también pruebas de la Fase 4.1 para el motor de parsing multi-archivo en bloques.py).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nina import bloques as bl  # noqa: E402
from nina import contexto as cx  # noqa: E402
from nina import diff as dif  # noqa: E402
from nina import trazas as tz  # noqa: E402
from nina.proyecto import Proyecto  # noqa: E402


def _raiz():
    return tempfile.TemporaryDirectory()


# ------------------------------------------------------------------ Proyecto
def test_proyecto_no_depende_ni_toca_el_cwd():
    cwd = os.getcwd()
    with _raiz() as a, _raiz() as b:
        PA, PB = Proyecto(a), Proyecto(b)
        (PA.raiz / "x.py").write_text("a\n", encoding="utf-8")
        (PB.raiz / "x.py").write_text("b\n", encoding="utf-8")
        assert PA.abs("x.py") != PB.abs("x.py")           # dos proyectos a la vez, sin pisarse
        assert PA.clave("x.py") == PB.clave("x.py") == "x.py"
        assert PA.ultimo_backup("x.py") is None
        PA.hacer_backup("x.py")
        assert PA.ultimo_backup("x.py") is not None and PB.ultimo_backup("x.py") is None
    assert os.getcwd() == cwd


def test_clave_y_abs():
    with _raiz() as d:
        P = Proyecto(d)
        sub = P.raiz / "src" / "a.js"
        assert P.clave(sub) == "src/a.js" and P.clave("src/a.js") == "src/a.js"
        assert P.abs("src/a.js") == sub and P.abs(sub) == sub
        assert P.clave("/otra/carpeta/x.js").endswith("otra/carpeta/x.js")  # fuera de la raíz: queda tal cual


def test_convenciones_y_git_sin_repo():
    with _raiz() as d:
        P = Proyecto(d)
        assert P.cargar_convenciones() == ""
        (P.raiz / "CONVENTIONS.md").write_text("  # reglas  \n", encoding="utf-8")
        assert P.cargar_convenciones() == "# reglas"
        (P.raiz / "a.py").write_text("x\n", encoding="utf-8")
        assert P.git_archivo_sucio("a.py") is False


def test_preparar_contexto_de_punta_a_punta():
    with _raiz() as d:
        P = Proyecto(d)
        (P.raiz / "src").mkdir()
        (P.raiz / "src" / "App.jsx").write_text("import U from './U.jsx'\nconst App = () => null\n", encoding="utf-8")
        (P.raiz / "CONVENTIONS.md").write_text("Usá hooks.", encoding="utf-8")
        P.ruta_mapa.write_text(json.dumps({"version": 2, "files": {
            "src/App.jsx": {"signatures": ["const App = () =>"], "imports": ["./U.jsx"]},
            "src/U.jsx": {"signatures": ["const U = () =>"], "imports": []},
        }}), encoding="utf-8")
        ctx = cx.preparar_contexto(P, "src/App.jsx", "cambiá el color")
        assert ctx.clave == "src/App.jsx" and ctx.ruta == P.raiz / "src" / "App.jsx"
        assert ctx.hay_convenciones and ctx.mapa_usados == 1 and ctx.mapa_total == 2
        sistema, usuario = ctx.mensajes[0]["content"], ctx.mensajes[1]["content"]
        assert "Usá hooks." in sistema and "# src/U.jsx" in sistema
        assert '<archivo ruta="src/App.jsx">' in usuario and "Instrucción: cambiá el color" in usuario
        assert cx.preparar_contexto(P, "src/App.jsx", "x", sin_mapa=True).mapa_usados == 0


# ------------------------------------------------------------------ Multi-archivo (Fase 4.1)
def test_extraer_ruta_ignora_explicaciones():
    texto = """Claro, acá tenés la solución.

En el archivo backend:
`src/Main.java`
"""
    assert bl._extraer_ruta(texto, "default") == "src/Main.java"
    
def test_interpretar_lote_detecta_multiples_archivos():
    respuesta = """
src/app.js
<<<<<<< SEARCH
console.log(1)
=======
console.log(2)
>>>>>>> REPLACE

Acá hay basura que tira el LLM. Y luego otro archivo:

backend/Main.java
<<<<<<< SEARCH
=======
// Nuevo archivo Java
>>>>>>> REPLACE
"""
    originales = {"src/app.js": "console.log(1)\n", "backend/Main.java": ""}
    nuevos, errs, avisos = bl.interpretar_respuesta_lote(respuesta, originales, "defecto")
    
    assert not errs
    assert "src/app.js" in nuevos and "console.log(2)" in nuevos["src/app.js"]
    assert "backend/Main.java" in nuevos and "// Nuevo archivo Java" in nuevos["backend/Main.java"]
    assert avisos.get("backend/Main.java") == ["Archivo creado desde cero."]

def test_interpretar_lote_falla_si_busca_vacio_en_existente():
    respuesta = """
src/app.js
<<<<<<< SEARCH
=======
// Esto sobreescribe el archivo!
>>>>>>> REPLACE
"""
    originales = {"src/app.js": "contenido existente\n"}
    nuevos, errs, avisos = bl.interpretar_respuesta_lote(respuesta, originales, "src/app.js")
    
    assert "src/app.js" in errs
    assert any("SEARCH vacío" in e for e in errs["src/app.js"])


# ------------------------------------------------------------------ trazas (antes dentro del comando `fix`)
MAPA = {
    "backend/src/main/java/com/lwt/service/UserService.java": {},
    "frontend/src/App.jsx": {},
    "app/main.py": {},
    "app/services/user.py": {},
}
JAVA = ("java.lang.NullPointerException\n"
        "\tat com.lwt.service.UserService.findAll(UserService.java:42)\n"
        "\tat org.springframework.aop.Foo.invoke(Foo.java:10)\n"
        "\tat com.lwt.web.Other.call(App.jsx:7)")
PY = ('Traceback (most recent call last):\n'
      '  File "app/main.py", line 10, in <module>\n    run()\n'
      '  File "app/services/user.py", line 33, in run\n    1/0')


def test_analizar_traza_java_toma_el_primer_archivo_del_proyecto():
    objetivo, linea, otros = tz.analizar_traza(JAVA, MAPA)
    assert objetivo == "backend/src/main/java/com/lwt/service/UserService.java" and linea == 42
    assert otros == ["frontend/src/App.jsx"]          # Foo.java no está en el mapa: se ignora


def test_analizar_traza_python_prioriza_el_ultimo_frame():
    objetivo, linea, otros = tz.analizar_traza(PY, MAPA)
    assert (objetivo, linea, otros) == ("app/services/user.py", 33, ["app/main.py"])


def test_analizar_traza_ignora_node_modules_y_sin_coincidencias():
    traza = "Error\n    at x (/app/node_modules/react/index.js:3:1)\n    at y (/lib/otro.js:1:1)"
    assert tz.analizar_traza(traza, MAPA) == (None, None, [])


def test_resolver_usa_el_disco_si_el_archivo_no_esta_en_el_mapa():
    with _raiz() as d:
        P = Proyecto(d)
        (P.raiz / "src").mkdir()
        (P.raiz / "src" / "nuevo.js").write_text("x\n", encoding="utf-8")
        assert tz.resolver_en_proyecto(str(P.raiz / "src" / "nuevo.js"), {}, P) == "src/nuevo.js"
        assert tz.resolver_en_proyecto("src/inexistente.js", {}, P) is None


def test_fragmento_alrededor():
    texto = "\n".join(f"l{i}" for i in range(1, 21))
    fr = tz.fragmento_alrededor(texto, 10).split("\n")
    assert fr[0] == "5: l5" and fr[-1] == "15: l15" and len(fr) == 11
    assert tz.fragmento_alrededor(texto, 1).split("\n")[0] == "1: l1"
    assert tz.fragmento_alrededor(texto, 20).split("\n")[-1] == "20: l20"


# ------------------------------------------------------------------ diff (antes dentro de web.py)
def test_filas_alineadas_y_colapso_de_una_linea_en_40():
    orig = "\n".join(f"linea {i}" for i in range(1, 41)) + "\n"
    nuevo = orig.replace("linea 20\n", "linea 20 CAMBIADA\n")
    filas = dif.filas_alineadas(orig, nuevo)
    assert len(filas) == 40
    cambiada = [f for f in filas if f[2] or f[5]]
    assert len(cambiada) == 1 and cambiada[0][2] == "del" and cambiada[0][5] == "add"
    col = dif.colapsar(filas)
    assert [f[1] for f in col if f[2] == "sep"] == ["⋯ 16 líneas sin cambios ⋯", "⋯ 17 líneas sin cambios ⋯"]
    assert len(col) == 2 + 7                              # 3 de contexto por lado + la línea cambiada + 2 separadores


def test_filas_alineadas_archivo_nuevo_todo_agregado():
    filas = dif.filas_alineadas("", "a\nb\n")
    assert [f[5] for f in filas] == ["add", "add"] and all(f[2] == "vacio" for f in filas)


# ------------------------------------------------------------------ interfaces
def test_cli_se_importa_y_expone_los_comandos():
    from nina import cli
    for nombre in ("refactor", "fix", "deshacer", "probar", "ejecutar", "mostrar_diff"):
        assert hasattr(cli, nombre)


def test_shims_de_compatibilidad():
    import frontend
    import generar_mapa
    from nina import cli, vigia
    assert frontend.app is cli.app and generar_mapa.main is vigia.main


def test_nucleo_no_importa_ui():
    """
    El núcleo no debe importar typer/rich/streamlit/watchdog (solo cli.py, vigia.py y la web).
    Se revisan los imports de nuestros propios módulos (no sys.modules): `httpx` puede traer `rich`
    por su cuenta si `click` está instalado, y eso no es culpa del núcleo.
    """
    import ast
    prohibidos = {"typer", "rich", "streamlit", "watchdog", "click"}
    carpeta = Path(__file__).resolve().parent.parent / "nina"
    nucleo = ["config", "proyecto", "bloques", "contexto", "trazas", "diff", "mapa", "llm"]
    for nombre in nucleo:
        arbol = ast.parse((carpeta / f"{nombre}.py").read_text(encoding="utf-8"))
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Import):
                modulos = [a.name.split(".")[0] for a in nodo.names]
            elif isinstance(nodo, ast.ImportFrom) and nodo.level == 0 and nodo.module:
                modulos = [nodo.module.split(".")[0]]
            else:
                continue
            assert not (set(modulos) & prohibidos), f"nina/{nombre}.py importa {set(modulos) & prohibidos}"