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
pytest -q                            # esperado: 64 passed, 1 xfailed (el xfail marca un hueco menor conocido)

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
| `tests/` | Suite de pruebas unitarias y de integración (67 tests). | 

**Regla de capas:** el núcleo (todo `nina/` salvo `cli.py` y `vigia.py`) no importa `typer`, `rich`, `streamlit` ni `watchdog`; un test lo vigila.

### 1. El Vigía (`nina/mapa.py` + `nina/vigia.py`)

* **tree-sitter** para Java, JS/JSX, TS/TSX y Python; HTML por regex.

* **Firmas completas** (anotaciones, genéricos, arrow functions, interfaces, decoradores).

* **Imports por archivo** (base del ranking de relevancia).

* **Watcher incremental con debounce (0,5 s).**

* Los archivos sin firmas ni imports **no entran al mapa**.

### 2. El Ejecutor Stateless (`nina/contexto.py`, `bloques.py`, `llm.py`, `cli.py`)

Cada ejecución arranca de cero. Utiliza drivers específicos para inyectar configuraciones críticas (ej. `num_ctx` en Ollama) previniendo recortes silenciosos. Modifica múltiples archivos en memoria simultáneamente, con validación de modelo de datos (`Propuesta.es_valida`) y sistema de auto-healing de hasta 3 intentos por fallas de parser.

### 3. La interfaz web (`web.py`)

* Selector de proyecto y archivos.

* Panel "Servidor" con selector de perfiles dinámico (consulta `/v1/models` en vivo) y guardado persistente.

* Diff **lado a lado** sincronizado por lotes.

* Métricas completas, control de API Keys y despliegue de razonamiento.

## ✅ Estado de implementación

**Hecho y funcionando**

* **Fase 0 completada:** Núcleo sólido, transaccional, tolerante a codificaciones, con validación previa de sintaxis y protegido contra path traversal.

* **Fase 4.1 completada:** Edición multi-archivo real. Diffs apilados, auto-inyección de contexto según el prompt, y "Deshacer" en lote.

**No hecho:** Prompt global (sin archivo pivote), fusión de tres vías (merge), y bucle agente (Agent Loop).

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
| 0.1 a 0.8 | Pruebas, persistencia, transacciones, validación de sintaxis, seguridad de rutas y resiliencia. **Estado: Hechas**. | Toda la deuda core |

### Fase 4 — Más potencia

| # | Tarea | Estado |
|---|---|---|
| 4.1 | **Edición multi-archivo y archivos nuevos:** Bloques SEARCH/REPLACE con encabezados de ruta. SEARCH vacío = creación. | ✅ **Hecha** |

**4.2 Prompt Global (Sin archivo pivote)**

* Permitir el uso de la interfaz web sin seleccionar ningún archivo en la barra lateral.
* Modificar el armador de contexto para que, si no hay archivo pivote, se base 100% en el mapa y en los nombres de archivos mencionados en el prompt para crear la estructura base.

**4.3 Fusión de tres vías (cambio en disco durante generación)**

* Merge a tres bandas entre estado inicial, propuesta IA y cambios locales de usuario (en lugar de bloquear el guardado si se detecta Git sucio).

**4.4 Integración con Git**

* Commit automático o `git stash` preventivo antes de escribir un lote.

**4.5 Resolución real de imports y limitación de vecindad**

* Portar resolutor indexado a Python para corregir la colisión de nombres (`index.js`).
* **Optimización de contexto:** Limitar explícitamente el árbol de importaciones inyectadas en el Prompt a dependencias inmediatas (profundidad 1 o 2 máximo).

### Fase 5 — Interfaz Avanzada y Visualización

**5.1 Mejoras de Configuración y Popovers**

* Acceso rápido a modelos vía popover directamente al lado del botón "Generar".

**5.3 Árbol de Proyecto Enriquecido**

* Reemplazar visor estático por árbol colapsable interactivo.
* **Profundidad de código:** Que el árbol renderice no solo archivos, sino que permita desplegar sus *atributos, métodos, y verbos HTTP (APIs)* internos mapeados directamente por Tree-sitter.

**5.4 Pantalla de Revisión Lote (Multi-archivo)**

* Lista lateral de impactos (✏️ modificado, 🆕 nuevo, ⚠️ error) para navegar fácilmente cuando se cambian +10 archivos.

**5.5 Gestión Visual de Recursos (Métricas)**

* Implementar una barra de progreso de tokens en vivo calculada en tiempo real para visualizar la ocupación de la VRAM.

### Fase 6 — Autonomía Delegada (Agent Loop)

* **Bucle de validación de entorno real:** El sistema corre comandos locales (`npm run lint`, `mvn test`, `pytest`) y auto-corrige fallos sin intervención (hasta X reintentos).
* **Ejecución de Comandos LLM:** Permitir que el LLM proponga o ejecute comandos bash/tests.
* **Micro-tareas (Checklists):** Fragmentación de planes grandes en tareas atómicas (`.ai_todo.md`) que resetean contexto para evitar alucinaciones.