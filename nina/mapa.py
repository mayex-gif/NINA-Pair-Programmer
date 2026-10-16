"""
Mapa del proyecto: extrae firmas e imports (tree-sitter con fallback a regex) y escribe `.ai_map.json`.
El watcher (watchdog) vive aparte, en `vigia.py`, para poder importar esto sin watchdog (p. ej. desde la web).
"""
import json
import os
import re
from pathlib import Path

from .config import EXTENSIONES_MAPA as EXTENSIONES
from .config import IGNORE_DIRS, MAX_LARGO_FIRMA, NOMBRE_MAPA as ARCHIVO_MAPA, VERSION_MAPA


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
    return parsers


PARSERS = _cargar_parsers()


USA_TREE_SITTER = bool(PARSERS)


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


def _agregar(extras: dict, clave_extra: str, valor: str):
    """Suma `valor` a extras[clave_extra] sin repetir (el orden de aparición se conserva)."""
    lista = extras.setdefault(clave_extra, [])
    if valor and valor not in lista:
        lista.append(valor)


_RE_MAPPING_JAVA = re.compile(r'@(Get|Post|Put|Delete|Patch|Request)Mapping\s*(?:\(([^)]*)\))?')
_RE_VERBO_JAVA = re.compile(r'RequestMethod\.(GET|POST|PUT|DELETE|PATCH)')


def _endpoint_java(texto: str):
    """('GET', '/users/{id}') a partir de las anotaciones de un método/clase Spring; None si no hay mapping."""
    m = _RE_MAPPING_JAVA.search(texto)
    if not m:
        return None
    verbo = m.group(1).upper()
    args = m.group(2) or ''
    if verbo == 'REQUEST':
        v = _RE_VERBO_JAVA.search(args)
        verbo = v.group(1) if v else ''
    ruta = re.search(r'"([^"]*)"', args)
    return verbo, (ruta.group(1) if ruta else '')


TIPOS_CLASE_JAVA = {'class_declaration', 'interface_declaration', 'enum_declaration',
                    'record_declaration', 'annotation_type_declaration'}


TIPOS_METODO_JAVA = {'method_declaration', 'constructor_declaration'}


def _modificadores(nodo, src: bytes) -> str:
    """Texto de los modificadores/anotaciones de una declaración Java ('' si no tiene)."""
    for c in nodo.children:
        if c.type == 'modifiers':
            return _txt(src, c.start_byte, c.end_byte)
    return ''


def _extraer_java(raiz, src: bytes):
    firmas, imports, extras = [], [], {}

    def visitar(nodo, nivel, clase='', prefijo_http=''):
        for hijo in nodo.children:
            t = hijo.type
            sangria = '  ' * nivel
            if t == 'import_declaration':
                imp = _txt(src, hijo.start_byte, hijo.end_byte)
                imports.append(re.sub(r'^import\s+(static\s+)?|;\s*$', '', imp).strip())
            elif t in TIPOS_CLASE_JAVA:
                firmas.append(sangria + _hasta_cuerpo(hijo, src))
                nombre = hijo.child_by_field_name('name')
                nombre = _txt(src, nombre.start_byte, nombre.end_byte) if nombre else '?'
                ep = _endpoint_java(_modificadores(hijo, src))
                cuerpo = hijo.child_by_field_name('body')
                if cuerpo:
                    visitar(cuerpo, nivel + 1, nombre, ep[1] if ep else '')
            elif t in TIPOS_METODO_JAVA:
                firmas.append(sangria + _hasta_cuerpo(hijo, src))
                ep = _endpoint_java(_modificadores(hijo, src))
                if ep:
                    ruta = (prefijo_http.rstrip('/') + '/' + ep[1].lstrip('/')).rstrip('/') or '/'
                    _agregar(extras, 'endpoints', f"{ep[0] or '*'} {ruta}")
            elif t == 'field_declaration':
                tipo = hijo.child_by_field_name('type')
                tipo = _txt(src, tipo.start_byte, tipo.end_byte) if tipo else ''
                mods = _compactar(_modificadores(hijo, src))
                for d in hijo.children:
                    if d.type == 'variable_declarator':
                        n = d.child_by_field_name('name')
                        if n:
                            decl = ' '.join(x for x in (mods, tipo, _txt(src, n.start_byte, n.end_byte)) if x)
                            _agregar(extras, 'fields', _compactar(f'{clave_clase(clase)}{decl}'))
            elif t == 'enum_constant':
                n = hijo.child_by_field_name('name')
                if n:
                    _agregar(extras, 'fields', f"{clave_clase(clase)}constante {_txt(src, n.start_byte, n.end_byte)}")
            elif t in ('enum_body_declarations', 'enum_body'):
                visitar(hijo, nivel, clase, prefijo_http)

    def clave_clase(clase):
        return f'{clase}: ' if clase else ''

    visitar(raiz, 0)
    return firmas, imports, extras


FUNCIONES_JS = {'arrow_function', 'function_expression', 'function', 'generator_function'}


def _extraer_js(raiz, src: bytes):
    firmas, imports, extras = [], [], {}

    def props_de(params, defecto='()') -> str:
        return _compactar(_txt(src, params.start_byte, params.end_byte)) if params else defecto

    def registrar(nombre: str, prefijo: str, params=None, es_funcion=False):
        """Exports (según el prefijo) y componentes React (función con nombre en mayúscula)."""
        if prefijo.startswith('export'):
            _agregar(extras, 'exports', ('default ' if 'default' in prefijo else '') + nombre)
        if es_funcion and nombre[:1].isupper():
            _agregar(extras, 'components', f'{nombre}{props_de(params)}')

    def miembros(cuerpo, dueño: str):
        """Atributos de una clase (field_definition) o propiedades de una interfaz / tipo objeto (property_signature)."""
        for m in cuerpo.children:
            if m.type in ('public_field_definition', 'field_definition', 'property_signature'):
                n = m.child_by_field_name('name') or m.child_by_field_name('property')
                if n is None:
                    continue
                tipo = m.child_by_field_name('type')
                texto = _txt(src, n.start_byte, n.end_byte)
                if m.type == 'property_signature' and any(c.type == '?' for c in m.children):
                    texto += '?'
                if tipo:
                    texto += _compactar(_txt(src, tipo.start_byte, tipo.end_byte))
                _agregar(extras, 'fields', _compactar(f'{dueño}: {texto}'))

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
                registrar(n, prefijo, params, es_funcion=True)
            elif valor.type == 'call_expression':
                # React.memo(() => ...), forwardRef(...), styled(...)
                args = valor.child_by_field_name('arguments')
                if args and any(a.type in FUNCIONES_JS for a in args.children):
                    callee = valor.child_by_field_name('function')
                    c = _txt(src, callee.start_byte, callee.end_byte) if callee else '?'
                    firmas.append(sangria + _compactar(f'{prefijo}{tipo_decl} {n} = {c}(…)'))
                    interna = next((a for a in args.children if a.type in FUNCIONES_JS), None)
                    registrar(n, prefijo, interna.child_by_field_name('parameters') if interna else None, es_funcion=True)
            elif prefijo.startswith('export'):
                registrar(n, prefijo)

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
                for c in hijo.children:
                    if c.type == 'export_clause':          # export { a, b as c }
                        for e in c.children:
                            if e.type == 'export_specifier':
                                n = e.child_by_field_name('alias') or e.child_by_field_name('name')
                                if n:
                                    _agregar(extras, 'exports', _txt(src, n.start_byte, n.end_byte))
                    elif c.type == 'identifier' and es_default:   # export default Foo
                        _agregar(extras, 'exports', 'default ' + _txt(src, c.start_byte, c.end_byte))
                visitar(hijo, nivel, prefijo='export default ' if es_default else 'export ')
            elif t in ('function_declaration', 'generator_function_declaration'):
                firmas.append(sangria + prefijo + _hasta_cuerpo(hijo, src))
                nombre = hijo.child_by_field_name('name')
                if nombre:
                    registrar(_txt(src, nombre.start_byte, nombre.end_byte), prefijo,
                              hijo.child_by_field_name('parameters'), es_funcion=True)
            elif t in ('class_declaration', 'abstract_class_declaration'):
                firmas.append(sangria + prefijo + _hasta_cuerpo(hijo, src))
                nombre = hijo.child_by_field_name('name')
                nombre = _txt(src, nombre.start_byte, nombre.end_byte) if nombre else '?'
                registrar(nombre, prefijo)
                cuerpo = hijo.child_by_field_name('body')
                if cuerpo:
                    miembros(cuerpo, nombre)
                    visitar(cuerpo, nivel + 1)
            elif t == 'method_definition':
                firmas.append(sangria + _hasta_cuerpo(hijo, src))
            elif t == 'interface_declaration':
                firmas.append(sangria + prefijo + _hasta_cuerpo(hijo, src))
                nombre = hijo.child_by_field_name('name')
                if nombre:
                    n = _txt(src, nombre.start_byte, nombre.end_byte)
                    registrar(n, prefijo)
                    if hijo.child_by_field_name('body'):
                        miembros(hijo.child_by_field_name('body'), n)
            elif t == 'type_alias_declaration':
                nombre = hijo.child_by_field_name('name')
                if nombre:
                    n = _txt(src, nombre.start_byte, nombre.end_byte)
                    firmas.append(sangria + f"{prefijo}type {n}")
                    registrar(n, prefijo)
                    valor = hijo.child_by_field_name('value')
                    if valor is not None and valor.type == 'object_type':
                        miembros(valor, n)
            elif t in ('lexical_declaration', 'variable_declaration'):
                declaradores(hijo, sangria, prefijo)

    visitar(raiz, 0)
    return firmas, imports, extras


_RE_RUTA_PY = re.compile(r"@\w[\w.]*\.(get|post|put|delete|patch|route)\(\s*[\'\"]([^\'\"]*)[\'\"]")


def _extraer_python(raiz, src: bytes):
    firmas, imports, extras = [], [], {}

    def campo(clase: str, asignacion):
        izq = asignacion.child_by_field_name('left')
        if izq is None:
            return
        tipo = asignacion.child_by_field_name('type')
        if izq.type == 'identifier':
            nombre = _txt(src, izq.start_byte, izq.end_byte)
        elif izq.type == 'attribute':      # self.x = ...
            obj, attr = izq.child_by_field_name('object'), izq.child_by_field_name('attribute')
            if obj is None or attr is None or _txt(src, obj.start_byte, obj.end_byte) != 'self':
                return
            nombre = _txt(src, attr.start_byte, attr.end_byte)
        else:
            return
        if tipo is not None:
            nombre += ': ' + _compactar(_txt(src, tipo.start_byte, tipo.end_byte))
        _agregar(extras, 'fields', f'{clase}: {nombre}')

    def campos_de_clase(clase: str, cuerpo):
        for h in cuerpo.children:
            if h.type == 'expression_statement' and h.children and h.children[0].type == 'assignment':
                campo(clase, h.children[0])
            elif h.type in ('function_definition', 'decorated_definition'):
                f = h.child_by_field_name('definition') if h.type == 'decorated_definition' else h
                n = f.child_by_field_name('name') if f is not None else None
                if n is not None and _txt(src, n.start_byte, n.end_byte) == '__init__':
                    cuerpo_f = f.child_by_field_name('body')
                    for x in (cuerpo_f.children if cuerpo_f is not None else []):
                        if x.type == 'expression_statement' and x.children and x.children[0].type == 'assignment':
                            campo(clase, x.children[0])

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
                    nombre = defn.child_by_field_name('name')
                    campos_de_clase(_txt(src, nombre.start_byte, nombre.end_byte) if nombre else '?', cuerpo)
                    visitar(cuerpo, nivel + 1)  # métodos; no se baja a funciones anidadas
            elif t == 'function_definition':
                firmas.append(sangria + firma(defn, decoradores))
                for m in _RE_RUTA_PY.finditer(decoradores):
                    verbo = 'ANY' if m.group(1) == 'route' else m.group(1).upper()
                    _agregar(extras, 'endpoints', f'{verbo} {m.group(2)}')

    visitar(raiz, 0)
    return firmas, imports, extras


def _extraer_tree_sitter(ruta: str, src: bytes):
    ext = os.path.splitext(ruta)[1]
    arbol = PARSERS[ext].parse(src)
    if ext == '.java':
        return _extraer_java(arbol.root_node, src)
    if ext == '.py':
        return _extraer_python(arbol.root_node, src)
    return _extraer_js(arbol.root_node, src)


def _extraer_regex(ruta: str, src: bytes):
    texto = src.decode('utf-8', 'replace')
    firmas, imports, extras = [], [], {}

    if ruta.endswith('.java'):
        for c in re.findall(r'(?:class|interface|enum|record)\s+(\w+)', texto):
            firmas.append(f'class {c}')
        for m in re.findall(r'(?:public|private|protected)\s+[\w<>\[\],? ]+\s+(\w+)\s*\([^)]*\)', texto):
            firmas.append(f'  método {m}(…)')
        imports = re.findall(r'^\s*import\s+(?:static\s+)?([\w.*]+);', texto, re.M)
        for tipo, nombre in re.findall(r'^\s*(?:@\w+\s+)*(?:private|protected|public)\s+(?:static\s+|final\s+)*([\w<>\[\],? ]+?)\s+(\w+)\s*(?:=[^;]*)?;', texto, re.M):
            _agregar(extras, 'fields', f'{tipo} {nombre}')

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

    return firmas, imports, extras


_RE_HOOK = re.compile(r'\b(use[A-Z]\w*)\s*\(')
_RE_JSX = re.compile(r'<([A-Z][\w.]*)[\s/>]')
_RE_API = re.compile(r"""\b(?:fetch|axios(?:\.\w+)?|api(?:\.\w+)?|http(?:\.\w+)?)\(\s*[`'"]([^`'"]+)[`'"]""")
EXTENSIONES_JS = ('.js', '.jsx', '.ts', '.tsx')


def _extras_texto(ruta: str, texto: str) -> dict:
    """Datos de front que se ven mejor con regex que con el árbol: hooks usados, componentes renderizados, llamadas a la API."""
    if os.path.splitext(ruta)[1] not in EXTENSIONES_JS:
        return {}
    extras = {}
    for h in _RE_HOOK.findall(texto):
        _agregar(extras, 'hooks', h)
    for c in _RE_JSX.findall(texto):
        _agregar(extras, 'renders', c)
    for u in _RE_API.findall(texto):
        if u.startswith(('/', 'http', '$', '`')) or '/' in u:
            _agregar(extras, 'api', u)
    return extras


MAX_ITEMS_EXTRA = 40


def extraer_archivo(ruta: str):
    """
    Devuelve {'signatures', 'imports'} más, solo si hay: 'fields' (atributos/props), 'endpoints', 'exports',
    'components' (React), 'hooks', 'renders' y 'api' (llamadas del front). None si no hay nada útil.
    """
    try:
        with open(ruta, 'rb') as f:
            src = f.read()
    except OSError:
        return None
    try:
        if os.path.splitext(ruta)[1] in PARSERS:
            firmas, imports, extras = _extraer_tree_sitter(ruta, src)
        else:
            firmas, imports, extras = _extraer_regex(ruta, src)
    except Exception:
        firmas, imports, extras = _extraer_regex(ruta, src)
    if not firmas and not imports:
        return None
    datos = {'signatures': firmas, 'imports': sorted(set(imports))}
    extras = {**extras, **_extras_texto(ruta, src.decode('utf-8', 'replace'))}
    for clave_extra in ('fields', 'endpoints', 'exports', 'components', 'hooks', 'renders', 'api'):
        if extras.get(clave_extra):
            datos[clave_extra] = extras[clave_extra][:MAX_ITEMS_EXTRA]
    return datos


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
    os.replace(tmp, destino)


def primer_error_sintaxis(codigo: str, ruta: str):
    """Línea (1-based) del primer nodo ERROR/faltante según tree-sitter, o None si no hay error o no hay parser."""
    ext = os.path.splitext(ruta)[1]
    if ext not in PARSERS:
        return None
    try:
        arbol = PARSERS[ext].parse(codigo.encode('utf-8', 'replace'))
        if not arbol.root_node.has_error:
            return None
        pila = [arbol.root_node]
        while pila:
            n = pila.pop(0)
            if n.type == 'ERROR' or n.is_missing:
                return n.start_point[0] + 1
            pila[0:0] = [c for c in n.children if c.has_error or c.is_missing]
        return 1
    except Exception:
        return None


# === NUEVA FUNCIÓN (FASE 0.6) ===
def tiene_errores_sintaxis(codigo: str, ruta: str) -> bool:
    """
    Devuelve True si el código tiene errores de sintaxis (según tree-sitter).
    Si el parser no está disponible para la extensión, devuelve False.
    """
    ext = os.path.splitext(ruta)[1]
    if ext not in PARSERS:
        return False
    try:
        arbol = PARSERS[ext].parse(codigo.encode('utf-8', 'replace'))
        return arbol.root_node.has_error
    except Exception:
        return False