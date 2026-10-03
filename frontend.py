"""Compatibilidad: el CLI vive ahora en nina/cli.py. `python frontend.py ...` sigue funcionando."""
from nina.cli import app

if __name__ == "__main__":
    app()
