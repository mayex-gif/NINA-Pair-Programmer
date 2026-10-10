# NINA — Documentación y Roadmap v2.3 🚀

**Filosofía:** herramienta de *coworking* (pair programming) 100 % local y stateless. Maximiza la velocidad de inferencia y protege la caché en hardware con poca VRAM (RTX 4060 de 8 GB), dándole a la IA el contexto justo del proyecto sin arrastrar historiales de chat.

**Principio rector:** antes de agregar funciones, volver *seguro y confiable* lo que ya existe. Nada se escribe en disco sin que veas el diff y sin que haya un backup.

> Este documento está alineado con el código actual (`nina/`, `web.py`, `tests/`, `.streamlit/config.toml`, `RepomapGraph.jsx`). Donde algo está planeado y no existe todavía, se marca como tal.

## 📦 Instalación

```
pip install -r requirements.txt
```

> `pytest` conviene moverlo a un `requirements-dev.txt`. `tree-sitter-html` está en la lista pero todavía sin uso: `generar_mapa.py` procesa `.html` por regex (se puede conectar luego en `_cargar_parsers`). `streamlit-tree-select` es para el árbol de la Fase 5.3.
>
> **Streamlit ≥ 1.52** para `web.py`: el autoscroll, los cronómetros y el contador de tokens se inyectan con `st.html(..., unsafe_allow_javascript=True)`, que apareció en esa versión (verificado: 1.51 no lo tiene; la interfaz está probada con 1.65). Con una versión anterior el JS cae a un iframe de altura 0, pero eso no está probado.

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
pytest -q                            # esperado: 72 passed, 1 xfailed
```

**Configuración persistente (`~/.nina/config.json`)**
NINA gestiona los perfiles de servidor, URLs, modelos, temperatura y API Keys desde la interfaz web y los guarda en un JSON local. Las variables de entorno (`AI_PERFIL`, `AI_API_URL`, `AI_MODELO`) siguen funcionando como valores por defecto si no hay configuración previa. ⚠️ Las API Keys se guardan en texto plano (ver deuda técnica).

**`.gitignore` recomendado:** `.ai_map.json` y `.ai_backups/` (la carpeta de backups ya incluye su propio `.gitignore`).

### 🖥️ Servidor de modelo de referencia (RTX 4060 8 GB)

Configuración con la que se llegó a ~28 t/s con `Qwen3.6-35B-A3B` (MoE, Q4_K_XL) en `llama-server`:

```
llama-server.exe -m "%MODEL_PATH%" -np 1 -fa on -c 65536 -ctk q8_0 -ctv q8_0 ^
  --jinja --port 8081 --temp 0.3 --top-p 0.95 --top-k 20 --min-p 0.05 ^
  --repeat-penalty 1.05 --presence-penalty 0.0 --no-reasoning-preserve ^
  --n-gpu-layers 999 --n-cpu-moe 34 --load-mode none
```

* En un MoE, `--n-gpu-layers 999` + `--n-cpu-moe N` manda a CPU los expertos de las primeras N capas y deja el resto en GPU. Se baja N de a 2 hasta quedar cerca de 7 GB de VRAM usada.
* El KV cache cuantizado (`-ctk/-ctv q8_0`) requiere `-fa on`.
* Un modelo **denso** (no MoE) se configura distinto: ahí se ajusta `-ngl`, no `--n-cpu-moe`.

## 🏗️ Arquitectura actual

| **Archivo** | **Rol** | 
|---|---|
| `generar_mapa.py`, `frontend.py` | Entradas de compatibilidad (3 líneas): delegan en `nina.vigia` y `nina.cli`. Los comandos de siempre siguen funcionando. | 
| `web.py` | Interfaz Streamlit (solo presentación). Conectada al `gestor_config` y al motor multi-archivo. | 
| `nina/config.py` | Constantes, configuración base, Prompt Global (incluye las reglas de razonamiento) y `GestorConfig` persistente. | 
| `nina/proyecto.py` | `Proyecto(raiz)`: rutas, mapa, convenciones, backups por lote transaccional y Git. | 
| `nina/contexto.py` | Modelos (`Propuesta`, `CambioArchivo`), inyección dinámica de dependencias, auto-healing. | 
| `nina/bloques.py` | Parser tolerante multi-archivo y aplicador de SEARCH/REPLACE. | 
| `nina/llm.py` | Cliente LLM por streaming con patrón de Drivers (`Ollama`, `LlamaCpp`). | 
| `nina/trazas.py` | Análisis de stack traces (archivo, línea y otros archivos del proyecto). | 
| `nina/diff.py` | Diff puramente funcional. | 
| `nina/mapa.py` | Extractores de firmas e imports (tree-sitter + regex). | 
| `nina/vigia.py` | Watcher incremental (watchdog). | 
| `nina/cli.py` | CLI (typer + rich). | 
| `tests/` | Suite de pruebas unitarias y de integración (73 tests; la web se prueba con un Streamlit simulado en `fake_streamlit.py`). | 

**Regla de capas:** el núcleo (todo `nina/` salvo `cli.py` y `vigia.py`) no importa `typer`, `rich`, `streamlit` ni `watchdog`; un test lo vigila.

### 1. El Vigía (`nina/mapa.py` + `nina/vigia.py`)

* **tree-sitter** para Java, JS/JSX, TS/TSX y Python; HTML por regex.
* **Firmas completas** y **imports por archivo** (base del ranking).
* **Watcher incremental con debounce (0,5 s).**

### 2. El Ejecutor Stateless (`nina/contexto.py`, `bloques.py`, `llm.py`, `cli.py`)

Cada ejecución arranca de cero. Utiliza drivers específicos para inyectar configuraciones críticas. Modifica múltiples archivos a la vez en memoria, con validación de modelo de datos (`Propuesta.es_valida`) y auto-healing (3 intentos) por archivo. Si la IA necesita leer un archivo que no estaba seleccionado, lo inyecta al vuelo leyendo el disco.

`generar_cambio(ctx, proyecto, llamar, on_reintento)`: `proyecto` es opcional (`None` = no leer otros archivos del disco); `on_reintento(n, errores)` se llama después de cada intento rechazado.

### 3. La interfaz web (`web.py`)

* Selector de proyecto y archivos (con Modo Global). **Cambiar de archivo descarta** la propuesta en pantalla.
* Diff **lado a lado** sincronizado (apilable por lote de archivos), renderizado en un iframe aislado para que el scroll sincronizado y el salto al primer cambio funcionen siempre.
* **Razonamiento y métricas persistentes:** todo lo que se ve durante la generación se guarda por intento en `session_state` (`ss.traza`) y la página se redibuja desde ahí al terminar. El razonamiento sigue visible después de guardar, descartar o tocar cualquier opción. Si cortás con "Stop", queda lo que había llegado.
* **Reintentos visibles:** cada intento muestra su razonamiento y su respuesta (la rechazada, marcada) y hay una tabla de métricas por intento (latencia, tiempo de pensamiento, tiempo de código, tokens, velocidad).
* **Autoscroll:** sigue el final del bloque en vivo solo mientras se genera; si subís con la rueda se suelta y aparece "↓ Seguir al final". Cronómetros en vivo y contador de tokens aproximado del prompt.
* Control de API Keys y perfiles de servidor.
* Restauración transaccional (Deshacer Lote), disponible también en Modo Global.
* Fusión a tres vías al guardar si el archivo cambió en disco mientras la IA generaba.

## ✅ Estado de implementación

**Hecho y funcionando**

* **Fase 0 (Cimientos):** transacciones atómicas, validación de sintaxis (tree-sitter), resiliencia a codificaciones y aislamiento del cwd.
* **Fase 4.1 (Edición multi-archivo y nuevos):** el parser agrupa cambios por rutas. Soporta creación de carpetas automáticas y escritura por lotes.
* **Fase 4.2 (Prompt Global):** interfaz liberada del archivo pivote. El motor infiere qué archivos querés tocar leyendo tu prompt e inyecta su código en el contexto.
* **Fase 4.3 (Fusión de tres vías) — en la web:** si el archivo cambió en disco, se reaplica la respuesta cruda sobre la versión fresca; si tocaste las mismas líneas, avisa el conflicto y no escribe. (El CLI se limita a no aplicar si el archivo cambió.)
* **Fase 5.1 (UX de generación):** razonamiento persistente y autoscroll anclado, verificados en un navegador real (Chromium + Streamlit 1.65).
* **Fase 5.4 (parcial):** tiempos de pensamiento y de código separados, y métricas por intento. Falta la lista lateral de impactos.

## 🧱 Deuda técnica detectada (arreglar antes de crecer)

1. **Ranking por nombre de archivo.** `_import_a_stem` colisiona (`index`, `utils`). Requiere el resolutor avanzado de dependencias (Fase 4.5).
2. **Sin tope contra bucles de razonamiento.** Hoy no hay `max_tokens` ni límite de razonamiento: si el modelo se queda en bucle, corre hasta llenar el contexto (`finish_reason=length`). A 28 t/s con 64k de contexto son decenas de minutos. Las reglas del prompt global ayudan pero no garantizan nada (ver Fase 4.6).
3. **API Keys en texto plano** en `~/.nina/config.json`.
4. **Por verificar:** que Ollama respete `options.num_ctx` por `/v1`; en ese caso el driver podría no estar limitando el contexto.
5. **Reintentos caros.** Primera prueba real (Qwen3.6-35B-A3B, crear un `page.tsx` de ~480 líneas): el intento 1 fue rechazado tras 368 s (8.2k tokens de salida) y el intento 2 tardó 658 s (14.7k tokens, de los cuales ~323 s fueron razonamiento). Total: 1025 s y 22.9k tokens para un archivo. Un reintento reenvía entero el intento rechazado (la entrada pasó de 1.3k a 8.6k tokens). Las estadísticas de `llama-server` llegan exactas (tokens de entrada y salida, t/s) y el razonamiento llega por `reasoning_content`.
6. **Falsos positivos del aviso de sintaxis.** tree-sitter-tsx marca error ante un `&` suelto en texto JSX (`<h2>A & B</h2>`), que TypeScript acepta. El aviso ahora indica la línea y aclara que puede ser un falso positivo; sigue siendo orientativo.
7. **Temperatura enviada por NINA:** el perfil manda `temperatura` (por defecto 0.1) en cada pedido y eso pisa el `--temp` del servidor. Con modelos de razonamiento una temperatura muy baja favorece los bucles; conviene revisar `~/.nina/config.json` (o exponerla en la interfaz).
8. **Errores de reintento:** `generar_cambio` informa `{ruta: [errores]}` a `on_reintento`; la web y el CLI mostraban solo la ruta (corregido).

---

## 🗺 ROADMAP

### Fase 4 — Más potencia

**4.4 Integración con Git**
* Commit automático o `git stash` preventivo antes de escribir un lote.

**4.5 Resolución real de imports y limitación de vecindad**
* Portar resolutor indexado a Python para corregir colisiones.
* Limitar explícitamente el árbol de importaciones inyectadas a dependencias inmediatas (profundidad 1 o 2).

**4.6 Guardas anti-bucle (NUEVA, prioridad alta)**
* Tope de tokens de salida (`max_tokens`) y de razonamiento por intento, configurables por perfil.
* Detector de repetición en el stream (líneas o frases que se repiten): cortar la conexión y reintentar.
* Reintento con razonamiento desactivado (`chat_template_kwargs: {"enable_thinking": false}` en `llama-server`) o con un aviso de que la respuesta anterior se cortó por bucle.
* Mostrar en la interfaz que el intento se cortó por bucle y cuántos tokens consumió.
* Reintentos más baratos: no reenviar entero el intento rechazado (resumir o recortar la respuesta anterior) cuando es largo.

### Fase 5 — Interfaz Avanzada y Visualización

**5.2 Cálculo y Barra de Tokens en Vivo**
* Hoy el contador del `textarea` es aproximado (caracteres / 3) y solo cuenta el texto de la instrucción.
* Falta sumarle el contexto seleccionado y detectar automáticamente el contexto máximo del modelo.

**5.3 Árbol de Proyecto Enriquecido**
* Reemplazar visor estático por árbol colapsable interactivo.
* Que el árbol permita desplegar atributos, métodos, verbos HTTP (APIs), **variables utilizadas y descripciones por archivo** mapeados por Tree-sitter.

**5.4 Pantalla de Revisión y Métricas Detalladas (resto)**
* Lista lateral de impactos en lotes (✏️ modificado, 🆕 nuevo, ⚠️ error).

**5.5 Watcher Reactivo**
* Conectar los eventos del Watcher (`vigia.py`) directamente a la interfaz web para que el sistema (archivos y contexto) se actualice solo al detectar un cambio en el disco externo.

**5.6 Vista previa de HTML (NUEVA)**
* Poder visualizar archivos `.html` (el actual y la propuesta de la IA) renderizados, además del diff.
* Renderizarlos en un iframe con `sandbox` (sin acceso a la página de NINA): el HTML lo escribe un modelo y no debe poder tocar la interfaz.

**5.7 Sección de recomendaciones en el sidebar (NUEVA)**
* Bloque al pie de la barra lateral con consejos para que el prompt rinda más, por ejemplo:
  * crear un archivo de convenciones (`CONVENTIONS.md`) en la raíz del proyecto;
  * mencionar en la instrucción las rutas de los archivos que debe usar o crear, y en qué carpeta;
  * otras buenas prácticas (instrucciones acotadas, un cambio por pedido, etc.).
* Idea a evaluar: que detecte si falta `CONVENTIONS.md` en el proyecto y lo sugiera.

### Fase 6 — Autonomía Delegada (Agent Loop)

* **Auto-verificación de Código:** El sistema corre comandos locales (`npm run lint`, `pytest`) tras generar cambios y auto-corrige los fallos detectados sin intervención humana.
* **Consola Integrada:** Capacidad de escribir y ejecutar código o comandos bash en una consola propia dentro de la interfaz.
* **Checklists para Procesos Largos:** Fragmentación de planes grandes en tareas atómicas. Literalmente dejar al sistema programando solo, resolviendo una checklist paso a paso.
* **Pruebas "Zero-to-Hero":** Validar la capacidad de NINA para crear y armar un proyecto completo y funcional desde la más absoluta nada.

> La Fase 6 depende de la 4.6: un agente que corre solo no puede quedar atrapado en un bucle de razonamiento.

### Fase 7 — Arquitectura y Despliegue

* **Gestión de Servidor Local:** Integrar la posibilidad de levantar un servidor Llama (o instancia de Ollama) directamente desde los controles internos de NINA, sin depender de consolas externas.
* **Despliegue a la Nube (Cloud-Ready):** Documentar y adaptar la arquitectura para subir el sistema de forma segura (añadir capas de autenticación, control de multi-usuarios, Dockerización y restricciones de Path Traversal robustas para entornos VPS compartidos).

---

## 🧠 Reglas de razonamiento (prompt global)

`nina/config.py` incluye reglas para que el modelo no dude de sus propias soluciones y no se quede en bucle: analizar una sola vez, trazar el plan de forma lineal, escribir el bloque SEARCH/REPLACE de inmediato y, ante una instrucción ambigua, asumir la interpretación más lógica. Se agregaron porque Qwen en modo razonamiento se queda a menudo dando vueltas.

* Es una mitigación blanda: un modelo en modo razonamiento no siempre obedece instrucciones sobre su propio razonamiento. Conviene medir su efecto (longitud del razonamiento y cantidad de bucles con y sin las reglas) y no darlas por buenas sin esa comparación.
* Pendiente de decidir: pedir que el razonamiento empiece con una posible respuesta y que las verificaciones se hagan todas juntas.

## 📝 Notas de trabajo (de la versión anterior)

* ~~Al cambiar de archivo, que funcione como descartar/limpiar vista.~~ Hecho.
* ~~Si necesita reintentar varias veces, dividir las métricas según cada intento.~~ Hecho.
* Razonamiento: primero una posible respuesta y verificaciones todas juntas → ver *Reglas de razonamiento*.
* Visualizar HTML → Fase 5.6.
* Recomendaciones en el sidebar → Fase 5.7.
* Dividir tokens de salida con tokens de pensamiento.
* Crear botones para copiar el codigo de cada archivo o todos los codigos juntos