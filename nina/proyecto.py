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

from .config import NOMBRE_BACKUPS, NOMBRE_CONVENCIONES, NOMBRE_MAPA, EXTENSIONES_EDITABLES, IGNORE_DIRS


# ------------------------------ E/S de archivos (no dependen del proyecto) ----- #
def leer_archivo(ruta: Path):
    """Lee preservando el tipo de salto de línea. Fallback tolerante si no es UTF-8 (Fase 0.8)."""
    try:
        with open(ruta, "r", encoding="utf-8", newline="") as f:
            crudo = f.read()
    except UnicodeDecodeError:
        with open(ruta, "r", encoding="latin-1", newline="") as f:
            crudo = f.read()
            
    eol = "\r\n" if "\r\n" in crudo else "\n"
    return crudo.replace("\r\n", "\n"), eol


def escribir_archivo(ruta: Path, texto: str, eol: str):
    """Escritura atómica usando un archivo temporal y os.replace."""
    directorio = ruta.parent
    directorio.mkdir(parents=True, exist_ok=True)
    
    fd, temp_path = tempfile.mkstemp(dir=directorio, prefix=".nina_tmp_", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(texto.replace("\n", eol))
        os.replace(temp_path, ruta)
    except Exception as e:
        Path(temp_path).unlink(missing_ok=True)
        raise e


def archivo_cambio_en_disco(ruta: Path, original: str) -> bool:
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

    # ------------------------------ Rutas y Seguridad (Fase 0.7) ------------- #
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
        """Ruta absoluta sin validación de seguridad (solo resolución de paths)."""
        p = Path(ruta).expanduser()
        return p if p.is_absolute() else self.raiz / p

    def clave(self, ruta) -> str:
        p = self.abs(ruta)
        try:
            return p.resolve().relative_to(self.raiz).as_posix()
        except ValueError:
            return p.as_posix()

    def validar_ruta_segura(self, ruta: Union[str, Path]) -> Path:
        """
        Garantiza que la ruta generada por la IA no escape del proyecto ni toque archivos sensibles.
        """
        p = self.abs(ruta).resolve()
        
        try:
            relativa = p.relative_to(self.raiz)
        except ValueError:
            raise ValueError(f"Ruta prohibida (intento de escape del directorio raíz): {ruta}")
            
        partes = relativa.parts
        for ignorada in IGNORE_DIRS:
            if ignorada in partes:
                raise ValueError(f"Ruta prohibida (intento de escritura en carpeta restringida '{ignorada}'): {ruta}")
                
        if p.suffix not in EXTENSIONES_EDITABLES:
            raise ValueError(f"Extensión no permitida para edición ({p.suffix}). Permitidas: {', '.join(EXTENSIONES_EDITABLES)}")
            
        return p

    # ------------------------------ Git -------------------------------------- #
    def git_archivo_sucio(self, ruta) -> bool:
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
        if isinstance(rutas, (str, Path)):
            rutas = [rutas]

        carpeta = self.carpeta_backups
        carpeta.mkdir(exist_ok=True)
        gitignore = carpeta / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n", encoding="utf-8")

        sello = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:19]
        lote_dir = carpeta / f"lote_{sello}"
        lote_dir.mkdir(exist_ok=True)

        manifiesto = {"id": sello, "archivos": {}}

        for r in rutas:
            ruta_abs = self.abs(r)
            clave = self.clave(r)
            if ruta_abs.exists():
                nombre_bak = clave.replace("/", "__") + ".bak"
                destino = lote_dir / nombre_bak
                shutil.copy2(ruta_abs, destino)
                manifiesto["archivos"][clave] = {"estado": "modificado", "backup": nombre_bak}
            else:
                manifiesto["archivos"][clave] = {"estado": "nuevo"}

        (lote_dir / "manifiesto.json").write_text(json.dumps(manifiesto, indent=2), encoding="utf-8")
        return lote_dir

    def ultimo_backup(self, ruta) -> Optional[Path]:
        carpeta = self.carpeta_backups
        if not carpeta.is_dir():
            return None
            
        clave_buscada = self.clave(ruta)
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

    def actualizar_archivo_en_mapa(self, ruta: Union[str, Path]):
        """Extrae firmas e imports del archivo y actualiza el JSON del mapa atómicamente (Fase 0.8)."""
        from .mapa import extraer_archivo, guardar_mapa
        
        mapa_actual = self.cargar_mapa()
        ruta_abs = self.abs(ruta)
        clave = self.clave(ruta)
        
        datos = extraer_archivo(str(ruta_abs))
        if datos:
            mapa_actual[clave] = datos
        elif clave in mapa_actual:
            del mapa_actual[clave]
            
        guardar_mapa(mapa_actual, self.raiz)