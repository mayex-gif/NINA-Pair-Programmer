"""
Fase 0.1 — Tests de caracterización del núcleo actual (frontend.py + generar_mapa.py).

Fijan el comportamiento que HOY funciona, para poder refactorizar (Fase 0.2 en adelante) sin romperlo.
Los tests marcados `xfail(strict=True)` documentan huecos conocidos: cuando se arregle el hueco el test
pasa a "XPASS" y falla a propósito, avisando que hay que quitarle la marca.

Ejecutar desde la raíz del repo:   pytest -q
"""
import contextlib
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import frontend as f  # noqa: E402
import generar_mapa as gm  # noqa: E402


@contextlib.contextmanager
def en_tmp():
    """El código actual depende del cwd (deuda técnica #1); por eso cada test trabaja en una carpeta temporal."""
    viejo = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        try:
            yield Path(d)
        finally:
            os.chdir(viejo)


ORIG = "a = 1\nb = 2\nc = 3\n"


def bloque(buscar, reemplazar):
    return f"<<<<<<< SEARCH\n{buscar}\n=======\n{reemplazar}\n>>>>>>> REPLACE"


# ----------------------------------------------------------------- aplicar_bloques
def test_reemplazo_simple():
    nuevo, err = f.aplicar_bloques(ORIG, [("b = 2", "b = 20")])
    assert err == [] and nuevo == "a = 1\nb = 20\nc = 3\n"


def test_search_ambiguo_es_error():
    nuevo, err = f.aplicar_bloques("x\nx\n", [("x", "y")])
    assert len(err) == 1 and "2 veces" in err[0]


def test_search_inexistente_da_pista_de_lineas_parecidas():
    orig = "def hola():\n    return 1\n"
    _, err = f.aplicar_bloques(orig, [("def hola():\n    return 2", "x")])
    assert len(err) == 1 and "no coincide" in err[0] and "return 1" in err[0]


def test_reintento_tolerante_a_espacios_al_final_de_linea():
    nuevo, err = f.aplicar_bloques("foo()  \nbar()\n", [("foo()\nbar()", "baz()")])
    assert err == [] and nuevo == "baz()\n"


def test_borrado_no_deja_linea_vacia():
    nuevo, err = f.aplicar_bloques("a\nb\nc\n", [("b", "")])
    assert err == [] and nuevo == "a\nc\n"


def test_varios_bloques_en_orden():
    nuevo, err = f.aplicar_bloques(ORIG, [("a = 1", "a = 10"), ("c = 3", "c = 30")])
    assert err == [] and nuevo == "a = 10\nb = 2\nc = 30\n"


def test_los_bloques_se_aplican_en_cadena_no_contra_el_original():
    # Caracterización: el bloque 2 puede apuntar a texto que produjo el bloque 1.
    # Importa para la Fase 4.2: por eso los "hunks" se calculan con difflib y no por bloque.
    nuevo, err = f.aplicar_bloques(ORIG, [("b = 2", "b = 20"), ("b = 20", "b = 21")])
    assert err == [] and "b = 21" in nuevo


# ----------------------------------------------------------------- interpretar_respuesta
def test_interpreta_search_replace():
    nuevo, err, av = f.interpretar_respuesta(bloque("b = 2", "b = 20"), ORIG)
    assert nuevo == "a = 1\nb = 20\nc = 3\n" and not err and not av


def test_todo_o_nada_si_un_bloque_falla():
    resp = bloque("b = 2", "b = 20") + "\n" + bloque("no existe", "x")
    nuevo, err, _ = f.interpretar_respuesta(resp, ORIG)
    assert nuevo is None and len(err) == 1


def test_fallback_archivo_completo_en_bloque_de_codigo_avisa():
    nuevo, err, av = f.interpretar_respuesta("```python\na = 9\n```", ORIG)
    assert nuevo == "a = 9\n" and not err and av


def test_sin_bloques_ni_codigo_no_aplica_nada():
    nuevo, err, _ = f.interpretar_respuesta("Claro, te ayudo con eso.", ORIG)
    assert nuevo is None and err


def test_archivo_vacio_acepta_bloque_de_codigo():
    nuevo, err, av = f.interpretar_respuesta("```js\nconst a = 1\n```", "")
    assert nuevo == "const a = 1\n" and not err and av == ["Archivo creado desde cero."]


@pytest.mark.xfail(strict=True, reason="Deuda #6 / Fase 0.6: en archivo vacío se acepta cualquier texto como código")
def test_archivo_vacio_no_acepta_texto_conversacional():
    nuevo, _, _ = f.interpretar_respuesta("Claro, acá tenés el archivo que me pediste.", "")
    assert nuevo is None


# ----------------------------------------------------------------- auto-healing (generar_cambio)
def _ctx(original):
    return f.Contexto(
        ruta=Path("a.py"), original=original, eol="\n",
        mensajes=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        tokens=1, mapa_usados=0, mapa_total=0, hay_convenciones=False,
    )


def test_auto_healing_corrige_en_el_segundo_intento():
    respuestas = iter([bloque("zzz", "q"), bloque("b = 2", "b = 20")])
    largos = []

    def llamar(mensajes):
        largos.append(len(mensajes))
        return next(respuestas)

    res = f.generar_cambio(_ctx(ORIG), llamar)
    assert res.nuevo == "a = 1\nb = 20\nc = 3\n" and res.intentos == 2
    assert largos == [2, 4]  # el reintento suma la respuesta fallida + el mensaje de error


def test_auto_healing_se_rinde_tras_max_intentos():
    res = f.generar_cambio(_ctx(ORIG), lambda m: bloque("zzz", "q"))
    assert res.nuevo is None and res.intentos == f.MAX_INTENTOS and res.errores


def test_respuesta_vacia_no_se_reintenta():
    res = f.generar_cambio(_ctx(ORIG), lambda m: "")
    assert res.nuevo is None and res.intentos == 1 and res.errores


# ----------------------------------------------------------------- mapa y relevancia
MAPA = {
    "frontend/src/App.jsx": {"signatures": ["const App = () =>"], "imports": ["./components/UserCard.jsx", "react"]},
    "frontend/src/components/UserCard.jsx": {"signatures": ["const UserCard = ({ user }) =>"], "imports": []},
    "backend/src/main/java/com/lwt/service/OrderService.java": {
        "signatures": ["@Service public class OrderService", "  public List<Order> findAll()"],
        "imports": ["com.lwt.repo.OrderRepository"],
    },
}
APP = Path("frontend/src/App.jsx")


def test_mapa_incluye_lo_importado_y_excluye_spring_no_relacionado():
    txt, usados, total = f.filtrar_mapa(MAPA, APP, "cambiá el color del botón")
    assert "UserCard.jsx" in txt and "OrderService" not in txt and total == 3 and usados == 1


def test_mapa_identificadores_en_comun_si_pueden_colar_backend():
    # Caracterización: el ranking es heurístico. Si la instrucción nombra algo del backend, entra.
    txt, _, _ = f.filtrar_mapa(MAPA, APP, "mostrá la lista que devuelve findAll de Order")
    assert "OrderService" in txt


def test_import_a_stem():
    assert f._import_a_stem("./components/UserCard.jsx") == "UserCard"
    assert f._import_a_stem("com.lwt.service.UserService") == "UserService"
    assert f._import_a_stem("com.lwt.model.*") is None
    assert f._import_a_stem(".models") == "models"


def test_import_a_stem_colisiona_en_index():
    # Deuda #9 / Fase 4.5: dos módulos distintos producen el mismo stem. Cuando exista el resolutor real, reemplazar.
    assert f._import_a_stem("./a/index.js") == f._import_a_stem("./b/index.js") == "index"


def test_mensaje_system_ordena_estatico_primero():
    msgs = f.construir_mensajes(Path("x.py"), "code", "haz algo", "CONV", "MAPA")
    sistema = msgs[0]["content"]
    assert sistema.index("Sos un asistente") < sistema.index("Convenciones") < sistema.index("Mapa de archivos")


def test_mapa_v1_se_sigue_leyendo():
    with en_tmp():
        Path(".ai_map.json").write_text(json.dumps({"a.js": ["f()", "g()"], "b.js": "h()"}), encoding="utf-8")
        mapa = f.cargar_mapa()
    assert mapa["a.js"] == {"signatures": ["f()", "g()"], "imports": []}
    assert mapa["b.js"]["signatures"] == ["h()"]


# ----------------------------------------------------------------- trazas (checklist: fix)
JAVA = ("java.lang.NullPointerException\n"
        "\tat com.lwt.service.UserService.findAll(UserService.java:42)\n"
        "\tat org.springframework.aop.Foo.invoke(Foo.java:10)")
NODE = ("TypeError: x is undefined\n"
        "    at handler (C:\\proj\\frontend\\src\\App.jsx:15:9)\n"
        "    at Object.<anonymous> (/app/node_modules/react/index.js:3:1)")
BROWSER = "at render (http://localhost:5173/src/components/UserCard.jsx:20:11)"
BROWSER_VITE = "at render (http://localhost:5173/src/components/UserCard.jsx?t=1723456:20:11)"
PY = ('Traceback (most recent call last):\n'
      '  File "app/main.py", line 10, in <module>\n    run()\n'
      '  File "app/services/user.py", line 33, in run\n    1/0')


def test_traza_java():
    assert f.PATRON_TRAZA.findall(JAVA)[0] == ("UserService.java", "42")


def test_traza_node_con_ruta_windows():
    assert f.PATRON_TRAZA.findall(NODE)[0] == ("C:\\proj\\frontend\\src\\App.jsx", "15")


def test_traza_url_de_navegador():
    assert f.PATRON_TRAZA.findall(BROWSER)[0][1] == "20"


@pytest.mark.xfail(strict=True, reason="Vite agrega ?t=<timestamp> a las URLs de HMR y el patrón actual no lo contempla")
def test_traza_url_de_navegador_con_query_de_vite():
    assert f.PATRON_TRAZA.findall(BROWSER_VITE)[0][1] == "20"


def test_traza_python_orden_original():
    assert f.PATRON_TRAZA_PY.findall(PY) == [("app/main.py", "10"), ("app/services/user.py", "33")]


def test_resolver_en_proyecto():
    assert f._resolver_en_proyecto("C:\\proj\\frontend\\src\\App.jsx", MAPA) == "frontend/src/App.jsx"
    assert f._resolver_en_proyecto("/app/node_modules/react/index.js", MAPA) is None


def test_resolver_en_proyecto_desempata_por_sufijo_comun():
    mapa = {"web/src/index.js": {}, "api/src/index.js": {}}
    assert f._resolver_en_proyecto("/home/u/api/src/index.js", mapa) == "api/src/index.js"


# ----------------------------------------------------------------- archivos, EOL y backups
def test_eol_crlf_se_preserva_en_ida_y_vuelta():
    with en_tmp():
        p = Path("a.txt")
        f.escribir_archivo(p, "a\nb\n", "\r\n")
        assert p.read_bytes() == b"a\r\nb\r\n"
        texto, eol = f.leer_archivo(p)
        assert texto == "a\nb\n" and eol == "\r\n"


def test_archivo_cambio_en_disco():
    with en_tmp():
        p = Path("a.txt")
        f.escribir_archivo(p, "uno\n", "\n")
        original, _ = f.leer_archivo(p)
        assert f.archivo_cambio_en_disco(p, original) is False
        f.escribir_archivo(p, "dos\n", "\n")
        assert f.archivo_cambio_en_disco(p, original) is True
        assert f.archivo_cambio_en_disco(Path("no_existe.txt"), original) is True


def test_backup_y_restauracion():
    with en_tmp():
        p = Path("a.py")
        f.escribir_archivo(p, "v1\n", "\n")
        bk = f.hacer_backup(p)
        f.escribir_archivo(p, "v2\n", "\n")
        assert f.ultimo_backup(p) == bk
        assert f.leer_archivo(bk)[0] == "v1\n"
        assert (Path(".ai_backups") / ".gitignore").read_text() == "*\n"


def test_sin_backup_previo():
    with en_tmp():
        assert f.ultimo_backup(Path("a.py")) is None


@pytest.mark.xfail(strict=True, reason="Deuda #5 / Fase 0.5: el sello de tiempo tiene resolución de 1 s y el 2.º backup pisa al 1.º")
def test_dos_backups_en_el_mismo_segundo_no_se_pisan():
    class Fijo(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, 12, 0, 0)

    original_dt = f.datetime
    f.datetime = Fijo
    try:
        with en_tmp():
            p = Path("a.py")
            f.escribir_archivo(p, "v1\n", "\n")
            b1 = f.hacer_backup(p)
            f.escribir_archivo(p, "v2\n", "\n")
            b2 = f.hacer_backup(p)
            assert b1 != b2 and f.leer_archivo(b1)[0] == "v1\n"
    finally:
        f.datetime = original_dt


# ----------------------------------------------------------------- diff y avisos
def test_diff_de_una_sola_linea_cuenta_1_y_1():
    assert f.mostrar_diff("a\nb\nc\n", "a\nB\nc\n", "x.py") == (1, 1)


def test_diff_sin_cambios():
    assert f.mostrar_diff("a\nb\n", "a\nb\n", "x.py") == (0, 0)


def test_aviso_de_recorte():
    grande = "\n".join(f"l{i}" for i in range(30))
    assert f.aviso_recorte(grande, "\n".join(f"l{i}" for i in range(10)))
    assert f.aviso_recorte(grande, "\n".join(f"l{i}" for i in range(28))) is None
    assert f.aviso_recorte("a\nb\n", "a\n") is None  # archivos chicos: no molesta


# ----------------------------------------------------------------- generar_mapa.py
def test_extraer_python():
    with en_tmp():
        Path("m.py").write_text(
            "import os\nfrom app.services import user\n\nclass A:\n    def f(self):\n        pass\n\ndef g(x: int):\n    return x\n",
            encoding="utf-8")
        d = gm.extraer_archivo("m.py")
    assert {"os", "app.services"} <= set(d["imports"])  # con tree-sitter además trae app.services.user
    assert any("A" in s for s in d["signatures"]) and any("g" in s for s in d["signatures"])


def test_extraer_java():
    with en_tmp():
        Path("U.java").write_text(
            "package a;\nimport com.lwt.repo.UserRepository;\n@Service\npublic class U {\n"
            "  public List<String> findAll() {\n    return null;\n  }\n}\n", encoding="utf-8")
        d = gm.extraer_archivo("U.java")
    assert "com.lwt.repo.UserRepository" in d["imports"]
    assert any("U" in s for s in d["signatures"]) and any("findAll" in s for s in d["signatures"])


def test_extraer_js_y_html():
    with en_tmp():
        Path("a.jsx").write_text("import X from './x.jsx'\nexport const App = () => null\n", encoding="utf-8")
        Path("p.html").write_text('<script src="app.js"></script><div id="root"></div>', encoding="utf-8")
        js, html = gm.extraer_archivo("a.jsx"), gm.extraer_archivo("p.html")
    assert "./x.jsx" in js["imports"] and any("App" in s for s in js["signatures"])
    assert "app.js" in html["imports"] and "#root" in html["signatures"]


def test_archivo_sin_firmas_ni_imports_no_entra_al_mapa():
    with en_tmp():
        Path("vacio.js").write_text("// nada\n", encoding="utf-8")
        assert gm.extraer_archivo("vacio.js") is None


def test_es_relevante_ignora_carpetas_pesadas():
    with en_tmp() as d:
        raiz = d.resolve()
        assert gm.es_relevante(raiz / "src" / "a.js", raiz) is True
        assert gm.es_relevante(raiz / "node_modules" / "x" / "a.js", raiz) is False
        assert gm.es_relevante(raiz / "venv" / "lib" / "a.py", raiz) is False
        assert gm.es_relevante(raiz / "notas.txt", raiz) is False


def test_escanear_y_guardar_mapa_de_forma_atomica():
    with en_tmp() as d:
        (d / "src").mkdir()
        (d / "src" / "a.js").write_text("import b from './b.js'\nfunction hola() {}\n", encoding="utf-8")
        (d / "node_modules").mkdir()
        (d / "node_modules" / "z.js").write_text("function z() {}\n", encoding="utf-8")
        archivos = gm.escanear_todo(d.resolve())
        gm.guardar_mapa(archivos, d.resolve())
        datos = json.loads((d / ".ai_map.json").read_text(encoding="utf-8"))
        assert datos["version"] == 2 and list(datos["files"]) == ["src/a.js"]
        assert not list(d.glob("*.tmp"))