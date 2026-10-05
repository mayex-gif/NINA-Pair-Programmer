# NINA — Documentación y Roadmap v2.2 🚀

**Filosofía:** herramienta de *coworking* (pair programming) 100 % local y stateless. Maximiza la velocidad de inferencia y protege la caché en hardware con poca VRAM (RTX 4060 de 8 GB), dándole a la IA el contexto justo del proyecto sin arrastrar historiales de chat.

**Principio rector:** antes de agregar funciones, volver *seguro y confiable* lo que ya existe. Nada se escribe en disco sin que veas el diff y sin que haya un backup.

> Este documento está alineado con el código actual (`frontend.py`, `generar_mapa.py`, `web.py`, `.streamlit/config.toml`, `RepomapGraph.jsx`). Donde algo está planeado y no existe todavía, se marca como tal.

## 📦 Instalación

```
pip install -r requirements.txt
```

> `pytest` conviene moverlo a un `requirements-dev.txt`. `tree-sitter-html` está en la lista pero todavía sin uso: `generar_mapa.py` procesa `.html` por regex (se puede conectar luego en `_cargar_parsers`). `streamlit-tree-select` es para el árbol de la Fase 5.3.

Si falta `tree-sitter` (o el paquete de un lenguaje), `generar_mapa.py` cae a regex **solo para ese lenguaje** y lo informa al iniciar. `.html` siempre va por regex.

**Ejecución**

```
python generar_mapa.py              # escanea y queda vigilando (--una-vez para escanear y salir)
streamlit run web.py                # interfaz web (copiá .streamlit/config.toml junto a web.py)
python frontend.py refactor "ruta/archivo.jsx" "Tu instrucción"
python frontend.py fix "stack trace"   # o sin argumento (stdin) o -p (portapapeles)
python frontend.py deshacer "ruta/archivo.jsx"
python frontend.py probar              # prueba conexión y muestra cómo llega el razonamiento
python -m nina refactor ...         # equivalente a frontend.py (el paquete nina/ es el núcleo)
# todos los comandos del CLI aceptan --proyecto/-C <raíz>; las rutas relativas se leen desde esa raíz
pytest -q                            # esperado: 67 passed, 1 xfailed
```

**Configuración persistente (`~/.nina/config.json`)**
A partir de la Fase 0.4, NINA gestiona los perfiles de servidor, URLs, modelos, temperatura y API Keys directamente desde la interfaz web, guardándolos en un archivo JSON local. Las variables de entorno (`AI_PERFIL`, `AI_API_URL`, `AI_MODELO`) siguen funcionando como valores por defecto si no hay configuración previa.

**`.gitignore` recomendado:** `.ai_map.json` y `.ai_backups/` (la carpeta de backups ya incluye su propio `.gitignore`).

## 🏗️ Arquitectura actual

| **Archivo** | **Rol** | 
|---|---|
| `generar_mapa.py`, `frontend.py` | Entradas de compatibilidad (3 líneas): delegan en `nina.vigia` y `nina.cli`. Los comandos de siempre siguen funcionando. | 
| `web.py` | Interfaz Streamlit (solo presentación). Conectada al `gestor_config` y motor multi-archivo. | 
| `nina/config.py` | Constantes, configuración base, Prompt Global y `GestorConfig` persistente. | 
| `nina/proyecto.py` | `Proyecto(raiz)`: rutas, mapa, convenciones, backups por lote transaccional y Git. | 
| `nina/contexto.py` | Modelos (`Propuesta`, `CambioArchivo`), inyección dinámica de dependencias, auto-healing. | 
| `nina/bloques.py` | Parser tolerante multi-archivo y aplicador de SEARCH/REPLACE. | 
| `nina/llm.py` | Cliente LLM por streaming con patrón de Drivers (`Ollama`, `LlamaCpp`). | 
| `nina/trazas.py` | Análisis de stack traces (archivo, línea y otros archivos del proyecto). | 
| `nina/diff.py` | Diff puramente funcional. | 
| `nina/mapa.py` | Extractores de firmas e imports (tree-sitter + regex). | 
| `nina/vigia.py` | Watcher incremental (watchdog). | 
| `nina/cli.py` | CLI (typer + rich). | 
| `tests/` | Suite de pruebas unitarias y de integración (68 tests). | 

**Regla de capas:** el núcleo (todo `nina/` salvo `cli.py` y `vigia.py`) no importa `typer`, `rich`, `streamlit` ni `watchdog`; un test lo vigila.

### 1. El Vigía (`nina/mapa.py` + `nina/vigia.py`)

* **tree-sitter** para Java, JS/JSX, TS/TSX y Python; HTML por regex.
* **Firmas completas** y **Imports por archivo** (base del ranking).
* **Watcher incremental con debounce (0,5 s).**

### 2. El Ejecutor Stateless (`nina/contexto.py`, `bloques.py`, `llm.py`, `cli.py`)

Cada ejecución arranca de cero. Utiliza drivers específicos para inyectar configuraciones críticas. Modifica múltiples archivos a la vez en memoria, con validación de modelo de datos (`Propuesta.es_valida`) y auto-healing (3 intentos) por archivo. Si la IA necesita leer un archivo que no estaba seleccionado, lo inyecta al vuelo leyendo el disco.

### 3. La interfaz web (`web.py`)

* Selector de proyecto y archivos (con Modo Global).
* Diff **lado a lado** sincronizado (apilable por lote de archivos).
* Métricas completas, control de API Keys y despliegue de razonamiento.
* Restauración transaccional (Deshacer Lote).

## ✅ Estado de implementación

**Hecho y funcionando**

* **Fase 0 (Cimientos completados):** Transacciones atómicas, validación de sintaxis (tree-sitter), resiliencia a codificaciones y aislamientos del cwd. 68 tests operativos.
* **Fase 4.1 (Edición multi-archivo y nuevos):** El parser agrupa cambios por rutas. Soporta creación de carpetas automáticas y escritura por lotes.
* **Fase 4.2 (Prompt Global):** Interfaz liberada del archivo pivote. El motor infiere qué archivos querés tocar leyendo tu prompt e inyecta su código en el contexto.

## 🧱 Deuda técnica detectada (arreglar antes de crecer)

1. **Ranking por nombre de archivo.** `_import_a_stem` colisiona (`index`, `utils`). Requiere el resolutor avanzado de dependencias (Fase 4.5).
2. **Backups en Modo Global invisibles.** El "Deshacer Lote" funciona en el backend, pero la barra lateral de UI exige tener un archivo seleccionado para mostrar el botón de restaurar.

---

## 🗺 ROADMAP

### Fase 4 — Más potencia

**4.3 Fusión de tres vías (cambio en disco durante generación)**
* Merge inteligente a tres bandas. Si el usuario edita el archivo mientras la IA piensa (pero no toca las mismas líneas que la IA quiere modificar), el motor aplica el SEARCH/REPLACE sobre la versión fresca del disco y permite el guardado sin bloquear.

**4.4 Integración con Git**
* Commit automático o `git stash` preventivo antes de escribir un lote.

**4.5 Resolución real de imports y limitación de vecindad**
* Portar resolutor indexado a Python para corregir colisiones.
* Limitar explícitamente el árbol de importaciones inyectadas a dependencias inmediatas (profundidad 1 o 2).

### Fase 5 — Interfaz Avanzada y Visualización

**5.1 UX de Generación (Scroll y Persistencia)**
* Anclar el scroll de la interfaz al final de la vista de "pensamiento" para evitar persecuciones con el mouse.
* Prevenir el borrado visual de la pantalla (glitch de Streamlit) durante la recepción de fragmentos largos.

**5.2 Cálculo y Barra de Tokens en Vivo**
* Implementar un contador reactivo en el `textarea` del prompt que muestre el peso combinado del texto + el árbol de contexto seleccionado.

**5.3 Árbol de Proyecto Enriquecido**
* Reemplazar visor estático por árbol colapsable interactivo.
* Que el árbol permita desplegar atributos, métodos y verbos HTTP (APIs) internos mapeados por Tree-sitter.

**5.4 Pantalla de Revisión Lote (Multi-archivo)**
* Lista lateral de impactos (✏️ modificado, 🆕 nuevo, ⚠️ error).

### Fase 6 — Autonomía Delegada (Agent Loop)

* **Bucle de validación de entorno real:** El sistema corre comandos locales (`npm run lint`, `pytest`) y auto-corrige fallos sin intervención.
* **Micro-tareas (Checklists):** Fragmentación de planes grandes en tareas atómicas (`.ai_todo.md`) que resetean contexto para evitar alucinaciones.