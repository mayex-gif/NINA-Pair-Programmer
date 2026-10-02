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
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

app = typer.Typer(add_completion=False, help="Pair programming local, stateless y con diff.")
console = Console()

# ----------------------------- Configuración -------------------------------- #
# ⚠️ Puerto: LM Studio = 1234 | llama.cpp = 8080 | Koboldcpp = 5001
API_URL = os.getenv("AI_API_URL", "http://localhost:8081/v1/chat/completions")
MODELO = os.getenv("AI_MODELO", "Qwen3.6-35B-A3B-UD-Q4_K_XL")
MAX_TOKENS_PROMPT = int(os.getenv("AI_MAX_TOKENS_PROMPT", "12000"))  # aviso si se supera
PRESUPUESTO_MAPA_CHARS = 6000   # tope de texto del mapa que se envía
MAX_ARCHIVOS_MAPA = 30

ARCHIVO_MAPA = Path(".ai_map.json")
ARCHIVO_CONVENCIONES = Path("CONVENTIONS.md")
CARPETA_BACKUPS = Path(".ai_backups")

SISTEMA_BASE = """Sos un asistente de Pair Programming experto. Modificás UN archivo por vez.

FORMATO DE RESPUESTA (obligatorio): devolvé ÚNICAMENTE bloques SEARCH/REPLACE, sin explicaciones ni saludos:

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
    if "/" in imp or imp.startswith("."):
        seg = imp.rstrip("/").split("/")[-1]
        return re.sub(r"\.(jsx?|tsx?|mjs)$", "", seg) or None
    return imp.split(".")[-1] or None


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
    return [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}]


# ------------------------------ LLM ----------------------------------------- #
def llamar_llm(mensajes: list) -> str:
    payload = {
        "model": MODELO,
        "messages": mensajes,
        "temperature": 0.1,
        "stream": True,
        "cache_prompt": True,  # llama.cpp reutiliza el prefijo; otros servidores lo ignoran
    }
    texto = ""
    try:
        with httpx.Client(timeout=httpx.Timeout(None, connect=10.0)) as client:
            with client.stream("POST", API_URL, json=payload) as r:
                if r.status_code != 200:
                    r.read()
                    console.print(f"[bold red]El servidor respondió {r.status_code}:[/bold red] {r.text[:500]}")
                    raise typer.Exit(1)
                with Live(console=console, refresh_per_second=10, vertical_overflow="visible") as live:
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
                        choices = data.get("choices") or []
                        if not choices:
                            continue
                        frag = (choices[0].get("delta") or {}).get("content") or ""
                        if frag:
                            texto += frag
                            live.update(Text(texto))
    except httpx.ConnectError:
        console.print(f"[bold red]No pude conectar con {API_URL}. ¿Está corriendo el servidor del modelo?[/bold red]")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelado.[/yellow]")
        raise typer.Exit(1)
    # Algunos servidores incluyen el razonamiento dentro del contenido
    return re.sub(r"<think>.*?</think>", "", texto, flags=re.DOTALL).strip()


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
            errores.append(f"Bloque {i}: el fragmento SEARCH aparece {n} veces (ambiguo).")
        else:
            nuevo = _reemplazo_tolerante(texto, buscar, reemplazar)
            if nuevo is None:
                errores.append(f"Bloque {i}: el fragmento SEARCH no coincide con el archivo.")
            else:
                texto = nuevo
    return texto, errores


def interpretar_respuesta(respuesta: str, original: str):
    """Devuelve (texto_nuevo | None, errores, avisos). Todo o nada: ante un error no se aplica nada."""
    bloques = PATRON_BLOQUE.findall(respuesta)
    if bloques:
        nuevo, errores = aplicar_bloques(original, bloques)
        return (None if errores else nuevo), errores, []
    # Fallback: el modelo devolvió el archivo completo dentro de un bloque ```
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


# ------------------------------ Pipeline ------------------------------------ #
def ejecutar(archivo: str, instruccion: str, sin_mapa: bool = False, extra_busqueda: str = ""):
    ruta = Path(archivo)
    if not ruta.is_file():
        console.print(f"[bold red]No se encontró el archivo {archivo}[/bold red]")
        raise typer.Exit(1)

    original, eol = leer_archivo(ruta)
    convenciones = cargar_convenciones()

    mapa_txt, usados, total = "", 0, 0
    if not sin_mapa:
        mapa_txt, usados, total = filtrar_mapa(cargar_mapa(), ruta, instruccion, extra_busqueda)

    mensajes = construir_mensajes(ruta, original, instruccion, convenciones, mapa_txt)
    tokens = estimar_tokens("".join(m["content"] for m in mensajes))
    console.print(
        f"[dim]Contexto: ~{tokens} tokens · mapa {usados}/{total} archivos · "
        f"convenciones: {'sí' if convenciones else 'no'}[/dim]"
    )
    if tokens > MAX_TOKENS_PROMPT:
        console.print(f"[bold yellow]⚠️  El prompt supera ~{MAX_TOKENS_PROMPT} tokens; puede degradar la calidad o la velocidad.[/bold yellow]")
        if not typer.confirm("¿Enviar igual?", default=False):
            raise typer.Exit(0)

    console.print(f"\n[bold cyan]Enviando {archivo} a {MODELO}...[/bold cyan]\n")
    respuesta = llamar_llm(mensajes)

    nuevo, errores, avisos = interpretar_respuesta(respuesta, original)
    console.print()
    for e in errores:
        console.print(f"[bold red]✗ {e}[/bold red]")
    for a in avisos:
        console.print(f"[bold yellow]⚠️  {a}[/bold yellow]")
    if nuevo is None:
        console.print("[yellow]No se modificó nada. Probá reformular la instrucción o reintentar.[/yellow]")
        raise typer.Exit(1)

    mas, menos = mostrar_diff(original, nuevo, clave_relativa(ruta))
    if mas == 0 and menos == 0:
        console.print("[yellow]La respuesta no produce cambios.[/yellow]")
        return

    lineas_orig, lineas_nuevo = len(original.splitlines()), len(nuevo.splitlines())
    if lineas_orig >= 20 and lineas_nuevo < lineas_orig * 0.6:
        console.print(f"[bold red]🚨 El archivo pasaría de {lineas_orig} a {lineas_nuevo} líneas. Posible recorte por alucinación.[/bold red]")
    console.print(f"[green]+{mas}[/green] [red]-{menos}[/red]")

    if typer.confirm("\n¿Aplicar estos cambios?", default=False):
        backup = hacer_backup(ruta)
        escribir_archivo(ruta, nuevo, eol)
        console.print(f"[bold green]✅ {archivo} guardado.[/bold green] [dim]Backup: {backup}  ·  Revertir: frontend.py deshacer \"{archivo}\"[/dim]")
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
        for ruta_t, num in PATRON_TRAZA.findall(traza):
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

    console.print(f"[bold]Archivo detectado:[/bold] {objetivo}" + (f" (línea {linea_error})" if linea_error else ""))
    if otros:
        console.print(f"[dim]Otros archivos del proyecto en el trace: {', '.join(otros[:5])}[/dim]")

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


if __name__ == "__main__":
    app()