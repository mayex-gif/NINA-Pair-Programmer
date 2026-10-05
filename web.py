"""
web.py — Interfaz Streamlit del motor.   Ejecutar:  streamlit run web.py
Reutiliza el núcleo del paquete nina/ (contexto, LLM, auto-healing, git, backups): CLI y web se comportan igual.
Tema gris oscuro: copiá la carpeta .streamlit/ junto a este archivo (o a ~/.streamlit).
"""
import html
import os
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from nina.config import (
    API_URL, EXTENSIONES_EDITABLES, IGNORE_DIRS, MAX_INTENTOS, MAX_TOKENS_PROMPT, MODELO, PERFIL, gestor_config
)
from nina.contexto import aviso_recorte, estimar_tokens, generar_cambio, preparar_contexto
from nina.diff import colapsar, filas_alineadas
from nina.llm import ErrorLLM, listar_modelos, stream_llm
from nina.proyecto import Proyecto, archivo_cambio_en_disco, escribir_archivo, leer_archivo

ALTO_FILA = 20  # px; fijo para poder calcular el scroll al primer cambio

st.set_page_config(page_title="NINA", page_icon="▪", layout="wide")
st.markdown("""
<style>
  /* Aumentamos el padding-top de 2.2rem a 4.5rem para despejar el título */
  .block-container { padding-top: 4.5rem; max-width: 1500px; }
  h1 { font-size: 1.35rem !important; font-weight: 600 !important; padding: 0 0 .2rem 0 !important; display: flex; align-items: center; }
  h3 { font-size: 1.02rem !important; font-weight: 600 !important; }
  footer, #MainMenu { visibility: hidden; }
  [data-testid="stSidebar"] { border-right: 1px solid #2e2e2e; }
  [data-testid="stMetricValue"] { font-size: 1.3rem; }
  [data-testid="stMetricLabel"] p { color: #8a8a8a; font-size: .74rem; text-transform: uppercase; letter-spacing: .05em; }
  .pensando { color:#8a8a8a; font:italic .84rem ui-monospace,Consolas,monospace; white-space:pre-wrap;
              max-height:200px; overflow:auto; border-left:2px solid #3a3a3a; padding-left:.75rem; }
</style>
""", unsafe_allow_html=True)

# Logo de NINA (Silueta de caniche)
SVG_NINA = """
<svg width="35" height="35" viewBox="0 0 24 24" fill="#d4d4d4" style="margin-right: 12px;" xmlns="http://www.w3.org/2000/svg">
  <path d="M17 4c-1.2 0-2.3.6-3 1.5-.6-.3-1.3-.4-2-.2-.9-1.1-2.3-1.7-3.7-1.3-.4-1-1.3-1.7-2.3-1.7-1.4 0-2.5 1.1-2.5 2.5 0 .4.1.8.3 1.2C2.2 8.3 1.2 9.6 1.2 11c0 1.5.9 2.8 2.2 3.3v3.2c0 .8.7 1.5 1.5 1.5h1c.8 0 1.5-.7 1.5-1.5v-2.7h4v2.7c0 .8.7 1.5 1.5 1.5h1c.8 0 1.5-.7 1.5-1.5v-3.2c1.3-.5 2.2-1.8 2.2-3.3 0-1.2-.6-2.2-1.5-2.7.5-.6.8-1.4.8-2.2 0-1.5-1.2-2.7-2.7-2.7-.1 0-.2 0-.3 0 .3-.5.5-1 .5-1.6 0-1.2-1-2.2-2.2-2.2z"/>
</svg>
"""

ss = st.session_state
for clave, valor in {"resultado": None, "ruta": None, "msg": None, "pens": "", "metricas": None,
                     "proyecto": os.getcwd(), "campo_proyecto": os.getcwd(), "aviso": None, "guardado": False}.items():
    ss.setdefault(clave, valor)


# ------------------------------- Proyecto / archivos -------------------------- #
def aplicar_proyecto(ruta_str: str):
    p = Path(ruta_str).expanduser()
    if not p.is_dir():
        ss.aviso = f"No existe la carpeta: {ruta_str}"
        ss.campo_proyecto = ss.proyecto
        return
    ss.proyecto = ss.campo_proyecto = str(p.resolve())
    ss.resultado, ss.archivo_sel, ss.aviso = None, None, None


def al_escribir_ruta():
    aplicar_proyecto(ss.campo_proyecto)


def examinar_carpeta():
    try:
        import tkinter as tk
        from tkinter import filedialog
        raiz = tk.Tk()
        raiz.withdraw()
        raiz.attributes("-topmost", True)
        try:
            carpeta = filedialog.askdirectory(title="Carpeta raíz del proyecto", initialdir=ss.proyecto)
        finally:
            raiz.destroy()
    except Exception:
        ss.aviso = "No pude abrir el selector de carpetas. Escribí la ruta a mano."
        return
    if carpeta:
        aplicar_proyecto(carpeta)


@st.cache_data(ttl=3, show_spinner=False)
def listar_archivos(raiz: str) -> list:
    """Recorre el disco (no depende del mapa): así aparecen también archivos nuevos o vacíos."""
    salida = []
    for subdir, dirs, files in os.walk(raiz):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith('.')]
        for f in files:
            if f.endswith(EXTENSIONES_EDITABLES):
                salida.append(Path(subdir, f).relative_to(raiz).as_posix())
    return sorted(salida)


# ----------------------------- Diff lado a lado ------------------------------- #

def renderizar_diff(viejo: str, nuevo: str, solo_cambios: bool = True):
    todas = filas_alineadas(viejo, nuevo)
    agregadas = sum(1 for f in todas if f[5] == "add")
    eliminadas = sum(1 for f in todas if f[2] == "del")
    filas = colapsar(todas) if solo_cambios else todas
    primer = next((i for i, f in enumerate(filas) if f[2] in ("del", "vacio") or f[5] in ("add", "vacio")), 0)
    alto = min(620, max(110, len(filas) * ALTO_FILA + 22))

    def columna(lado: int) -> str:
        n, t, c = (0, 1, 2) if lado == 0 else (3, 4, 5)
        return "".join(
            f'<div class="r {f[c]}"><span class="n">{"" if f[n] is None else f[n]}</span>{html.escape(f[t]) or " "}</div>'
            for f in filas
        )

    codigo = f"""
    <style>
      body {{ margin:0; background:#1a1a1a; }}
      .wrap {{ display:flex; gap:10px; height:{alto}px; font:13px/{ALTO_FILA}px Consolas,Menlo,monospace; color:#d4d4d4; }}
      .col {{ flex:1; overflow:auto; border:1px solid #333; border-radius:4px; background:#1e1e1e; }}
      .in {{ display:inline-block; min-width:100%; }}
      .r {{ display:block; white-space:pre; height:{ALTO_FILA}px; padding-right:8px; }}
      .n {{ display:inline-block; width:3.5em; margin-right:1em; text-align:right; color:#666; user-select:none; }}
      .del {{ background:rgba(190,80,80,.20); }}
      .add {{ background:rgba(90,160,100,.20); }}
      .vacio {{ background:repeating-linear-gradient(45deg,#232323,#232323 6px,#1e1e1e 6px,#1e1e1e 12px); }}
      .sep {{ background:#262626; color:#7a7a7a; font-style:italic; text-align:center; }}
      ::-webkit-scrollbar {{ height:10px; width:10px; }} ::-webkit-scrollbar-thumb {{ background:#3a3a3a; border-radius:5px; }}
      ::-webkit-scrollbar-track {{ background:#1e1e1e; }}
    </style>
    <div class="wrap">
      <div class="col" id="izq"><div class="in">{columna(0)}</div></div>
      <div class="col" id="der"><div class="in">{columna(1)}</div></div>
    </div>
    <script>
      const izq = document.getElementById('izq'), der = document.getElementById('der');
      let dueno = null, timer = null;
      function sync(origen, destino) {{
        if (dueno && dueno !== origen) return;   // ignora el evento que provoca nuestro propio scroll
        dueno = origen;
        destino.scrollTop = origen.scrollTop; destino.scrollLeft = origen.scrollLeft;
        clearTimeout(timer); timer = setTimeout(() => dueno = null, 60);
      }}
      izq.addEventListener('scroll', () => sync(izq, der));
      der.addEventListener('scroll', () => sync(der, izq));
      const y = Math.max(0, {primer} * {ALTO_FILA} - 60);
      izq.scrollTop = y; der.scrollTop = y;
    </script>
    """
    if hasattr(st, "iframe"):  # Streamlit reciente: components.v1.html está deprecado
        st.iframe(codigo.strip(), height=alto + 18)
    else:
        components.html(codigo, height=alto + 18)
    return agregadas, eliminadas


# El proyecto activo se pasa explícitamente al núcleo (ya no se cambia el directorio del proceso).
P = Proyecto(ss.proyecto)


# ---------------------------------- Sidebar ----------------------------------- #
with st.sidebar:
    st.markdown("### Proyecto")
    st.text_input("Carpeta", key="campo_proyecto", on_change=al_escribir_ruta, label_visibility="collapsed")
    st.button("Examinar…", on_click=examinar_carpeta)
    if ss.aviso:
        st.warning(ss.aviso)

    archivos = listar_archivos(str(P.raiz))
    st.markdown("### Archivo")
    archivo_sel = st.selectbox("Archivo", archivos, index=None, key="archivo_sel",
                               placeholder="Escribí para buscar…", label_visibility="collapsed")
    st.caption(f"{len(archivos)} archivos de código")

    with st.expander("Servidor"):
        nombres = list(gestor_config.config.get("perfiles", {}).keys())
        idx = nombres.index(gestor_config.config.get("perfil_activo")) if gestor_config.config.get("perfil_activo") in nombres else 0
        
        # 1. Callback para guardar el perfil al cambiar el selectbox
        def cambiar_perfil():
            gestor_config.config["perfil_activo"] = st.session_state.selector_perfil
            gestor_config.guardar()
            
        perfil_sel = st.selectbox("Perfil", nombres, index=idx, key="selector_perfil", on_change=cambiar_perfil)
        datos_perfil = gestor_config.config.get("perfiles", {}).get(perfil_sel, {})
        
        # 2. Callback para guardar URL y API Key
        def guardar_red():
            gestor_config.config["perfiles"][perfil_sel]["url"] = st.session_state[f"url_{perfil_sel}"]
            gestor_config.config["perfiles"][perfil_sel]["api_key"] = st.session_state[f"api_{perfil_sel}"]
            gestor_config.guardar()

        # Usamos keys dinámicas (f"url_{perfil_sel}") para que al cambiar de perfil, Streamlit limpie la caja de texto
        url_actual = st.text_input("URL", datos_perfil.get("url", ""), key=f"url_{perfil_sel}", on_change=guardar_red)
        api_key_actual = st.text_input("API Key", datos_perfil.get("api_key", ""), type="password", key=f"api_{perfil_sel}", on_change=guardar_red)
        
        # Listar modelos disponibles
        try:
            modelos_disp = listar_modelos(url_actual)
        except Exception:
            modelos_disp = []
            
        modelo_guardado = datos_perfil.get("modelo", "")
        
        # 3. Callback para guardar el modelo
        def guardar_modelo():
            gestor_config.config["perfiles"][perfil_sel]["modelo"] = st.session_state[f"mod_{perfil_sel}"]
            gestor_config.guardar()

        if modelos_disp:
            idx_mod = modelos_disp.index(modelo_guardado) if modelo_guardado in modelos_disp else 0
            st.selectbox("Modelo", modelos_disp, index=idx_mod, key=f"mod_{perfil_sel}", on_change=guardar_modelo)
        else:
            st.text_input("Modelo", modelo_guardado, key=f"mod_{perfil_sel}", on_change=guardar_modelo, help="No se pudo conectar para listar modelos.")
            
        if st.button("Probar conexión"):
            if modelos_disp:
                st.success("Conectado")
                st.caption(f"Modelos disponibles: {', '.join(modelos_disp)}")
            else:
                st.error("No hay respuesta del servidor o la API Key es inválida.")

    with st.expander("Opciones"):
        sin_mapa = st.checkbox("No enviar el mapa del proyecto")
        ver_pens = st.checkbox("Mostrar razonamiento en vivo", value=True)
        solo_cambios = st.checkbox("Diff: solo zonas modificadas", value=True)

    if archivo_sel:
        bk = P.ultimo_backup(archivo_sel)
        if bk:
            with st.expander("Deshacer"):
                st.caption(f"Último backup: {bk.name}")
                if st.button("Restaurar este backup"):
                    previo, _ = leer_archivo(bk)
                    _, eol_actual = leer_archivo(P.abs(archivo_sel))
                    P.hacer_backup(archivo_sel)  # el estado actual también queda respaldado
                    escribir_archivo(P.abs(archivo_sel), previo, eol_actual)
                    ss.msg, ss.resultado = f"Restaurado {archivo_sel} desde {bk.name}", None
                    st.rerun()

# ---------------------------------- Principal --------------------------------- #
st.markdown(f"<h1>{SVG_NINA} NINA - Pair Programmer</h1>", unsafe_allow_html=True)
st.caption("Local · stateless · cada pedido arranca de cero")
if ss.msg:
    st.success(ss.msg)
    ss.msg = None

if not archivo_sel:
    st.info("Elegí un archivo en la barra lateral para empezar.")
    st.stop()
ruta = P.abs(archivo_sel)

forzar = True
if P.git_archivo_sucio(ruta):
    st.warning(f"`{archivo_sel}` tiene cambios sin commitear en Git: lo que haga la IA se va a mezclar con los tuyos.")
    forzar = st.checkbox("Entiendo el riesgo, modificar igual")

instruccion = st.text_area("Instrucción", height=110, label_visibility="collapsed",
                           placeholder=f"¿Qué querés que haga la IA con {archivo_sel}?")
generar = st.button("Generar", type="primary", disabled=not forzar)

# --------------------------------- Generación --------------------------------- #
if generar:
    if not instruccion.strip():
        st.warning("Escribí una instrucción primero.")
    else:
        ss.guardado = False
        ss.resultado, ss.pens, ss.metricas = None, "", None
        ctx = preparar_contexto(P, ruta, instruccion, sin_mapa)
        st.caption(f"Contexto ~{ctx.tokens} tokens · mapa {ctx.mapa_usados}/{ctx.mapa_total} archivos · "
                   f"convenciones: {'sí' if ctx.hay_convenciones else 'no'}")
        if ctx.tokens > MAX_TOKENS_PROMPT:
            st.warning(f"El prompt supera ~{MAX_TOKENS_PROMPT} tokens; puede degradar la calidad o la velocidad.")

        estado = st.status("Esperando al modelo…", expanded=True)
        with estado:
            z_pens, z_resp, z_aviso = st.empty(), st.empty(), st.empty()

        intento = {"pens": "", "resp": "", "fase": None, "t_p": 0.0, "t_r": 0.0}
        todas_stats = []
        ultimo_razonamiento = {"t": ""}

        def pintar_pens(t):
            z_pens.markdown(f'<div class="pensando">{html.escape(t[-2500:])}</div>', unsafe_allow_html=True)

        def al_pensar(t):
            intento["pens"] = ultimo_razonamiento["t"] = t
            if intento["fase"] is None:
                intento["fase"] = "pensando"
                estado.update(label="Pensando…")
            if ver_pens and time.monotonic() - intento["t_p"] > 0.15:
                intento["t_p"] = time.monotonic()
                pintar_pens(t)

        def al_responder(t):
            intento["resp"] = t
            if intento["fase"] != "respondiendo":
                intento["fase"] = "respondiendo"
                estado.update(label="Escribiendo cambios…")
            if time.monotonic() - intento["t_r"] > 0.15:
                intento["t_r"] = time.monotonic()
                z_resp.code(t[-4000:], language=None)

        def llamar(mensajes):
            intento.update(pens="", resp="", fase=None)
            z_pens.empty()
            z_resp.empty()
            try:
                # Al no pasarle url ni modelo, stream_llm lee directamente el perfil activo guardado en config.json
                return stream_llm(mensajes, al_responder, al_pensar, on_estadisticas=todas_stats.append)
            finally:  # último repintado sin throttle
                if ver_pens and intento["pens"]:
                    pintar_pens(intento["pens"])
                if intento["resp"]:
                    z_resp.code(intento["resp"][-4000:], language=None)

        def al_reintentar(n, errores):
            z_aviso.warning(f"Reintento automático {n}/{MAX_INTENTOS - 1}: " + " | ".join(errores)[:400])

        t0 = time.monotonic()
        try:
            res = generar_cambio(ctx, llamar, al_reintentar)
        except ErrorLLM as e:
            estado.update(label="Error del servidor", state="error", expanded=True)
            st.error(str(e))
        else:
            seg = time.monotonic() - t0
            reales = bool(todas_stats) and all(s.get("tokens_entrada") and s.get("tokens_salida") for s in todas_stats)
            entrada = sum(s["tokens_entrada"] for s in todas_stats) if reales else ctx.tokens
            salida = sum(s["tokens_salida"] for s in todas_stats) if reales else estimar_tokens(res.respuesta + ultimo_razonamiento["t"])
            tps = next((s["tps"] for s in reversed(todas_stats) if s.get("tps")), None)
            ss.metricas = {"entrada": entrada, "salida": salida, "tps": tps, "seg": seg,
                           "exactas": reales, "intentos": res.intentos}
            ss.pens, ss.resultado, ss.ruta = ultimo_razonamiento["t"], res, ruta
            
            if not res.es_valida:
                estado.update(label="Sin cambio válido", state="error", expanded=False)
            else:
                estado.update(label=f"Listo en {seg:.0f}s", state="complete", expanded=False)

# ---------------------------------- Revisión ---------------------------------- #
res = ss.resultado
if res is not None:
    st.divider()
    m = ss.metricas
    if m:
        c1, c2, c3, c4 = st.columns(4)
        aprox = "" if m["exactas"] else "~"
        c1.metric("Entrada", f"{aprox}{m['entrada']} tks")
        c2.metric("Salida", f"{aprox}{m['salida']} tks", help="Incluye el razonamiento del modelo.")
        c3.metric("Velocidad", f"{m['tps']:.1f} t/s" if m["tps"] else "—")
        c4.metric("Tiempo", f"{m['seg']:.1f} s", f"{m['intentos']} intento(s)" if m["intentos"] > 1 else None,
                  delta_color="off")
        if not m["exactas"]:
            st.caption("El servidor no informó el conteo de tokens: los valores con ~ son estimados.")

    if not res.es_valida or not res.cambios:
        st.error("No se obtuvo un cambio válido. No se modificó nada.")
        # Mostrar errores del primer archivo que haya fallado (o generales)
        errores = next((c.errores for c in res.cambios if c.errores), ["No se generaron cambios."])
        for e in errores:
            st.code(e, language=None)
        with st.expander("Respuesta del modelo"):
            st.code(res.respuesta or "(vacía)", language=None)
    else:
        st.markdown(f"### Cambios propuestos ({len(res.cambios)} archivos)")
        
        hay_cambios_reales = False
        rutas_a_guardar = []
        
        # 1. Renderizar cada archivo modificado
        for cambio in res.cambios:
            st.markdown(f"**Archivo:** `{cambio.clave}`")
            
            for aviso in cambio.avisos:
                st.warning(aviso)
                
            recorte = aviso_recorte(cambio.original, cambio.nuevo)
            if recorte:
                st.error(recorte)
                
            if cambio.nuevo == cambio.original:
                st.info("La respuesta no produce cambios en este archivo.")
            else:
                hay_cambios_reales = True
                rutas_a_guardar.append(cambio)
                a, b = renderizar_diff(cambio.original, cambio.nuevo, solo_cambios)
                st.caption(f"`{cambio.clave}`   ·   +{a}  −{b}")
            st.write("") # Espaciador

        # 2. Botones de acción globales
        b1, b2, _ = st.columns([1, 1, 5])
        
        if b1.button("Aplicar y guardar todo", type="primary", disabled=not hay_cambios_reales or ss.guardado):
            # Validar que ningún archivo haya cambiado en disco mientras leíamos
            archivos_sucios = [c.clave for c in rutas_a_guardar if archivo_cambio_en_disco(P.abs(c.clave), c.original)]
            
            if archivos_sucios:
                st.error(f"Los siguientes archivos cambiaron en disco desde que se generó la propuesta: {', '.join(archivos_sucios)}. No se guardó nada: volvé a generar.")
            else:
                # Hacer backup en lote
                rutas_abs = [P.abs(c.clave) for c in rutas_a_guardar]
                backup = P.hacer_backup(rutas_abs)
                
                # Escribir todos los archivos
                for c in rutas_a_guardar:
                    escribir_archivo(P.abs(c.clave), c.nuevo, c.eol)
                    # Actualizamos el mapa atómicamente por cada archivo guardado (Fase 0.8)
                    P.actualizar_archivo_en_mapa(c.clave)
                    
                ss.msg = f"{len(rutas_a_guardar)} archivo(s) guardado(s). Lote de backup: {backup.name}"
                ss.guardado = True  # Mantiene el diff en pantalla pero bloquea el botón
                st.rerun()
                
        if b2.button("Descartar"):
            ss.resultado, ss.msg = None, "Cambios descartados."
            st.rerun()
            
        with st.expander("Respuesta completa del modelo"):
            st.code(res.respuesta, language=None)

    if ss.pens:
        with st.expander("Razonamiento del modelo"):
            st.markdown(f'<div class="pensando" style="max-height:420px">{html.escape(ss.pens)}</div>',
                        unsafe_allow_html=True)