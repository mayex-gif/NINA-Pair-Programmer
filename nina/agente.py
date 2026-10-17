"""
Fase 6 – Agente headless: planificar -> ejecutar tarea por tarea -> verificar -> reparar, con checkpoints en Git.

Todo sigue siendo "stateless": cada tarea es un prompt nuevo (contexto = convenciones + mapa + instrucción de la tarea
+ resultado de la verificación fallida). El estado vive en disco (`.nina/plan.json` y `.nina/eventos.jsonl`), así que
una corrida interrumpida se puede reanudar: las tareas "hecha" se saltean.

Garantías:
  * Antes de cada tarea se hace un checkpoint Git; si la tarea no se logra se vuelve a él (el árbol queda como estaba).
  * Los comandos de verificación pasan por `ejecutor` (lista blanca, sin shell, con timeout).
  * Presupuestos: máx. de reparaciones por tarea, de tareas fallidas seguidas y de tiempo total.
  * Las escrituras usan las mismas protecciones que la UI (ruta segura, backup por lote, EOL original).
"""
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import ejecutor, gitutil
from .config import EXTENSIONES_AGENTE, NOMBRES_AGENTE
from .contexto import generar_cambio, preparar_contexto
from .proyecto import Proyecto, escribir_archivo

PENDIENTE, HECHA, FALLIDA = "pendiente", "hecha", "fallida"


@dataclass
class Tarea:
    id: str
    titulo: str
    instruccion: str
    verificacion: List[str] = field(default_factory=list)   # comandos; todos deben terminar con código 0
    archivos: List[str] = field(default_factory=list)       # rutas que toca la tarea: se mandan completas al modelo
    esperas: Dict[str, str] = field(default_factory=dict)   # comando -> texto que debe aparecer en su salida
    estado: str = PENDIENTE
    intentos: int = 0
    detalle: str = ""


@dataclass
class Presupuesto:
    reparaciones_por_tarea: int = 3
    fallidas_seguidas: int = 2          # tras N tareas fallidas seguidas, el agente se detiene
    segundos_totales: int = 4 * 3600
    timeout_comando: int = 300
    rondas_aceptacion: int = 2          # veces que se intenta reparar si falla la aceptación final


# ------------------------------------------------------------------ persistencia
def carpeta_estado(proyecto: Proyecto) -> Path:
    return proyecto.raiz / ".nina"


def preparar_estado(proyecto: Proyecto) -> Path:
    """
    Crea `.nina/` con su .gitignore ANTES del primer checkpoint. Si el registro de eventos entra en un commit, el
    `git reset --hard` de un revert lo devuelve a ese estado y se pierden justo los eventos de la tarea fallida
    (se vio en la prueba real: solo quedaban `tarea_inicio` y `tarea_revertida`).
    """
    d = carpeta_estado(proyecto)
    d.mkdir(exist_ok=True)
    g = d / ".gitignore"
    if not g.exists() or "eventos.jsonl" not in g.read_text(encoding="utf-8"):
        g.write_text("eventos.jsonl\n", encoding="utf-8")
    gitutil.dejar_de_seguir(proyecto.raiz, ".nina/eventos.jsonl")
    return d


def _leer_plan(proyecto: Proyecto) -> dict:
    f = carpeta_estado(proyecto) / "plan.json"
    if not f.is_file():
        return {}
    return json.loads(f.read_text(encoding="utf-8-sig"))      # tolera el BOM que agrega PowerShell/Notepad al editarlo a mano


def guardar_plan(proyecto: Proyecto, objetivo: str, tareas: List[Tarea], aceptacion: Optional[list] = None):
    """`aceptacion=None` conserva la que ya tenga el plan en disco (se puede editar a mano)."""
    d = preparar_estado(proyecto)
    if aceptacion is None:
        aceptacion = _leer_plan(proyecto).get("aceptacion", [])
    (d / "plan.json").write_text(
        json.dumps({"objetivo": objetivo, "aceptacion": aceptacion, "tareas": [asdict(t) for t in tareas]},
                   indent=2, ensure_ascii=False), encoding="utf-8")


def cargar_plan(proyecto: Proyecto):
    """(objetivo, [Tarea]) o ("", []) si no hay plan."""
    datos = _leer_plan(proyecto)
    return datos.get("objetivo", ""), [Tarea(**t) for t in datos.get("tareas", [])]


def cargar_aceptacion(proyecto: Proyecto) -> list:
    """Pruebas de aceptación del plan: [{"comando": "...", "espera": "texto en la salida (opcional)"}]."""
    return [a for a in _leer_plan(proyecto).get("aceptacion", []) if isinstance(a, dict) and a.get("comando")]


def registrar(proyecto: Proyecto, evento: str, **datos):
    d = preparar_estado(proyecto)
    with open(d / "eventos.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "evento": evento, **datos}, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------ planificación
PROMPT_PLAN = """Sos el planificador de un sistema de desarrollo automático. Dividí el objetivo en tareas PEQUEÑAS, ordenadas y \
verificables (cada una toca pocos archivos y puede comprobarse con un comando).
Respondé SOLO con un JSON, sin texto alrededor, con esta forma:
{"tareas": [{"titulo": "...", "instruccion": "qué crear o modificar, con rutas de archivo", "archivos": ["ruta/a.py"], "verificacion": ["python -m pytest -q"]}],
 "aceptacion": [{"comando": "python cli.py listar", "espera": "texto que debe aparecer en la salida"}]}
Los `aceptacion` son comandos que se ejecutan EN ORDEN al final, sobre el proyecto terminado, y comprueban que el OBJETIVO se cumple de punta a punta (no solo que existan archivos): ejecutá la aplicación como lo haría un usuario. No uses `python -c` con sentencias compuestas. Implementá la lógica real en cada tarea, sin esqueletos vacíos.
Usá `python` (nunca `python3`). Cada verificación debe poder pasar al terminar ESA tarea (no dependas de archivos de tareas posteriores). Preferí pytest sobre archivos concretos. REGLA: cada tarea que crea o cambia código debe crear en esa misma tarea la prueba que la verifica; nunca dejes las pruebas para una tarea posterior (pytest sin pruebas no verifica nada). En `python -c` no uses `try:`, `for` ni otras sentencias compuestas (una sola línea con `;` no las admite): para eso escribí una prueba pytest.
Comandos de verificación permitidos: %s. Máximo %d tareas.

OBJETIVO:
%s
"""


def extraer_json(texto: str) -> Optional[dict]:
    """Saca el primer objeto JSON de una respuesta (tolera ```json ...``` y texto alrededor)."""
    texto = re.sub(r"```(?:json)?", "", texto)
    ini = texto.find("{")
    while ini != -1:
        profundidad, en_str, esc = 0, False, False
        for i in range(ini, len(texto)):
            c = texto[i]
            if en_str:
                esc = (c == "\\") and not esc
                if c == '"' and not esc:
                    en_str = False
                if c != "\\":
                    esc = False
            elif c == '"':
                en_str = True
            elif c == "{":
                profundidad += 1
            elif c == "}":
                profundidad -= 1
                if profundidad == 0:
                    try:
                        return json.loads(texto[ini:i + 1])
                    except json.JSONDecodeError:
                        break
        ini = texto.find("{", ini + 1)
    return None


def planificar_con_aceptacion(objetivo: str, llamar: Callable[[list], str], max_tareas: int = 12,
                              permitidos=ejecutor.PERMITIDOS_POR_DEFECTO):
    """Pide el plan al modelo. Devuelve (tareas, aceptacion). Descarta lo mal formado y los comandos no permitidos."""
    prompt = PROMPT_PLAN % (", ".join(permitidos), max_tareas, objetivo)
    datos = extraer_json(llamar([{"role": "user", "content": prompt}]))
    if not datos or not isinstance(datos.get("tareas"), list):
        raise ValueError("El planificador no devolvió un JSON con 'tareas'.")
    tareas = []
    for i, t in enumerate(datos["tareas"][:max_tareas], 1):
        if not isinstance(t, dict) or not str(t.get("instruccion", "")).strip():
            continue
        cmds = []
        for c in t.get("verificacion") or []:
            try:
                ejecutor.validar(str(c), permitidos)
                cmds.append(str(c))
            except ejecutor.ComandoNoPermitido:
                pass
        archivos = [str(a) for a in (t.get("archivos") or []) if isinstance(a, str)]
        tareas.append(Tarea(id=f"T{i:02d}", titulo=str(t.get("titulo") or f"Tarea {i}"),
                            instruccion=str(t["instruccion"]), verificacion=cmds, archivos=archivos))
    if not tareas:
        raise ValueError("El plan quedó vacío.")
    aceptacion = []
    for a in datos.get("aceptacion") or []:
        if isinstance(a, dict) and a.get("comando"):
            try:
                ejecutor.validar(str(a["comando"]), permitidos)
            except ejecutor.ComandoNoPermitido:
                continue
            aceptacion.append({"comando": str(a["comando"]), "espera": str(a.get("espera") or "")})
    return tareas, aceptacion


def planificar(objetivo: str, llamar: Callable[[list], str], max_tareas: int = 12,
               permitidos=ejecutor.PERMITIDOS_POR_DEFECTO) -> List[Tarea]:
    return planificar_con_aceptacion(objetivo, llamar, max_tareas, permitidos)[0]


# ------------------------------------------------------------------ ejecución
def aplicar_propuesta(proyecto: Proyecto, propuesta) -> List[str]:
    """Escribe el lote validado (ruta segura + backup transaccional). Devuelve las claves escritas."""
    rutas = [proyecto.validar_ruta_segura(proyecto.abs(c.clave), EXTENSIONES_AGENTE, NOMBRES_AGENTE) for c in propuesta.cambios]
    proyecto.hacer_backup(rutas)
    for c, r in zip(propuesta.cambios, rutas):
        r.parent.mkdir(parents=True, exist_ok=True)
        escribir_archivo(r, c.nuevo, c.eol)
    return [c.clave for c in propuesta.cambios]


def verificacion_invalida(cmd: str, salida: str) -> bool:
    """True si el SyntaxError viene del propio `python -c "..."` del plan (traceback en File "<string>")."""
    return " -c" in cmd and "SyntaxError" in salida and 'File "<string>"' in salida


def _instruccion(t: Tarea, fallo: str = "", reintento: bool = False, objetivo: str = "") -> str:
    base = f"[{t.id}] {t.titulo}\n{t.instruccion}"
    if objetivo:
        base += (f"\n\nOBJETIVO GENERAL DEL PROYECTO (no lo pierdas de vista: hacé SOLO esta tarea, pero con la lógica real que "
                 f"el objetivo necesita, sin esqueletos ni funciones vacías):\n{objetivo}")
    if reintento:
        base += ("\n\nOJO: en un intento anterior de esta tarea ya se escribieron archivos. Su contenido actual está arriba: "
                 "si existen, modificalos con bloques SEARCH/REPLACE sobre ese contenido (no uses un SEARCH vacío).")
    if fallo:
        base += ("\n\nLa verificación de esta tarea FALLÓ con la siguiente salida. Corregí el código para que pase "
                 f"(no desactives ni borres las pruebas):\n{fallo}")
    return base


def ejecutar_tarea(proyecto: Proyecto, t: Tarea, llamar, pres: Presupuesto, permitidos, on_evento=None, objetivo: str = "") -> bool:
    ev = on_evento or (lambda *a, **k: None)
    punto = gitutil.checkpoint(proyecto.raiz, f"nina: antes de {t.id} {t.titulo}")
    fallo, tocados = "", []
    for intento in range(1, pres.reparaciones_por_tarea + 2):
        t.intentos = intento
        ctx = preparar_contexto(proyecto, None, _instruccion(t, fallo, reintento=intento > 1, objetivo=objetivo),
                                incluir=list(dict.fromkeys(t.archivos + tocados)))
        prop = generar_cambio(ctx, proyecto, llamar)
        if not prop.es_valida:
            fallo = "; ".join(e for c in prop.cambios for e in c.errores) or "respuesta inválida"
            t.detalle = f"sin cambio válido: {fallo}"
            ev("tarea_sin_cambio_valido", id=t.id, intento=intento, error=fallo)
            continue
        try:
            escritos = aplicar_propuesta(proyecto, prop)
        except (ValueError, OSError) as e:
            fallo = f"no se pudo escribir: {e}"
            t.detalle = fallo
            ev("tarea_escritura_rechazada", id=t.id, error=str(e))
            continue
        ev("tarea_escrita", id=t.id, intento=intento, archivos=escritos)
        tocados = list(dict.fromkeys(tocados + escritos))
        for k in escritos:                       # el mapa también debe ver lo recién escrito (el Vigía no corre en modo headless)
            try:
                proyecto.actualizar_archivo_en_mapa(k)
            except Exception:
                pass
        fallos, omitidas, sin_pruebas = [], 0, False
        for cmd in t.verificacion:
            r = ejecutor.ejecutar(proyecto.raiz, cmd, permitidos, pres.timeout_comando)
            ev("verificacion", id=t.id, comando=cmd, codigo=r.codigo)
            if r.sin_pruebas:
                # Pasó en la prueba real: la tarea 1 solo escribía código, las pruebas eran de la tarea 2, y el agente gastó
                # 4 intentos (7 min) reparando algo que no estaba roto. No se reintenta; queda advertido en la tarea.
                omitidas += 1
                sin_pruebas = True
                ev("verificacion_sin_pruebas", id=t.id, comando=cmd)
                continue
            if not r.ok and verificacion_invalida(cmd, r.salida):
                # El fallo es del comando que escribió el planificador (p. ej. `try:` en una línea de -c), no del código:
                # reparar el código no lo arregla. Se omite y se deja constancia en la tarea.
                omitidas += 1
                ev("verificacion_invalida", id=t.id, comando=cmd, salida=r.salida[-300:])
                continue
            if r.ok and t.esperas.get(cmd) and t.esperas[cmd] not in r.salida:
                fallos.append(f"$ {cmd}\nSalió con código 0, pero la salida debía contener «{t.esperas[cmd]}» y fue:\n{r.salida}")
                break
            if not r.ok:
                fallos.append(f"$ {cmd}\n{r.salida}")
                break
        if not fallos:
            t.estado, t.detalle = HECHA, f"{len(escritos)} archivo(s), {intento} intento(s)"
            if omitidas:
                t.detalle += (" · ⚠ pytest no encontró pruebas (la tarea no quedó verificada)" if sin_pruebas
                              else f" · ⚠ {omitidas} verificación(es) omitida(s) por comando inválido")
            gitutil.checkpoint(proyecto.raiz, f"nina: {t.id} {t.titulo}")
            return True
        fallo = fallos[0]
        t.detalle = "la verificación falla"
    t.estado = FALLIDA
    if punto:
        gitutil.revertir_a(proyecto.raiz, punto)
        ev("tarea_revertida", id=t.id, a=punto)
    return False


def ejecutar_plan(proyecto: Proyecto, tareas: List[Tarea], llamar, pres: Optional[Presupuesto] = None,
                  permitidos=ejecutor.PERMITIDOS_POR_DEFECTO, objetivo: str = "",
                  on_evento: Optional[Callable] = None, reloj: Callable[[], float] = time.monotonic) -> str:
    """Corre las tareas pendientes en orden. Devuelve "completo" | "detenido_por_fallos" | "sin_tiempo"."""
    pres = pres or Presupuesto()
    inicio, seguidas = reloj(), 0
    preparar_estado(proyecto)

    def ev(nombre, **d):
        registrar(proyecto, nombre, **d)
        if on_evento:
            on_evento(nombre, d)

    for t in tareas:
        if t.estado == HECHA:
            continue
        if reloj() - inicio > pres.segundos_totales:
            ev("sin_tiempo", pendientes=[x.id for x in tareas if x.estado != HECHA])
            return "sin_tiempo"
        ev("tarea_inicio", id=t.id, titulo=t.titulo)
        t.estado = PENDIENTE
        ok = ejecutar_tarea(proyecto, t, llamar, pres, permitidos, ev, objetivo)
        guardar_plan(proyecto, objetivo, tareas)
        ev("tarea_fin", id=t.id, estado=t.estado, detalle=t.detalle)
        seguidas = 0 if ok else seguidas + 1
        if seguidas >= pres.fallidas_seguidas:
            ev("detenido_por_fallos", seguidas=seguidas)
            return "detenido_por_fallos"
    return _aceptacion(proyecto, tareas, llamar, pres, permitidos, objetivo, ev)


def _correr_aceptacion(proyecto, aceptacion, permitidos, pres, ev) -> list:
    """Corre las pruebas de aceptación en orden. Devuelve las que fallan: [(comando, espera, salida)]."""
    fallan = []
    for a in aceptacion:
        cmd, espera = a["comando"], a.get("espera") or ""
        try:
            r = ejecutor.ejecutar(proyecto.raiz, cmd, permitidos, pres.timeout_comando)
        except ejecutor.ComandoNoPermitido as e:
            ev("aceptacion_invalida", comando=cmd, error=str(e))     # no se arregla con código: se omite
            continue
        ok = r.ok and (not espera or espera in r.salida)
        ev("aceptacion", comando=cmd, codigo=r.codigo, ok=ok)
        if not ok:
            fallan.append((cmd, espera, r.salida))
    return fallan


def _aceptacion(proyecto, tareas, llamar, pres, permitidos, objetivo, ev) -> str:
    """Prueba final del OBJETIVO (no de cada tarea). Si falla, crea tareas de reparación (hasta `rondas_aceptacion`)."""
    aceptacion = cargar_aceptacion(proyecto)
    if not aceptacion:
        return "completo"
    for ronda in range(1, pres.rondas_aceptacion + 2):
        fallan = _correr_aceptacion(proyecto, aceptacion, permitidos, pres, ev)
        if not fallan:
            ev("aceptacion_ok", comandos=len(aceptacion))
            return "completo"
        if ronda > pres.rondas_aceptacion:
            break
        detalle = "\n\n".join(
            f"$ {c}" + (f"\n(debía contener «{e}»)" if e else "") + f"\nSalida:\n{s[-1500:]}" for c, e, s in fallan)
        rep = Tarea(id=f"A{ronda:02d}", titulo="Reparar la aceptación del objetivo",
                    instruccion=("La aplicación terminada NO cumple el objetivo al probarla de punta a punta. Corregí el código "
                                 f"(implementá lo que falte de verdad):\n{detalle}"),
                    archivos=list(dict.fromkeys(a for t in tareas for a in t.archivos)),
                    verificacion=[c for c, _, _ in fallan], esperas={c: e for c, e, _ in fallan if e})
        tareas.append(rep)
        ev("tarea_inicio", id=rep.id, titulo=rep.titulo)
        ok = ejecutar_tarea(proyecto, rep, llamar, pres, permitidos, ev, objetivo)
        guardar_plan(proyecto, objetivo, tareas)
        ev("tarea_fin", id=rep.id, estado=rep.estado, detalle=rep.detalle)
        if not ok:
            break
    ev("aceptacion_fallida")
    return "aceptacion_fallida"
