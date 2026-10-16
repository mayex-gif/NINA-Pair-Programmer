"""
Ejecutor de comandos para el agente (Fase 6): corre SOLO comandos cuyo primer token está en una lista blanca,
sin shell (sin pipes, redirecciones ni `&&`), con cwd en la raíz del proyecto, timeout y salida acotada.
"""
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

PERMITIDOS_POR_DEFECTO = ("python", "python3", "pytest", "pip", "npm", "npx", "node", "mvn", "gradle", "cargo", "go", "git")
# Subcomandos que nunca se permiten aunque el binario esté en la lista (instalar globalmente, publicar, borrar ramas...)
PROHIBIDOS = {("npm", "publish"), ("pip", "uninstall"), ("git", "push"), ("git", "remote"), ("git", "clean"), ("git", "reset")}
MAX_SALIDA = 6000


class ComandoNoPermitido(Exception):
    pass


@dataclass
class Resultado:
    comando: str
    codigo: int
    salida: str
    segundos_agotado: bool = False

    @property
    def ok(self) -> bool:
        return self.codigo == 0 and not self.segundos_agotado


def _recortar(texto: str, tope: int = MAX_SALIDA) -> str:
    if len(texto) <= tope:
        return texto
    mitad = tope // 2
    return texto[:mitad] + "\n[... salida recortada ...]\n" + texto[-mitad:]


def dividir_comando(comando: str, windows: Optional[bool] = None) -> list:
    """
    Parte el comando en argumentos como lo haría la consola. En Windows se usa shlex no-POSIX (para no comerse las
    barras de `tests\\x.py`), que CONSERVA las comillas: hay que sacárselas, o `python -c "print(1)"` le llegaría a
    Python con comillas literales (un string suelto: no ejecuta nada y termina en 0, o sea, una verificación falsa).
    """
    windows = (sys.platform == "win32") if windows is None else windows
    partes = shlex.split(comando, posix=not windows)
    if windows:
        partes = [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in partes]
    return partes


def validar(comando: str, permitidos: Iterable[str] = PERMITIDOS_POR_DEFECTO, windows: Optional[bool] = None) -> list:
    try:
        partes = dividir_comando(comando, windows)
    except ValueError as e:
        raise ComandoNoPermitido(f"Comando mal formado: {e}")
    if not partes:
        raise ComandoNoPermitido("Comando vacío.")
    binario = Path(partes[0]).name.lower().removesuffix(".exe")
    if binario != partes[0].lower() and ("/" in partes[0] or "\\" in partes[0]):
        raise ComandoNoPermitido(f"Rutas explícitas a binarios no permitidas: {partes[0]}")
    ok_bin = {Path(p).name.lower().removesuffix(".exe") for p in permitidos}
    if binario not in ok_bin and not (binario == "python3" and "python" in ok_bin):
        raise ComandoNoPermitido(f"'{partes[0]}' no está en la lista de comandos permitidos.")
    if len(partes) > 1 and (binario, partes[1]) in PROHIBIDOS:
        raise ComandoNoPermitido(f"'{binario} {partes[1]}' está prohibido.")
    for a in partes[1:]:
        if a in ("&&", "||", ";", "|", ">", ">>", "<"):
            raise ComandoNoPermitido("No se permiten operadores de shell (se ejecuta sin shell).")
    return partes


def ejecutar(raiz, comando: str, permitidos: Iterable[str] = PERMITIDOS_POR_DEFECTO, timeout: int = 300) -> Resultado:
    partes = validar(comando, permitidos)
    if Path(partes[0]).name.lower().removesuffix(".exe") in ("python", "python3"):
        partes[0] = sys.executable      # `python3` no existe en Windows; se usa el intérprete que corre NINA (mismo entorno)
    try:
        r = subprocess.run(partes, cwd=raiz, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})   # sin .pyc viejos entre intentos
    except FileNotFoundError:
        return Resultado(comando, 127, f"No se encontró el ejecutable '{partes[0]}'.")
    except subprocess.TimeoutExpired as e:
        parcial = (e.stdout or "") if isinstance(e.stdout, str) else ""
        return Resultado(comando, -1, _recortar(parcial) + f"\n[timeout de {timeout}s]", segundos_agotado=True)
    return Resultado(comando, r.returncode, _recortar((r.stdout or "") + (r.stderr or "")))
