# Changelog de NINA

Cambios sobre `feature/fase-4` (commit a564866 "QoL"). Todo el código está cubierto por `pytest` (134 pruebas, 1 xfail conocido).

## 2.5.4 — 2026-10-16
- `plan.json` editado a mano con BOM (PowerShell, Notepad) ya se lee.

## 2.5.3 — 2026-10-16
- **Agente:** `pytest` con código 5 (sin pruebas) ya no cuenta como fallo ni dispara reparaciones: en la prueba real el plan separaba código (T01) y pruebas (T02), y el agente gastó 4 intentos (~7 min) hasta que el modelo, por su cuenta, escribió un test. Ahora la tarea pasa con la advertencia `pytest no encontró pruebas` en `detalle` y el evento `verificacion_sin_pruebas`. El planificador recibe la regla de crear el test en la misma tarea que el código.
- Primera corrida real del lazo de reparación (Qwen3.6-35B-A3B): funcionó, con la verificación real (los códigos 5 prueban que ya no pasa en vacío).

## 2.5.2 — 2026-10-16
- **CRÍTICO (Windows): las verificaciones del agente pasaban en vacío.** `shlex` no-POSIX conserva las comillas, así que `python -c "..."` le llegaba a Python con comillas literales (un string suelto: no ejecuta nada y sale con 0). Ahora `ejecutor.dividir_comando` las quita. Las tareas T01–T03 de tu primera corrida con éxito **no estaban realmente verificadas**: volver a correrlas.
- **Agente:** si el `python -c` del plan tiene un `SyntaxError` propio (p. ej. `try:` en una línea), se omite esa verificación y queda anotado en `detalle` y en los eventos (reparar el código no lo arregla); el planificador recibe la instrucción de no usar sentencias compuestas en `-c`.
- **Pruebas:** la de migración de config ya no fija `reenviar_respuesta_hasta_tokens`; nuevas pruebas de regresión del comillado en Windows. 141 passed, 1 xfailed.

## 2.5.1 — 2026-10-16 (correcciones tras la primera prueba real en Windows)
- **Agente:** el modelo no veía los archivos que la tarea iba a modificar (el mapa estaba desactualizado y no hay Vigía en modo headless), así que mandaba `SEARCH` vacío sobre un archivo existente y las tareas fallaban. Ahora cada tarea lleva `archivos` (los pide el planificador), se mandan completos (`preparar_contexto(..., incluir=)`), los archivos escritos se incorporan al mapa y el reintento avisa que debe editar con SEARCH/REPLACE.
- **Agente/ejecutor:** el plan usaba `python3`, que no existe en Windows; ahora `python`/`python3` se ejecutan con el intérprete que corre NINA y el planificador recibe la indicación de usar `python`. `permitidos` acepta nombres con `.exe`.
- **Anti-bucle:** falso positivo real (`def promedio(items):` citado 5 veces mientras el modelo redactaba el bloque). Umbrales por defecto: 10 repeticiones y líneas de 30+ caracteres.
- **Pruebas:** las de config/servidor ya no dependen de los valores por defecto de `config.py` (fallaban si cambiabas temperatura); nuevas pruebas de regresión. 138 passed, 1 xfailed.

## 2.5 — 2026-10-15

### Corregido en el código que ya tenías
- **`nina/config.py`: `SISTEMA_BASE`** — las "REGLAS ESTRICTAS DE RAZONAMIENTO" quedaban fuera del string (el modelo nunca las recibía). Ahora están dentro.
- **Contador de tokens de la web**: no sumaba el archivo ni el mapa; ahora `prompt = instrucción + contexto`, calibrado con el factor real tokens/caracteres del servidor.
- **`repeat_penalty`** por defecto en 1.0 (y `min_p` 0.0): con 1.1 se vio `returntotal(items)` (hipótesis, sin A/B).
- `MAX_INTENTOS` queda en 5 (tu cambio).

### Fase 4.6 – guardas anti-bucle (`nina/llm.py`, `nina/contexto.py`)
- `CorteLLM(motivo)`: `limite_pensamiento` | `bucle` | `longitud`. `detectar_bucle()` corta razonamientos que repiten líneas.
- Tras un corte se reintenta con `razonar=False` y un aviso en el último mensaje de usuario; la respuesta rechazada solo se reenvía si es chica y entra en el contexto (`debe_reenviar`).

### Config como fuente única (`nina/config.py`, `nina/servidor.py`)
- Por perfil: `muestreo`, `limites` (incl. `profundidad_mapa`), `servidor`. `completar_config` migra configs viejas.
- `frontend.py servidor [--bat|--ejecutar]` genera/lanza `llama-server`. **La web no edita modelo/muestreo/límites** (pedido expreso).

### Mapa v3 y vecindad (`nina/mapa.py`, `nina/grafo.py`, `nina/contexto.py`)
- Extracción de atributos, endpoints, props, hooks y llamadas a API. `resolver_imports` y `vecindad` (Fase 4.5). Archivo existente → solo vecinos a N saltos; archivo nuevo/Modo Global → árbol entero.
- `RepomapGraph.jsx` muestra los conteos de atributos/endpoints/hooks.

### Web (`web.py`)
- Tokens de pensamiento y de código por separado; manejo de intentos cortados; botones de copiar (por archivo y todos juntos); archivo nuevo mostrado sin panel "antes".
- **Fase 5.4**: lista de impactos del lote. **Fase 5.7**: expander "Recomendaciones".
- Quitado: el bloque de la barra lateral de muestreo/límites/profundidad/comando de servidor.

### Fase 4.4 – Git (`nina/gitutil.py`) · Fase 6 – agente (`nina/agente.py`, `nina/ejecutor.py`) · Fase 5.2 (`servidor.contexto_del_servidor`)
- Ver README. Comando nuevo: `frontend.py agente`. Estado de las tareas en `.nina/plan.json`, eventos en `.nina/eventos.jsonl`.

### Pruebas
- Nuevas: `test_antibucle.py`, `test_mapa_vecindad.py`, `test_agente.py`; ampliados `test_web_smoke.py`, `fake_streamlit.py`, `test_nucleo.py`, `test_cli.py`.

### Pendiente / no hecho
- 5.3 (árbol interactivo), 5.5 (redibujado automático), 5.6 (vista previa HTML con `sandbox`), 7 (botón de servidor, Docker, auth multiusuario).
- El agente no se probó con el modelo real ni en proyectos grandes.
