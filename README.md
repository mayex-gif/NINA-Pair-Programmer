# NINA — Documentación y Roadmap v2.1 🚀

**Filosofía:** herramienta de *coworking* (pair programming) 100 % local y stateless. Maximiza la velocidad de inferencia y protege la caché en hardware con poca VRAM (RTX 4060 de 8 GB), dándole a la IA el contexto justo del proyecto sin arrastrar historiales de chat.

**Principio rector:** antes de agregar funciones, volver *seguro y confiable* lo que ya existe. Nada se escribe en disco sin que veas el diff y sin que haya un backup.

> Este documento está alineado con el código actual (`frontend.py`, `generar_mapa.py`, `web.py`, `.streamlit/config.toml`, `RepomapGraph.jsx`). Donde algo está planeado y no existe todavía, se marca como tal.

---

## 📦 Instalación

```bash
pip install -r requirements.txt
```

> `pytest` conviene moverlo a un `requirements-dev.txt`. `tree-sitter-html` está en la lista pero todavía sin uso: `generar_mapa.py` procesa `.html` por regex (se puede conectar luego en `_cargar_parsers`). `streamlit-tree-select` es para el árbol de la Fase 5.3.

Si falta `tree-sitter` (o el paquete de un lenguaje), `generar_mapa.py` cae a regex **solo para ese lenguaje** y lo informa al iniciar. `.html` siempre va por regex.

**Ejecución**

```bash
python generar_mapa.py              # escanea y queda vigilando (--una-vez para escanear y salir)
streamlit run web.py                # interfaz web (copiá .streamlit/config.toml junto a web.py)
python frontend.py refactor "ruta/archivo.jsx" "Tu instrucción"
python frontend.py fix "stack trace"   # o sin argumento (stdin) o -p (portapapeles)
python frontend.py deshacer "ruta/archivo.jsx"
python frontend.py probar              # prueba conexión y muestra cómo llega el razonamiento
```

**Variables de entorno** (hoy; la Fase 5 las complementa con una configuración persistente):

| Variable | Default | Uso |
|---|---|---|
| `AI_PERFIL` | `ollama` | Perfil de `PERFILES`: `ollama`, `ollama-7b`, `llama-server` |
| `AI_API_URL` | según perfil | Pisa la URL del perfil (LM Studio = 1234, llama.cpp = 8080/8081, Koboldcpp = 5001, Ollama = 11434) |
| `AI_MODELO` | según perfil | Pisa el modelo del perfil |
| `AI_MAX_TOKENS_PROMPT` | `12000` | Si el prompt estimado lo supera, avisa / pide confirmación |
| `AI_MAX_INTENTOS` | `3` | Intentos totales del auto-healing por pedido |

**`.gitignore` recomendado:** `.ai_map.json` y `.ai_backups/` (la carpeta de backups ya incluye su propio `.gitignore`).

---

## 🏗️ Arquitectura actual

| Archivo | Rol |
|---|---|
| `generar_mapa.py` | **El Vigía.** Escanea y vigila el proyecto; escribe `.ai_map.json` (firmas + imports). |
| `frontend.py` | **El Ejecutor.** Contexto, llamada al LLM, interpretación de SEARCH/REPLACE, auto-healing, backups, git y CLI (typer). Es también el *núcleo* que importa `web.py`. |
| `web.py` | Interfaz Streamlit: elegir proyecto y archivo, instrucción, diff lado a lado, métricas, razonamiento, deshacer. |
| `.streamlit/config.toml` | Tema gris oscuro de la web. |
| `RepomapGraph.jsx` | Visor de grafo (React Flow + dagre). **Se retira** (ver "Decisiones de diseño"); su resolutor de imports se porta a Python. |
| `CONVENTIONS.md` *(opcional, en el proyecto)* | Reglas globales que se concatenan al mensaje `system`. |

### 1. El Vigía (`generar_mapa.py`)

* **tree-sitter** para Java, JS/JSX, TS/TSX y Python; HTML por regex (ids, `<script src>` y `<link href>`).
* **Firmas completas** (anotaciones, genéricos, arrow functions, `React.memo`/`forwardRef`, interfaces, tipos TS, decoradores Python).
* **Imports por archivo** (base del ranking de relevancia).
* **Watcher incremental con debounce (0,5 s):** re-parsea solo el archivo tocado; detecta crear/borrar/renombrar; ignora `node_modules`, `target`, `venv`, `.git`, etc.
* **Escritura atómica** de `.ai_map.json` (`os.replace`).
* Los archivos sin firmas ni imports **no entran al mapa**.

Formato (v2): `{"version": 2, "files": {"ruta": {"signatures": [...], "imports": [...]}}}`. El lector también acepta el v1 plano.

### 2. El Ejecutor Stateless (`frontend.py`)

Cada ejecución arranca de cero. Flujo de `refactor` / `fix`:

1. Lee el archivo preservando CRLF/LF.
2. Chequeo de Git: si el archivo tiene cambios sin commitear, avisa y pide confirmación.
3. Arma el prompt: reglas base + `CONVENTIONS.md` + **mapa filtrado por relevancia** + código.
4. Estima tokens (caracteres / 3) y avisa si supera `AI_MAX_TOKENS_PROMPT`.
5. Streaming al modelo (razonamiento visible por separado de la respuesta; métricas de tokens, t/s y tiempos).
6. Interpreta **bloques SEARCH/REPLACE** y los aplica en memoria (todo o nada).
7. **Auto-healing:** si un bloque no se puede aplicar, reenvía al modelo el error (con las líneas más parecidas del archivo real) y pide el set completo de bloques; hasta `AI_MAX_INTENTOS`.
8. Muestra el diff y advierte si el archivo se achica de forma sospechosa (< 60 % de líneas).
9. Confirmación (`[y/N]`, por defecto no). Antes de escribir verifica que el archivo no haya cambiado en disco; hace **backup** en `.ai_backups/` y recién entonces escribe.

`fix` extrae pares `archivo:línea` de trazas Java, Node/navegador y Python, los resuelve contra el mapa, toma el primer archivo del proyecto y adjunta el fragmento alrededor de la línea fallida.

**Relevancia del mapa:**

| Criterio | Puntos |
|---|---|
| El archivo objetivo lo importa | +5 |
| El archivo importa al objetivo | +4 |
| Su nombre aparece en la instrucción | +3 |
| Identificadores en común con la instrucción | hasta +3 |
| Está en la misma carpeta | +1 |

Presupuesto: ~6000 caracteres, máx. 30 archivos. La consola/web muestra `mapa 4/120 archivos`.

**Detalles pensados para 8 GB de VRAM:** `cache_prompt: true`; mensaje `system` con lo estático primero (reglas + convenciones) y lo variable después (mapa); mapa como texto compacto, no JSON indentado.

### 3. La interfaz web (`web.py`)

* Selector de carpeta del proyecto (texto o diálogo "Examinar…") y buscador de archivos (lista el disco, no el mapa).
* Instrucción + botón *Generar*; advertencia de Git con casilla "Entiendo el riesgo".
* Diff **lado a lado** con filas alineadas, scroll sincronizado, colapso de zonas sin cambios y salto al primer cambio.
* Métricas (entrada, salida, velocidad, tiempo, intentos), razonamiento en vivo y expandible.
* *Aplicar y guardar* / *Descartar* / *Restaurar último backup*.
* Panel "Servidor" (perfil, URL, modelo, probar conexión) y "Opciones".

---

## ✅ Estado de implementación

**Hecho y funcionando**

* Fase 1 y 2: backups + `deshacer`, diff visual, `CONVENTIONS.md`, formato SEARCH/REPLACE, escritura todo-o-nada.
* Fase 3: filtrado del mapa por relevancia, `fix` auto-guiado.
* **Adelantado de la antigua Fase 4:** reintento automático (auto-healing), aviso de Git por archivo sucio, protección contra cambios en disco durante la espera.
* **Adelantado de la antigua Fase 5:** web con Streamlit y visor de grafo (JSX, a retirar).
* Soporte de Python y HTML en el Vigía; perfiles de servidor; comando `probar`; razonamiento y métricas en streaming.

**No hecho:** edición multi-archivo, creación de archivos nuevos, revisión por bloques, configuración persistente, tests, integración Git más allá del aviso, resolución real de imports en el ranking, validación de sintaxis posterior al cambio.

---

## 🔎 ¿Puede el sistema trabajar con varios archivos? (estado real)

**No.** Ni modificar varios archivos ni mostrar varios diffs. Está limitado a *un* archivo en cada capa:

| Capa | Dónde | Limitación |
|---|---|---|
| Prompt | `SISTEMA_BASE` | "Modificás UN archivo por vez". |
| Modelo de datos | `Contexto`, `Resultado` | Una sola `ruta` / un solo `original` / un solo `nuevo`. |
| Parser | `interpretar_respuesta(respuesta, original)` | Los bloques SEARCH/REPLACE no llevan ruta; todos se aplican al mismo texto. |
| CLI | `ejecutar` | Exige `ruta.is_file()`. |
| Web | `st.selectbox` + `ss.resultado` | Un archivo seleccionado, un resultado, un diff. |
| Backups | `hacer_backup` / `ultimo_backup` | Un backup por archivo; sin noción de "lote". |

**Crear archivos nuevos:** tampoco. Solo funciona el caso de un archivo que **ya existe pero está vacío** (se acepta el contenido completo). Si no existe en disco, el CLI falla y la web no lo lista. Además, `deshacer` no sabría eliminar un archivo creado.

---

## 🧱 Deuda técnica detectada (arreglar antes de crecer)

Ordenada por impacto sobre lo que viene (multi-archivo, configuración, revisión por bloques).

1. **Estado global de directorio.** `web.py` usa `os.chdir()` (afecta a todo el proceso, incluidas otras pestañas) y `frontend.py` define rutas relativas a nivel de módulo (`.ai_map.json`, `.ai_backups`, `CONVENTIONS.md`) y `clave_relativa` depende de `Path.cwd()`. Con varios archivos, esto se vuelve una fuente de errores sutiles.
2. **Núcleo y CLI mezclados.** `frontend.py` contiene la lógica y a la vez importa `typer`/`rich`; la web importa el módulo del CLI. Además `PERFIL`, `API_URL` y `MODELO` se fijan al importar, por lo que cambiar de servidor en la web no llega al resto del código ni al CLI.
3. **Cero tests**, y está a punto de cambiar el parser, el aplicador y el ranking. Sin red de seguridad, el refactor es a ciegas.
4. **Escritura no atómica.** `escribir_archivo` abre con `"w"` (trunca primero): un corte a mitad de escritura deja el archivo roto. Para varios archivos se necesita además "todo o nada" entre archivos.
5. **Backups con resolución de 1 segundo** (`%Y%m%d-%H%M%S`): dos aplicaciones en el mismo segundo se pisan, y `ultimo_backup` elige por orden alfabético. No hay lote ni manifiesto.
6. **Sin validación posterior al cambio.** Un SEARCH/REPLACE válido puede dejar el archivo con errores de sintaxis; hoy solo se detecta si el archivo "se achica". Con tree-sitter ya instalado, `root_node.has_error` es un chequeo gratis para Java/JS/TS/Python. También cubre el caso del archivo vacío donde `interpretar_respuesta` acepta cualquier texto como contenido.
7. **Configuración de servidor no persistente.** Los perfiles están fijos en el código; el panel "Servidor" permite editar URL/modelo pero no guarda, y "Probar conexión" lista modelos que **no se pueden elegir**. Faltan: API key (`Authorization`), campos del payload condicionales (`cache_prompt` puede ser rechazado por APIs en la nube) y parámetros por servidor. Hoy `stream_llm` fija `temperature: 0.1` para cualquier modelo; para Qwen3.x en modo razonamiento la guía del fabricante apunta a 0.6 con `top_p 0.95` y `top_k 20` (ver Apéndice A).
8. **Contexto de Ollama.** Por el endpoint `/v1` no se puede fijar `num_ctx`, y el contexto por defecto de Ollama suele ser chico (2048–4096 según versión y VRAM; verificalo con `ollama ps`). Prompts de ~12 000 tokens pueden recortarse **en silencio**. El perfil por defecto es Ollama. Hace falta un *driver* por tipo de servidor (ver Fase 0).
9. **Ranking por nombre de archivo.** `_import_a_stem` reduce cada import a su último segmento (`index`, `App`, `utils`, `User` colisionan entre carpetas). El resolutor de `RepomapGraph.jsx` es bastante mejor (índice de sufijos, `index.*`, alias `@/`, imports relativos de Python, `<script src>`).
10. **Constantes duplicadas y con deriva:** `EXTENSIONES` e `IGNORE_DIRS` existen en `generar_mapa.py` y en `web.py` con valores distintos (la web incluye `.css`, el Vigía no).
11. **Mapa desactualizado tras aplicar.** La web no inicia el Vigía ni refresca el mapa después de guardar; si el Vigía no está corriendo, el siguiente pedido usa firmas viejas.
12. **Lectura estricta en UTF-8.** Un archivo en otra codificación (típico en código Java viejo en Windows) levanta `UnicodeDecodeError` sin mensaje claro.
13. **Patrón de trazas incompleto.** Las URLs de Vite con `?t=<timestamp>` (`…/App.jsx?t=1723:20:11`) no coinciden con `PATRON_TRAZA`, así que `fix` no encuentra el archivo en trazas del navegador con HMR. Test `xfail` ya escrito.
14. **Lógica no testeable** por estar dentro de comandos CLI o de `web.py` (ver 0.2).
15. **Menores:** línea duplicada `b1, b2, _ = st.columns(...)` en `web.py`; sin botón de cancelar generación en la web; `mostrar_diff` y `renderizar_diff` duplican la lógica de diff; el doc anterior estaba desactualizado (Python/HTML, auto-healing, web, perfiles).

---

## 🗺️ ROADMAP

### Fase 0 — Cimientos (antes de cualquier función nueva)

Objetivo: que multi-archivo, configuración y revisión por bloques se construyan sobre piezas estables y testeadas. Orden recomendado:

| # | Tarea | Resuelve |
|---|---|---|
| 0.1 | **Tests con pytest** de las funciones puras: `aplicar_bloques`, `interpretar_respuesta`, `filtrar_mapa`, parser de trazas, `extraer_archivo` (fixtures Java/JS/Py/HTML), y luego el resolutor de imports. **Estado: iniciada** — `tests/test_nucleo.py` (43 tests: 40 caracterizan lo que hoy funciona y 3 `xfail` marcan huecos conocidos). Falta lo que hoy no es testeable (ver 0.2). | #3 |
| 0.2 | **Separar el núcleo:** paquete `nina/` con `core` (sin UI), `cli.py` y `web.py` como consumidores finos. Introducir un objeto `Proyecto(raiz)` que concentre rutas del mapa, backups, convenciones y config; eliminar `os.chdir` y los `Path` globales. Una sola fuente para `EXTENSIONES` / `IGNORE_DIRS`. Sacar a funciones puras lo que hoy no se puede testear: la extracción de frames del comando `fix` y las funciones de diff de `web.py` (el módulo levanta Streamlit al importarse). | #1 #2 #10 #14 |
| 0.3 | **Modelo de datos multi-archivo:** `Propuesta` → lista de `CambioArchivo` (ruta, original o *nuevo archivo*, eol, texto nuevo, errores, avisos, bloques). CLI y web consumen lo mismo. Un archivo es el caso particular de un lote. | #1 |
| 0.4 | **Configuración persistente + drivers por servidor** (ver 5.1): API key, payload según tipo de servidor, `num_ctx` y detección de recorte para Ollama. | #7 #8 |
| 0.5 | **Escritura atómica** (archivo temporal + `os.replace`) y **backups por lote** con manifiesto (qué archivos, cuáles eran nuevos). `deshacer` revierte el lote completo, incluso eliminando archivos creados. Sello de tiempo con milisegundos o contador. | #4 #5 |
| 0.6 | **Validación de sintaxis** del texto resultante con tree-sitter (`has_error`) antes de ofrecer *Aplicar*; advertir si el original estaba bien y el resultado no. | #6 |
| 0.7 | **Seguridad de rutas:** toda ruta que venga del modelo se normaliza y debe quedar dentro de la raíz del proyecto, fuera de carpetas ignoradas y de `.git`, y con extensión permitida. Sin borrados ni renombrados en esta etapa. | (nuevo; imprescindible para 4.x) |
| 0.8 | Lectura tolerante de codificación con mensaje claro; refresco del mapa tras aplicar (llamando a `extraer_archivo` de los archivos tocados); limpiar lo menor (#15); ampliar `PATRON_TRAZA` para `?t=…`. | #11 #12 #13 #15 |

### Fase 4 — Más potencia

**4.1 Edición multi-archivo y archivos nuevos**

*Formato de respuesta* (extiende el actual; la ruta va en la línea previa al bloque):

```
backend/src/main/java/com/lwt/dto/UserDto.java
<<<<<<< SEARCH
=======
public record UserDto(Long id, String name) {}
>>>>>>> REPLACE

backend/src/main/java/com/lwt/service/UserService.java
<<<<<<< SEARCH
public List<User> findAll()
=======
public List<UserDto> findAll()
>>>>>>> REPLACE
```

* **SEARCH vacío = archivo nuevo** (error si el archivo ya existe con contenido).
* Si el alcance es un solo archivo y el modelo omite la ruta, se asume ese archivo.
* El parser agrupa los bloques por ruta y aplica cada grupo a su original, con las mismas reglas de unicidad.
* Rutas fuera del alcance (selección o plan) se rechazan con error y entran al auto-healing.
* **Auto-healing por archivo:** solo se reintentan los archivos con errores; los que salieron bien se conservan.

*Dos modos de uso:*

* **A. Manual:** elegís N archivos (selección múltiple / casillas en el árbol) y marcás "permitir crear archivos nuevos". El prompt incluye los N archivos; si la suma supera `AI_MAX_TOKENS_PROMPT`, se propone pasar al modo B.
* **B. Plan y ejecución (precursor de la Fase 6):**
  1. *Planificador:* la IA lee el mapa y la instrucción y devuelve la lista de archivos a tocar/crear con una línea de intención y los **contratos entre archivos** (p. ej. "`UserService.findAll()` devuelve `List<UserDto>`").
  2. Vos editás la lista (quitar, agregar, marcar nuevo).
  3. *Ejecución:* una llamada **stateless por archivo**, cada una con su contexto filtrado, el plan completo y su propio auto-healing. Mantiene la VRAM liviana y evita que un archivo grande pise a los demás. Los contratos del plan son lo que mantiene la coherencia entre archivos.

*Aplicación:* todo el lote se valida (rutas, sintaxis, cambios en disco) y se escribe de forma transaccional con backup de lote; si falla un archivo, se revierte lo ya escrito.

**4.2 Revisión por bloques estilo conflicto de merge (GitHub / VS Code)**

Hoy es "aplicar todo o descartar todo". Pasa a ser decisión por bloque, sin perder el camino rápido.

* **Unidad:** *hunk* calculado con `difflib` entre original y propuesta (agrupando cambios cercanos con 3 líneas de contexto). Sirve igual si el modelo respondió con SEARCH/REPLACE o con el archivo completo.
* **Por bloque, tres opciones:**
  * ✅ **Aceptar IA** (lo nuevo)
  * ⛔ **Mantener actual** (lo que había)
  * ➕ **Conservar ambos** (actual primero, luego la propuesta)
  * *(opcional)* ✏️ **Editar**: campo de texto con el bloque para ajustar a mano.
* **Globales** (por archivo y por lote): *Aceptar todo*, *Rechazar todo*, *Aceptar los pendientes*. Por defecto todo viene aceptado, así el flujo de un solo clic de hoy se mantiene; los contadores muestran "3 de 5 aceptados".
* **Composición:** función pura `componer(original, hunks, decisiones) → texto`, cubierta por tests (0.1). El resultado pasa por la validación de sintaxis (0.6): si aceptar solo una parte rompe la sintaxis (p. ej. se aceptó el uso pero no el import), se advierte antes de guardar.

```
┌ UserService.java · bloque 2/4 ─────────────────────────┐
│ Actual                  │ Propuesta IA                 │
│ - return repo.findAll() │ + return repo.findActive()   │
│  (•) Aceptar IA   ( ) Mantener actual   ( ) Ambos     │
└─────────────────────────────────────────────────────────┘
[Aceptar todo] [Rechazar todo]            [Aplicar 3 archivos]
```

**4.3 Fusión de tres vías cuando el archivo cambió en disco**

Hoy, si editás el archivo mientras la IA responde, se descarta todo (`archivo_cambio_en_disco`). Con base = lo que se envió, *ours* = disco actual y *theirs* = propuesta, se hace un merge de tres vías (con `git merge-file`, o equivalente con `difflib` si no hay Git) y las zonas en conflicto real se resuelven con la misma interfaz de 4.2. Este es el caso más parecido a un merge de GitHub.

**4.4 Integración con Git**

Aviso por archivo sucio ya existe. Falta un *checkpoint* previo a escribir el lote: `git stash create` + `git stash store` (no modifica el árbol de trabajo) o commit en una rama `nina/<fecha>`. Complementa, no reemplaza, a `.ai_backups/`.

**4.5 Resolución real de imports**

Portar `resolverImports` de `RepomapGraph.jsx` a Python (módulo compartido del núcleo) y usarlo en `filtrar_mapa` en lugar de `_import_a_stem`. Luego sumar `tsconfig` paths y `package.json`/`pom.xml` para alias y paquetes reales. Con tests (0.1).

### Fase 5 — Interfaz

**5.1 Botón ⚙️ Configuración** (diálogo `st.dialog`, junto al título)

* **Servidores / modelos:**
  * Lista de perfiles persistente (archivo de usuario, p. ej. `~/.nina/config.json`; las variables `AI_*` siguen pisando) con *agregar, editar, duplicar, borrar*.
  * Cada perfil: nombre, **tipo** (Ollama, llama.cpp, LM Studio, Koboldcpp, API compatible con OpenAI), URL base, modelo, API key (opcional; mejor como nombre de variable de entorno que como texto), **muestreo** (temperatura, `top_p`, `top_k`, `presence_penalty`), interruptor **pensar sí/no** (`chat_template_kwargs.enable_thinking`) y contexto máximo.
  * **Probar conexión** y **cargar modelos**: la lista que devuelve `/v1/models` se muestra como desplegable para *elegir* el modelo (hoy solo se muestra como texto).
  * Indicador de estado (conectado / sin respuesta) en la cabecera.
* **Proyecto:** carpeta, extensiones y carpetas ignoradas (una sola fuente compartida con el Vigía), comandos de validación por lenguaje (insumo de la Fase 6).
* **Contexto:** presupuesto del mapa, límite de tokens, intentos del auto-healing, enviar o no el mapa.
* **Visualización:** razonamiento en vivo, diff solo zonas modificadas.
* Los drivers por servidor (0.4) deciden el payload: p. ej. no enviar `cache_prompt` a APIs que lo rechazan, usar `/api/chat` con `options.num_ctx` en Ollama y comparar el conteo real de tokens de entrada contra el estimado para detectar recortes silenciosos.

**5.2 Selector rápido junto a la instrucción**

Un `st.popover` / par de desplegables (perfil + modelo) al lado del botón *Generar*, para cambiar de servidor o modelo sin abrir la configuración. Lee y escribe el mismo estado que el diálogo ⚙️, de modo que ambos siempre coinciden.

**5.3 Árbol de proyecto y grafo de dependencias (reemplaza `RepomapGraph.jsx`)**

* **Árbol de carpetas** colapsable con casillas (`streamlit-tree-select`): alimenta la selección multi-archivo de 4.1 y marca archivos con cambios de Git, archivos propuestos por el plan (4.1-B) y archivos ya modificados en la propuesta actual.
* **Grafo de dependencias de la vecindad** de los archivos elegidos (profundidad 1–2) con `st.graphviz_chart` (DOT, `rankdir=LR`; se renderiza en el navegador, sin instalar Graphviz). El grafo completo de un proyecto grande es ilegible; la vecindad sí sirve y además muestra qué contexto recibirá la IA.

**5.4 Pantalla de revisión multi-archivo**

Lista lateral de archivos de la propuesta con estado (✏️ modificado, 🆕 nuevo, ⚠️ error, ✅ revisado), diff lado a lado y bloques de 4.2 para el archivo activo, y un único botón *Aplicar N archivos*.

**5.5 Otros**

Botón *Reindexar mapa* (y opción de lanzar el Vigía en segundo plano desde la web); botón *Cancelar* durante la generación; guardar la última instrucción por archivo.

### Fase 6 — Autonomía delegada (Agent Loop y Plan-and-Execute)

Objetivo: que el motor trabaje solo en tareas largas (p. ej. mientras salís a entrenar), mitigando alucinaciones y cuidando la VRAM con reseteos suaves. Se apoya en las fases anteriores: **el planificador de 4.1-B ya es el "Arquitecto"** y los comandos de validación se configuran en 5.1.

**1. Bucle de autocorrección con el entorno real**
Tras aplicar los cambios, el script corre el comando de validación del proyecto (`mvn test-compile`, `npm run lint`, `tsc --noEmit`, `python -m py_compile`…). Si falla, abre un prompt *stateless* nuevo con el error y pide la reparación (máx. 3 reintentos). Complementa —no reemplaza— el chequeo rápido de sintaxis de 0.6.

**2. Reseteo suave y checklist**
El plan se guarda en `.ai_todo.md` (`[ ] Crear DTO`, `[ ] Armar Controller`, `[ ] Actualizar UI`). El bucle toma la primera tarea sin marcar, filtra el contexto solo para esa tarea, ejecuta, valida y la tacha `[x]`. Al terminar cada micro-tarea se destruye la sesión y se arranca limpio con la siguiente, manteniendo la velocidad al 100 %. Cada micro-tarea genera su propio backup de lote, por lo que cualquiera se puede deshacer por separado.

---

## 🖥️ Apéndice A — llama-server para 8 GB de VRAM (a validar con tus métricas)

Modelo MoE (35B totales, ~3B activos) en una RTX 4060 de 8 GB: no entra completo en VRAM. La estrategia recomendada es dejar **atención y KV en la GPU y los expertos en RAM**, en lugar de bajar `-ngl` y mandar capas enteras a la CPU.

```bat
@echo off
title Llama Server - Qwen 35B
cd /d "C:\llama-server"
set MODEL_PATH=D:\LLMs\Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf

:: -ngl 999 + --cpu-moe : atención y KV en GPU, expertos MoE en RAM. Después probá --n-cpu-moe N (N más bajo = más expertos en GPU)
:: -fit off             : con offload manual, evita que el autoajuste pise tu configuración
:: -ctk/-ctv q8_0       : KV a 8 bits (~mitad de VRAM de contexto)
C:\llama-server\llama-server.exe -m "%MODEL_PATH%" ^
  -c 32768 -np 1 -ctk q8_0 -ctv q8_0 ^
  -ngl 999 --cpu-moe -fit off ^
  --jinja --port 8081
pause
```

**Cómo afinarlo:** arrancá con `--cpu-moe`, mirá la memoria de GPU dedicada con el contexto cargado y reemplazalo por `--n-cpu-moe 30`, `25`, `20`… hasta quedar en ~7 GB usados (dejá margen: en Windows el driver puede desbordar a memoria compartida en vez de fallar, y la velocidad cae de golpe). Compará cada variante con las métricas de NINA (t/s y tiempo al primer token). Como NINA manda prompts de 3–12 mil tokens, el *prefill* pesa tanto como la generación: probá también `-ub 1024`.

**Sampling y razonamiento (lado NINA):** la guía del modelo para razonamiento en tareas de código es temperatura 0.6, `top_p 0.95`, `top_k 20`; en modo sin razonamiento, 0.7 / 0.8 / 20. Para ediciones SEARCH/REPLACE conviene medir ambos modos (*pensar sí/no*, ver 5.1): sin razonamiento suele ser bastante más rápido.

---

## 🧭 Decisiones de diseño

* **`RepomapGraph.jsx` se retira.** Requiere toolchain de Node (Vite/CRA, `reactflow`, `dagre`), exige copiar `.ai_map.json` a `public/`, vive aparte de la interfaz Streamlit, y su paleta depende de nombres de carpeta fijos (`backend`/`frontend`). Lo valioso es su **resolutor de imports**, que se conserva portado a Python (4.5) y alimenta tanto el ranking de contexto como el grafo del árbol (5.3).
* **Hunks por `difflib` y no por bloque SEARCH/REPLACE.** Así la revisión por bloques funciona también cuando el modelo devuelve el archivo completo, y es la misma pieza que reutiliza la fusión de tres vías.
* **Planificar con contratos y ejecutar por archivo.** Es más lento que una única llamada, pero encaja con 8 GB de VRAM, permite auto-healing por archivo y limita el daño de un error.
* **Seguridad primero en multi-archivo:** rutas validadas, escritura transaccional, backup de lote y undo de lote antes de habilitar la creación de archivos.

---

## ⚠️ Limitaciones conocidas

* **La relevancia es heurística** (hasta que llegue 4.5): resuelve imports por nombre de archivo; puede fallar con alias, `index.*` o clases homónimas en paquetes distintos.
* **Un cambio que toca varios archivos requiere varias ejecuciones** (hasta 4.1).
* **`fix` usa el primer archivo del proyecto del trace**, que no siempre es la causa raíz; el resto se lista como referencia.
* **Estimación de tokens aproximada** (caracteres / 3).
* **tree-sitter ≥ 0.22**; con versiones viejas cae a regex.
* **En Windows**, pegar un trace multilínea como argumento entre comillas suele romperse: usá `fix` sin argumento (stdin, `Ctrl+Z` + `Enter`) o `-p`.

---

## ✅ Checklist de pruebas manuales

**Vigente** — leyenda: ✅ cubierto por test automático (`tests/test_nucleo.py`) · 📖 verificado solo leyendo el código (falta correrlo a mano) · ⚠️ con matiz

* [x] ✅⚠️ Cambio de una sola variable: el diff cuenta `+1 −1`. *Matiz:* consola y web muestran además 3 líneas de contexto por lado, no "únicamente esa línea".
* [x] 📖⚠️ Sin mencionar framework: `cargar_convenciones()` inyecta `CONVENTIONS.md` en el `system` (el orden estático → variable está testeado). *Matiz:* que el modelo las respete solo se comprueba a mano, y el archivo se busca en el directorio actual (deuda #1).
* [x] ✅⚠️ Editar un `.jsx`: el mapa no trae firmas de Spring no relacionadas. *Matiz:* el ranking es heurístico; si la instrucción nombra algo del backend (p. ej. `findAll`, `Order`), esas firmas sí entran (hay un test que lo documenta).
* [x] 📖 Rechazar: el archivo queda intacto y no se crea backup (lógica de UI/CLI, sin test).
* [x] ✅⚠️ Aceptar y luego `deshacer`: ciclo backup → restauración testeado (el botón web, 📖). *Matiz:* dos aplicaciones en el mismo segundo se pisan (`xfail`, Fase 0.5).
* [x] ✅⚠️ `fix` con trazas de Java, Node (ruta Windows), URL de navegador y Python: los patrones están testeados. *Matiz:* las URLs de Vite con `?t=<timestamp>` **no se detectan** (`xfail`), y la orquestación vive dentro del comando `fix`, todavía sin test.
* [x] 📖 Apagar el servidor del modelo: `httpx.ConnectError` → `ErrorLLM` con mensaje claro (requiere prueba manual real).
* [x] ✅ Editar el archivo a mano mientras la IA responde: `archivo_cambio_en_disco` testeado; no se guarda nada y se avisa.
* [x] 📖 Archivo con cambios sin commitear: `git_archivo_sucio` + confirmación (CLI) / casilla que habilita *Generar* (web).

**Nuevas (a medida que se implementen)**

* [ ] Dos `Aplicar` seguidos en el mismo segundo: ambos backups existen.
* [ ] Cambio que toca 3 archivos (uno nuevo): se ven 3 diffs, se aplican juntos y `deshacer` revierte el lote, incluyendo borrar el archivo creado.
* [ ] El modelo responde con una ruta fuera del proyecto (`../`, absoluta, `.git/`): se rechaza.
* [ ] Aceptar solo la mitad de los bloques: el resultado compone bien y avisa si rompe la sintaxis.
* [ ] "Conservar ambos" deja el código actual y el nuevo, en ese orden.
* [ ] Cambiar de servidor/modelo desde ⚙️ y desde el selector rápido: ambos reflejan lo mismo y el cambio persiste al reiniciar.
* [ ] Prompt grande con Ollama: se avisa si el servidor recortó el contexto.
* [ ] Árbol: marcar 2 archivos alimenta el multi-archivo y el grafo muestra sus dependencias.


MUCHO MAS ADELANTE
- MOSTRAR GESTION DE TOKENS DEL CHAT CONTRA LO USADO, MEJOR SI LO CALCULA EN EL MOMENTO ____________  0 TOKENS--------32K TOKENS
- PERMITIR USAR COMANDOS AL LLM PARA PROBAR FUNCIONALIDADES.