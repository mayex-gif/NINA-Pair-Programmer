"""
Proyecto: todo lo que depende de DÓNDE está el proyecto (mapa, convenciones, backups, git).
Reemplaza al estado global (`os.chdir` + rutas relativas a nivel de módulo): cada Proyecto lleva su raíz explícita,
así la web puede tener varios a la vez y nada depende del directorio actual del proceso.
"""
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import NOMBRE_BACKUPS, NOMBRE_CONVENCIONES, NOMBRE_MAPA


# ------------------------------ E/S de archivos (no dependen del proyecto) ----- #
def leer_archivo(ruta: Path):
    """Lee preservando el tipo de salto de línea (CRLF/LF) para no ensuciar el diff de git."""
    with open(ruta, "r", encoding="utf-8", newline="") as f:
        crudo = f.read()
    eol = "\r\n" if "\r\n" in crudo else "\n"
    return crudo.replace("\r\n", "\n"), eol


def escribir_archivo(ruta: Path, texto: str, eol: str):
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        f.write(texto.replace("\n", eol))


def archivo_cambio_en_disco(ruta: Path, original: str) -> bool:
    """True si el archivo ya no es el que le mandamos a la IA (lo editaste mientras tanto)."""
    try:
        actual, _ = leer_archivo(ruta)
    except OSError:
        return True
    return actual != original


class Proyecto:
    def __init__(self, raiz="."):
        self.raiz = Path(raiz).expanduser().resolve()

    def __repr__(self):
        return f"Proyecto({str(self.raiz)!r})"

    # ------------------------------ Rutas ------------------------------------ #
    @property
    def ruta_mapa(self) -> Path:
        return self.raiz / NOMBRE_MAPA

    @property
    def ruta_convenciones(self) -> Path:
        return self.raiz / NOMBRE_CONVENCIONES

    @property
    def carpeta_backups(self) -> Path:
        return self.raiz / NOMBRE_BACKUPS

    def abs(self, ruta) -> Path:
        """Ruta absoluta. Las relativas se interpretan desde la raíz del proyecto."""
        p = Path(ruta).expanduser()
        return p if p.is_absolute() else self.raiz / p

    def clave(self, ruta) -> str:
        """Ruta relativa a la raíz, con '/' (así se identifican los archivos en el mapa)."""
        p = self.abs(ruta)
        try:
            return p.resolve().relative_to(self.raiz).as_posix()
        except ValueError:
            return p.as_posix()

    # ------------------------------ Git -------------------------------------- #
    def git_archivo_sucio(self, ruta) -> bool:
        """True si el archivo tiene cambios sin commitear. Sin git instalado o fuera de un repo -> False."""
        try:
            r = subprocess.run(
                ["git", "status", "--porcelain", "--", str(self.abs(ruta))],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                check=True, timeout=10, cwd=self.raiz,
            )
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False
        return bool(r.stdout.strip())

    # ------------------------------ Backups ---------------------------------- #
    def hacer_backup(self, ruta) -> Path:
        carpeta = self.carpeta_backups
        carpeta.mkdir(exist_ok=True)
        gitignore = carpeta / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n", encoding="utf-8")  # git ignora todo el contenido
        sello = datetime.now().strftime("%Y%m%d-%H%M%S")
        nombre = self.clave(ruta).replace("/", "__")
        destino = carpeta / f"{nombre}.{sello}.bak"
        shutil.copy2(self.abs(ruta), destino)
        return destino

    def ultimo_backup(self, ruta) -> Optional[Path]:
        carpeta = self.carpeta_backups
        if not carpeta.is_dir():
            return None
        prefijo = self.clave(ruta).replace("/", "__") + "."
        candidatos = sorted(p for p in carpeta.glob("*.bak") if p.name.startswith(prefijo))
        return candidatos[-1] if candidatos else None

    # ------------------------------ Contexto en disco ------------------------ #
    def cargar_convenciones(self) -> str:
        if self.ruta_convenciones.is_file():
            return self.ruta_convenciones.read_text(encoding="utf-8").strip()
        return ""

    def cargar_mapa(self) -> dict:
        """Devuelve {ruta: {'signatures': [...], 'imports': [...]}} (soporta el formato viejo v1)."""
        try:
            datos = json.loads(self.ruta_mapa.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        if isinstance(datos, dict) and "files" in datos:
            return datos["files"]
        return {
            ruta: {"signatures": [s] if isinstance(s, str) else list(s), "imports": []}
            for ruta, s in datos.items()
        }
