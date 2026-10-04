"""
Streamlit simulado, solo para tests: permite ejecutar web.py de punta a punta sin Streamlit instalado
(o sin abrir un navegador). Los widgets devuelven valores controlados desde el test.
"""
import sys
import types


class StopApp(Exception):
    pass


class RerunApp(Exception):
    pass


class Any_:
    """Objeto permisivo: acepta cualquier atributo/llamada y sirve como context manager."""
    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return Any_()

    def __call__(self, *a, **k):
        return Any_()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(())

    def __bool__(self):
        return False


class SS(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError:
            raise AttributeError(k)

    def __setattr__(self, k, v):
        self[k] = v


class Control:
    """Lo que el 'usuario' hace en cada ejecución del script."""
    def __init__(self):
        self.clicks = set()
        self.checks = {}
        self.instruccion = ""
        self.log = []          # (tipo, texto) de los mensajes que la app muestra


def instalar(control: Control, ss: SS):
    st = types.ModuleType("streamlit")
    st.__path__ = []
    st.session_state = ss

    def registrar(tipo):
        def f(texto="", *a, **k):
            control.log.append((tipo, str(texto)))
            return Any_()
        return f

    for tipo in ("warning", "error", "success", "info", "caption"):
        setattr(st, tipo, registrar(tipo))
    for nombre in ("set_page_config", "markdown", "divider", "code", "metric", "iframe"):
        setattr(st, nombre, lambda *a, **k: Any_())
    st.sidebar = Any_()
    st.status = lambda *a, **k: Any_()
    st.empty = lambda *a, **k: Any_()
    st.expander = lambda *a, **k: Any_()
    st.cache_data = lambda *a, **k: (lambda f: f)

    def stop():
        raise StopApp()

    def rerun():
        raise RerunApp()

    st.stop, st.rerun = stop, rerun

    def text_input(label, value=None, key=None, **k):
        if key is not None and key in ss:
            return ss[key]
        v = "" if value is None else value
        if key is not None:
            ss[key] = v
        return v

    def selectbox(label, options, index=0, key=None, **k):
        if key is not None and ss.get(key) is not None:
            return ss[key]
        return None if index is None else list(options)[index]

    class Col(Any_):
        def button(self, label, **k):
            return label in control.clicks

    st.text_input = text_input
    st.selectbox = selectbox
    st.button = lambda label, **k: label in control.clicks
    st.checkbox = lambda label, value=False, **k: control.checks.get(label, value)
    st.text_area = lambda *a, **k: control.instruccion
    st.columns = lambda spec, **k: [Col() for _ in range(spec if isinstance(spec, int) else len(spec))]

    comp = types.ModuleType("streamlit.components")
    comp.__path__ = []
    v1 = types.ModuleType("streamlit.components.v1")
    v1.html = lambda *a, **k: Any_()
    comp.v1 = v1
    st.components = comp
    previos = {k: sys.modules.get(k) for k in ("streamlit", "streamlit.components", "streamlit.components.v1")}
    sys.modules.update({"streamlit": st, "streamlit.components": comp, "streamlit.components.v1": v1})

    def restaurar():
        for k, v in previos.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return restaurar
