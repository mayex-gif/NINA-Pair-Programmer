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
# Lo que el AGENTE puede crear además del código: archivos de configuración y documentación de un proyecto.
# Nunca scripts ejecutables (.sh, .bat, .ps1, .exe) ni secretos (.env).
EXTENSIONES_AGENTE = ('.json', '.md', '.txt', '.toml', '.cfg', '.ini', '.yml', '.yaml')
NOMBRES_AGENTE = ('Dockerfile', '.gitignore')
IGNORE_DIRS = {
    '.git', '.nina', 'node_modules', 'target', '.next', 'dist', 'build', 'out', 'coverage',
    '.idea', '.vscode', '__pycache__', '.ai_backups',
    'venv', '.venv', 'site-packages', '.pytest_cache', '.mypy_cache', '.tox',
}
NOMBRE_MAPA = ".ai_map.json"   # (el agente tampoco puede escribirlo)
NOMBRE_CONVENCIONES = "CONVENTIONS.md"
NOMBRE_BACKUPS = ".ai_backups"
VERSION_MAPA = 3
MAX_LARGO_FIRMA = 220

# --- Contexto y Sistema ----------------------------------------------------- #
PRESUPUESTO_MAPA_CHARS = 8000       # texto del mapa con detalle (firmas, atributos, endpoints...)
PRESUPUESTO_ARBOL_CHARS = 3000      # lista de rutas del resto del árbol (solo archivo nuevo / Modo Global)
MAX_ARCHIVOS_MAPA = 30
MAX_INTENTOS = int(os.getenv("AI_MAX_INTENTOS", "5"))

SISTEMA_BASE = """Sos un asistente de Pair Programming experto. Podés modificar múltiples archivos o crear archivos nuevos.

FORMATO DE RESPUESTA (obligatorio): devolvé ÚNICAMENTE bloques SEARCH/REPLACE, sin explicaciones ni saludos.
PROHIBIDO usar texto conversacional, saludos, explicaciones o pedir disculpas.
PROHIBIDO usar etiquetas como <archivo> ó </archivo>.

EJEMPLO DE RESPUESTA CORRECTA:
ruta/exacta/del/archivo.ext
<<<<<<< SEARCH
(líneas EXACTAS del archivo actual que querés cambiar)
=======
(líneas nuevas que las reemplazan)
>>>>>>> REPLACE

Reglas:
- Escribí SIEMPRE la ruta exacta del archivo justo arriba del bloque <<<<<<< SEARCH, respetando el árbol del proyecto.
- SEARCH debe copiar el código actual tal cual (espacios e indentación incluidos) y aparecer UNA sola vez en el archivo. Si hace falta, sumá 1-3 líneas de contexto para que sea único.
- Para insertar código: usá en SEARCH una línea vecina existente y repetila en REPLACE junto con el código nuevo.
- Para borrar código: dejá REPLACE vacío.
- Para CREAR un archivo nuevo o reemplazar uno entero: dejá SEARCH vacío.
- Podés usar varios bloques, en el orden en que aparecen en el archivo.
- No reescribas el archivo completo ni toques nada que no se haya pedido.
- No uses bloques de Markdown (```).

REGLAS ESTRICTAS DE RAZONAMIENTO Y EJECUCIÓN:
- Eres un ejecutor técnico decisivo. Prohibido dudar de tus propias soluciones.
- Prohibido usar frases de auto-cuestionamiento en tu pensamiento (ej. "Wait...", "Let me think...", "Maybe the user means...").
- Analiza el problema UNA SOLA VEZ, traza el plan de forma lineal y escribe el bloque SEARCH/REPLACE inmediatamente.
- Si una instrucción es ambigua, asume la interpretación más lógica y técnica aplicable al código, no debatas contigo mismo sobre las posibilidades.

Finalizar las respuestas sin repeticion de pensamiento.
"""

# --- Configuración Persistente (Fase 0.4) ----------------------------------- #
# FUENTE ÚNICA DE VERDAD: todo lo que antes se repetía entre el .bat y NINA vive acá, por perfil:
#   · "muestreo"  → lo que NINA manda en CADA pedido (temperatura, top_p...). Pisa los valores por defecto del servidor.
#   · "limites"   → topes de seguridad de NINA (tokens de salida/razonamiento, detección de bucles, reintentos).
#   · "servidor"  → cómo se lanza llama-server (contexto, capas en GPU...). `python frontend.py servidor --bat X.bat`
#                   genera el .bat desde estos valores, así no hay que mantenerlo a mano. El puerto sale de la `url`.
# Lo que guardes en ~/.nina/config.json (o edites desde la web) pisa estos valores por defecto, clave por clave.
import copy

CONFIG_DIR = Path.home() / ".nina"
CONFIG_FILE = CONFIG_DIR / "config.json"

MUESTREO_DEFECTO = {
    "temperature": 0.3,       # con modelos de razonamiento evitá valores muy bajos (0.0-0.1): favorecen los bucles
    "top_p": 0.95,
    "top_k": 20,
    "min_p": 0.0,
    "repeat_penalty": 1.0,    # >1.0 castiga repetir tokens del contexto, y el código es repetitivo: puede comerse espacios
                              # ("returntotal(x)") o desviar un SEARCH. Para bucles ya están las guardas de `limites`.
    "presence_penalty": 0.0,
}

LIMITES_DEFECTO = {
    "max_tokens_salida": 32768,                 # tope duro por intento (se manda como max_tokens); 0 = sin tope
    "max_tokens_pensamiento": 12000,            # si el razonamiento pasa de esto (estimado), se corta el intento; 0 = sin tope
    "detectar_bucles": True,                    # corta el intento si el razonamiento repite las mismas líneas
    "reenviar_respuesta_hasta_tokens": 64000,   # en un reintento, la respuesta rechazada solo se reenvía si pesa menos que esto
    "profundidad_mapa": 2,                      # saltos de import (en ambos sentidos) alrededor del archivo pedido que entran al mapa; 0 = todo
}

SERVIDOR_DEFECTO = {
    "exe": r"C:\llama-server\llama-server.exe",
    "modelo_gguf": r"C:\LLMs\Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf",
    "contexto": 65536,            # -c   (también lo usa NINA para saber si un reintento entra en el contexto)
    "paralelo": 1,                # -np
    "flash_attn": True,           # -fa on
    "cache_k": "q8_0",            # -ctk
    "cache_v": "q8_0",            # -ctv
    "jinja": True,                # --jinja
    "gpu_layers": 999,            # --n-gpu-layers
    "n_cpu_moe": 34,              # --n-cpu-moe (solo MoE; None para no usarlo). Bajarlo de a 2 hasta ~7 GB de VRAM
    "load_mode": "none",          # --load-mode (None para no usarlo)
    "reasoning_preserve": False,  # False → --no-reasoning-preserve
    "extra": [],                  # flags adicionales tal cual, p. ej. ["--cache-reuse", "256"]
}

DEFAULT_CONFIG = {
    "perfil_activo": "Ollama Local",
    "perfiles": {
        "Ollama Local": {
            "tipo": "ollama",
            "url": "http://localhost:11434/v1/chat/completions",
            "modelo": "qwen2.5-coder:7b",
            "max_tokens_prompt": 12000,
            "muestreo": {**MUESTREO_DEFECTO, "temperature": 0.1},
            "limites": dict(LIMITES_DEFECTO),
        },
        "Llama Server": {
            "tipo": "llama.cpp",
            "url": "http://localhost:8081/v1/chat/completions",
            "modelo": "Qwen3.6-35B-A3B-UD-Q4_K_XL",
            "max_tokens_prompt": 12000,
            "muestreo": dict(MUESTREO_DEFECTO),
            "limites": dict(LIMITES_DEFECTO),
            "servidor": dict(SERVIDOR_DEFECTO),
        }
    }
}


def _completar(perfil: dict, base: dict) -> dict:
    """Devuelve `perfil` con las claves que le falten tomadas de `base` (lo guardado manda; se completa en profundidad)."""
    salida = copy.deepcopy(perfil)
    for clave, valor in base.items():
        if clave not in salida:
            salida[clave] = copy.deepcopy(valor)
        elif isinstance(valor, dict) and isinstance(salida[clave], dict):
            salida[clave] = _completar(salida[clave], valor)
    return salida


def completar_config(config: dict) -> dict:
    """Completa una config guardada (posiblemente de una versión vieja) con los valores por defecto."""
    config = copy.deepcopy(config)
    defecto = DEFAULT_CONFIG["perfiles"]
    generico = {"muestreo": MUESTREO_DEFECTO, "limites": LIMITES_DEFECTO}
    perfiles = config.setdefault("perfiles", {})
    for nombre, datos in list(perfiles.items()):
        perfiles[nombre] = _completar(datos, defecto.get(nombre, generico))
    for nombre, datos in defecto.items():
        perfiles.setdefault(nombre, copy.deepcopy(datos))
    config.setdefault("perfil_activo", DEFAULT_CONFIG["perfil_activo"])
    # la clave vieja `temperatura` quedó reemplazada por muestreo.temperature: se ignora a propósito
    for datos in perfiles.values():
        datos.pop("temperatura", None)
    return config


class GestorConfig:
    def __init__(self):
        self.config = self.cargar()

    def cargar(self):
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    return completar_config(json.load(f))
            except Exception:
                pass
        return completar_config(DEFAULT_CONFIG)

    def guardar(self):
        CONFIG_DIR.mkdir(exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4)

    @property
    def perfil_actual(self):
        nombre = self.config.get("perfil_activo", "Ollama Local")
        perfiles = self.config.get("perfiles", {})
        return perfiles.get(nombre) or perfiles.get("Ollama Local") or copy.deepcopy(DEFAULT_CONFIG["perfiles"]["Ollama Local"])


gestor_config = GestorConfig()

# Variables de compatibilidad temporal para no romper cli.py antes de tiempo
API_URL = gestor_config.perfil_actual["url"]
MODELO = gestor_config.perfil_actual["modelo"]
PERFIL = gestor_config.config.get("perfil_activo", "Ollama Local")
MAX_TOKENS_PROMPT = gestor_config.perfil_actual.get("max_tokens_prompt", 12000)
