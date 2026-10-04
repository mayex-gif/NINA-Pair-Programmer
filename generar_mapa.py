"""Compatibilidad: el Vigía vive ahora en nina/vigia.py. `python generar_mapa.py ...` sigue funcionando."""
from nina.vigia import main

if __name__ == "__main__":
    main()
