"""
Fase 4.4 – Git como red de seguridad: checkpoints (commits) antes de que NINA escriba un lote y vuelta atrás exacta.
Sin dependencias: usa el binario `git`. Si el proyecto no es un repo (o no hay git) todo degrada a "no hacer nada".
"""
import subprocess
from pathlib import Path
from typing import Optional

_ID = ["-c", "user.name=NINA", "-c", "user.email=nina@localhost", "-c", "commit.gpgsign=false"]


def _git(raiz: Path, *args: str, timeout: int = 30) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(["git", *args], cwd=raiz, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def es_repo(raiz) -> bool:
    r = _git(Path(raiz), "rev-parse", "--is-inside-work-tree")
    return bool(r and r.returncode == 0 and r.stdout.strip() == "true")


def inicializar(raiz) -> bool:
    """git init si todavía no es un repo (el agente lo necesita para poder revertir una tarea fallida)."""
    raiz = Path(raiz)
    if es_repo(raiz):
        return True
    r = _git(raiz, "init")
    return bool(r and r.returncode == 0)


def rama_actual(raiz) -> str:
    r = _git(Path(raiz), "rev-parse", "--abbrev-ref", "HEAD")
    return r.stdout.strip() if r and r.returncode == 0 else ""


def crear_rama(raiz, nombre: str) -> bool:
    r = _git(Path(raiz), "checkout", "-B", nombre)
    return bool(r and r.returncode == 0)


def checkpoint(raiz, mensaje: str) -> Optional[str]:
    """Commitea TODO el árbol de trabajo (incluye lo tuyo sin commitear: es una foto) y devuelve el hash, o None."""
    raiz = Path(raiz)
    if not es_repo(raiz):
        return None
    _git(raiz, "add", "-A")
    r = _git(raiz, *_ID, "commit", "--allow-empty", "-m", mensaje)
    if not r or r.returncode != 0:
        return None
    h = _git(raiz, "rev-parse", "HEAD")
    return h.stdout.strip() if h and h.returncode == 0 else None


def revertir_a(raiz, hash_: str) -> bool:
    """Deja el árbol exactamente como en `hash_` (descarta cambios y archivos nuevos sin seguimiento). Destructivo."""
    raiz = Path(raiz)
    r = _git(raiz, "reset", "--hard", hash_)
    if not r or r.returncode != 0:
        return False
    # .ai_backups está ignorado por su propio .gitignore, así que `clean` no lo toca
    c = _git(raiz, "clean", "-fd")
    return bool(c and c.returncode == 0)
