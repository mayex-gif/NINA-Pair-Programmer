# NINA — Documentación y Roadmap v2.6.1 🚀

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
python frontend.py agente "objetivo"   # Fase 6: planifica, ejecuta tareas con checkpoint Git y verifica (--solo-planificar para revisar el plan)
python frontend.py probar              # prueba conexión y muestra cómo llega el razonamiento
python -m nina refactor ...         # equivalente a frontend.py (el paquete nina/ es el núcleo)
# todos los comandos del CLI aceptan --proyecto/-C <raíz>; las rutas relativas se leen desde esa raíz
python frontend.py servidor            # imprime el comando de llama-server generado desde tu perfil (config.py)
python frontend.py servidor --bat servidor.bat   # o escribe un .bat equivalente; --ejecutar lo lanza en primer plano
python probar_reintento.py             # prueba real del auto-healing: arruina a propósito el intento 1 (ver abajo)
pytest -q                            # esperado: 152 passed, 1 xfailed (sin modelo ni servidor; el agente se prueba con un LLM falso y git real)
```

**Configuración: una sola fuente de verdad (`nina/config.py` → `~/.nina/config.json`)**
Cada perfil tiene tres secciones. Se editan en `~/.nina/config.json` (la web **no** las modifica), que pisa los valores por defecto de `config.py` clave por clave:

| Sección | Qué controla |
|---|---|
| `muestreo` | `temperature`, `top_p`, `top_k`, `min_p`, `repeat_penalty`, `presence_penalty`. **NINA los manda en cada pedido**, así que mandan sobre cualquier valor con el que se haya lanzado el servidor. |
| `limites` | `max_tokens_salida`, `max_tokens_pensamiento`, `detectar_bucles`, `reenviar_respuesta_hasta_tokens` (4.6) y `profundidad_mapa` (cuántos saltos de import alrededor del archivo entran al mapa; 0 = todo). |
| `servidor` | Cómo se lanza `llama-server` (ejecutable, GGUF, contexto, `--n-cpu-moe`, caché K/V…). `python frontend.py servidor --bat servidor.bat` genera el `.bat` **desde acá**, con el mismo muestreo y el puerto tomado de la `url`: el `.bat` deja de mantenerse a mano y no puede quedar descoordinado. |

Más abajo, en *Config: qué manda*, el detalle de por qué un cambio en `config.py` puede no verse. Una config guardada por una versión vieja se completa sola con los valores nuevos (`completar_config`); la clave vieja `temperatura` se ignora. Las variables de entorno (`AI_PERFIL`, `AI_API_URL`, `AI_MODELO`) siguen funcionando como valores por defecto si no hay configuración previa. ⚠️ Las API Keys se guardan en texto plano (ver deuda técnica).

**`.gitignore` recomendado:** `.ai_map.json` y `.ai_backups/` (la carpeta de backups ya incluye su propio `.gitignore`).

### 🖥️ Servidor de modelo de referencia (RTX 4060 8 GB)

Configuración con la que se llegó a ~28 t/s con `Qwen3.6-35B-A3B` (MoE, Q4_K_XL) en `llama-server`. Ya no se escribe a mano: sale de `config.py` (`SERVIDOR_DEFECTO` + `MUESTREO_DEFECTO`) con `python frontend.py servidor`:

```
llama-server.exe -m "%MODEL_PATH%" -np 1 -fa on -c 65536 -ctk q8_0 -ctv q8_0 ^
  --jinja --port 8081 --temp 0.3 --top-p 0.95 --top-k 20 --min-p 0.0 ^
  --repeat-penalty 1.0 --presence-penalty 0.0 --no-reasoning-preserve ^
  --n-gpu-layers 999 --n-cpu-moe 34 --load-mode none
```

* **`--repeat-penalty` en 1.0 a propósito.** Con 1.1 se vio un modelo escribir `returntotal(items)` (sin el espacio) en un REPLACE aunque su razonamiento decía `return total`. Una penalización mayor a 1 castiga repetir tokens que ya están en el contexto, y el código es repetitivo: puede comerse espacios o desviar un SEARCH que tiene que ser una copia exacta. Es una hipótesis (no se aisló con un A/B), pero para un editor SEARCH/REPLACE no hay motivo para arriesgarlo; los bucles los cortan las guardas de la 4.6.
* En un MoE, `--n-gpu-layers 999` + `--n-cpu-moe N` manda a CPU los expertos de las primeras N capas y deja el resto en GPU. Se baja N de a 2 hasta quedar cerca de 7 GB de VRAM usada.
* El KV cache cuantizado (`-ctk/-ctv q8_0`) requiere `-fa on`.
* Un modelo **denso** (no MoE) se configura distinto: ahí se ajusta `-ngl`, no `--n-cpu-moe`.

## 🏗️ Arquitectura actual

| **Archivo** | **Rol** | 
|---|---|
| `nina/agente.py`, `nina/ejecutor.py`, `nina/gitutil.py` | Fase 6 y 4.4: planificador y lazo del agente, ejecutor de comandos con lista blanca (sin shell) y checkpoints Git. Ver *El agente* |
| `nina/recomendaciones.py` | Fases 5.4 y 5.7: consejos del sidebar y resumen de impactos |
| `generar_mapa.py`, `frontend.py` | Entradas de compatibilidad (3 líneas): delegan en `nina.vigia` y `nina.cli`. Los comandos de siempre siguen funcionando. | 
| `web.py` | Interfaz Streamlit (solo presentación). Conectada al `gestor_config` y al motor multi-archivo. | 
| `nina/config.py` | Constantes, configuración base, Prompt Global (incluye las reglas de razonamiento) y `GestorConfig` persistente. | 
| `nina/proyecto.py` | `Proyecto(raiz)`: rutas, mapa, convenciones, backups por lote transaccional y Git. | 
| `nina/contexto.py` | Modelos (`Propuesta`, `CambioArchivo`), mapa filtrado por vecindad, inyección dinámica de dependencias, auto-healing y reintentos baratos. | 
| `nina/grafo.py` | Resolución real de imports (Java, Python, JS/TS, HTML) y vecindad de un archivo a N saltos. | 
| `nina/servidor.py` | Arma el comando / `.bat` de `llama-server` desde el perfil de `config.py`. | 
| `nina/bloques.py` | Parser tolerante multi-archivo y aplicador de SEARCH/REPLACE. | 
| `nina/llm.py` | Cliente LLM por streaming con patrón de Drivers (`Ollama`, `LlamaCpp`), muestreo del perfil y guardas anti-bucle (`CorteLLM`). | 
| `nina/trazas.py` | Análisis de stack traces (archivo, línea y otros archivos del proyecto). | 
| `nina/diff.py` | Diff puramente funcional. | 
| `nina/mapa.py` | Extractores del mapa v3: firmas, imports, atributos, endpoints, componentes, hooks… (tree-sitter + regex). | 
| `nina/vigia.py` | Watcher incremental (watchdog). | 
| `nina/cli.py` | CLI (typer + rich). | 
| `tests/` | Suite de pruebas unitarias y de integración (152 tests; la web se prueba con un Streamlit simulado en `fake_streamlit.py`). | 

**Regla de capas:** el núcleo (todo `nina/` salvo `cli.py` y `vigia.py`) no importa `typer`, `rich`, `streamlit` ni `watchdog`; un test lo vigila.

### 1. El Vigía (`nina/mapa.py` + `nina/vigia.py`)

* **tree-sitter** para Java, JS/JSX, TS/TSX y Python; HTML por regex.
* **Mapa v3** (`"version": 3`). Por archivo guarda las firmas y los imports de siempre y, solo cuando existen:

| Clave | Backend | Frontend |
|---|---|---|
| `fields` | atributos de clase con tipo y anotaciones (`User: @Id private Long id`), constantes de enum, campos de `dataclass`/`self.x` de Python | props de `interface`/`type` (`Props: onPick?: (id: number) => void`), atributos de clase TS |
| `endpoints` | `GET /api/users/{id}` (Spring: prefijo de clase + mapping del método; FastAPI/Flask: decoradores) | — |
| `exports`, `components` | — | lo que exporta el archivo y los componentes con sus props (`UserCard({ user, onPick }: Props)`) |
| `hooks`, `renders`, `api` | — | hooks usados (`useState`, `useUsers`), componentes que renderiza y URLs que consume (`/api/users/`) |

  Un mapa v2 sigue funcionando (esas claves simplemente no están); para enriquecerlo, `python generar_mapa.py --una-vez`.
* **Watcher incremental con debounce (0,5 s).**

#### Qué parte del mapa recibe la IA
* **Archivo existente:** solo su **vecindad**: los archivos a hasta `profundidad_mapa` saltos de import (2 por defecto; los que importa y los que lo importan), más lo que la instrucción nombra o comparte varios identificadores. Cada bloque se rotula (`[lo importa]`, `[lo usa]`, `[a 2 saltos]`) y los más cercanos van primero. Los imports se resuelven de verdad (`nina/grafo.py`: relativos, alias `@/`, `index`, paquetes Java, módulos Python relativos), no por nombre de archivo.
* **Archivo nuevo, vacío o Modo Global:** el **árbol entero**: los más relevantes con todo su detalle y el resto como lista de rutas, para que la IA vea dónde encaja lo nuevo.
* Presupuesto: `PRESUPUESTO_MAPA_CHARS` (8000) para el detalle y `PRESUPUESTO_ARBOL_CHARS` (3000) para la lista de rutas; máximo 30 archivos con detalle.

### 2. El Ejecutor Stateless (`nina/contexto.py`, `bloques.py`, `llm.py`, `cli.py`)

Cada ejecución arranca de cero. Los intentos tienen guardas (Fase 4.6): `llm.stream_llm` corta la conexión si el razonamiento repite las mismas líneas (`detectar_bucle`), si supera `max_tokens_pensamiento` o si el servidor termina con `finish_reason=length`, y lanza `CorteLLM`. Un corte **no es un error fatal**: `generar_cambio` reintenta pidiendo al modelo que no razone (`chat_template_kwargs.enable_thinking=false` en llama.cpp, `think=false` en Ollama) y agrega un aviso al último mensaje del usuario. En un reintento por error de bloques, la respuesta rechazada solo se reenvía si pesa menos de `reenviar_respuesta_hasta_tokens` (64 000) **y** entra en el contexto del servidor; si no, se reemplaza por un aviso corto. Utiliza drivers específicos para inyectar configuraciones críticas. Modifica múltiples archivos a la vez en memoria, con validación de modelo de datos (`Propuesta.es_valida`) y auto-healing (`AI_MAX_INTENTOS`, 5 por defecto) por archivo. Si la IA necesita leer un archivo que no estaba seleccionado, lo inyecta al vuelo leyendo el disco.

`generar_cambio(ctx, proyecto, llamar, on_reintento)`: `proyecto` es opcional (`None` = no leer otros archivos del disco); `on_reintento(n, errores)` se llama después de cada intento rechazado.

### 3. La interfaz web (`web.py`)

* Selector de proyecto y archivos (con Modo Global). **Cambiar de archivo descarta** la propuesta en pantalla.
* Diff **lado a lado** sincronizado (apilable por lote de archivos), renderizado en un iframe aislado para que el scroll sincronizado y el salto al primer cambio funcionen siempre.
* **Razonamiento y métricas persistentes:** todo lo que se ve durante la generación se guarda por intento en `session_state` (`ss.traza`) y la página se redibuja desde ahí al terminar. El razonamiento sigue visible después de guardar, descartar o tocar cualquier opción. Si cortás con "Stop", queda lo que había llegado.
* **Reintentos visibles:** cada intento muestra su razonamiento y su respuesta (la rechazada, marcada) y hay una tabla de métricas por intento (latencia, tiempo de pensamiento, tiempo de código, tokens, velocidad).
* **Autoscroll:** sigue el final del bloque en vivo solo mientras se genera; si subís con la rueda se suelta y aparece "↓ Seguir al final". Cronómetros en vivo.
* **Contador de tokens del prompt:** debajo del cuadro de la instrucción, `Prompt ≈ N tokens · instrucción ~X + contexto ~Y`. El contexto (sistema + convenciones + mapa + archivo) lo calcula Python; el JS solo suma lo que escribís. Se calibra solo con los tokens reales que informa el servidor. (Antes se dibujaba dentro del `textarea` y el contenedor lo recortaba.)
* **Tokens de pensamiento y de código por separado** (métricas y tabla por intento). Si el servidor informa `reasoning_tokens` son exactos; si no, se reparten en proporción a los caracteres y se marcan con `~`. Un intento cortado muestra una estimación.
* **Archivo nuevo:** se muestra solo el archivo creado (con número de líneas y botón de copiar), sin la ventana de cambios con el "antes" vacío. Los archivos modificados siguen con el diff lado a lado.
* **Botones de copiar:** el código de cada archivo propuesto y, si el lote tiene más de un archivo, todos juntos con un encabezado de comentario por archivo.
* **Barra lateral:** proyecto, archivo, **Recomendaciones** (5.7) y tu bloque de servidor/perfiles. La web **no** edita el modelo, el muestreo ni los límites: esos valores se cambian en `~/.nina/config.json` (o con `frontend.py servidor`), por decisión de diseño.
* **Impactos del lote (5.4):** sobre los diffs, una línea con ✏️ modificado / 🆕 nuevo / ⚠️ con errores / ⏸ sin cambios por archivo.
* Control de API Keys y perfiles de servidor.
* Restauración transaccional (Deshacer Lote), disponible también en Modo Global.
* Fusión a tres vías al guardar si el archivo cambió en disco mientras la IA generaba.

## 🤖 El agente (Fase 6, v1)

```
python frontend.py agente "Creá una API con /health y su prueba" -C D:\mi_proyecto --solo-planificar   # 1) solo el plan
python frontend.py agente -C D:\mi_proyecto                                                            # 2) ejecuta el plan guardado (reanuda)
```

1. **Planifica:** el modelo devuelve un JSON de tareas chicas (`titulo`, `instruccion`, `archivos`, `verificacion`) que se guarda en `.nina/plan.json`. Podés editarlo a mano antes de ejecutar.
2. **Aceptación (el objetivo, no solo las tareas):** el plan puede traer `aceptacion`: comandos que corren **en orden al final** sobre el proyecto terminado, con `espera` (texto que debe aparecer en la salida). Si fallan, el agente crea tareas de reparación (`A01`, `A02`…, hasta 2 rondas) con la salida del fallo. El planificador las propone, pero **lo ideal es que las escribas vos** en `.nina/plan.json`: son tu contrato de qué significa "terminado". Ejemplo: `"aceptacion": [{"comando": "python cli.py agregar pan"}, {"comando": "python cli.py listar", "espera": "pan"}]`.
3. **Por tarea:** checkpoint Git → prompt nuevo (stateless) con convenciones, mapa y **los archivos de la tarea completos** → bloques SEARCH/REPLACE → escritura con backup (`validar_ruta_segura`: nada fuera del proyecto) → verificación.
4. **Verificación:** cada comando corre por `nina/ejecutor.py`: lista blanca (`python`, `pytest`, `npm`, `node`, `mvn`, `git` sin `push`/`reset`/`clean`…), **sin shell** (no hay `&&`, pipes ni redirecciones), con timeout y salida recortada. `python`/`python3` se ejecutan con el intérprete que corre NINA. Si falla, la salida se manda al modelo para reparar (`--reparaciones`, 3 por defecto); si no se logra, `git reset --hard` al checkpoint y la tarea queda `fallida`.
5. **Frenos:** se detiene tras 2 tareas fallidas seguidas o al agotar `--horas` (4). Lo hecho no se repite al reanudar.
6. **Registro:** `.nina/plan.json` (estado por tarea), `.nina/eventos.jsonl` (cada paso con hora) y los commits `nina: antes de Txx …` / `nina: Txx …` en `git log`. La carpeta `.nina` está oculta para la vista de archivos de algunos editores: usá `dir .nina -Force`.

**Límites conocidos (honestos):**
* Si `pytest` no encuentra pruebas (código 5), la tarea pasa pero queda marcada `⚠ pytest no encontró pruebas`: no estaba verificada. (En la prueba real, el plan dejó las pruebas para la tarea siguiente y el agente reparó en vano 3 veces antes de que el modelo escribiera un test por su cuenta.)
* La verificación es tan buena como el comando que escribe el planificador. Si el `python -c` del plan tiene un `SyntaxError` propio, se **omite** y queda anotado en `detalle` (`⚠ verificación omitida`). Y si el mismo modelo escribe el código y sus pruebas, que pasen no garantiza que estén bien: conviene leerlas.
* Windows: el comillado de `python -c "…"` se resuelve en `ejecutor.dividir_comando` (un bug de esto hizo que las verificaciones pasaran en vacío en la v2.5/2.5.1; corregido en la 2.5.2).
* **Qué puede escribir el agente:** código (`.py .js .ts .java .html .css…`) y archivos de proyecto (`.json .md .txt .toml .cfg .ini .yml .yaml`, `Dockerfile`, `.gitignore`). Nunca scripts ejecutables, `.env`, `.ai_map.json` ni `.nina/`.
* Probado con Qwen3.6-35B-A3B real: calculadora (2 tareas), reparación por verificación fallida, **revert** de una tarea imposible (el archivo desaparece y el árbol vuelve al checkpoint) y una app de 3 módulos con pruebas y aceptación de 4 comandos (la aceptación pasó completa; ~11 min en total). En esa última, el planificador debilitó la tarea del CLI ("sin ejecutar la lógica") y el `cli.py` resultante **no hacía nada** aunque las 12 pruebas pasaban: de ahí la aceptación a nivel objetivo y que cada tarea reciba el objetivo general.
* Falta: replanificación cuando una tarea cambia el plan, replanificación cuando una tarea cambia el plan, descubrir el comando de test del proyecto, planificador y ejecutor con modelos distintos, y probarlo en un proyecto de varios archivos.
* Probalo siempre primero en una carpeta aparte: hace commits y `reset --hard` dentro del proyecto indicado.

## ✅ Estado de implementación

**Hecho y funcionando**

* **Fase 0 (Cimientos):** transacciones atómicas, validación de sintaxis (tree-sitter), resiliencia a codificaciones y aislamiento del cwd.
* **Fase 4.1 (Edición multi-archivo y nuevos):** el parser agrupa cambios por rutas. Soporta creación de carpetas automáticas y escritura por lotes.
* **Fase 4.2 (Prompt Global):** interfaz liberada del archivo pivote. El motor infiere qué archivos querés tocar leyendo tu prompt e inyecta su código en el contexto.
* **Fase 4.3 (Fusión de tres vías) — en la web:** si el archivo cambió en disco, se reaplica la respuesta cruda sobre la versión fresca; si tocaste las mismas líneas, avisa el conflicto y no escribe. (El CLI se limita a no aplicar si el archivo cambió.)
* **Fase 5.1 (UX de generación):** razonamiento persistente y autoscroll anclado, verificados en un navegador real (Chromium + Streamlit 1.65).
* **Fase 4.5 (hecha):** resolución real de imports (`nina/grafo.py`, port de `resolverImports` de `RepomapGraph.jsx`) y mapa limitado a la vecindad de N saltos.
* **Fase 4.6 (hecha):** guardas anti-bucle, tope de tokens de salida y de razonamiento, reintento sin razonamiento, respuesta rechazada no reenviada si es grande, tokens de pensamiento/código separados y estadísticas de intentos cortados.
* **Config como fuente única:** muestreo, límites y servidor por perfil; `servidor --bat` genera el `.bat`.
* **Fase 4.4 (hecha, `nina/gitutil.py`):** `checkpoint` (commit de todo el árbol), `revertir_a`, `inicializar`, `crear_rama`. Lo usa el agente antes de cada tarea; si el proyecto no es un repo, degrada a no hacer nada. La UI web todavía no hace checkpoint al guardar (ahí ya existe el backup `.ai_backups/lote_*`).
* **Fase 5.2 (parcial):** el contador suma el contexto real y se calibra con el servidor; `servidor.contexto_del_servidor(url)` lee `n_ctx` de `/props`. Falta mostrarlo como barra en la web.
* **Fase 5.4 (hecha):** tiempos/tokens separados, métricas por intento, vista de archivo nuevo, copiar código y la lista de impactos del lote.
* **Fase 5.7 (hecha, `nina/recomendaciones.py`):** el sidebar sugiere crear `CONVENTIONS.md`, generar/regenerar el mapa y ajustar el tope de prompt.
* **Fase 6 (v1, `nina/agente.py` + `nina/ejecutor.py`):** ver la sección *El agente*. **Validado con Qwen3.6-35B-A3B real en un caso chico** (calculadora + pruebas: 2 tareas, ~1 min cada una, `pytest` con código 0 en ambas); el lazo de reparación y el revert con el modelo real **todavía no** se vieron, solo con un LLM falso.
* **Fase 5.5:** el Vigía en segundo plano (`iniciar_vigia_background`, tuyo) mantiene el mapa al día; falta que la web se redibuje sola ante un cambio externo.
* **Fase 5.3 (datos listos):** el mapa v3 ya trae atributos, endpoints, props y hooks; falta la interfaz de árbol y que `RepomapGraph.jsx` los muestre.

## 🧱 Deuda técnica detectada (arreglar antes de crecer)

1. **Ranking por nombre de archivo.** `_import_a_stem` colisiona (`index`, `utils`). Requiere el resolutor avanzado de dependencias (Fase 4.5).
2. ~~Sin tope contra bucles de razonamiento.~~ Resuelto en la Fase 4.6 (guardas en `llm.py`). Los umbrales por defecto (12 000 tokens de razonamiento, 10 repeticiones de una línea de 30+ caracteres) son una estimación: conviene ajustarlos con uso real.
3. **API Keys en texto plano** en `~/.nina/config.json`.
4. **Por verificar:** que Ollama respete `options.num_ctx` por `/v1`; en ese caso el driver podría no estar limitando el contexto.
5. ~~Reintentos caros.~~ Atenuado en la 4.6 (la respuesta grande ya no se reenvía y tras un corte se reintenta sin razonar). Primera prueba real (Qwen3.6-35B-A3B, crear un `page.tsx` de ~480 líneas): el intento 1 fue rechazado tras 368 s (8.2k tokens de salida) y el intento 2 tardó 658 s (14.7k tokens, de los cuales ~323 s fueron razonamiento). Total: 1025 s y 22.9k tokens para un archivo. Un reintento reenvía entero el intento rechazado (la entrada pasó de 1.3k a 8.6k tokens). Las estadísticas de `llama-server` llegan exactas (tokens de entrada y salida, t/s) y el razonamiento llega por `reasoning_content`.
6. **Falsos positivos del aviso de sintaxis.** tree-sitter-tsx marca error ante un `&` suelto en texto JSX (`<h2>A & B</h2>`), que TypeScript acepta. El aviso ahora indica la línea y aclara que puede ser un falso positivo; sigue siendo orientativo.
7. ~~Temperatura enviada por NINA.~~ Ahora es `muestreo.temperature` (0.3 por defecto en `config.py`; se edita en `config.json`) y es la que manda; el `--temp` del `.bat` solo vale como valor por defecto del servidor.
8. **Errores de reintento:** `generar_cambio` informa `{ruta: [errores]}` a `on_reintento`; la web y el CLI mostraban solo la ruta (corregido).
9. **NINA no detecta errores semánticos.** En una prueba real el modelo escribió `returntotal(items)` y NINA lo dio por válido: es una expresión Python sintácticamente correcta (tree-sitter no se queja) y el SEARCH coincidía. Idea para más adelante: un lint opcional (p. ej. `pyflakes`) sobre los archivos propuestos, o la auto-verificación de la Fase 6.
10. **Mapa v2 viejo.** Un `.ai_map.json` generado antes de esta versión no trae atributos/endpoints/hooks: volver a correr `python generar_mapa.py --una-vez` (el Vigía lo va completando archivo por archivo a medida que se guardan).
11. **Límite de la vecindad.** Con `profundidad_mapa` = 2, una dependencia a 3 saltos que la instrucción no nombra no llega a la IA. Se puede subir en `limites.profundidad_mapa` de `config.json` (o poner 0 para mandar todo) y sigue valiendo mencionar el archivo en la instrucción.
12. **Calidad de la verificación del agente** (ver *El agente*): comandos inválidos del planificador, pruebas escritas por el mismo modelo.
13. **Detector de bucles ajustado con un caso real.** Con 5 repeticiones/20 caracteres marcó como bucle un razonamiento que citaba `def promedio(items):` varias veces mientras armaba el bloque; ahora 10 repeticiones y 30+ caracteres.

---

## 🗺 ROADMAP

### Fase 4 — Más potencia

**4.4 Integración con Git** ✅ hecha en el agente (ver Estado). Queda: checkpoint opcional al guardar desde la web/CLI y un botón "volver al punto anterior".

**4.5 Resolución real de imports y vecindad** ✅ hecha (ver Estado). Queda: `tsconfig.json` paths (hoy solo `@/` y `~/`), y que `_import_a_stem` de `contexto.py` (modo `profundidad_mapa = 0`) use el resolutor.

**4.6 Guardas anti-bucle** ✅ hecha (ver Estado). Queda por validar con uso real: umbrales por defecto y que Ollama respete `think=false` y `options.num_ctx` por `/v1` (hoy podría ignorarlos).

### Fase 5 — Interfaz Avanzada y Visualización

**5.2 Cálculo y Barra de Tokens en Vivo** (parcial)
* Hecho: el contador suma instrucción + contexto y se calibra con el servidor.
* Falta detectar automáticamente el contexto máximo del modelo (hoy sale de `servidor.contexto` si el perfil lo tiene) y mostrarlo como barra.

**5.3 Árbol de Proyecto Enriquecido**
* Reemplazar visor estático por árbol colapsable interactivo.
* Que el árbol permita desplegar atributos, métodos, verbos HTTP (APIs), hooks y props: **los datos ya están en el mapa v3**; falta la interfaz (y que `RepomapGraph.jsx` los muestre). Descripciones por archivo: pendiente.

**5.4 Pantalla de Revisión y Métricas Detalladas** ✅ hecha (ver Estado).

**5.5 Watcher Reactivo**
* Conectar los eventos del Watcher (`vigia.py`) directamente a la interfaz web para que el sistema (archivos y contexto) se actualice solo al detectar un cambio en el disco externo.

**5.6 Vista previa de HTML (NUEVA)**
* Poder visualizar archivos `.html` (el actual y la propuesta de la IA) renderizados, además del diff.
* Renderizarlos en un iframe con `sandbox` (sin acceso a la página de NINA): el HTML lo escribe un modelo y no debe poder tocar la interfaz.

**5.7 Sección de recomendaciones en el sidebar** ✅ hecha (falta el consejo de "mencioná las rutas en la instrucción")
* Bloque al pie de la barra lateral con consejos para que el prompt rinda más, por ejemplo:
  * crear un archivo de convenciones (`CONVENTIONS.md`) en la raíz del proyecto;
  * mencionar en la instrucción las rutas de los archivos que debe usar o crear, y en qué carpeta;
  * otras buenas prácticas (instrucciones acotadas, un cambio por pedido, etc.).
* Idea a evaluar: que detecte si falta `CONVENTIONS.md` en el proyecto y lo sugiera.

### Fase 6 — Autonomía Delegada (Agent Loop)

**Hecho (v1):** planificador → tareas → ejecutor con checkpoints → verificación → reparación → registro de eventos (ver Estado). **Falta para "Zero-to-Hero":** archivo de especificación/contrato del producto, planificador con un modelo más chico (9B) y ejecutor 35B-A3B en secuencia, descubrimiento automático del comando de test del proyecto, tareas que dependen unas de otras con replanificación, consola en la UI, y pruebas largas reales.

* **Auto-verificación de Código:** hecha en la v1 (corre `pytest`/`npm test`/etc. tras generar cambios y repara con la salida). Falta ampliarla a más lenguajes y a comandos de lint.
* **Consola Integrada:** Capacidad de escribir y ejecutar código o comandos bash en una consola propia dentro de la interfaz.
* **Checklists para Procesos Largos:** Fragmentación de planes grandes en tareas atómicas. Literalmente dejar al sistema programando solo, resolviendo una checklist paso a paso.
* **Pruebas "Zero-to-Hero":** Validar la capacidad de NINA para crear y armar un proyecto completo y funcional desde la más absoluta nada.

> La Fase 6 depende de la 4.6: un agente que corre solo no puede quedar atrapado en un bucle de razonamiento.

### Fase 7 — Arquitectura y Despliegue

* **Gestión de Servidor Local:** (hecho: `frontend.py servidor [--bat|--ejecutar]` genera/lanza el comando desde la config, y `n_ctx` se lee de `/props`). Falta el botón en la interfaz y soporte para Ollama, sin depender de consolas externas.
* **Despliegue a la Nube (Cloud-Ready):** Documentar y adaptar la arquitectura para subir el sistema de forma segura (añadir capas de autenticación, control de multi-usuarios, Dockerización y restricciones de Path Traversal robustas para entornos VPS compartidos).

---

## ⚙️ Config: qué manda

`~/.nina/config.json` **manda sobre los valores por defecto de `config.py`**: `completar_config` solo agrega las claves que faltan. Si cambiás un default en `config.py` (p. ej. `"extra": ["--no-mmproj"]`) y no se refleja en `frontend.py servidor`, es porque tu `config.json` ya tiene esa clave: editá el JSON (o borrá la clave para que tome el default).

## 🧠 Reglas de razonamiento (prompt global)

`nina/config.py` incluye reglas para que el modelo no dude de sus propias soluciones y no se quede en bucle: analizar una sola vez, trazar el plan de forma lineal, escribir el bloque SEARCH/REPLACE de inmediato y, ante una instrucción ambigua, asumir la interpretación más lógica. Se agregaron porque Qwen en modo razonamiento se queda a menudo dando vueltas.

* Es una mitigación blanda: un modelo en modo razonamiento no siempre obedece instrucciones sobre su propio razonamiento. Conviene medir su efecto (longitud del razonamiento y cantidad de bucles con y sin las reglas) y no darlas por buenas sin esa comparación.
* Pendiente de decidir: pedir que el razonamiento empiece con una posible respuesta y que las verificaciones se hagan todas juntas.
* Para medirlo está `probar_reintento.py` (junto a `frontend.py`): crea un proyecto temporal, arruina a propósito el SEARCH del intento 1 y muestra, por intento, el razonamiento, la respuesta, los mensajes enviados y las estadísticas. Se corre con `python probar_reintento.py` o `python probar_reintento.py "otra instrucción"`; el resultado esperado es `es_valida=True · intentos=2` (3 si el intento 2 se corta por bucle y el 3.º va sin razonamiento; también es un resultado correcto).

## 📝 Notas de trabajo (de la versión anterior)

* ~~Al cambiar de archivo, que funcione como descartar/limpiar vista.~~ Hecho.
* ~~Si necesita reintentar varias veces, dividir las métricas según cada intento.~~ Hecho.
* Razonamiento: primero una posible respuesta y verificaciones todas juntas → ver *Reglas de razonamiento*.
* Historial detallado de cambios: `CHANGELOG.md`.
* Visualizar HTML → Fase 5.6.
* Recomendaciones en el sidebar → Fase 5.7.
