"""
Configuración y constantes compartidas (única fuente de verdad para el Vigía, el CLI y la web).
No importa nada de UI ni de red.
"""
import os
import json
from pathlib import Path

# --- Archivos del proyecto -------------------------------------------------- #
EXTENSIONES_MAPA = ('.java', '.ts', '.tsx', '.js', '.jsx', '.py', '.html')
EXTENSIONES_EDITABLES = EXTENSIONES_MAPA + ('.css',)
IGNORE_DIRS = {
    '.git', 'node_modules', 'target', '.next', 'dist', 'build', 'out', 'coverage',
    '.idea', '.vscode', '__pycache__', '.ai_backups',
    'venv', '.venv', 'site-packages', '.pytest_cache', '.mypy_cache', '.tox',
}
NOMBRE_MAPA = ".ai_map.json"
NOMBRE_CONVENCIONES = "CONVENTIONS.md"
NOMBRE_BACKUPS = ".ai_backups"
VERSION_MAPA = 2
MAX_LARGO_FIRMA = 220

# --- Contexto y Sistema ----------------------------------------------------- #
PRESUPUESTO_MAPA_CHARS = 6000
MAX_ARCHIVOS_MAPA = 30
MAX_INTENTOS = int(os.getenv("AI_MAX_INTENTOS", "3"))

SISTEMA_BASE = """Sos un asistente de Pair Programming experto. Podés modificar múltiples archivos o crear archivos nuevos.

FORMATO DE RESPUESTA (obligatorio): devolvé ÚNICAMENTE bloques SEARCH/REPLACE, sin explicaciones ni saludos.
PROHIBIDO usar texto conversacional, saludos, explicaciones o pedir disculpas.
PROHIBIDO usar etiquetas como <archivo> ó </archivo>.

EJEMPLO DE RESPUESTA CORRECTA:
src/app.js
<<<<<<< SEARCH
(líneas EXACTAS del archivo actual que querés cambiar)
=======
(líneas nuevas que las reemplazan)
>>>>>>> REPLACE

Reglas:
- Escribí SIEMPRE la ruta del archivo justo arriba del bloque <<<<<<< SEARCH.
- SEARCH debe copiar el código actual tal cual (espacios e indentación incluidos) y aparecer UNA sola vez en el archivo. Si hace falta, sumá 1-3 líneas de contexto para que sea único.
- Para insertar código: usá en SEARCH una línea vecina existente y repetila en REPLACE junto con el código nuevo.
- Para borrar código: dejá REPLACE vacío.
- Para CREAR un archivo nuevo o reemplazar uno entero: dejá SEARCH vacío.
- Podés usar varios bloques, en el orden en que aparecen en el archivo.
- No reescribas el archivo completo ni toques nada que no se haya pedido.
- No uses bloques de Markdown (```)."""

# --- Configuración Persistente (Fase 0.4) ----------------------------------- #
CONFIG_DIR = Path.home() / ".nina"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "perfil_activo": "Ollama Local",
    "perfiles": {
        "Ollama Local": {
            "tipo": "ollama",
            "url": "http://localhost:11434/v1/chat/completions",
            "modelo": "qwen2.5-coder:7b",
            "max_tokens_prompt": 12000,
            "temperatura": 0.1
        },
        "Llama Server": {
            "tipo": "llama.cpp",
            "url": "http://localhost:8081/v1/chat/completions",
            "modelo": "Qwen3.6-35B-A3B-UD-Q4_K_XL",
            "max_tokens_prompt": 12000,
            "temperatura": 0.1
        }
    }
}

class GestorConfig:
    def __init__(self):
        self.config = self.cargar()

    def cargar(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return DEFAULT_CONFIG.copy()

    def guardar(self):
        CONFIG_DIR.mkdir(exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4)
    
    @property
    def perfil_actual(self):
        nombre = self.config.get("perfil_activo", "Ollama Local")
        return self.config.get("perfiles", {}).get(nombre, DEFAULT_CONFIG["perfiles"]["Ollama Local"])

gestor_config = GestorConfig()

# Variables de compatibilidad temporal para no romper cli.py antes de tiempo
API_URL = gestor_config.perfil_actual["url"]
MODELO = gestor_config.perfil_actual["modelo"]
PERFIL = gestor_config.config.get("perfil_activo", "Ollama Local")
MAX_TOKENS_PROMPT = gestor_config.perfil_actual.get("max_tokens_prompt", 12000)