"""
frontend.py — El Ejecutor Stateless.

Comandos:
    python frontend.py refactor "ruta/archivo.jsx" "Instrucción para la IA"
    python frontend.py fix "stack trace"          # o:  fix  (pegás el trace)  |  fix -p (portapapeles)
    python frontend.py deshacer "ruta/archivo.jsx"

Cada ejecución arranca de cero (sin historial) para no inflar el KV cache de la GPU.
"""
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import httpx
import typer
from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

app = typer.Typer(add_completion=False, help="Pair programming local, stateless y con diff.")
console = Console()

# ----------------------------- Configuración -------------------------------- #
# Perfiles de servidor: (URL, modelo).
#   Puertos típicos: LM Studio = 1234 | llama.cpp = 8080/8081 | Koboldcpp = 5001 | Ollama = 11434
# Elegí uno con la variable AI_PERFIL, o pisalos con AI_API_URL / AI_MODELO.
PERFILES = {
    "ollama": ("http://localhost:11434/v1/chat/completions", "qwen3.6-coder:latest"),
    "ollama-7b": ("http://localhost:11434/v1/chat/completions", "qwen2.5-coder:7b"),
    "llama-server": ("http://localhost:8081/v1/chat/completions", "Qwen3.6-35B-A3B-UD-Q4_K_XL"),
}
PERFIL = os.getenv("AI_PERFIL", "ollama")
if PERFIL not in PERFILES:
    PERFIL = "ollama"
API_URL = os.getenv("AI_API_URL", PERFILES[PERFIL][0])
MODELO = os.getenv("AI_MODELO", PERFILES[PERFIL][1])
MAX_TOKENS_PROMPT = int(os.getenv("AI_MAX_TOKENS_PROMPT", "12000"))  # aviso si se supera
PRESUPUESTO_MAPA_CHARS = 6000   # tope de texto del mapa que se envía
MAX_ARCHIVOS_MAPA = 30
MAX_INTENTOS = int(os.getenv("AI_MAX_INTENTOS", "3"))  # auto-healing: intentos totales por pedido

ARCHIVO_MAPA = Path(".ai_map.json")
ARCHIVO_CONVENCIONES = Path("CONVENTIONS.md")
CARPETA_BACKUPS = Path(".ai_backups")

SISTEMA_BASE = """Sos un asistente de Pair Programming experto. Modificás UN archivo por vez.

FORMATO DE RESPUESTA (obligatorio): devolvé ÚNICAMENTE bloques SEARCH/REPLACE, sin explicaciones ni saludos:
PROHIBIDO usar texto conversacional, saludos, explicaciones o pedir disculpas.
PROHIBIDO usar etiquetas como <archivo> ó </archivo>.

EJEMPLO DE RESPUESTA CORRECTA:
<<<<<<< SEARCH
(líneas EXACTAS del archivo actual que querés cambiar)
=======
(líneas nuevas que las reemplazan)
>>>>>>> REPLACE

Reglas:
- SEARCH debe copiar el código actual tal cual (espacios e indentación incluidos) y aparecer UNA sola vez en el archivo. Si hace falta, sumá 1-3 líneas de contexto para que sea único.
- Para insertar código: usá en SEARCH una línea vecina existente y repetila en REPLACE junto con el código nuevo.
- Para borrar código: dejá REPLACE vacío.
- Podés usar varios bloques, en el orden en que aparecen en el archivo.
- No reescribas el archivo completo ni toques nada que no se haya pedido.
- No uses bloques de Markdown (```)."""


# ------------------------------ Utilidades ---------------------------------- #
def estimar_tokens(texto: str) -> int:
    return len(texto) // 3  # aproximación para código


def leer_archivo(ruta: Path):
    """Lee preservando el tipo de salto de línea (CRLF/LF) para no ensuciar el diff de git."""
    with open(ruta, "r", encoding="utf-8", newline="") as f:
        crudo = f.read()
    eol = "\r\n" if "\r\n" in crudo else "\n"
    return crudo.replace("\r\n", "\n"), eol


def escribir_archivo(ruta: Path, texto: str, eol: str):
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        f.write(texto.replace("\n", eol))


def clave_relativa(ruta: Path) -> str:
    try:
        return ruta.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return ruta.as_posix()


def git_archivo_sucio(ruta: Path) -> bool:
    """True si el archivo tiene cambios sin commitear. Sin git instalado o fuera de un repo -> False."""
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "--", str(ruta)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return bool(r.stdout.strip())


def archivo_cambio_en_disco(ruta: Path, original: str) -> bool:
    """True si el archivo ya no es el que le mandamos a la IA (lo editaste mientras tanto)."""
    try:
        actual, _ = leer_archivo(ruta)
    except OSError:
        return True
    return actual != original


# ------------------------------ Backups ------------------------------------- #
def hacer_backup(ruta: Path) -> Path:
    CARPETA_BACKUPS.mkdir(exist_ok=True)
    gitignore = CARPETA_BACKUPS / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")  # git ignora todo el contenido
    sello = datetime.now().strftime("%Y%m%d-%H%M%S")
    nombre = clave_relativa(ruta).replace("/", "__")
    destino = CARPETA_BACKUPS / f"{nombre}.{sello}.bak"
    shutil.copy2(ruta, destino)
    return destino


def ultimo_backup(ruta: Path) -> Optional[Path]:
    if not CARPETA_BACKUPS.is_dir():
        return None
    prefijo = clave_relativa(ruta).replace("/", "__") + "."
    candidatos = sorted(p for p in CARPETA_BACKUPS.glob("*.bak") if p.name.startswith(prefijo))
    return candidatos[-1] if candidatos else None


# ------------------------------ Contexto ------------------------------------ #
def cargar_convenciones() -> str:
    if ARCHIVO_CONVENCIONES.is_file():
        return ARCHIVO_CONVENCIONES.read_text(encoding="utf-8").strip()
    return ""


def cargar_mapa() -> dict:
    """Devuelve {ruta: {'signatures': [...], 'imports': [...]}} (soporta el formato viejo v1)."""
    try:
        datos = json.loads(ARCHIVO_MAPA.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if isinstance(datos, dict) and "files" in datos:
        return datos["files"]
    return {
        ruta: {"signatures": [s] if isinstance(s, str) else list(s), "imports": []}
        for ruta, s in datos.items()
    }


def _palabras(texto: str) -> set:
    return {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", texto)}


def _import_a_stem(imp: str) -> Optional[str]:
    """'./components/UserCard.jsx' -> 'UserCard' | 'com.x.service.UserService' -> 'UserService'."""
    imp = imp.strip().rstrip(";")
    if imp.endswith(".*"):
        return None
    if "/" in imp:  # JS/TS: './components/UserCard.jsx'
        seg = imp.rstrip("/").split("/")[-1]
        return re.sub(r"\.(jsx?|tsx?|mjs)$", "", seg) or None
    # Java 'com.x.UserService' | Python 'app.services.user' o relativo '.models'
    return imp.lstrip(".").split(".")[-1] or None


def filtrar_mapa(mapa: dict, archivo: Path, instruccion: str, extra: str = ""):
    """
    Rankea los archivos del mapa por relevancia para el archivo objetivo y arma un texto compacto.
    Puntaje: lo que el objetivo importa (+5), lo que importa al objetivo (+4),
    nombre mencionado en el prompt (+3), identificadores en común (hasta +3), misma carpeta (+1).
    """
    clave_obj = clave_relativa(archivo)
    stem_obj = Path(clave_obj).stem
    dir_obj = Path(clave_obj).parent
    imports_obj = {_import_a_stem(i) for i in mapa.get(clave_obj, {}).get("imports", [])} - {None}
    palabras = _palabras(instruccion + " " + extra)

    puntuados = []
    for ruta, info in mapa.items():
        if ruta == clave_obj:
            continue
        stem = Path(ruta).stem
        p = 0
        if stem in imports_obj:
            p += 5
        if stem_obj in {_import_a_stem(i) for i in info.get("imports", [])}:
            p += 4
        if stem.lower() in palabras:
            p += 3
        p += min(len(palabras & _palabras(" ".join(info.get("signatures", [])))), 3)
        if Path(ruta).parent == dir_obj:
            p += 1
        if p > 0:
            puntuados.append((p, ruta))

    puntuados.sort(key=lambda x: (-x[0], x[1]))
    bloques, largo, usados = [], 0, 0
    for _, ruta in puntuados[:MAX_ARCHIVOS_MAPA]:
        bloque = f"# {ruta}\n" + "\n".join(mapa[ruta].get("signatures", []))
        if largo + len(bloque) > PRESUPUESTO_MAPA_CHARS:
            break
        bloques.append(bloque)
        largo += len(bloque)
        usados += 1
    return "\n\n".join(bloques), usados, len(mapa)


def construir_mensajes(ruta: Path, codigo: str, instruccion: str, convenciones: str, mapa_txt: str):
    sistema = SISTEMA_BASE  # parte estática primero: llama.cpp reutiliza este prefijo en su caché
    if convenciones:
        sistema += f"\n\n## Convenciones del proyecto (obligatorias)\n{convenciones}"
    if mapa_txt:
        sistema += (
            "\n\n## Mapa de archivos relacionados (solo firmas, para contexto de imports y dependencias)\n"
            f"{mapa_txt}"
        )
    usuario = (
        f'<archivo ruta="{clave_relativa(ruta)}">\n{codigo}\n</archivo>\n\n'
        f"Instrucción: {instruccion}"
    )
    if not codigo.strip():
        usuario += "\n\nEl archivo está VACÍO: devolvé el código completo del archivo dentro de un único bloque ```, sin SEARCH/REPLACE."
    return [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}]


# ------------------------------ LLM ----------------------------------------- #
class ErrorLLM(Exception):
    """Fallo de conexión o respuesta inválida/cortada del servidor del modelo."""


def separar_pensamiento(contenido: str):
    """Algunos servidores mezclan el razonamiento dentro de `content` con etiquetas <think>."""
    if "<think>" in contenido:
        antes, resto = contenido.split("<think>", 1)
        if "</think>" in resto:
            pensado, despues = resto.split("</think>", 1)
            return pensado.strip(), (antes + despues).strip()
        return resto.strip(), antes.strip()  # todavía pensando
    if "</think>" in contenido:  # la plantilla del modelo ya abrió <think> por nosotros
        pensado, despues = contenido.split("</think>", 1)
        return pensado.strip(), despues.strip()
    return "", contenido


def stream_llm(
    mensajes: list,
    on_fragmento: Optional[Callable[[str], None]] = None,
    on_pensamiento: Optional[Callable[[str], None]] = None,
    url: Optional[str] = None,
    modelo: Optional[str] = None,
    on_estadisticas: Optional[Callable[[dict], None]] = None,
) -> str:
    """
    Llama al modelo por streaming. Sin dependencias de UI: sirve para el CLI y para la web.
    - on_fragmento(respuesta_acumulada): la respuesta visible (sin razonamiento).
    - on_pensamiento(razonamiento_acumulado): el razonamiento, venga como `reasoning_content` (llama.cpp),
      `reasoning`/`thinking` (Ollama) o como <think>…</think> dentro del contenido.
    - on_estadisticas(dict): al terminar. Usa los números reales del servidor (usage/timings) si los manda;
      si no, solo tiempos. Claves: tokens_entrada, tokens_salida, tps, seg_total, seg_primer_token.
    Devuelve solo la respuesta. Si el servidor corta por límite de tokens/contexto, lanza ErrorLLM
    (una respuesta truncada aplicaría solo parte de los bloques).
    """
    payload = {
        "model": modelo or MODELO,
        "messages": mensajes,
        "temperature": 0.1,
        "stream": True,
        "cache_prompt": True,  # llama.cpp reutiliza el prefijo; otros servidores lo ignoran
        "stream_options": {"include_usage": True},  # pide el conteo real de tokens al final
    }
    destino = url or API_URL
    t0, t_primero, usage, timings = time.monotonic(), None, {}, {}
    razonamiento, contenido, finish = "", "", None
    ult_pens, ult_resp = "", ""
    try:
        with httpx.Client(timeout=httpx.Timeout(None, connect=10.0)) as client:
            with client.stream("POST", destino, json=payload) as r:
                if r.status_code != 200:
                    r.read()
                    raise ErrorLLM(f"El servidor respondió {r.status_code}: {r.text[:500]}")
                for linea in r.iter_lines():
                    if not linea.startswith("data:"):
                        continue
                    dato = linea[5:].strip()
                    if dato == "[DONE]":
                        break
                    try:
                        data = json.loads(dato)
                    except json.JSONDecodeError:
                        continue
                    usage.update(data.get("usage") or {})
                    timings = data.get("timings") or timings
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    finish = choices[0].get("finish_reason") or finish
                    delta = choices[0].get("delta") or {}
                    r_frag = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thinking") or ""
                    c_frag = delta.get("content") or ""
                    if (r_frag or c_frag) and t_primero is None:
                        t_primero = time.monotonic()
                    razonamiento += r_frag
                    contenido += c_frag
                    inline, respuesta = separar_pensamiento(contenido)
                    pensamiento = (razonamiento + "\n" + inline).strip() if inline else razonamiento
                    if pensamiento != ult_pens:
                        ult_pens = pensamiento
                        if on_pensamiento:
                            on_pensamiento(pensamiento)
                    if respuesta != ult_resp:
                        ult_resp = respuesta
                        if on_fragmento:
                            on_fragmento(respuesta)
    except httpx.ConnectError:
        raise ErrorLLM(f"No pude conectar con {destino}. ¿Está corriendo el servidor del modelo?")
    except httpx.HTTPError as e:
        raise ErrorLLM(f"Error de red hablando con el modelo: {e}")
    if finish == "length":
        raise ErrorLLM(
            "El modelo se quedó sin tokens o contexto antes de terminar (finish_reason=length). "
            "Los modelos que razonan gastan mucho contexto pensando: subí el contexto del servidor "
            "(llama-server: -c, Ollama: num_ctx) o probá con un archivo/mapa más chico."
        )
    if on_estadisticas:
        t_fin = time.monotonic()
        salida = usage.get("completion_tokens") or timings.get("predicted_n")
        tps = timings.get("predicted_per_second")
        if not tps and salida and t_primero and t_fin > t_primero:
            tps = salida / (t_fin - t_primero)
        on_estadisticas({
            "tokens_entrada": usage.get("prompt_tokens"),
            "tokens_salida": salida,
            "tps": tps,
            "seg_total": t_fin - t0,
            "seg_primer_token": (t_primero - t0) if t_primero else None,
        })
    return ult_resp.strip()


def listar_modelos(url: Optional[str] = None) -> list:
    """Consulta GET /v1/models (llama.cpp, Ollama y LM Studio lo soportan). Sirve para probar la conexión."""
    base = (url or API_URL).split("/chat/completions")[0].rstrip("/")
    try:
        r = httpx.get(base + "/models", timeout=5.0)
        r.raise_for_status()
        datos = r.json()
    except httpx.ConnectError:
        raise ErrorLLM(f"No pude conectar con {base}. ¿Está corriendo el servidor?")
    except (httpx.HTTPError, ValueError) as e:
        raise ErrorLLM(f"{base}/models no respondió como se esperaba: {e}")
    items = datos.get("data") or datos.get("models") or []
    ids = [(m.get("id") or m.get("name") or m.get("model")) for m in items if isinstance(m, dict)]
    return [i for i in ids if i]


def llamar_llm(mensajes: list) -> str:
    """Versión CLI: muestra el razonamiento (atenuado, últimas líneas) y después la respuesta."""
    estado = {"p": "", "r": ""}
    stats = {}

    def pintar(live):
        partes = []
        if estado["p"] and not estado["r"]:
            cola = "\n".join(estado["p"].splitlines()[-6:])
            partes.append(Text("💭 pensando…\n" + cola, style="dim italic"))
        elif estado["p"]:
            partes.append(Text(f"💭 razonó {len(estado['p'])} caracteres\n", style="dim"))
        if estado["r"]:
            partes.append(Text(estado["r"]))
        live.update(Group(*partes))

    try:
        with Live(Text("⏳ Esperando al modelo…", style="dim"), console=console,
                  refresh_per_second=10, vertical_overflow="visible") as live:
            def al_pensar(t):
                estado["p"] = t
                pintar(live)

            def al_responder(t):
                estado["r"] = t
                pintar(live)

            resultado = stream_llm(mensajes, al_responder, al_pensar, on_estadisticas=stats.update)
        partes = []
        if stats.get("tokens_entrada"):
            partes.append(f"{stats['tokens_entrada']} tks entrada")
        if stats.get("tokens_salida"):
            partes.append(f"{stats['tokens_salida']} tks salida")
        if stats.get("tps"):
            partes.append(f"{stats['tps']:.1f} t/s")
        if stats.get("seg_primer_token") is not None:
            partes.append(f"primer token a los {stats['seg_primer_token']:.1f}s")
        partes.append(f"total {stats.get('seg_total', 0):.1f}s")
        console.print(f"[dim]↳ {' · '.join(partes)}[/dim]")
        return resultado
    except ErrorLLM as e:
        console.print(f"[bold red]{escape(str(e))}[/bold red]")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelado.[/yellow]")
        raise typer.Exit(1)


# ------------------------- Interpretar la respuesta ------------------------- #
PATRON_BLOQUE = re.compile(
    r"<<<<<<< SEARCH[ \t]*\r?\n(.*?)\r?\n=======[ \t]*\r?\n(.*?)(?:\r?\n)?>>>>>>> REPLACE",
    re.DOTALL,
)


def _reemplazo_tolerante(texto: str, buscar: str, reemplazar: str) -> Optional[str]:
    """Reintento ignorando espacios al final de línea y líneas vacías en los bordes."""
    lineas = texto.split("\n")
    b = [l.rstrip() for l in buscar.strip("\n").split("\n")]
    if not any(b):
        return None
    n = len(b)
    hits = [i for i in range(len(lineas) - n + 1) if [l.rstrip() for l in lineas[i:i + n]] == b]
    if len(hits) != 1:
        return None
    i = hits[0]
    nuevas = reemplazar.strip("\n").split("\n") if reemplazar.strip("\n") else []
    return "\n".join(lineas[:i] + nuevas + lineas[i + n:])


def _lineas_parecidas(texto: str, buscar: str, max_lineas: int = 12) -> str:
    """Zona del archivo más parecida al SEARCH fallido (ayuda al modelo a corregirse)."""
    lineas = texto.split("\n")
    b = buscar.strip("\n").split("\n")
    n = min(len(b), len(lineas))
    if n == 0:
        return ""
    objetivo = "\n".join(l.strip() for l in b[:n])
    mejor, mejor_i = 0.0, -1
    for i in range(len(lineas) - n + 1):
        sm = difflib.SequenceMatcher(None, objetivo, "\n".join(l.strip() for l in lineas[i:i + n]))
        if sm.real_quick_ratio() > mejor and sm.quick_ratio() > mejor:
            r = sm.ratio()
            if r > mejor:
                mejor, mejor_i = r, i
    if mejor < 0.6:
        return ""
    return "\n".join(lineas[mejor_i:mejor_i + min(n, max_lineas)])


def aplicar_bloques(original: str, bloques: list):
    texto, errores = original, []
    for i, (buscar, reemplazar) in enumerate(bloques, 1):
        buscar, reemplazar = buscar.replace("\r\n", "\n"), reemplazar.replace("\r\n", "\n")
        n = texto.count(buscar)
        if n == 1:
            if reemplazar == "" and texto.count(buscar + "\n") == 1:
                texto = texto.replace(buscar + "\n", "", 1)  # borrado sin dejar línea vacía
            else:
                texto = texto.replace(buscar, reemplazar, 1)
        elif n > 1:
            errores.append(f"Bloque {i}: el fragmento SEARCH aparece {n} veces (ambiguo). Sumá más líneas de contexto para que sea único.")
        else:
            nuevo = _reemplazo_tolerante(texto, buscar, reemplazar)
            if nuevo is None:
                msg = f"Bloque {i}: el fragmento SEARCH no coincide con el archivo."
                pista = _lineas_parecidas(texto, buscar)
                if pista:
                    msg += f" Las líneas más parecidas del archivo real son:\n```\n{pista}\n```"
                errores.append(msg)
            else:
                texto = nuevo
    return texto, errores


PATRON_NUEVO = re.compile(
    r"<<<<<<< SEARCH[ \t]*\r?\n.*?=======[ \t]*\r?\n(.*?)(?:\r?\n)?>>>>>>> REPLACE", re.DOTALL
)


def interpretar_respuesta(respuesta: str, original: str):
    """Devuelve (texto_nuevo | None, errores, avisos). Todo o nada: ante un error no se aplica nada."""
    # 1. Archivo vacío: aceptamos el contenido completo
    if not original.strip():
        nuevos = PATRON_NUEVO.findall(respuesta)  # por si igual respondió con SEARCH vacío / REPLACE
        if nuevos:
            return "\n".join(nuevos).strip("\n") + "\n", [], ["Archivo creado desde cero."]
        m = re.search(r"```[\w+-]*\n(.*?)```", respuesta, re.DOTALL)
        if m:
            return m.group(1).rstrip("\n") + "\n", [], ["Archivo creado desde cero."]
        if respuesta.strip() and "<<<<<<<" not in respuesta:
            return respuesta.strip() + "\n", [], ["Archivo creado desde cero."]

    # 2. Bloques SEARCH/REPLACE para archivos existentes
    bloques = PATRON_BLOQUE.findall(respuesta)
    if bloques:
        nuevo, errores = aplicar_bloques(original, bloques)
        return (None if errores else nuevo), errores, []

    # 3. Fallback: el modelo devolvió el archivo completo dentro de un bloque ```
    m = re.search(r"```[\w+-]*\n(.*?)```", respuesta, re.DOTALL)
    if m:
        return m.group(1).rstrip("\n") + "\n", [], ["El modelo devolvió el archivo completo (no usó SEARCH/REPLACE). Revisá bien el diff."]

    return None, ["No pude interpretar la respuesta: no hay bloques SEARCH/REPLACE ni un bloque de código."], []


# ------------------------------ Diff ---------------------------------------- #
def mostrar_diff(original: str, nuevo: str, nombre: str):
    diff = list(difflib.unified_diff(
        original.splitlines(), nuevo.splitlines(),
        fromfile=f"a/{nombre}", tofile=f"b/{nombre}", lineterm="", n=3,
    ))
    if not diff:
        return 0, 0
    salida, mas, menos = Text(), 0, 0
    for l in diff:
        if l.startswith(("+++", "---")):
            salida.append(l + "\n", style="bold")
        elif l.startswith("@@"):
            salida.append(l + "\n", style="cyan")
        elif l.startswith("+"):
            salida.append(l + "\n", style="green"); mas += 1
        elif l.startswith("-"):
            salida.append(l + "\n", style="red"); menos += 1
        else:
            salida.append(l + "\n", style="dim")
    console.print(Panel(salida, title="Diff", border_style="blue"))
    return mas, menos


# ------------------- Núcleo común (CLI y web) + auto-healing ----------------- #
@dataclass
class Contexto:
    ruta: Path
    original: str
    eol: str
    mensajes: list
    tokens: int
    mapa_usados: int
    mapa_total: int
    hay_convenciones: bool


@dataclass
class Resultado:
    original: str
    eol: str
    nuevo: Optional[str] = None            # None = no se pudo obtener un cambio válido
    errores: list = field(default_factory=list)
    avisos: list = field(default_factory=list)
    intentos: int = 1
    respuesta: str = ""                     # última respuesta cruda del modelo (para diagnosticar)


def preparar_contexto(ruta: Path, instruccion: str, sin_mapa: bool = False, extra: str = "") -> Contexto:
    original, eol = leer_archivo(ruta)
    convenciones = cargar_convenciones()
    mapa_txt, usados, total = "", 0, 0
    if not sin_mapa:
        mapa_txt, usados, total = filtrar_mapa(cargar_mapa(), ruta, instruccion, extra)
    mensajes = construir_mensajes(ruta, original, instruccion, convenciones, mapa_txt)
    tokens = estimar_tokens("".join(m["content"] for m in mensajes))
    return Contexto(ruta, original, eol, mensajes, tokens, usados, total, bool(convenciones))


def mensaje_reintento(errores: list) -> str:
    return (
        "Tu respuesta anterior no se pudo aplicar:\n"
        + "\n".join(f"- {e}" for e in errores)
        + "\n\nCada SEARCH debe copiar EXACTAMENTE el archivo ORIGINAL que te envié (sin ningún cambio aplicado). "
        "Reenviá TODOS los bloques SEARCH/REPLACE de nuevo (los que estaban bien y los corregidos), "
        "usando solo ese formato y sin explicaciones."
    )


def generar_cambio(
    ctx: Contexto,
    llamar: Optional[Callable[[list], str]] = None,
    on_reintento: Optional[Callable[[int, list], None]] = None,
) -> Resultado:
    """
    Pide el cambio al modelo y, si los bloques no se pueden aplicar, lo reintenta (auto-healing).
    Cada intento se aplica siempre contra el ORIGINAL, y se le pide al modelo el set completo de bloques,
    así un bloque bueno nunca se pierde por corregir otro. Todo ocurre dentro de esta llamada: sigue siendo stateless.
    """
    llamar = llamar or stream_llm
    mensajes = list(ctx.mensajes)  # copia: el historial del reintento no contamina el contexto original
    res = Resultado(original=ctx.original, eol=ctx.eol)
    for intento in range(1, MAX_INTENTOS + 1):
        respuesta = llamar(mensajes)
        res.respuesta, res.intentos = respuesta, intento
        if not respuesta.strip():  # reintentar el mismo prompt daría lo mismo
            res.errores = ["El modelo no devolvió ninguna respuesta (¿agotó el contexto o los tokens mientras razonaba?)."]
            return res
        nuevo, errores, avisos = interpretar_respuesta(respuesta, ctx.original)
        res.intentos, res.errores, res.avisos = intento, errores, avisos
        if not errores:
            res.nuevo = nuevo
            return res
        if intento < MAX_INTENTOS:
            if on_reintento:
                on_reintento(intento, errores)
            mensajes.append({"role": "assistant", "content": respuesta})
            mensajes.append({"role": "user", "content": mensaje_reintento(errores)})
    return res


def aviso_recorte(original: str, nuevo: str) -> Optional[str]:
    lo, ln = len(original.splitlines()), len(nuevo.splitlines())
    if lo >= 20 and ln < lo * 0.6:
        return f"🚨 El archivo pasaría de {lo} a {ln} líneas. Posible recorte por alucinación."
    return None


# ------------------------------ Pipeline CLI -------------------------------- #
def ejecutar(archivo: str, instruccion: str, sin_mapa: bool = False, extra_busqueda: str = ""):
    ruta = Path(archivo)
    if not ruta.is_file():
        console.print(f"[bold red]No se encontró el archivo {escape(archivo)}[/bold red]")
        raise typer.Exit(1)

    # 1. Seguridad Git: no mezclar cambios de la IA con cambios tuyos sin guardar
    if git_archivo_sucio(ruta):
        console.print(f"[bold yellow]⚠️ {escape(archivo)} tiene cambios sin commitear en Git.[/bold yellow]")
        if not typer.confirm("¿Querés que la IA lo modifique igual y correr el riesgo de mezclar cambios?", default=False):
            raise typer.Exit(0)

    ctx = preparar_contexto(ruta, instruccion, sin_mapa, extra_busqueda)
    console.print(
        f"[dim]Contexto: ~{ctx.tokens} tokens · mapa {ctx.mapa_usados}/{ctx.mapa_total} archivos · "
        f"convenciones: {'sí' if ctx.hay_convenciones else 'no'}[/dim]"
    )
    if ctx.tokens > MAX_TOKENS_PROMPT:
        console.print(f"[bold yellow]⚠️  El prompt supera ~{MAX_TOKENS_PROMPT} tokens; puede degradar la calidad o la velocidad.[/bold yellow]")
        if not typer.confirm("¿Enviar igual?", default=False):
            raise typer.Exit(0)

    # 2. Generación con auto-healing
    console.print(f"\n[bold cyan]Enviando {escape(archivo)} a {escape(MODELO)}...[/bold cyan]\n")

    def al_reintentar(intento: int, errores: list):
        console.print()
        for e in errores:
            console.print(f"[bold red]✗ {escape(e)}[/bold red]")
        console.print(f"\n[bold yellow]🔄 Reintento automático ({intento}/{MAX_INTENTOS - 1})...[/bold yellow]\n")

    res = generar_cambio(ctx, llamar_llm, al_reintentar)
    console.print()
    for a in res.avisos:
        console.print(f"[bold yellow]⚠️  {escape(a)}[/bold yellow]")
    if res.nuevo is None:
        for e in res.errores:
            console.print(f"[bold red]✗ {escape(e)}[/bold red]")
        console.print("[bold red]❌ No se obtuvo un cambio válido. No se modificó nada; probá reformular la instrucción.[/bold red]")
        raise typer.Exit(1)

    mas, menos = mostrar_diff(res.original, res.nuevo, clave_relativa(ruta))
    if mas == 0 and menos == 0:
        console.print("[yellow]La respuesta no produce cambios.[/yellow]")
        return
    console.print(f"[green]+{mas}[/green] [red]-{menos}[/red]")
    recorte = aviso_recorte(res.original, res.nuevo)
    if recorte:
        console.print(f"[bold red]{recorte}[/bold red]")

    if typer.confirm("\n¿Aplicar estos cambios?", default=False):
        if archivo_cambio_en_disco(ruta, res.original):
            console.print("[bold red]El archivo cambió en disco mientras esperabas. No se guardó nada; volvé a ejecutar.[/bold red]")
            raise typer.Exit(1)
        backup = hacer_backup(ruta)
        escribir_archivo(ruta, res.nuevo, res.eol)
        console.print(
            f"[bold green]✅ {escape(archivo)} guardado.[/bold green] "
            f"[dim]Backup: {escape(str(backup))}  ·  Revertir: frontend.py deshacer \"{escape(archivo)}\"[/dim]"
        )
    else:
        console.print("[yellow]Cambios descartados.[/yellow]")



# ------------------------------ Comandos ------------------------------------ #
@app.command()
def refactor(
    archivo: str = typer.Argument(..., help="Ruta del archivo a modificar"),
    instruccion: str = typer.Argument(..., help="Qué querés que haga la IA"),
    sin_mapa: bool = typer.Option(False, "--sin-mapa", help="No enviar el mapa del proyecto"),
):
    """Modifica un archivo según una instrucción (con diff y backup)."""
    ejecutar(archivo, instruccion, sin_mapa)


PATRON_TRAZA = re.compile(r"([\w@\-./\\:]*?[\w\-]+\.(?:java|jsx?|tsx?|mjs)):(\d+)(?::\d+)?")
PATRON_TRAZA_PY = re.compile(r'File "([^"]+\.py)", line (\d+)')


def _resolver_en_proyecto(ruta_traza: str, mapa: dict) -> Optional[str]:
    norm = ruta_traza.replace("\\", "/")
    if "node_modules/" in norm:
        return None
    nombre = norm.split("/")[-1]
    candidatos = [k for k in mapa if k == nombre or k.endswith("/" + nombre)]
    if not candidatos and Path(norm).is_file():
        return clave_relativa(Path(norm))
    if not candidatos:
        return None

    def sufijo_comun(k: str) -> int:
        a, b = k.split("/")[::-1], norm.split("/")[::-1]
        n = 0
        for x, y in zip(a, b):
            if x != y:
                break
            n += 1
        return n

    candidatos.sort(key=sufijo_comun, reverse=True)
    return candidatos[0]


@app.command()
def fix(
    traza: Optional[str] = typer.Argument(None, help="Stack trace. Omitilo (o usá '-') para pegarlo por stdin"),
    portapapeles: bool = typer.Option(False, "--portapapeles", "-p", help="Leer el trace del portapapeles (pip install pyperclip)"),
    archivo: Optional[str] = typer.Option(None, "--archivo", "-a", help="Forzar el archivo a corregir"),
    sin_mapa: bool = typer.Option(False, "--sin-mapa"),
):
    """Detecta el archivo que falla a partir de un stack trace y pide el arreglo."""
    if portapapeles:
        try:
            import pyperclip
            traza = pyperclip.paste()
        except ImportError:
            console.print("[red]Falta pyperclip: pip install pyperclip[/red]")
            raise typer.Exit(1)
    elif traza is None or traza == "-":
        if sys.stdin.isatty():
            console.print("[cyan]Pegá el stack trace y terminá con Ctrl+Z + Enter (Windows) o Ctrl+D (Linux/Mac):[/cyan]")
        traza = sys.stdin.read()
    traza = (traza or "").strip()
    if not traza:
        console.print("[red]No recibí ningún stack trace.[/red]")
        raise typer.Exit(1)

    mapa = cargar_mapa()
    objetivo, linea_error, otros = archivo, None, []
    if not objetivo:
        # Python lista el frame culpable ÚLTIMO (al revés que Java/JS), por eso se invierte
        frames = PATRON_TRAZA.findall(traza) + list(reversed(PATRON_TRAZA_PY.findall(traza)))
        for ruta_t, num in frames:
            resuelto = _resolver_en_proyecto(ruta_t, mapa)
            if not resuelto:
                continue
            if objetivo is None:
                objetivo, linea_error = resuelto, int(num)
            elif resuelto != objetivo and resuelto not in otros:
                otros.append(resuelto)
    if not objetivo:
        console.print("[red]No encontré ningún archivo del proyecto en el trace. Usá --archivo para indicarlo.[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]Archivo detectado:[/bold] {escape(objetivo)}" + (f" (línea {linea_error})" if linea_error else ""))
    if otros:
        console.print(f"[dim]Otros archivos del proyecto en el trace: {escape(', '.join(otros[:5]))}[/dim]")

    fragmento = ""
    if linea_error:
        lineas, _ = leer_archivo(Path(objetivo))
        lineas = lineas.split("\n")
        ini, fin = max(0, linea_error - 6), min(len(lineas), linea_error + 5)
        fragmento = "\n".join(f"{i + 1}: {lineas[i]}" for i in range(ini, fin))

    instruccion = "Corregí el error descrito por este stack trace, con el cambio mínimo necesario."
    if linea_error:
        instruccion += f"\nEl fallo está en la línea {linea_error}. Fragmento (con números de línea, que NO forman parte del código):\n{fragmento}"
    instruccion += f"\n\nStack trace:\n{traza[:4000]}"
    ejecutar(objetivo, instruccion, sin_mapa, extra_busqueda=traza)


@app.command()
def deshacer(archivo: str = typer.Argument(..., help="Archivo a restaurar desde su último backup")):
    """Restaura el último backup de un archivo."""
    ruta = Path(archivo)
    backup = ultimo_backup(ruta)
    if not backup:
        console.print("[yellow]No hay backups para ese archivo.[/yellow]")
        raise typer.Exit(1)
    actual, eol = leer_archivo(ruta) if ruta.is_file() else ("", "\n")
    previo, _ = leer_archivo(backup)
    mostrar_diff(actual, previo, clave_relativa(ruta))
    if typer.confirm(f"¿Restaurar {backup.name}?", default=False):
        if ruta.is_file():
            hacer_backup(ruta)  # el estado actual también queda respaldado
        escribir_archivo(ruta, previo, eol)
        console.print("[bold green]✅ Restaurado.[/bold green]")


@app.command()
def probar():
    """Prueba la conexión con el servidor y muestra cómo llegan el razonamiento y la respuesta."""
    import time
    console.print(f"[bold]Perfil:[/bold] {PERFIL}  [bold]URL:[/bold] {escape(API_URL)}  [bold]Modelo:[/bold] {escape(MODELO)}")
    try:
        console.print(f"[green]✓ /models responde:[/green] {escape(', '.join(listar_modelos()) or '(lista vacía)')}")
    except ErrorLLM as e:
        console.print(f"[yellow]⚠️ {escape(str(e))}[/yellow]")

    t0, marcas = time.monotonic(), {}
    def marca(nombre):
        return lambda _t: marcas.setdefault(nombre, time.monotonic() - t0)
    pens = {"t": ""}
    try:
        resp = stream_llm(
            [{"role": "user", "content": "Respondé solamente con la palabra: ok"}],
            on_fragmento=marca("respuesta"),
            on_pensamiento=lambda t: (pens.update(t=t), marca("razonamiento")(t)),
        )
    except ErrorLLM as e:
        console.print(f"[bold red]✗ {escape(str(e))}[/bold red]")
        raise typer.Exit(1)
    console.print(f"[green]✓ Respuesta:[/green] {escape(repr(resp))}")
    console.print(f"[dim]Razonamiento recibido: {len(pens['t'])} caracteres · "
                  f"primer razonamiento a los {marcas.get('razonamiento', float('nan')):.1f}s · "
                  f"primera respuesta a los {marcas.get('respuesta', float('nan')):.1f}s · total {time.monotonic() - t0:.1f}s[/dim]")


if __name__ == "__main__":
    app()