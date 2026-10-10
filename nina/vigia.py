"""
El Vigía: escanea el proyecto, escribe `.ai_map.json` y queda vigilando cambios (actualiza SOLO el archivo modificado).

Uso:
    python generar_mapa.py            # escanea y queda vigilando (Ctrl+C para salir)
    python generar_mapa.py --una-vez  # escanea una sola vez y termina
    python generar_mapa.py ruta/proyecto
"""
import argparse
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .config import EXTENSIONES_MAPA as EXTENSIONES
from .config import NOMBRE_MAPA as ARCHIVO_MAPA
from .mapa import PARSERS, clave, es_relevante, escanear_todo, extraer_archivo, guardar_mapa, vincular_imports


class MapaHandler(FileSystemEventHandler):
    def __init__(self, archivos: dict, raiz: Path):
        self.archivos = archivos
        self.raiz = raiz
        self.pendientes: set[Path] = set()
        self.lock = threading.Lock()
        self.timer = None

    def _encolar(self, ruta_str):
        ruta = Path(ruta_str)
        if not es_relevante(ruta, self.raiz):
            return
        with self.lock:
            self.pendientes.add(ruta)
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(0.5, self._procesar)  # agrupa ráfagas de guardado
            self.timer.start()

    def _procesar(self):
        with self.lock:
            lote, self.pendientes = self.pendientes, set()
        for ruta in lote:
            k = clave(ruta, self.raiz)
            if ruta.exists():
                datos = extraer_archivo(str(ruta))
                if datos:
                    self.archivos[k] = datos
                    print(f"[Watch] ✏️  {k}")
                else:
                    self.archivos.pop(k, None)
            elif self.archivos.pop(k, None) is not None:
                print(f"[Watch] 🗑️  {k}")
                
        # FASE 4.5: Recalculamos los links rápidamente por si cambió un import
        vincular_imports(self.archivos)
        
        guardar_mapa(self.archivos, self.raiz)
        print(f"        Mapa actualizado ({len(self.archivos)} archivos).")

    def on_modified(self, e):
        if not e.is_directory:
            self._encolar(e.src_path)

    def on_created(self, e):
        if not e.is_directory:
            self._encolar(e.src_path)

    def on_deleted(self, e):
        if not e.is_directory:
            self._encolar(e.src_path)

    def on_moved(self, e):
        if not e.is_directory:
            self._encolar(e.src_path)
            self._encolar(e.dest_path)


def main():
    ap = argparse.ArgumentParser(description="Generador y vigía del mapa del proyecto")
    ap.add_argument('raiz', nargs='?', default='.')
    ap.add_argument('--una-vez', action='store_true', help="Escanear una vez y salir")
    args = ap.parse_args()

    raiz = Path(args.raiz).resolve()
    activos = sorted(e.lstrip('.') for e in PARSERS)
    faltan = sorted(e.lstrip('.') for e in EXTENSIONES if e not in PARSERS)
    modo = f"tree-sitter [{', '.join(activos) or 'ninguno'}] + regex [{', '.join(faltan) or 'ninguno'}]"
    print(f"Escaneando {raiz} con {modo}...")
    archivos = escanear_todo(raiz)
    guardar_mapa(archivos, raiz)
    print(f"✅ {len(archivos)} archivos indexados en '{ARCHIVO_MAPA}'.")

    if args.una_vez:
        return

    observer = Observer()
    observer.schedule(MapaHandler(archivos, raiz), path=str(raiz), recursive=True)
    observer.start()
    print("👀 Watcher activo (Ctrl+C para salir)...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\nDeteniendo watcher.")
    observer.join()


# Mantenemos un registro de los observers activos para no duplicar si se llama varias veces
_observers_activos = {}

def iniciar_vigia_background(ruta_raiz: str):
    """Lanza el watcher en un hilo secundario invisible (ideal para Streamlit)."""
    raiz = Path(ruta_raiz).resolve()
    
    # Si ya hay un vigía cuidando esta carpeta, no hacemos nada
    if raiz in _observers_activos:
        return _observers_activos[raiz]
        
    print(f"🚀 Iniciando Vigía en segundo plano para: {raiz}")
    archivos = escanear_todo(raiz)
    guardar_mapa(archivos, raiz)
    
    observer = Observer()
    observer.schedule(MapaHandler(archivos, raiz), path=str(raiz), recursive=True)
    # Hacemos que el hilo sea "daemon" para que muera automáticamente si cerrás Streamlit
    observer.daemon = True 
    observer.start()
    
    _observers_activos[raiz] = observer
    return observer


if __name__ == "__main__":
    main()
