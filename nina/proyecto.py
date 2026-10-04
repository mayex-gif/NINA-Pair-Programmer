"""
Proyecto: todo lo que depende de DÓNDE está el proyecto (mapa, convenciones, backups, git).
Reemplaza al estado global (`os.chdir` + rutas relativas a nivel de módulo): cada Proyecto lleva su raíz explícita,
así la web puede tener varios a la vez y nada depende del directorio actual del proceso.
"""
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from .config import NOMBRE_BACKUPS, NOMBRE_CONVENCIONES, NOMBRE_MAPA


# ------------------------------ E/S de archivos (no dependen del proyecto) ----- #
def leer_archivo(ruta: Path):
    """Lee preservando el tipo de salto de línea (CRLF/LF) para no ensuciar el diff de git."""
    with open(ruta, "r", encoding="utf-8", newline="") as f:
        crudo = f.read()
    eol = "\r\n" if "\r\n" in crudo else "\n"
    return crudo.replace("\r\n", "\n"), eol


def escribir_archivo(ruta: Path, texto: str, eol: str):
    """Escritura atómica usando un archivo temporal y os.replace."""
    directorio = ruta.parent
    directorio.mkdir(parents=True, exist_ok=True)
    
    # Se crea un temporal en la misma carpeta para asegurar que os.replace sea atómico (mismo disco)
    fd, temp_path = tempfile.mkstemp(dir=directorio, prefix=".nina_tmp_", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(texto.replace("\n", eol))
        os.replace(temp_path, ruta)
    except Exception as e:
        Path(temp_path).unlink(missing_ok=True)
        raise e


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

    # ------------------------------ Backups por Lote ------------------------- #
    def hacer_backup(self, rutas: Union[Path, str, list]) -> Path:
        """Crea un backup transaccional para un lote de archivos con manifiesto."""
        if isinstance(rutas, (str, Path)):
            rutas = [rutas]

        carpeta = self.carpeta_backups
        carpeta.mkdir(exist_ok=True)
        gitignore = carpeta / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n", encoding="utf-8")

        # Sello con milisegundos para evitar colisiones
        sello = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:19]
        lote_dir = carpeta / f"lote_{sello}"
        lote_dir.mkdir(exist_ok=True)

        manifiesto = {"id": sello, "archivos": {}}

        for r in rutas:
            ruta_abs = self.abs(r)
            clave = self.clave(r)
            if ruta_abs.exists():
                # Archivo existente: se copia al backup
                nombre_bak = clave.replace("/", "__") + ".bak"
                destino = lote_dir / nombre_bak
                shutil.copy2(ruta_abs, destino)
                manifiesto["archivos"][clave] = {"estado": "modificado", "backup": nombre_bak}
            else:
                # Archivo nuevo
                manifiesto["archivos"][clave] = {"estado": "nuevo"}

        (lote_dir / "manifiesto.json").write_text(json.dumps(manifiesto, indent=2), encoding="utf-8")
        return lote_dir

    def ultimo_backup(self, ruta) -> Optional[Path]:
        """Busca el último backup de un archivo específico iterando los manifiestos."""
        carpeta = self.carpeta_backups
        if not carpeta.is_dir():
            return None
            
        clave_buscada = self.clave(ruta)
        # Ordenamos los lotes del más nuevo al más viejo
        lotes = sorted([d for d in carpeta.glob("lote_*") if d.is_dir()], reverse=True)

        for lote in lotes:
            manifiesto_file = lote / "manifiesto.json"
            if not manifiesto_file.exists():
                continue
            try:
                manifiesto = json.loads(manifiesto_file.read_text(encoding="utf-8"))
                if clave_buscada in manifiesto.get("archivos", {}):
                    datos_archivo = manifiesto["archivos"][clave_buscada]
                    if datos_archivo.get("estado") == "modificado":
                        return lote / datos_archivo["backup"]
                    elif datos_archivo.get("estado") == "nuevo":
                        # Si fue nuevo, el archivo no existía antes. Devolvemos None.
                        return None
            except json.JSONDecodeError:
                pass
                
        return None

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