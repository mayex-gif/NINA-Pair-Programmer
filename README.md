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
pytest -q                            # esperado: 60 passed, 3 xfailed (los 3 xfail marcan huecos conocidos)

```

**Configuración persistente (`~/.nina/config.json`)**
A partir de la Fase 0.4, NINA gestiona los perfiles de servidor, URLs, modelos, temperatura y API Keys directamente desde la interfaz web, guardándolos en un archivo JSON local. Las variables de entorno (`AI_PERFIL`, `AI_API_URL`, `AI_MODELO`) siguen funcionando como valores por defecto si no hay configuración previa.

**`.gitignore` recomendado:** `.ai_map.json` y `.ai_backups/` (la carpeta de backups ya incluye su propio `.gitignore`).

## 🏗️ Arquitectura actual

| 

| **Archivo** | **Rol** | 
| `generar_mapa.py`, `frontend.py` | Entradas de compatibilidad (3 líneas): delegan en `nina.vigia` y `nina.cli`. Los comandos de siempre siguen funcionando. | 
| `web.py` | Interfaz Streamlit (solo presentación). Conectada al `gestor_config`. | 
| `nina/config.py` | Constantes, variables base y `GestorConfig` para persistencia en `~/.nina/config.json`. | 
| `nina/proyecto.py` | `Proyecto(raiz)`: rutas, mapa, convenciones, backups y Git. Reemplaza al `os.chdir`. | 
| `nina/contexto.py` | Modelos de datos multi-archivo (`Propuesta`, `CambioArchivo`), ranking, armado del prompt, auto-healing. | 
| `nina/bloques.py` | Parser y aplicador de SEARCH/REPLACE (funciones puras). | 
| `nina/llm.py` | Cliente LLM por streaming con patrón de Drivers (`OllamaDriver`, `LlamaCppDriver`) para ajustar payloads. | 
| `nina/trazas.py` | Análisis de stack traces (archivo, línea y otros archivos del proyecto). | 
| `nina/diff.py` | Diff (funciones puras); la web y el CLI solo lo dibujan. | 
| `nina/mapa.py` | Extractores de firmas e imports (tree-sitter + regex) y escritura de `.ai_map.json`. | 
| `nina/vigia.py` | Watcher incremental (watchdog). | 
| `nina/cli.py` | CLI (typer + rich). | 
| `tests/` | Suite de pruebas unitarias y de integración (63 tests). | 

**Regla de capas:** el núcleo (todo `nina/` salvo `cli.py` y `vigia.py`) no importa `typer`, `rich`, `streamlit` ni `watchdog`; un test lo vigila.

### 1. El Vigía (`nina/mapa.py` + `nina/vigia.py`)

* **tree-sitter** para Java, JS/JSX, TS/TSX y Python; HTML por regex.

* **Firmas completas** (anotaciones, genéricos, arrow functions, interfaces, decoradores).

* **Imports por archivo** (base del ranking de relevancia).

* **Watcher incremental con debounce (0,5 s).**

* Los archivos sin firmas ni imports **no entran al mapa**.

### 2. El Ejecutor Stateless (`nina/contexto.py`, `bloques.py`, `llm.py`, `cli.py`)

Cada ejecución arranca de cero. Utiliza drivers específicos para inyectar configuraciones críticas (ej. `num_ctx` en Ollama) previniendo recortes silenciosos. Modifica un archivo a la vez en memoria, con validación de modelo de datos (`Propuesta.es_valida`) y sistema de auto-healing de hasta 3 intentos por fallas de parser.

### 3. La interfaz web (`web.py`)

* Selector de proyecto y archivos.

* Panel "Servidor" con selector de perfiles dinámico (consulta `/v1/models` en vivo) y guardado persistente.

* Diff **lado a lado** sincronizado.

* Métricas completas, control de API Keys y despliegue de razonamiento.

## ✅ Estado de implementación

**Hecho y funcionando**

* **Fase 0.1 a 0.4 completadas:** 63 tests operativos. Núcleo aislado en `nina/` con manejo de estados vía `Proyecto(raiz)` y `GestorConfig`.

* Modelo de datos estructurado en lotes (`Propuesta` y `CambioArchivo`) preparado para multi-archivo.

* Configuración persistente de UI y patrón de Drivers para servidores LLM.

* Backups + `deshacer`, diff visual, aviso de Git sucio y auto-healing tolerante a fallos.

* Soporte de Tree-sitter para Python, TS/JS y Java.

**No hecho:** escritura transaccional atómica de lotes (Fase 0.5), validación de sintaxis estricta post-edición, ejecución real multi-archivo simultánea y bucle agente (Agent Loop).

## 🔎 ¿Puede el sistema trabajar con varios archivos? (estado real)

**En transición.** La capa de datos (`Contexto`, `Propuesta`, `CambioArchivo`) y las capas de presentación (`web.py`, `cli.py`) ya operan procesando listas de cambios. Sin embargo, el **Prompt** (`SISTEMA_BASE`), el **Parser** de bloques y el gestor de **Backups** todavía están limitados artificialmente a resolver y guardar un solo archivo por ciclo, hasta que se complete la Fase 0.5 y 4.1.

## 🧱 Deuda técnica detectada (arreglar antes de crecer)

1. ✅ *(resuelto en 0.2)* **Estado global de directorio.** 
2. ✅ *(resuelto en 0.4)* **Núcleo y CLI mezclados.**
3. ✅ *(resuelto en 0.1)* **Cero tests.**
4. ✅ *(resuelto en 0.5)* **Escritura no atómica.** Archivos temporales y `os.replace` implementados.
5. ✅ *(resuelto en 0.5)* **Backups con resolución de 1 segundo.** Lotes transaccionales con milisegundos y manifiesto JSON.
6. ✅ *(resuelto en 0.6)* **Sin validación posterior al cambio.** `tree-sitter` valida la sintaxis antes de guardar.
7. ✅ *(resuelto en 0.4)* **Configuración de servidor no persistente.**
8. ✅ *(resuelto en 0.4)* **Contexto de Ollama recortado.**
9. **Ranking por nombre de archivo.** `_import_a_stem` colisiona (`index`, `utils`). Requiere el resolutor avanzado de dependencias (Fase 4.5).
10. ✅ *(resuelto en 0.2)* **Constantes duplicadas.** 
11. ✅ *(resuelto en 0.8)* **Mapa desactualizado tras aplicar.** Se actualiza atómicamente el mapa en disco al instante.
12. ✅ *(resuelto en 0.8)* **Lectura estricta en UTF-8.** Fallback automático a `latin-1` implementado.
13. ✅ *(resuelto en 0.8)* **Patrón de trazas incompleto.** Expresión regular ajustada para ignorar timestamps de Vite (`?t=...`).

---

## 🗺 ROADMAP

### Fase 0 — Cimientos (Completada ✅)

| # | Tarea | Resuelve |
|---|---|---|
| 0.1 | **Tests con pytest** de las funciones puras y comandos. **Estado: Hecha**. | #3 |
| 0.2 | **Separar el núcleo:** paquete `nina/`, clase `Proyecto(raiz)`. **Estado: Hecha**. | #1, #2, #10 |
| 0.3 | **Modelo de datos multi-archivo:** `Propuesta` -> lista de `CambioArchivo`. **Estado: Hecha**. | Bloqueo core |
| 0.4 | **Configuración persistente + drivers:** UI conectada a `config.json`, APIs dinámicas. **Estado: Hecha**. | #7, #8 |
| 0.5 | **Escritura atómica** (`os.replace`) y **backups por lote** con manifiesto. **Estado: Hecha**. | #4, #5 |
| 0.6 | **Validación de sintaxis** del texto resultante con tree-sitter (`has_error`). **Estado: Hecha**. | #6 |
| 0.7 | **Seguridad de rutas:** normalización contra Path Traversal y carpetas restringidas. **Estado: Hecha**. | Core security |
| 0.8 | Lectura tolerante de codificación; refresco del mapa; patrón de Vite. **Estado: Hecha**. | #11, #12, #13 |

### Fase 4 — Más potencia

**4.1 Edición multi-archivo y archivos nuevos**

* Bloques SEARCH/REPLACE con encabezados de ruta.

* SEARCH vacío = creación de archivo nuevo.

* Planificador inicial: IA devuelve plan de intención y *contratos* (ej. firmas de funciones) que mantiene la coherencia. Ejecución stateless individual por archivo cuidando la VRAM.

**4.2 Revisión por bloques estilo conflicto de merge**

* Evaluar *hunks* con `difflib`. Interfaz de ✅ Aceptar IA, ⛔ Mantener actual, ➕ Ambos.

**4.3 Fusión de tres vías (cambio en disco durante generación)**

* Merge a tres bandas entre estado inicial, propuesta IA y cambios locales de usuario.

**4.4 Integración con Git**

* Commit automático o `git stash` preventivo antes de escribir un lote.

**4.5 Resolución real de imports y limitación de vecindad**

* Portar resolutor indexado a Python.

* **Optimización de contexto:** Limitar explícitamente el árbol de importaciones inyectadas en el Prompt a dependencias inmediatas (profundidad 1 o 2 máximo respecto a los archivos en modificación).

### Fase 5 — Interfaz Avanzada y Visualización

**5.1 Mejoras de Configuración y Popovers**

* Acceso rápido a modelos vía popover directamente al lado del botón "Generar".

**5.3 Árbol de Proyecto Enriquecido**

* Reemplazar visor estático por árbol colapsable interactivo.

* **Profundidad de código:** Que el árbol renderice no solo archivos, sino que permita desplegar sus *atributos, métodos, y verbos HTTP (APIs)* internos mapeados directamente por Tree-sitter.

**5.4 Pantalla de Revisión Lote (Multi-archivo)**

* Lista lateral de impactos (✏️ modificado, 🆕 nuevo, ⚠️ error).

**5.5 Gestión Visual de Recursos (Métricas)**

* Implementar una barra de progreso de tokens en vivo (ej. `[██████░░░░] 12k / 32k`) calculada en tiempo real para visualizar la ocupación de la VRAM y el límite de contexto. Mejorar la disposición de las métricas.

### Fase 6 — Autonomía Delegada (Agent Loop)

* **Bucle de validación de entorno real:** El sistema corre comandos locales (`npm run lint`, `mvn test`, `pytest`) y auto-corrige fallos sin intervención (hasta X reintentos).

* **Ejecución de Comandos LLM:** Permitir que el LLM proponga o ejecute (previa confirmación) comandos bash/tests de validación para probar funcionalidades directamente desde la terminal integrada.

* **Micro-tareas (Checklists):** Fragmentación de planes grandes en tareas atómicas (`.ai_todo.md`) que resetean contexto para evitar alucinaciones.



ANCLAR AL FINAL EL PENSAMIENTO O LA GENERACION DE CODIGO
QUE NO SE BORREN LOS CAMBIOS A MEDIDA QUE SE AGREGAN MAS DATOS