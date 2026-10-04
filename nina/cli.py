"""
CLI de NINA (typer + rich). Solo presentación: toda la lógica vive en el núcleo (config, proyecto, contexto, bloques, llm, trazas).

Comandos (también funcionan como `python frontend.py ...` o `python -m nina ...`):
    refactor "ruta/archivo.jsx" "Instrucción para la IA"
    fix "stack trace"          # o:  fix  (pegás el trace)  |  fix -p (portapapeles)
    deshacer "ruta/archivo.jsx"
    probar

Todos aceptan --proyecto/-C para indicar la raíz (por defecto, la carpeta actual); las rutas relativas se leen desde esa raíz.
Cada ejecución arranca de cero (sin historial) para no inflar el KV cache de la GPU.
"""
import sys
import time
from typing import Optional

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from .config import API_URL, MAX_INTENTOS, MAX_TOKENS_PROMPT, MODELO, PERFIL
from .contexto import aviso_recorte, generar_cambio, preparar_contexto
from .diff import diff_unificado
from .llm import ErrorLLM, listar_modelos, stream_llm
from .proyecto import Proyecto, archivo_cambio_en_disco, escribir_archivo, leer_archivo
from .trazas import analizar_traza, fragmento_alrededor

app = typer.Typer(add_completion=False, help="Pair programming local, stateless y con diff.")
console = Console()


def _proyecto(raiz: str) -> Proyecto:
    p = Proyecto(raiz)
    if not p.raiz.is_dir():
        console.print(f"[bold red]No existe la carpeta del proyecto: {escape(raiz)}[/bold red]")
        raise typer.Exit(1)
    return p


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


# ------------------------------ Diff ---------------------------------------- #
def mostrar_diff(original: str, nuevo: str, nombre: str):
    diff = diff_unificado(original, nuevo, nombre)
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
def ejecutar(proyecto: Proyecto, archivo: str, instruccion: str, sin_mapa: bool = False, extra_busqueda: str = ""):
    ruta = proyecto.abs(archivo)
    if not ruta.is_file():
        console.print(f"[bold red]No se encontró el archivo {escape(archivo)}[/bold red]")
        raise typer.Exit(1)

    # 1. Seguridad Git: no mezclar cambios de la IA con cambios tuyos sin guardar
    if proyecto.git_archivo_sucio(ruta):
        console.print(f"[bold yellow]⚠️ {escape(archivo)} tiene cambios sin commitear en Git.[/bold yellow]")
        if not typer.confirm("¿Querés que la IA lo modifique igual y correr el riesgo de mezclar cambios?", default=False):
            raise typer.Exit(0)

    ctx = preparar_contexto(proyecto, ruta, instruccion, sin_mapa, extra_busqueda)
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
    
    cambio = res.cambios[0] if res.cambios else None

    if cambio:
        for a in cambio.avisos:
            console.print(f"[bold yellow]⚠️  {escape(a)}[/bold yellow]")
            
    if not res.es_valida or not cambio:
        errores = cambio.errores if cambio else ["El modelo no devolvió una respuesta válida."]
        for e in errores:
            console.print(f"[bold red]✗ {escape(e)}[/bold red]")
        console.print("[bold red]❌ No se obtuvo un cambio válido. No se modificó nada; probá reformular la instrucción.[/bold red]")
        raise typer.Exit(1)

    mas, menos = mostrar_diff(cambio.original, cambio.nuevo, ctx.clave)
    if mas == 0 and menos == 0:
        console.print("[yellow]La respuesta no produce cambios.[/yellow]")
        return
    console.print(f"[green]+{mas}[/green] [red]-{menos}[/red]")
    recorte = aviso_recorte(cambio.original, cambio.nuevo)
    if recorte:
        console.print(f"[bold red]{recorte}[/bold red]")

    if typer.confirm("\n¿Aplicar estos cambios?", default=False):
        if archivo_cambio_en_disco(ruta, cambio.original):
            console.print("[bold red]El archivo cambió en disco mientras esperabas. No se guardó nada; volvé a ejecutar.[/bold red]")
            raise typer.Exit(1)
        backup = proyecto.hacer_backup(ruta)
        escribir_archivo(ruta, cambio.nuevo, cambio.eol)
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
    proyecto: str = typer.Option(".", "--proyecto", "-C", help="Raíz del proyecto"),
):
    """Modifica un archivo según una instrucción (con diff y backup)."""
    ejecutar(_proyecto(proyecto), archivo, instruccion, sin_mapa)


@app.command()
def fix(
    traza: Optional[str] = typer.Argument(None, help="Stack trace. Omitilo (o usá '-') para pegarlo por stdin"),
    portapapeles: bool = typer.Option(False, "--portapapeles", "-p", help="Leer el trace del portapapeles (pip install pyperclip)"),
    archivo: Optional[str] = typer.Option(None, "--archivo", "-a", help="Forzar el archivo a corregir"),
    sin_mapa: bool = typer.Option(False, "--sin-mapa"),
    proyecto: str = typer.Option(".", "--proyecto", "-C", help="Raíz del proyecto"),
):
    """Detecta el archivo que falla a partir de un stack trace y pide el arreglo."""
    p = _proyecto(proyecto)
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

    objetivo, linea_error, otros = archivo, None, []
    if not objetivo:
        objetivo, linea_error, otros = analizar_traza(traza, p.cargar_mapa(), p)
    if not objetivo:
        console.print("[red]No encontré ningún archivo del proyecto en el trace. Usá --archivo para indicarlo.[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]Archivo detectado:[/bold] {escape(objetivo)}" + (f" (línea {linea_error})" if linea_error else ""))
    if otros:
        console.print(f"[dim]Otros archivos del proyecto en el trace: {escape(', '.join(otros[:5]))}[/dim]")

    fragmento = ""
    if linea_error:
        texto, _ = leer_archivo(p.abs(objetivo))
        fragmento = fragmento_alrededor(texto, linea_error)

    instruccion = "Corregí el error descrito por este stack trace, con el cambio mínimo necesario."
    if linea_error:
        instruccion += f"\nEl fallo está en la línea {linea_error}. Fragmento (con números de línea, que NO forman parte del código):\n{fragmento}"
    instruccion += f"\n\nStack trace:\n{traza[:4000]}"
    ejecutar(p, objetivo, instruccion, sin_mapa, extra_busqueda=traza)


@app.command()
def deshacer(
    archivo: str = typer.Argument(..., help="Archivo a restaurar desde su último backup"),
    proyecto: str = typer.Option(".", "--proyecto", "-C", help="Raíz del proyecto"),
):
    """Restaura el último backup de un archivo."""
    p = _proyecto(proyecto)
    ruta = p.abs(archivo)
    backup = p.ultimo_backup(ruta)
    if not backup:
        console.print("[yellow]No hay backups para ese archivo.[/yellow]")
        raise typer.Exit(1)
    actual, eol = leer_archivo(ruta) if ruta.is_file() else ("", "\n")
    previo, _ = leer_archivo(backup)
    mostrar_diff(actual, previo, p.clave(ruta))
    if typer.confirm(f"¿Restaurar {backup.name}?", default=False):
        if ruta.is_file():
            p.hacer_backup(ruta)  # el estado actual también queda respaldado
        escribir_archivo(ruta, previo, eol)
        console.print("[bold green]✅ Restaurado.[/bold green]")


@app.command()
def probar():
    """Prueba la conexión con el servidor y muestra cómo llegan el razonamiento y la respuesta."""
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