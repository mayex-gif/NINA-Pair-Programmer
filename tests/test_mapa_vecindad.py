"""
Mapa v3 y recorte por vecindad: extracción rica (atributos, endpoints, hooks...), resolución real de imports (4.5)
y envío de solo las N ramas inmediatas del archivo pedido (o el árbol entero si el archivo es nuevo).
"""
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nina import contexto as cx  # noqa: E402
from nina import grafo  # noqa: E402
from nina import mapa as gm  # noqa: E402
from nina.proyecto import Proyecto  # noqa: E402


@contextlib.contextmanager
def en_tmp():
    viejo = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        try:
            yield Path(d)
        finally:
            os.chdir(viejo)


def _m(imports=None, **extra):
    return {"signatures": ["sig"], "imports": imports or [], **extra}


# cadena a -> b -> c -> d, más un archivo aislado z
CADENA = {
    "src/a.js": _m(["./b.js"]),
    "src/b.js": _m(["./c.js"]),
    "src/c.js": _m(["./d.js"]),
    "src/d.js": _m(),
    "src/zeta.js": _m(),
}


# ------------------------------------------------------------------ extracción rica
def test_java_atributos_y_endpoints():
    with en_tmp() as d:
        (d / "U.java").write_text(
            "import javax.persistence.*;\n@Entity public class User {\n @Id @GeneratedValue private Long id;\n"
            " private String name, email;\n public Long getId() { return id; }\n}\nenum Estado { ACTIVO, INACTIVO }\n",
            encoding="utf-8")
        (d / "C.java").write_text(
            '@RestController @RequestMapping("/api/users") public class C {\n'
            ' @GetMapping("/{id}") public User get(Long id) { return null; }\n'
            ' @PostMapping public User create(User u) { return u; }\n'
            ' @RequestMapping(value="/x", method = RequestMethod.DELETE) public void del() {}\n}\n', encoding="utf-8")
        u, c = gm.extraer_archivo("U.java"), gm.extraer_archivo("C.java")
    assert "User: @Id @GeneratedValue private Long id" in u["fields"]
    assert "User: private String name" in u["fields"] and "User: private String email" in u["fields"]
    assert "Estado: constante ACTIVO" in u["fields"]
    assert c["endpoints"] == ["GET /api/users/{id}", "POST /api/users", "DELETE /api/users/x"]
    assert "endpoints" not in u


def test_react_ts_componentes_props_hooks_y_api():
    with en_tmp() as d:
        (d / "UserCard.tsx").write_text(
            "import { useUsers } from '../hooks/useUsers';\nimport Avatar from './Avatar';\n"
            "interface Props { user: User; onPick?: (id: number) => void }\n"
            "export const UserCard = ({ user, onPick }: Props) => {\n"
            "  const [open, setOpen] = useState(false);\n  const { data } = useUsers();\n"
            "  fetch('/api/users/' + user.id);\n  return <div><Avatar /></div>;\n};\n"
            "export default function Page() { return <UserCard user={null} /> }\n", encoding="utf-8")
        r = gm.extraer_archivo("UserCard.tsx")
    assert "Props: user: User" in r["fields"] and "Props: onPick?: (id: number) => void" in r["fields"]
    assert r["components"][0] == "UserCard({ user, onPick }: Props)"
    assert r["exports"] == ["UserCard", "default Page"]
    assert r["hooks"] == ["useState", "useUsers"]
    assert r["renders"] == ["Avatar", "UserCard"] and r["api"] == ["/api/users/"]


def test_python_campos_y_rutas():
    with en_tmp() as d:
        (d / "s.py").write_text(
            "class Cfg:\n    host: str = 'x'\n    port = 8\n    def __init__(self, repo):\n"
            "        self.repo = repo\n        self.n: int = 0\n"
            "@app.get('/health')\ndef health(): pass\n", encoding="utf-8")
        r = gm.extraer_archivo("s.py")
    assert r["fields"] == ["Cfg: host: str", "Cfg: port", "Cfg: repo", "Cfg: n: int"]
    assert r["endpoints"] == ["GET /health"]


def test_un_archivo_sin_extras_queda_igual_que_antes():
    with en_tmp() as d:
        (d / "a.js").write_text("function f() {}\n", encoding="utf-8")
        assert gm.extraer_archivo("a.js") == {"signatures": ["function f()"], "imports": []}


# ------------------------------------------------------------------ resolución de imports
def test_resolver_imports_js_relativos_alias_e_index():
    mapa = {
        "src/App.jsx": _m(["./components/Card", "@/hooks/useX", "react", "../lib/api"]),
        "src/components/Card/index.jsx": _m(),
        "src/hooks/useX.js": _m(),
        "lib/api.js": _m(),
    }
    destinos = {b for a, b in grafo.resolver_imports(mapa) if a == "src/App.jsx"}
    assert destinos == {"src/components/Card/index.jsx", "src/hooks/useX.js", "lib/api.js"}


def test_resolver_imports_java_y_python():
    mapa = {
        "b/src/com/x/web/C.java": _m(["com.x.model.User", "org.spring.Foo", "java.util.List"]),
        "b/src/com/x/model/User.java": _m(),
        "app/svc.py": _m([".models", ".models.User", "app.util"]),
        "app/models.py": _m(),
        "app/util/__init__.py": _m(),
    }
    ar = set(grafo.resolver_imports(mapa))
    assert ("b/src/com/x/web/C.java", "b/src/com/x/model/User.java") in ar
    assert ("app/svc.py", "app/models.py") in ar and ("app/svc.py", "app/util/__init__.py") in ar
    assert not any(a == "b/src/com/x/web/C.java" and b.endswith("Foo.java") for a, b in ar)


def test_vecindad_por_saltos_y_en_ambos_sentidos():
    assert grafo.vecindad(CADENA, "src/b.js", 1) == {"src/a.js": 1, "src/c.js": 1}   # importador e importado
    assert grafo.vecindad(CADENA, "src/a.js", 2) == {"src/b.js": 1, "src/c.js": 2}
    assert grafo.vecindad(CADENA, "src/a.js", 0) == {} and grafo.vecindad(CADENA, "no/existe.js", 3) == {}


# ------------------------------------------------------------------ filtrar_mapa por vecindad
def test_filtrar_solo_manda_las_ramas_cercanas():
    txt, usados, total = cx.filtrar_mapa(CADENA, "src/a.js", "cambiá algo", profundidad=1)
    assert "# src/b.js" in txt and "src/c.js" not in txt and "src/zeta.js" not in txt and usados == 1 and total == 5
    txt, usados, _ = cx.filtrar_mapa(CADENA, "src/a.js", "cambiá algo", profundidad=2)
    assert "# src/b.js" in txt and "# src/c.js" in txt and "src/d.js" not in txt and usados == 2
    # los más cercanos van primero y se rotulan
    assert txt.index("src/b.js") < txt.index("src/c.js") and "[lo importa]" in txt and "[a 2 saltos]" in txt


def test_filtrar_marca_quien_usa_al_objetivo():
    txt, _, _ = cx.filtrar_mapa(CADENA, "src/b.js", "x", profundidad=1)
    assert "# src/a.js  [lo usa]" in txt and "# src/c.js  [lo importa]" in txt


def test_filtrar_incluye_lo_lejano_si_la_instruccion_lo_nombra():
    txt, _, _ = cx.filtrar_mapa(CADENA, "src/a.js", "usá lo de zeta", profundidad=1)
    assert "# src/zeta.js" in txt


def test_filtrar_profundidad_cero_es_el_comportamiento_clasico():
    txt, usados, _ = cx.filtrar_mapa(CADENA, "src/a.js", "cambiá algo", profundidad=0)
    assert txt.startswith("# src/b.js") and usados >= 1        # sin límite de saltos: ranking por relevancia (incluye la carpeta)


def test_filtrar_archivo_nuevo_manda_el_arbol_entero():
    txt, usados, total = cx.filtrar_mapa(CADENA, "", "creá un archivo nuevo", completo=True)
    assert usados == total == 5 and all(f"# src/{n}.js" in txt for n in ("a", "b", "c", "d", "zeta"))


def test_filtrar_arbol_completo_lista_las_rutas_que_no_caben_con_detalle(monkeypatch):
    monkeypatch.setattr(cx, "PRESUPUESTO_MAPA_CHARS", 40)
    txt, usados, total = cx.filtrar_mapa(CADENA, "", "nuevo", completo=True)
    assert usados < total and "## Resto del árbol del proyecto (solo rutas)" in txt
    assert sum(1 for n in ("a", "b", "c", "d", "zeta") if f"src/{n}.js" in txt) == 5       # todas aparecen: con detalle o como ruta


def test_el_bloque_muestra_los_datos_extra():
    info = _m(fields=["User: id"], endpoints=["GET /x"], hooks=["useState"], api=["/api/x"])
    b = cx.bloque_mapa("a.tsx", info)
    assert "· atributos: User: id" in b and "· endpoints: GET /x" in b and "· hooks: useState" in b and "· llama a: /api/x" in b
    muchos = _m(fields=[f"C: f{i}" for i in range(20)])
    assert "(+6 más)" in cx.bloque_mapa("a.java", muchos)


def test_preparar_contexto_archivo_nuevo_vs_existente():
    with tempfile.TemporaryDirectory() as t:
        P = Proyecto(Path(t).resolve())
        (P.raiz / "src").mkdir()
        for n, imp in (("a", "./b.js"), ("b", "./c.js"), ("c", ""), ("zeta", "")):
            (P.raiz / "src" / f"{n}.js").write_text(f"import x from '{imp}'\nfunction {n}() {{}}\n" if imp else f"function {n}() {{}}\n", encoding="utf-8")
        P.ruta_mapa.write_text(json.dumps({"version": 3, "files": {k: v for k, v in CADENA.items() if k != "src/d.js"}}), encoding="utf-8")
        existente = cx.preparar_contexto(P, "src/a.js", "cambiá algo")
        assert existente.mapa_usados == 2                                   # b y c (profundidad 2); z queda afuera
        assert "src/zeta.js" not in existente.mensajes[0]["content"]
        nuevo = cx.preparar_contexto(P, None, "creá un archivo nuevo")      # Modo Global / archivo nuevo → árbol entero
        assert nuevo.mapa_usados == 4 and "# src/zeta.js" in nuevo.mensajes[0]["content"]
