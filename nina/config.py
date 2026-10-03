"""
Configuración y constantes compartidas (única fuente de verdad para el Vigía, el CLI y la web).
No importa nada de UI ni de red.
"""
import os

# --- Archivos del proyecto -------------------------------------------------- #
EXTENSIONES_MAPA = ('.java', '.ts', '.tsx', '.js', '.jsx', '.py', '.html')  # las que el Vigía indexa
EXTENSIONES_EDITABLES = EXTENSIONES_MAPA + ('.css',)                         # las que se pueden elegir en la web
IGNORE_DIRS = {
    '.git', 'node_modules', 'target', '.next', 'dist', 'build', 'out', 'coverage',
    '.idea', '.vscode', '__pycache__', '.ai_backups',
    'venv', '.venv', 'site-packages', '.pytest_cache', '.mypy_cache', '.tox',
}
NOMBRE_MAPA = ".ai_map.json"
NOMBRE_CONVENCIONES = "CONVENTIONS.md"
NOMBRE_BACKUPS = ".ai_backups"
VERSION_MAPA = 2
MAX_LARGO_FIRMA = 220  # recorta firmas kilométricas (ej. anotaciones largas)

# --- Servidor del modelo ---------------------------------------------------- #
# Perfiles: (URL, modelo). Puertos típicos: LM Studio = 1234 | llama.cpp = 8080/8081 | Koboldcpp = 5001 | Ollama = 11434
# Elegí uno con AI_PERFIL, o pisalos con AI_API_URL / AI_MODELO.  (Fase 0.4: pasarán a una configuración persistente.)
PERFILES = {
    "ollama": ("http://localhost:11434/v1/chat/completions", "qwen3.6-coder:latest"),
    "ollama-7b": ("http://localhost:11434/v1/chat/completions", "qwen2.5-coder:7b"),
    "llama-server": ("http://localhost:8081/v1/chat/completions", "Qwen3.6-35B-A3B-UD-Q4_K_XL"),
}
PERFIL = os.getenv("AI_PERFIL", "ollama")
if PERFIL not in PERFILES:
    PERFIL = "ollama"
API_URL = os.getenv("AI_API_URL", PERFILES[PERFIL][0])
MODELO = os.getenv("AI_MODELO", PERFILES[PERFIL][1])
MAX_TOKENS_PROMPT = int(os.getenv("AI_MAX_TOKENS_PROMPT", "12000"))  # aviso si se supera
MAX_INTENTOS = int(os.getenv("AI_MAX_INTENTOS", "3"))  # auto-healing: intentos totales por pedido

# --- Contexto --------------------------------------------------------------- #
PRESUPUESTO_MAPA_CHARS = 6000  # tope de texto del mapa que se envía
MAX_ARCHIVOS_MAPA = 30

SISTEMA_BASE = """Sos un asistente de Pair Programming experto. Modificás UN archivo por vez.

FORMATO DE RESPUESTA (obligatorio): devolvé ÚNICAMENTE bloques SEARCH/REPLACE, sin explicaciones ni saludos:
PROHIBIDO usar texto conversacional, saludos, explicaciones o pedir disculpas.
PROHIBIDO usar etiquetas como <archivo> ó </archivo>.

EJEMPLO DE RESPUESTA CORRECTA:
<<<<<<< SEARCH
(líneas EXACTAS del archivo actual que querés cambiar)
=======
(líneas nuevas que las reemplazan)
>>>>>>> REPLACE

Reglas:
- SEARCH debe copiar el código actual tal cual (espacios e indentación incluidos) y aparecer UNA sola vez en el archivo. Si hace falta, sumá 1-3 líneas de contexto para que sea único.
- Para insertar código: usá en SEARCH una línea vecina existente y repetila en REPLACE junto con el código nuevo.
- Para borrar código: dejá REPLACE vacío.
- Podés usar varios bloques, en el orden en que aparecen en el archivo.
- No reescribas el archivo completo ni toques nada que no se haya pedido.
- No uses bloques de Markdown (```)."""
