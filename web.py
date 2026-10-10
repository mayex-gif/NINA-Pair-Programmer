"""
web.py — Interfaz Streamlit del motor.   Ejecutar:  streamlit run web.py
Reutiliza el núcleo del paquete nina/ (contexto, LLM, auto-healing, git, backups): CLI y web se comportan igual.
Tema gris oscuro: copiá la carpeta .streamlit/ junto a este archivo (o a ~/.streamlit).
"""
import html
import json
import os
import re
import shutil
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from nina.bloques import interpretar_respuesta_lote
from nina.config import (
    API_URL, EXTENSIONES_EDITABLES, IGNORE_DIRS, MAX_INTENTOS, MAX_TOKENS_PROMPT, MODELO, PERFIL, gestor_config
)
from nina.contexto import aviso_recorte, estimar_tokens, generar_cambio, preparar_contexto
from nina.diff import colapsar, filas_alineadas
from nina.llm import ErrorLLM, listar_modelos, stream_llm
from nina.proyecto import Proyecto, archivo_cambio_en_disco, escribir_archivo, leer_archivo
from nina.vigia import iniciar_vigia_background

import base64

ALTO_FILA = 20  # px; fijo para poder calcular el scroll al primer cambio

# 1. Metadatos de la página: Título actualizado
st.set_page_config(page_title="NINA - Pair Programmer", layout="wide")

# 2. Logo de NINA (Tamaño reducido: width y height a 45)
SVG_NINA = """
<svg width="45" height="45" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 320" role="img" aria-label="Nina" xmlns:c2pa="http://c2pa.org/manifest"><metadata><c2pa:manifest>AAAWgmp1bWIAAAAeanVtZGMycGEAEQAQgAAAqgA4m3EDYzJwYQAAABZcanVtYgAAAEdqdW1kYzJtYQARABCAAACqADibcQN1cm46YzJwYTpjYjc5NzIyYy1hZWFhLTQxMTUtYTEyNS1hODc2NzI0MzhiNWMAAAADl2p1bWIAAAApanVtZGMyYXMAEQAQgAAAqgA4m3EDYzJwYS5hc3NlcnRpb25zAAAAALxqdW1iAAAARGp1bWRjYm9yABEAEIAAAKoAOJtxE2MycGEuaW5ncmVkaWVudC52MwAAAAAYYzJzaJBIq43zMf/41we0PYxpaJUAAABwY2JvcqNpZGM6Zm9ybWF0bWltYWdlL3N2Zyt4bWxqaW5zdGFuY2VJRHgseG1wOmlpZDo1MjFlNjBjNC0yMjc0LTQ5YmUtODVkMy0wZDVhMzVmMjY2MWZscmVsYXRpb25zaGlwaHBhcmVudE9mAAAB4mp1bWIAAABBanVtZGNib3IAEQAQgAAAqgA4m3ETYzJwYS5hY3Rpb25zLnYyAAAAABhjMnNoZrtNqs3t4QApjBpbq2bsdwAAAZljYm9yomdhY3Rpb25zgqJmYWN0aW9ua2MycGEub3BlbmVkanBhcmFtZXRlcnOha2luZ3JlZGllbnRzgaJjdXJseC1zZWxmI2p1bWJmPWMycGEuYXNzZXJ0aW9ucy9jMnBhLmluZ3JlZGllbnQudjNkaGFzaFggV5w5FYW1ELM8/f8Oxe5YglLoobwNnq0fU6QqilKadeakZmFjdGlvbngdY29tLmFudGhyb3BpYy5jbGF1ZGUucHJvdmlkZWRqcGFyYW1ldGVyc6F4H2NvbS5hbnRocm9waWMub3JpZ2luLWNvbmZpZGVuY2VndW5rbm93bmtkZXNjcmlwdGlvbnhmQ2xhdWRlIHByb3ZpZGVkIHRoaXMgZmlsZSBhdCB0aGUgcmVxdWVzdCBvZiBhIHVzZXIgYW5kIG1heSBoYXZlIGNyZWF0ZWQgb3IgbW9kaWZpZWQgdGhlIGZpbGUgY29udGVudHMubXNvZnR3YXJlQWdlbnShZG5hbWVmQ2xhdWRlcmFsbEFjdGlvbnNJbmNsdWRlZPUAAADIanVtYgAAAEBqdW1kY2JvcgARABCAAACqADibcRNjMnBhLmhhc2guZGF0YQAAAAAYYzJzaKWkID8rLI4dtKCR2AkbVpAAAACAY2JvcqVjYWxnZnNoYTI1NmNwYWRNAAAAAAAAAAAAAAAAAGRoYXNoWCArDGTD15EMi1xl2G8OxQ8eHAyXB2pADL1uMgO/DVyxFGRuYW1lbmp1bWJmIG1hbmlmZXN0amV4Y2x1c2lvbnOBomVzdGFydBiaZmxlbmd0aBkeBAAAAj5qdW1iAAAAJ2p1bWRjMmNsABEAEIAAAKoAOJtxA2MycGEuY2xhaW0udjIAAAACD2Nib3KlY2FsZ2ZzaGEyNTZpc2lnbmF0dXJleE1zZWxmI2p1bWJmPS9jMnBhL3VybjpjMnBhOmNiNzk3MjJjLWFlYWEtNDExNS1hMTI1LWE4NzY3MjQzOGI1Yy9jMnBhLnNpZ25hdHVyZWppbnN0YW5jZUlEeCx4bXA6aWlkOjk1NTVmNGExLTVjZjgtNDIyYS1hNDc3LTMyZGEzNmE3NGZlN3JjcmVhdGVkX2Fzc2VydGlvbnODomN1cmx4LXNlbGYjanVtYmY9YzJwYS5hc3NlcnRpb25zL2MycGEuaW5ncmVkaWVudC52M2RoYXNoWCBXnDkVhbUQszz9/w7F7liCUuihvA2erR9TpCqKUpp15qJjdXJseCpzZWxmI2p1bWJmPWMycGEuYXNzZXJ0aW9ucy9jMnBhLmFjdGlvbnMudjJkaGFzaFgg7Oe7vMGh3qR9eJn2EOPMKR/DoPWYQKoeNVxz3W65uPuiY3VybHgpc2VsZiNqdW1iZj1jMnBhLmFzc2VydGlvbnMvYzJwYS5oYXNoLmRhdGFkaGFzaFggrOg8CaVvNyAL4VHrxlX7ICvwWP/NB5/2218QOEYHAud0Y2xhaW1fZ2VuZXJhdG9yX2luZm+jZG5hbWVvQW50aHJvcGljIEZpbGVzZ3ZlcnNpb25lMS4wLjBrc3BlY1ZlcnNpb25lMi40LjAAABA4anVtYgAAAChqdW1kYzJjcwARABCAAACqADibcQNjMnBhLnNpZ25hdHVyZQAAABAIY2JvctKEWQISogEmGCFZAgowggIGMIIBjaADAgECAhRA5aAK7sI50L64g/oGQgU9Z1UTADAKBggqhkjOPQQDAzBJMRcwFQYDVQQKEw5BbnRocm9waWMsIFBCQzEuMCwGA1UEAxMlQW50aHJvcGljIENvbnRlbnQgQ3JlZGVudGlhbHMgUm9vdCBDQTAeFw0yNjA4MDcxODQzNTZaFw0yODA4MDYxOTQzNTZaMEQxFzAVBgNVBAoTDkFudGhyb3BpYywgUEJDMSkwJwYDVQQDEyBBbnRocm9waWMgQ2xhdWRlIENvbnRlbnQgU2lnbmluZzBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABJh6CmvLUBgFFNU0vUKlOVtE6djd17L5SuwX0LemFisBM3dkd/3cyjxFA3Qo5S46fX0/ihY0VZ7mfb9KF703t5OjWDBWMA4GA1UdDwEB/wQEAwIHgDAVBgNVHSUEDjAMBgorBgEEAYPoXgIBMAwGA1UdEwEB/wQCMAAwHwYDVR0jBBgwFoAUzlHiBIFOZFsj+OPEz5o+nMHXXMIwCgYIKoZIzj0EAwMDZwAwZAIwMXMdFJ4BetLLVY7ORuE9noqbbAZOZn/aArXyTwFAZfKrPzxF2vPoJNf1+UCdg1XGAjBwX1zd9WGqYkqmL5SFqw1QySjr1zJfpJM9+1rdDwSPLMOPOjKuiXjoU/pUUeG9RwmhY3BhZFkNngAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAPZYQGoggZrMXYywHAOrYHvK33zj8EmcqKdn0ClA2FKYKa8fdJQmk2fo17tcW1fkgeHzcq7ZRmRc5iDxtU04+82BoiM=</c2pa:manifest></metadata>
  <title>Nina</title>
  <path fill="currentColor" d="M289,38 C291,28 284,17 273,16 C265,16 258,22 252,24 C244,18 236,14 230,7 L236,16 C226,12 212,12 202,18 C186,26 176,42 174,58 C160,64 148,76 142,92 L147,93 L141,101 L147,103 C144,115 144,128 150,138 L156,142 L148,150 C128,172 100,194 82,218 C68,234 58,248 56,262 C48,270 36,268 32,256 C30,248 32,242 38,238 C26,238 14,252 12,270 C11,292 28,308 50,308 L150,304 C168,304 178,302 184,294 C186,300 188,303 192,303 L225,303 C231,301 233,293 229,288 C238,262 242,230 246,198 C249,174 252,156 244,138 L238,134 L246,132 C238,120 236,108 246,96 C252,88 256,78 258,68 C266,62 276,58 282,50 C287,46 289,42 289,38 Z"/>
</svg>
"""
# El SVG trae un bloque <metadata> (manifiesto C2PA en base64, ~6 KB) que no aporta nada: se saca al cargar.
SVG_NINA = re.sub(r"<metadata>.*?</metadata>", "", SVG_NINA, flags=re.S)

# 3. Favicon (SVG -> base64) + JS de la interfaz: favicon, cronómetros, auto-scroll y contador de tokens.
b64_svg = base64.b64encode(SVG_NINA.encode('utf-8')).decode('utf-8')

JS_NINA = r"""
(() => {
  const win = window.frameElement ? window.parent : window;
  const doc = win.document;
  const VER = 4;
  if (win.__nina && win.__nina.ver === VER) return;       // idempotente: no se duplica en cada rerun
  if (win.__nina && win.__nina.stop) win.__nina.stop();   // versión vieja (hot reload): se desarma
  const S = { ver: VER, pinned: true, intent: 0 };
  const intervalos = [], limpiezas = [];
  S.stop = () => { intervalos.forEach(clearInterval); limpiezas.forEach(f => f()); };
  win.__nina = S;
  const escuchar = (el, ev, fn, opt) => { el.addEventListener(ev, fn, opt); limpiezas.push(() => el.removeEventListener(ev, fn, opt)); };

  // 1. Favicon (se reemplaza, no se acumula)
  doc.querySelectorAll("link[rel~='icon']").forEach(l => l.remove());
  const link = doc.createElement('link');
  link.rel = 'icon'; link.type = 'image/svg+xml';
  link.href = 'data:image/svg+xml;base64,__FAVICON__';
  doc.head.appendChild(link);

  // 2. Cronómetros en vivo. Streamlit solo manda el texto al empezar y al terminar cada fase;
  //    entre medio los segundos corren acá. Se toca el nodo de texto (no innerText) para no romper a React.
  const RE = /^(.*?)(Pensando|Escribiendo código) durante (\d+) segundos\.\.\.$/;
  function cronometros() {
    const ahora = Date.now();
    doc.querySelectorAll('details > summary').forEach(sm => {
      const p = sm.querySelector('p');
      const nodo = p && [...p.childNodes].find(n => n.nodeType === 3);
      const m = nodo && RE.exec(nodo.nodeValue.trim());
      if (!m) { sm.__nkey = null; return; }
      const clave = m[1] + m[2];
      if (sm.__nkey !== clave) { sm.__nkey = clave; sm.__nt0 = ahora; }
      const txt = `${m[1]}${m[2]} durante ${Math.floor((ahora - sm.__nt0) / 1000)} segundos...`;
      if (nodo.nodeValue !== txt) nodo.nodeValue = txt;
    });
  }

  // 3. Auto-scroll. Streamlit scrollea el contenedor stMain, no la ventana.
  //    Sigue el final del bloque en vivo (.nina-vivo) solo mientras se genera y mientras no te hayas ido hacia arriba.
  const scroller = () => doc.querySelector('[data-testid="stMain"]') || doc.scrollingElement;
  const corriendo = () => !!doc.querySelector('[data-testid="stStatusWidget"]');
  const vivo = () => {
    const v = [...doc.querySelectorAll('.nina-vivo')].filter(e => !e.closest('[data-stale="true"]'));
    return v.length ? v[v.length - 1] : null;
  };
  const exceso = (sc, t) => t.getBoundingClientRect().bottom - (sc.getBoundingClientRect().bottom - 28);
  function seguir() {
    const sc = scroller(), t = vivo();
    if (!sc || !t || !corriendo() || !S.pinned) return;
    const d = exceso(sc, t);
    if (d > 1) sc.scrollTop += d;
  }
  let pendiente = false;
  const agendar = () => { if (!pendiente) { pendiente = true; requestAnimationFrame(() => { pendiente = false; seguir(); botonFinal(); }); } };

  const intencion = () => { S.intent = Date.now(); };
  escuchar(doc, 'wheel', e => { if (e.deltaY < 0) { S.pinned = false; } intencion(); }, { passive: true });
  escuchar(doc, 'touchmove', intencion, { passive: true });
  escuchar(doc, 'keydown', e => { if (['PageUp', 'ArrowUp', 'Home'].includes(e.key)) S.pinned = false; intencion(); });
  escuchar(doc, 'pointerdown', intencion);
  escuchar(doc, 'scroll', () => {
    // Solo un scroll provocado por vos (no el nuestro ni el de Streamlit al reacomodar) cambia el anclaje.
    if (Date.now() - S.intent > 800) return;
    const sc = scroller(), t = vivo();
    if (sc && t && exceso(sc, t) <= 40) S.pinned = true;
    botonFinal();
  }, true);

  // Botón flotante para volver a engancharse
  const btn = doc.createElement('button');
  btn.textContent = '↓ Seguir al final';
  btn.style.cssText = 'position:fixed;right:28px;bottom:24px;z-index:99999;display:none;padding:6px 14px;' +
    'border-radius:16px;border:1px solid #444;background:#2a2a2a;color:#ddd;font:600 .8rem sans-serif;cursor:pointer;' +
    'box-shadow:0 2px 8px rgba(0,0,0,.5)';
  btn.onclick = () => { S.pinned = true; seguir(); botonFinal(); };
  doc.body.appendChild(btn);
  limpiezas.push(() => btn.remove());
  function botonFinal() { btn.style.display = (corriendo() && vivo() && !S.pinned) ? 'block' : 'none'; }

  // 4. Contador de tokens desglosado (Adentro de la caja)
  function contador() {
    const ta = doc.querySelector('textarea[aria-label="Instrucción"]');
    if (!ta) return;
    const widget = ta.closest('[data-testid="stTextArea"]');
    if (!widget) return;
    let c = ta.__ncontador;
    if (!c || !c.isConnected) {
      widget.style.position = 'relative';
      c = doc.createElement('div');
      c.style.cssText = 'position:absolute; bottom:25px; right:25px; font-size:0.75rem; color:#a0a0a0; font-weight:600; pointer-events:none; background:rgba(30,30,30,0.9); padding:4px 8px; border-radius:4px; border:1px solid #444; z-index:10; display:flex; gap:8px; white-space:nowrap;';
      widget.appendChild(c);
      ta.__ncontador = c;
    }
    
    let baseData = doc.getElementById('nina-tokens-data');
    let tksConv = parseInt(baseData?.getAttribute('data-conv')) || 0;
    let tksMap = parseInt(baseData?.getAttribute('data-map')) || 0;
    let tksArch = parseInt(baseData?.getAttribute('data-arch')) || 0;
    let tksBase = parseInt(baseData?.getAttribute('data-base')) || 0; // Total real incluyendo XML
    
    const promptTks = Math.floor(ta.value.length / 3);
    const total = promptTks + tksMap + tksConv;
    
    let textParts = [];
    if (promptTks >= 0) textParts.push(`Prompt: ~${promptTks}`);
    if (tksArch > 0) textParts.push(`Archivo: ~${tksArch}`);
    if (tksMap > 0) textParts.push(`Mapa: ~${tksMap}`);
    if (tksConv > 0) textParts.push(`Conv: ~${tksConv}`);
    if (total > 0) textParts.push(`TOTAL: ~${total} tks`);
    
    const txt = textParts.join(' | ');
    if (c.innerText !== txt) c.innerText = txt;
    if (!ta.__nbound) { ta.__nbound = true; ta.addEventListener('input', contador); }
  }

  const obs = new MutationObserver(() => { agendar(); });
  obs.observe(doc.body, { childList: true, subtree: true, characterData: true });
  limpiezas.push(() => obs.disconnect());
  intervalos.push(setInterval(() => { cronometros(); contador(); agendar(); }, 400));
})();
""".replace("__FAVICON__", b64_svg)


def inyectar_js(codigo_js: str):
    """Ejecuta JS en la página. st.html NO ejecuta <script> salvo que se lo pidas (Streamlit >= 1.52);
    con versiones viejas se usa un iframe de altura 0 (el JS llega a la página con window.parent)."""
    try:
        st.html(f"<script>{codigo_js}</script>", unsafe_allow_javascript=True)
    except TypeError:
        html_code = f"<script>{codigo_js}</script>"
        b64_html = base64.b64encode(html_code.encode('utf-8')).decode('utf-8')
        components.iframe(f"data:text/html;base64,{b64_html}", height=0)


inyectar_js(JS_NINA)

# 4. Estilos visuales base
st.markdown("""
<style>
  .block-container { padding-top: 4.5rem; max-width: 1500px; }
  h1 { font-size: 1.35rem !important; font-weight: 600 !important; padding: 0 0 .2rem 0 !important; }
  h3 { font-size: 1.02rem !important; font-weight: 600 !important; }
  footer, #MainMenu { visibility: hidden; }
  [data-testid="stSidebar"] { border-right: 1px solid #2e2e2e; }
  [data-testid="stMetricValue"] { font-size: 1.3rem; }
  [data-testid="stMetricLabel"] p { color: #8a8a8a; font-size: .74rem; text-transform: uppercase; letter-spacing: .05em; }
  
  /* Liberamos el texto: sin max-height ni overflow para scrollear nativo */
  .pensando { color:#8a8a8a; font:italic .84rem ui-monospace,Consolas,monospace; white-space:pre-wrap;
              border-left:2px solid #3a3a3a; padding-left:.75rem; }
              
  .respuesta-vivo { background:#1e1e1e; border:1px solid #333; padding:1em; border-radius:4px; 
                    font:13px Consolas,monospace; white-space:pre-wrap; color:#d4d4d4; }
  /* Streamlit atenúa lo 'viejo' durante cada rerun: lo dejamos nítido para que no parpadee */
  [data-stale="true"] { opacity: 1 !important; transition: none !important; }
</style>
""", unsafe_allow_html=True)

ss = st.session_state
for clave, valor in {"resultado": None, "ruta": None, "msg": None, "pens": "", "metricas": None,
                     "proyecto": os.getcwd(), "campo_proyecto": os.getcwd(), "aviso": None, "guardado": False,
                     "traza": None, "error_llm": None, "ctx_info": None, "modelo_usado": "", "ultimo_lote": None}.items():
    ss.setdefault(clave, valor)


def limpiar_resultado(conservar_resultado: bool = False):
    """Descarta lo que quedó en pantalla de una generación (propuesta, razonamiento, métricas)."""
    if not conservar_resultado:
        ss.resultado = None
    ss.traza, ss.metricas, ss.error_llm, ss.ctx_info, ss.guardado = None, None, None, None, False


def al_cambiar_archivo():
    limpiar_resultado()   # cambiar de archivo equivale a descartar


# ------------------------------- Proyecto / archivos -------------------------- #
def aplicar_proyecto(ruta_str: str):
    p = Path(ruta_str).expanduser()
    if not p.is_dir():
        ss.aviso = f"No existe la carpeta: {ruta_str}"
        ss.campo_proyecto = ss.proyecto
        return
    ss.proyecto = ss.campo_proyecto = str(p.resolve())
    limpiar_resultado()
    ss.archivo_sel, ss.aviso = None, None


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
    es_nuevo = not viejo.strip()
    todas = filas_alineadas(viejo, nuevo)
    agregadas = sum(1 for f in todas if f[5] == "add")
    eliminadas = sum(1 for f in todas if f[2] == "del")
    filas = colapsar(todas) if solo_cambios else todas
    primer = next((i for i, f in enumerate(filas) if f[2] in ("del", "vacio") or f[5] in ("add", "vacio")), 0)
    alto = min(620, len(filas) * ALTO_FILA)

    def columna(lado: int) -> str:
        n, t, c = (0, 1, 2) if lado == 0 else (3, 4, 5)
        return "".join(
            f'<div class="r {f[c]}"><span class="n">{"" if f[n] is None else f[n]}</span>{html.escape(f[t]) or " "}</div>'
            for f in filas
        )

    col_izq = f'<div class="col" id="izq"><div class="in">{columna(0)}</div></div>' if not es_nuevo else ''

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
      
      /* Estilos del boton copiar integrado */
      .copy-btn {{
          position: absolute;
          top: 8px; right: 15px;
          background: #2d2d2d;
          border: 1px solid #4d4d4d;
          color: #a0a0a0;
          border-radius: 4px;
          padding: 4px 10px;
          font-size: 11px;
          font-family: sans-serif;
          cursor: pointer;
          z-index: 10;
          transition: 0.2s;
      }}
      .copy-btn:hover {{ background: #3d3d3d; color: #fff; }}
      
      ::-webkit-scrollbar {{ height:10px; width:10px; }} 
      ::-webkit-scrollbar-thumb {{ background:#3a3a3a; border-radius:5px; }}
      ::-webkit-scrollbar-track {{ background:#1e1e1e; }}
    </style>
    <div class="wrap">
      {col_izq}
      <div style="position:relative; flex:1; display:flex; min-width:0;">
          <div class="col" id="der" style="width:100%;"><div class="in">{columna(1)}</div></div>
          <button class="copy-btn" id="btn-copy">Copiar</button>
      </div>
    </div>
    
    <!-- Texto puro seguro para copiar -->
    <textarea id="raw-nuevo" style="display:none;">{html.escape(nuevo)}</textarea>
    
    <script>
      const der = document.getElementById('der');
      const izq = document.getElementById('izq');
      let dueno = null, timer = null;
      function sync(origen, destino) {{
        if (!destino || (dueno && dueno !== origen)) return;
        dueno = origen;
        destino.scrollTop = origen.scrollTop; destino.scrollLeft = origen.scrollLeft;
        clearTimeout(timer); timer = setTimeout(() => dueno = null, 60);
      }}
      if (izq) {{
          izq.addEventListener('scroll', () => sync(izq, der));
          der.addEventListener('scroll', () => sync(der, izq));
      }}
      const y = Math.max(0, {primer} * {ALTO_FILA} - 60);
      if (izq) izq.scrollTop = y;
      der.scrollTop = y;
      
      // Lógica del portapapeles
      const btnCopy = document.getElementById('btn-copy');
      if (btnCopy) {{
          btnCopy.addEventListener('click', () => {{
              const code = document.getElementById('raw-nuevo').value;
              const temp = document.createElement('textarea');
              temp.value = code;
              document.body.appendChild(temp);
              temp.select();
              try {{
                  document.execCommand('copy');
                  btnCopy.innerText = 'Copiado';
                  setTimeout(() => {{ btnCopy.innerText = 'Copiar'; }}, 2000);
              }} catch(e) {{
                  btnCopy.innerText = 'Error';
              }}
              document.body.removeChild(temp);
          }});
      }}
    </script>
    """
    b64_html = base64.b64encode(codigo.strip().encode('utf-8')).decode('utf-8')
    components.iframe(f"data:text/html;base64,{b64_html}", height=alto + 4)
    
    return agregadas, eliminadas


# El proyecto activo se pasa explícitamente al núcleo (ya no se cambia el directorio del proceso).
P = Proyecto(ss.proyecto)

# --- MAGIA FASE 4.5: Vigía Integrado ---
@st.cache_resource(show_spinner=False)
def arrancar_vigia(ruta: str):
    iniciar_vigia_background(ruta)

# Esto se ejecuta instantáneamente. Si ya estaba corriendo, el caché de Streamlit lo ignora.
arrancar_vigia(ss.proyecto)
# ---------------------------------------


def ultimo_lote_proyecto():
    """Último lote de backup del proyecto activo (el guardado en esta sesión o, si no hay, el más reciente en disco)."""
    try:
        if ss.ultimo_lote:
            d = Path(ss.ultimo_lote)
            if (d / "manifiesto.json").exists() and d.resolve().is_relative_to(Path(P.raiz).resolve()):
                return d
        lotes = sorted(p for p in Path(P.carpeta_backups).glob("lote_*") if (p / "manifiesto.json").exists())
        return lotes[-1] if lotes else None
    except Exception:
        return None


def restaurar_lote(lote_dir: Path):
    from nina.mapa import guardar_mapa

    manifiesto = json.loads((lote_dir / "manifiesto.json").read_text(encoding="utf-8"))
    rutas_lote = list(manifiesto["archivos"].keys())

    # 1. Backup de seguridad del estado actual antes de pisarlo (así restaurar también se puede deshacer)
    P.hacer_backup(rutas_lote)

    # 2. Revertir cada archivo del manifiesto
    for ruta_rel, datos in manifiesto["archivos"].items():
        ruta_abs = P.abs(ruta_rel)
        if datos["estado"] == "modificado":
            shutil.copy2(lote_dir / datos["backup"], ruta_abs)
            P.actualizar_archivo_en_mapa(ruta_rel)
        elif datos["estado"] == "nuevo":
            if ruta_abs.exists():
                ruta_abs.unlink()
                mapa_actual = P.cargar_mapa()   # Limpiar el mapa si se elimina un archivo
                if ruta_rel in mapa_actual:
                    del mapa_actual[ruta_rel]
                    guardar_mapa(mapa_actual, P.raiz)

    limpiar_resultado()
    ss.msg = f"Restaurado el lote completo ({len(rutas_lote)} archivos)."


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
                               placeholder="Escribí para buscar…", label_visibility="collapsed",
                               on_change=al_cambiar_archivo)
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

        # Usamos keys dinámicas (f"url_{perfil_sel}") para que al cambiar de perfil, Streamlit limpie la caja de ren
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

    lote_dir = None
    if archivo_sel:
        bk_file = P.ultimo_backup(archivo_sel)
        if bk_file:
            lote_dir = bk_file.parent       # el lote más reciente que tocó este archivo
    if lote_dir is None:
        lote_dir = ultimo_lote_proyecto()   # Modo Global: el último lote del proyecto
    if lote_dir:
        with st.expander("Deshacer cambios (Lote completo)"):
            st.caption(f"Lote: {lote_dir.name}")
            if st.button("Restaurar transacción"):
                restaurar_lote(lote_dir)
                st.rerun()

# ---------------------------------- Principal --------------------------------- #
st.markdown(f"<h1>{SVG_NINA} NINA - Pair Programmer</h1>", unsafe_allow_html=True)
st.caption("Local · stateless · cada pedido arranca de cero")
if ss.msg:
    st.success(ss.msg)
    ss.msg = None

if not archivo_sel:
    st.info("💡 **Modo Global:** No hay ningún archivo seleccionado. Podés pedirle a NINA que analice el proyecto o cree archivos nuevos.")
    ruta = None
    forzar = True
    placeholder = "¿Qué querés crear o modificar en el proyecto?"
else:
    ruta = P.abs(archivo_sel)
    forzar = True
    if P.git_archivo_sucio(ruta):
        st.warning(f"`{archivo_sel}` tiene cambios sin commitear en Git: lo que haga la IA se va a mezclar con los tuyos.")
        forzar = st.checkbox("Entiendo el riesgo, modificar igual")
    placeholder = f"¿Qué querés que haga la IA con {archivo_sel}?"

# === CÁLCULO DE TOKENS DESGLOSADO ===
tks_conv = estimar_tokens(P.cargar_convenciones() or "")
tks_arch = estimar_tokens(leer_archivo(P.abs(ruta))[0]) if ruta and P.abs(ruta).exists() else 0
ctx_base = preparar_contexto(P, ruta, "", sin_mapa)
    
tks_map = 0 if sin_mapa else max(0, ctx_base.tokens - tks_conv - tks_arch - 180)

# Inyectamos data-base con el token count real y total de Python
st.markdown(f'<span id="nina-tokens-data" data-conv="{tks_conv}" data-map="{tks_map}" data-arch="{tks_arch}" data-base="{ctx_base.tokens}" style="display:none;"></span>', unsafe_allow_html=True)
# ====================================

instruccion = st.text_area("Instrucción", height=280, label_visibility="collapsed", placeholder=placeholder)
generar = st.button("Generar", type="primary", disabled=not forzar)

# --------------------------------- Generación --------------------------------- #
# Todo lo que se ve durante la generación se guarda en session_state (ss.traza, un registro por intento) y,
# al terminar, la página se redibuja desde ahí. Así el razonamiento y las métricas sobreviven a cualquier rerun
# (guardar, descartar, cambiar una opción...) en vez de desaparecer con los widgets en vivo.
def mostrar_cabecera():
    info = ss.ctx_info
    if not info:
        return
    st.caption(f"Mapa {info['mapa']} archivos · convenciones: {'sí' if info['conv'] else 'no'}")
    if info["tokens"] > MAX_TOKENS_PROMPT:
        st.warning(f"El prompt supera ~{MAX_TOKENS_PROMPT} tokens; puede degradar la calidad o la velocidad.")
    st.markdown(f"**🤖 {ss.modelo_usado}**")


def aplanar_errores(errores) -> list:
    """`generar_cambio` informa los errores como {ruta: [mensajes]}; acá se pasan a lista de textos."""
    if isinstance(errores, dict):
        return [f"{ruta}: {e}" for ruta, lista in errores.items() for e in lista]
    return [str(e) for e in errores]


def msg_rechazo(reg):
    return f"Intento {reg['n']} rechazado, se reintenta contra el original: " + " | ".join(reg["errores"])[:400]


def mostrar_traza():
    traza = ss.traza or []
    for reg in traza:
        pref = f"Intento {reg['n']} · " if len(traza) > 1 else ""
        if reg["estado"] == "en curso":
            st.warning(f"{pref}Interrumpido antes de terminar.")
        if reg["pens"]:
            with st.status(f"{pref}Pensó durante {int(reg['seg_pens'])} segundos", state="complete", expanded=False):
                st.html(f'<div class="pensando">{html.escape(reg["pens"])}</div>')
        if reg["resp"]:
            malo = bool(reg["errores"]) or reg["estado"] == "error"
            etiqueta = (f"{pref}Respuesta rechazada tras {int(reg['seg_cod'])} segundos" if malo
                        else f"{pref}Escribió código durante {int(reg['seg_cod'])} segundos")
            with st.status(etiqueta, state="error" if malo else "complete", expanded=False):
                st.html(f'<div class="respuesta-vivo">{html.escape(reg["resp"])}</div>')
        if reg["errores"]:
            st.warning(msg_rechazo(reg))
    if ss.error_llm:
        st.error(ss.error_llm)


def mostrar_metricas_intentos():
    traza = ss.traza or []
    if len(traza) < 2:
        return
    ok_final = ss.resultado is not None and ss.resultado.es_valida
    filas = []
    for i, r in enumerate(traza):
        s = r["stats"] or {}
        valido = ok_final and i == len(traza) - 1 and not r["errores"]
        filas.append({
            "Intento": str(r["n"]),
            "Resultado": "✅ válido" if valido else "❌ rechazado",
            "Latencia": f"{r['ttft']:.2f} s",
            "Pensamiento": f"{r['seg_pens']:.1f} s",
            "Código": f"{r['seg_cod']:.1f} s",
            "Entrada": str(s.get("tokens_entrada") or "—"),
            "Salida": str(s.get("tokens_salida") or "—"),
            "Tks Pens.": str(s.get("tokens_pens") or "—"),
            "Tks Cód.": str(s.get("tokens_cod") or "—"),
            "Velocidad": f"{s['tps']:.1f} t/s" if s.get("tps") else "—",
            "Tiempo": f"{r['seg']:.1f} s",
        })
    with st.expander("Métricas por intento", expanded=True):
        st.dataframe(filas, hide_index=True)


if generar and not instruccion.strip():
    st.warning("Escribí una instrucción primero.")

if generar and instruccion.strip():
    limpiar_resultado()   # descarta lo que quedó de un intento anterior
    # limpiar_resultado(conservar_resultado=True)   # el diff anterior queda en pantalla hasta que haya uno nuevo
    ctx = preparar_contexto(P, ruta, instruccion, sin_mapa)
    perfil_actual = gestor_config.config.get("perfil_activo")
    ss.modelo_usado = gestor_config.config.get("perfiles", {}).get(perfil_actual, {}).get("modelo", "Modelo LLM")
    ss.ctx_info = {"tokens": ctx.tokens, "mapa": f"{ctx.mapa_usados}/{ctx.mapa_total}", "conv": ctx.hay_convenciones}
    mostrar_cabecera()

    traza = []
    ss.traza = traza          # misma lista: si se corta con "Stop", lo que llegó queda guardado
    zona = st.container()
    w = {"reg": None}         # widgets del intento en curso
    t0_global = time.monotonic()

    def prefijo(reg):
        return f"Intento {reg['n']} · " if reg["n"] > 1 else ""

    def pintar(clave_zona, clase, texto):
        if w.get(clave_zona) is not None:
            w[clave_zona].html(f'<div class="{clase} nina-vivo">{html.escape(texto)}</div>')

    def al_pensar(t):
        reg = w["reg"]
        ahora = time.monotonic()
        reg["pens"] = t
        if reg["t0_pens"] is None:
            reg["t0_pens"] = ahora
            # Recién ahora cambiamos la etiqueta para que el JS empiece a contar
            w["p"].update(label=f"{prefijo(reg)}Pensando durante 0 segundos...")
            
        if ver_pens and ahora - reg["t_pint_p"] > 0.15:
            reg["t_pint_p"] = ahora
            pintar("z_p", "pensando", t)

    def al_responder(t):
        reg = w["reg"]
        ahora = time.monotonic()
        reg["resp"] = t
        if reg["t0_resp"] is None:
            reg["t0_resp"] = ahora
            if reg["t0_pens"] is not None:
                w["p"].update(label=f"{prefijo(reg)}Pensó durante {int(ahora - reg['t0_pens'])} segundos",
                              expanded=False, state="complete")
            else:
                w["p"].update(label=f"{prefijo(reg)}Sin razonamiento", expanded=False, state="complete")
            w["s"] = w["c"].status(f"{prefijo(reg)}Escribiendo código durante 0 segundos...", expanded=True)
            with w["s"]:
                w["z_r"] = st.empty()
        if ahora - reg["t_pint_r"] > 0.15:
            reg["t_pint_r"] = ahora
            pintar("z_r", "respuesta-vivo", t)

    def llamar(mensajes):
        reg = {"n": len(traza) + 1, "pens": "", "resp": "", "t_ini": time.monotonic(), "t0_pens": None,
               "t0_resp": None, "t_pint_p": 0.0, "t_pint_r": 0.0, "ttft": 0.0, "seg_pens": 0.0, "seg_cod": 0.0,
               "seg": 0.0, "stats": None, "errores": None, "estado": "en curso"}
        traza.append(reg)
        w.update(reg=reg, s=None, z_r=None)
        with zona:
            # Arranca diciendo "Esperando..." para no activar el timer JS antes de tiempo
            w["p"] = st.status(f"{prefijo(reg)}Esperando al servidor...", expanded=ver_pens)
            with w["p"]:
                w["z_p"] = st.empty()
            w["c"] = st.empty()

        def guardar_stats(s):
            reg["stats"] = s

        try:
            salida = stream_llm(mensajes, al_responder, al_pensar, on_estadisticas=guardar_stats)
            reg["estado"] = "ok"
            return salida
        except ErrorLLM:
            reg["estado"] = "error"
            raise
        finally:
            fin = time.monotonic()
            primero = reg["t0_pens"] or reg["t0_resp"] or fin
            reg["ttft"] = primero - reg["t_ini"]
            if reg["t0_pens"]:
                reg["seg_pens"] = (reg["t0_resp"] or fin) - reg["t0_pens"]
            if reg["t0_resp"]:
                reg["seg_cod"] = fin - reg["t0_resp"]
            reg["seg"] = fin - reg["t_ini"]
            try:    # pintura final de lo que quedó sin dibujar por el throttle
                if ver_pens and reg["pens"]:
                    pintar("z_p", "pensando", reg["pens"])
                if reg["resp"] and w["z_r"] is not None:
                    pintar("z_r", "respuesta-vivo", reg["resp"])
                    w["s"].update(label=f"{prefijo(reg)}Escribió código durante {int(reg['seg_cod'])} segundos",
                                  expanded=False, state="complete")
                elif reg["t0_resp"] is None:
                    w["p"].update(label=f"{prefijo(reg)}Pensó durante {int(reg['seg_pens'])} segundos",
                                  expanded=False, state="error" if reg["estado"] == "error" else "complete")
            except Exception:
                pass

    def al_reintentar(n, errores):
        reg = traza[-1]
        reg["errores"] = aplanar_errores(errores)
        try:
            if w.get("s") is not None:
                w["s"].update(label=f"{prefijo(reg)}Respuesta rechazada", expanded=False, state="error")
            with zona:
                st.warning(msg_rechazo(reg))
        except Exception:
            pass

    try:
        res = generar_cambio(ctx, P, llamar, al_reintentar)
    except ErrorLLM as e:
        ss.error_llm = str(e)
        st.rerun()
    else:
        seg_total = time.monotonic() - t0_global
        if not res.es_valida and traza:
            traza[-1]["errores"] = next((c.errores for c in res.cambios if c.errores), None) or ["No se generaron cambios."]
        todas_stats = [r["stats"] for r in traza if r["stats"]]
        reales = bool(todas_stats) and len(todas_stats) == len(traza) and \
            all(s.get("tokens_entrada") and s.get("tokens_salida") for s in todas_stats)
        entrada = sum(s["tokens_entrada"] for s in todas_stats) if reales else ctx.tokens
        salida = sum(s["tokens_salida"] for s in todas_stats) if reales else estimar_tokens(res.respuesta + traza[-1]["pens"])
        tps = next((s["tps"] for s in reversed(todas_stats) if s.get("tps")), None)
        ss.metricas = {"entrada": entrada, "salida": salida, "tps": tps, "seg": seg_total,
                       "ttft": traza[0]["ttft"], "exactas": reales, "intentos": res.intentos}
        ss.resultado, ss.ruta = res, ruta
        st.rerun()   # redibuja todo desde session_state: nada de lo mostrado depende de widgets en vivo

elif ss.traza or ss.error_llm:
    mostrar_cabecera()
    mostrar_traza()

# ---------------------------------- Revisión ---------------------------------- #
res = ss.resultado
if res is not None:
    st.divider()
    m = ss.metricas
    if m:
        c1, c2, c3, c4, c5 = st.columns(5)
        aprox = "" if m["exactas"] else "~"
        c1.metric("Latencia", f"{m['ttft']:.2f} s")
        c2.metric("Entrada", f"{aprox}{m['entrada']} tks")
        
        # ARREGLO: Leer el pensamiento del último intento real
        ultimo_pensamiento = ss.traza[-1]["pens"] if ss.traza else ""
        tks_p = m.get("tokens_pens") or (len(ultimo_pensamiento) // 3)
        tks_c = m.get("tokens_cod") or max(0, m['salida'] - tks_p)
        
        c3.metric("Salida", f"{aprox}{m['salida']} tks", f"{tks_p} pens / {tks_c} cód", delta_color="off")
        c4.metric("Velocidad", f"{m['tps']:.1f} t/s" if m["tps"] else "—")
        c5.metric("Tiempo Total", f"{m['seg']:.1f} s", f"{m['intentos']} intento(s)" if m["intentos"] > 1 else None, delta_color="off")
        if not m["exactas"]:
            st.caption("El servidor no informó el conteo de tokens: los valores con ~ son estimados.")
    mostrar_metricas_intentos()

    if not res.es_valida or not res.cambios:
        st.error("No se obtuvo un cambio válido. No se modificó nada.")
        # Mostrar errores del primer archivo que haya fallado (o generales)
        errores = next((c.errores for c in res.cambios if c.errores), ["No se generaron cambios."])
        for e in errores:
            st.code(e, language=None)
        
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
        
        if b1.button("Aplicar y guardar todo", type="primary", disabled=not hay_cambios_reales or ss.guardado, key="btn_guardar_lote"):
            archivos_con_conflictos = []
            
            for c in rutas_a_guardar:
                ruta_abs = P.abs(c.clave)
                if not ruta_abs.exists() and not c.original.strip():
                    continue
                
                # FASE 4.3: Fusión a tres bandas si el archivo cambió en disco
                if archivo_cambio_en_disco(ruta_abs, c.original):
                    fresco, eol_fresco = leer_archivo(ruta_abs)
                    
                    # Re-evaluamos la respuesta cruda de la IA contra el archivo fresco
                    nuevos_fresco, errs, _ = interpretar_respuesta_lote(res.respuesta, {c.clave: fresco}, c.clave)
                    
                    if errs.get(c.clave):
                        # La IA y vos tocaron la misma zona. Conflicto insalvable.
                        archivos_con_conflictos.append(c.clave)
                    else:
                        # Fusión exitosa. Actualizamos los datos antes de escribir.
                        c.original = fresco
                        c.nuevo = nuevos_fresco[c.clave]
                        c.eol = eol_fresco
            
            if archivos_con_conflictos:
                st.error(f"⚠️ **Conflicto de Merge:** Modificaste a mano `{', '.join(archivos_con_conflictos)}` en las mismas líneas que la IA quería cambiar. Volvé a generar para que la IA lea tu nuevo código.")
            else:
                rutas_abs = [P.abs(c.clave) for c in rutas_a_guardar]
                
                for ruta_abs in rutas_abs:
                    ruta_abs.parent.mkdir(parents=True, exist_ok=True)
                
                backup = P.hacer_backup(rutas_abs)
                
                for c in rutas_a_guardar:
                    escribir_archivo(P.abs(c.clave), c.nuevo, c.eol)
                    P.actualizar_archivo_en_mapa(c.clave)
                    
                ss.ultimo_lote = str(backup)
                ss.msg = f"{len(rutas_a_guardar)} archivo(s) guardado(s) (Fusión exitosa). Lote de backup: {backup.name}"
                ss.guardado = True
                st.rerun()
                
        # Cambiamos dinámicamente el texto y el mensaje según el estado
        texto_btn_secundario = "Limpiar vista" if ss.guardado else "Descartar"
        
        if b2.button(texto_btn_secundario):
            ss.msg = "Vista limpiada." if ss.guardado else "Cambios descartados."
            limpiar_resultado()
            st.rerun()