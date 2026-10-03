"""
generar_mapa.py — El Vigía del proyecto.

Escanea el proyecto, extrae firmas (clases, métodos, funciones, componentes) e imports
usando tree-sitter (con fallback a regex si no está instalado) y los guarda en
`.ai_map.json`. Después queda vigilando cambios y actualiza SOLO el archivo modificado.

Uso:
    python generar_mapa.py            # escanea y queda vigilando (Ctrl+C para salir)
    python generar_mapa.py --una-vez  # escanea una sola vez y termina
    python generar_mapa.py ruta/proyecto
"""
import argparse
import json
import os
import re
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

EXTENSIONES = ('.java', '.ts', '.tsx', '.js', '.jsx', '.py', '.html')
IGNORE_DIRS = {
    '.git', 'node_modules', 'target', '.next', 'dist', 'build', 'out', 'coverage',
    '.idea', '.vscode', '__pycache__', '.ai_backups',
    'venv', '.venv', 'site-packages', '.pytest_cache', '.mypy_cache', '.tox',  # entornos de Python
}
ARCHIVO_MAPA = ".ai_map.json"
VERSION_MAPA = 2
MAX_LARGO_FIRMA = 220  # recorta firmas kilométricas (ej. anotaciones largas)

# --------------------------------------------------------------------------- #
# Parsers tree-sitter (opcionales)
# --------------------------------------------------------------------------- #
def _cargar_parsers() -> dict:
    """Un parser por extensión. Si falta el paquete de UN lenguaje, solo ese cae a regex (no todos)."""
    parsers = {}
    try:
        from tree_sitter import Language, Parser
    except Exception:
        return parsers

    def agregar(extensiones, modulo, funcion="language"):
        try:
            lenguaje = Language(getattr(__import__(modulo), funcion)())
            for e in extensiones:
                parsers[e] = Parser(lenguaje)
        except Exception:
            pass

    agregar(('.java',), 'tree_sitter_java')
    agregar(('.js', '.jsx'), 'tree_sitter_javascript')
    agregar(('.ts',), 'tree_sitter_typescript', 'language_typescript')
    agregar(('.tsx',), 'tree_sitter_typescript', 'language_tsx')
    agregar(('.py',), 'tree_sitter_python')
    return parsers  # .html siempre va por regex


PARSERS = _cargar_parsers()
USA_TREE_SITTER = bool(PARSERS)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _txt(src: bytes, ini: int, fin: int) -> str:
    return src[ini:fin].decode('utf-8', 'replace')


def _compactar(s: str) -> str:
    s = re.sub(r'\s+', ' ', s).strip()
    return s if len(s) <= MAX_LARGO_FIRMA else s[:MAX_LARGO_FIRMA] + '…'


def _hasta_cuerpo(nodo, src: bytes) -> str:
    """Texto del nodo desde su inicio hasta donde empieza el cuerpo (= la firma)."""
    cuerpo = nodo.child_by_field_name('body')
    fin = cuerpo.start_byte if cuerpo else nodo.end_byte
    return _compactar(_txt(src, nodo.start_byte, fin))


# --------------------------------------------------------------------------- #
# Extracción con tree-sitter
# --------------------------------------------------------------------------- #
TIPOS_CLASE_JAVA = {'class_declaration', 'interface_declaration', 'enum_declaration',
                    'record_declaration', 'annotation_type_declaration'}
TIPOS_METODO_JAVA = {'method_declaration', 'constructor_declaration'}


def _extraer_java(raiz, src: bytes):
    firmas, imports = [], []

    def visitar(nodo, nivel):
        for hijo in nodo.children:
            t = hijo.type
            sangria = '  ' * nivel
            if t == 'import_declaration':
                imp = _txt(src, hijo.start_byte, hijo.end_byte)
                imports.append(re.sub(r'^import\s+(static\s+)?|;\s*$', '', imp).strip())
            elif t in TIPOS_CLASE_JAVA:
                firmas.append(sangria + _hasta_cuerpo(hijo, src))
                cuerpo = hijo.child_by_field_name('body')
                if cuerpo:
                    visitar(cuerpo, nivel + 1)
            elif t in TIPOS_METODO_JAVA:
                firmas.append(sangria + _hasta_cuerpo(hijo, src))
            elif t == 'enum_body_declarations':
                visitar(hijo, nivel)

    visitar(raiz, 0)
    return firmas, imports


FUNCIONES_JS = {'arrow_function', 'function_expression', 'function', 'generator_function'}


def _extraer_js(raiz, src: bytes):
    firmas, imports = [], []

    def declaradores(nodo, sangria, prefijo):
        tipo_decl = _txt(src, nodo.children[0].start_byte, nodo.children[0].end_byte) if nodo.children else 'const'
        for d in nodo.children:
            if d.type != 'variable_declarator':
                continue
            nombre = d.child_by_field_name('name')
            valor = d.child_by_field_name('value')
            if not nombre or not valor:
                continue
            n = _txt(src, nombre.start_byte, nombre.end_byte)
            if valor.type in FUNCIONES_JS:
                params = valor.child_by_field_name('parameters') or valor.child_by_field_name('parameter')
                p = _txt(src, params.start_byte, params.end_byte) if params else '()'
                if not p.startswith('('):
                    p = f'({p})'
                firmas.append(sangria + _compactar(f'{prefijo}{tipo_decl} {n} = {p} =>'))
            elif valor.type == 'call_expression':
                # React.memo(() => ...), forwardRef(...), styled(...)
                args = valor.child_by_field_name('arguments')
                if args and any(a.type in FUNCIONES_JS for a in args.children):
                    callee = valor.child_by_field_name('function')
                    c = _txt(src, callee.start_byte, callee.end_byte) if callee else '?'
                    firmas.append(sangria + _compactar(f'{prefijo}{tipo_decl} {n} = {c}(…)'))

    def visitar(nodo, nivel, prefijo=''):
        sangria = '  ' * nivel
        for hijo in nodo.children:
            t = hijo.type
            if t == 'import_statement':
                origen = hijo.child_by_field_name('source')
                if origen:
                    imports.append(_txt(src, origen.start_byte, origen.end_byte).strip('\'"`'))
            elif t == 'export_statement':
                es_default = any(c.type == 'default' for c in hijo.children)
                visitar(hijo, nivel, prefijo='export default ' if es_default else 'export ')
            elif t in ('function_declaration', 'generator_function_declaration'):
                firmas.append(sangria + prefijo + _hasta_cuerpo(hijo, src))
            elif t in ('class_declaration', 'abstract_class_declaration'):
                firmas.append(sangria + prefijo + _hasta_cuerpo(hijo, src))
                cuerpo = hijo.child_by_field_name('body')
                if cuerpo:
                    visitar(cuerpo, nivel + 1)
            elif t == 'method_definition':
                firmas.append(sangria + _hasta_cuerpo(hijo, src))
            elif t == 'interface_declaration':
                firmas.append(sangria + prefijo + _hasta_cuerpo(hijo, src))
            elif t == 'type_alias_declaration':
                nombre = hijo.child_by_field_name('name')
                if nombre:
                    firmas.append(sangria + f"{prefijo}type {_txt(src, nombre.start_byte, nombre.end_byte)}")
            elif t in ('lexical_declaration', 'variable_declaration'):
                declaradores(hijo, sangria, prefijo)

    visitar(raiz, 0)
    return firmas, imports


def _extraer_python(raiz, src: bytes):
    firmas, imports = [], []

    def nombre_de(n):
        if n.type == 'aliased_import':
            n = n.child_by_field_name('name') or n
        return _txt(src, n.start_byte, n.end_byte)

    def firma(defn, decoradores=''):
        txt = _hasta_cuerpo(defn, src).rstrip()
        if txt.endswith(':'):
            txt = txt[:-1].rstrip()  # solo el ':' final: los type hints (a: int) se conservan
        return _compactar((decoradores + ' ' + txt).strip())

    def visitar(nodo, nivel):
        sangria = '  ' * nivel
        for hijo in nodo.children:
            t = hijo.type
            defn, decoradores = hijo, ''
            if t == 'decorated_definition':  # @app.get("/x") def ... | @dataclass class ...
                defn = hijo.child_by_field_name('definition')
                decoradores = ' '.join(_compactar(_txt(src, d.start_byte, d.end_byte))
                                       for d in hijo.children if d.type == 'decorator')
                if defn is None:
                    continue
                t = defn.type
            if t == 'import_statement':
                for n in hijo.children:
                    if n.type in ('dotted_name', 'aliased_import'):
                        imports.append(nombre_de(n))
            elif t == 'import_from_statement':
                mod = hijo.child_by_field_name('module_name')
                m = _txt(src, mod.start_byte, mod.end_byte) if mod else ''
                imports.append(m)
                for n in hijo.children_by_field_name('name'):
                    imports.append(f"{m}.{nombre_de(n)}")  # from app.services import user -> app.services.user
            elif t == 'class_definition':
                firmas.append(sangria + firma(defn, decoradores))
                cuerpo = defn.child_by_field_name('body')
                if cuerpo:
                    visitar(cuerpo, nivel + 1)  # métodos; no se baja a funciones anidadas
            elif t == 'function_definition':
                firmas.append(sangria + firma(defn, decoradores))

    visitar(raiz, 0)
    return firmas, imports


def _extraer_tree_sitter(ruta: str, src: bytes):
    ext = os.path.splitext(ruta)[1]
    arbol = PARSERS[ext].parse(src)
    if ext == '.java':
        return _extraer_java(arbol.root_node, src)
    if ext == '.py':
        return _extraer_python(arbol.root_node, src)
    return _extraer_js(arbol.root_node, src)


# --------------------------------------------------------------------------- #
# Fallback con regex (si no hay tree-sitter)
# --------------------------------------------------------------------------- #
def _extraer_regex(ruta: str, src: bytes):
    texto = src.decode('utf-8', 'replace')
    firmas, imports = [], []

    if ruta.endswith('.java'):
        for c in re.findall(r'(?:class|interface|enum|record)\s+(\w+)', texto):
            firmas.append(f'class {c}')
        for m in re.findall(r'(?:public|private|protected)\s+[\w<>\[\],? ]+\s+(\w+)\s*\([^)]*\)', texto):
            firmas.append(f'  método {m}(…)')
        imports = re.findall(r'^\s*import\s+(?:static\s+)?([\w.*]+);', texto, re.M)

    elif ruta.endswith('.py'):
        for c in re.findall(r'^[ \t]*((?:async\s+)?(?:class|def)\s+\w+[^\n]*?):\s*$', texto, re.M):
            firmas.append(_compactar(c))
        imports = [m.group(1) or m.group(2) for m in
                   re.finditer(r'^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))', texto, re.M)]

    elif ruta.endswith('.html'):
        imports = re.findall(r'<(?:script|link)[^>]*(?:src|href)=[\'"]([^\'"]+)[\'"]', texto, re.I)
        ids = re.findall(r'<[a-zA-Z1-6]+[^>]*\sid=[\'"]([^\'"]+)[\'"][^>]*>', texto, re.I)
        firmas = [f"#{i}" for i in ids]  # los id principales: qué secciones existen

    else:  # JS/TS
        for f in re.findall(r'(?:function|const|let|var)\s+(\w+)\s*(?:=\s*(?:async\s*)?)?\(', texto):
            firmas.append(f'{f}(…)')
        imports = re.findall(r'import\s+(?:[^\'"]+?\s+from\s+)?[\'"]([^\'"]+)[\'"]', texto)

    return firmas, imports


# --------------------------------------------------------------------------- #
# API del mapa
# --------------------------------------------------------------------------- #
def extraer_archivo(ruta: str):
    """Devuelve {'signatures': [...], 'imports': [...]} o None si no hay nada útil."""
    try:
        with open(ruta, 'rb') as f:
            src = f.read()
    except OSError:
        return None
    try:
        if os.path.splitext(ruta)[1] in PARSERS:
            firmas, imports = _extraer_tree_sitter(ruta, src)
        else:
            firmas, imports = _extraer_regex(ruta, src)
    except Exception:
        firmas, imports = _extraer_regex(ruta, src)
    if not firmas and not imports:
        return None
    return {'signatures': firmas, 'imports': sorted(set(imports))}


def es_relevante(ruta: Path, raiz: Path) -> bool:
    if ruta.suffix not in EXTENSIONES:
        return False
    try:
        partes = ruta.resolve().relative_to(raiz).parts
    except ValueError:
        return False
    return not any(p in IGNORE_DIRS for p in partes[:-1])


def clave(ruta: Path, raiz: Path) -> str:
    return ruta.resolve().relative_to(raiz).as_posix()


def escanear_todo(raiz: Path) -> dict:
    archivos = {}
    for subdir, dirs, files in os.walk(raiz):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for nombre in files:
            ruta = Path(subdir) / nombre
            if ruta.suffix in EXTENSIONES:
                datos = extraer_archivo(str(ruta))
                if datos:
                    archivos[clave(ruta, raiz)] = datos
    return archivos


def guardar_mapa(archivos: dict, raiz: Path):
    destino = raiz / ARCHIVO_MAPA
    tmp = destino.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'version': VERSION_MAPA, 'files': archivos}, f, indent=1, ensure_ascii=False)
    os.replace(tmp, destino)  # escritura atómica: el ejecutor nunca lee un JSON a medias


# --------------------------------------------------------------------------- #
# Watcher incremental con debounce
# --------------------------------------------------------------------------- #
class MapaHandler(FileSystemEventHandler):
    def __init__(self, archivos: dict, raiz: Path):
        self.archivos = archivos
        self.raiz = raiz
        self.pendientes: set[Path] = set()
        self.lock = threading.Lock()
        self.timer = None

    def _encolar(self, ruta_str):
        ruta = Path(ruta_str)
        if not es_relevante(ruta, self.raiz):
            return
        with self.lock:
            self.pendientes.add(ruta)
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(0.5, self._procesar)  # agrupa ráfagas de guardado
            self.timer.start()

    def _procesar(self):
        with self.lock:
            lote, self.pendientes = self.pendientes, set()
        for ruta in lote:
            k = clave(ruta, self.raiz)
            if ruta.exists():
                datos = extraer_archivo(str(ruta))
                if datos:
                    self.archivos[k] = datos
                    print(f"[Watch] ✏️  {k}")
                else:
                    self.archivos.pop(k, None)
            elif self.archivos.pop(k, None) is not None:
                print(f"[Watch] 🗑️  {k}")
        guardar_mapa(self.archivos, self.raiz)
        print(f"        Mapa actualizado ({len(self.archivos)} archivos).")

    def on_modified(self, e):
        if not e.is_directory:
            self._encolar(e.src_path)

    def on_created(self, e):
        if not e.is_directory:
            self._encolar(e.src_path)

    def on_deleted(self, e):
        if not e.is_directory:
            self._encolar(e.src_path)

    def on_moved(self, e):
        if not e.is_directory:
            self._encolar(e.src_path)
            self._encolar(e.dest_path)


def main():
    ap = argparse.ArgumentParser(description="Generador y vigía del mapa del proyecto")
    ap.add_argument('raiz', nargs='?', default='.')
    ap.add_argument('--una-vez', action='store_true', help="Escanear una vez y salir")
    args = ap.parse_args()

    raiz = Path(args.raiz).resolve()
    activos = sorted(e.lstrip('.') for e in PARSERS)
    faltan = sorted(e.lstrip('.') for e in EXTENSIONES if e not in PARSERS)
    modo = f"tree-sitter [{', '.join(activos) or 'ninguno'}] + regex [{', '.join(faltan) or 'ninguno'}]"
    print(f"Escaneando {raiz} con {modo}...")
    archivos = escanear_todo(raiz)
    guardar_mapa(archivos, raiz)
    print(f"✅ {len(archivos)} archivos indexados en '{ARCHIVO_MAPA}'.")

    if args.una_vez:
        return

    observer = Observer()
    observer.schedule(MapaHandler(archivos, raiz), path=str(raiz), recursive=True)
    observer.start()
    print("👀 Watcher activo (Ctrl+C para salir)...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\nDeteniendo watcher.")
    observer.join()


if __name__ == "__main__":
    main()