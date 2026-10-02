\# Documentación y Roadmap v2: Motor de Pair Programming Local 🚀



\*\*Filosofía del proyecto:\*\* herramienta de \*coworking\* (pair programming) 100% local y stateless. Pensada para maximizar la velocidad de inferencia y proteger la caché en hardware con poca VRAM (RTX 4060 de 8 GB), dándole a la IA el contexto justo del proyecto sin arrastrar historiales de chat.



\*\*Principio rector de esta versión:\*\* antes de agregar funciones, volver \*seguro y confiable\* lo que ya existe. Nada se escribe en disco sin que veas el diff y sin que haya un backup.



\---



\## 📦 Instalación



```bash

pip install watchdog httpx typer rich tree-sitter tree-sitter-java tree-sitter-javascript tree-sitter-typescript

pip install pyperclip   # opcional, solo para `fix --portapapeles`

```



Si `tree-sitter` no está instalado, `generar\_mapa.py` cae automáticamente a regex (menos preciso) y avisa por consola.



\*\*Variables de entorno opcionales\*\* (evitan editar el script):



| Variable | Default | Uso |

|---|---|---|

| `AI\_API\_URL` | `http://localhost:8081/v1/chat/completions` | Endpoint del servidor (LM Studio = 1234, llama.cpp = 8080, Koboldcpp = 5001) |

| `AI\_MODELO` | `Qwen3.6-35B-A3B-UD-Q4\_K\_XL` | Nombre del modelo |

| `AI\_MAX\_TOKENS\_PROMPT` | `12000` | Si el prompt estimado lo supera, pide confirmación antes de enviar |



\*\*Recomendación:\*\* agregar `.ai\_map.json` y `.ai\_backups/` al `.gitignore` del proyecto (la carpeta de backups ya incluye su propio `.gitignore`).



\---



\## 🏗️ ARQUITECTURA ACTUAL ✅



\### 1. El Vigía (`generar\_mapa.py`)



Mantiene a la IA informada sobre la arquitectura sin gastar tokens.



\* \*\*Parser real con tree-sitter\*\* (Java, JS, JSX, TS, TSX) en lugar de regex. Maneja genéricos, anotaciones multilínea, arrow functions, clases anidadas, `React.memo`/`forwardRef`, interfaces y tipos de TypeScript.

\* \*\*Firmas completas\*\*, no solo nombres. Por ejemplo, para Spring se ve `@Transactional(readOnly = true) public User findById(Long id)`.

\* \*\*Imports por archivo\*\*, base del filtrado por relevancia.

\* \*\*Watcher incremental con debounce:\*\* al guardar un archivo solo se re-parsea ese archivo (antes se re-escaneaba todo el proyecto). Detecta también archivos creados, borrados y renombrados, e ignora carpetas pesadas (`node\_modules`, `target`, etc.), que antes disparaban re-escaneos.

\* \*\*Escritura atómica\*\* de `.ai\_map.json`: el ejecutor nunca lee un JSON a medias.



```bash

python generar\_mapa.py             # escanea y queda vigilando

python generar\_mapa.py --una-vez   # escanea una vez y termina

python generar\_mapa.py ruta/proyecto

```



\*\*Formato del mapa (v2):\*\*



```json

{

&#x20; "version": 2,

&#x20; "files": {

&#x20;   "backend/src/service/UserService.java": {

&#x20;     "signatures": \["@Service public class UserService", "  public List<User> findAll()"],

&#x20;     "imports": \["com.lwt.backend.repo.UserRepository"]

&#x20;   }

&#x20; }

}

```



\### 2. El Ejecutor Stateless (`frontend.py`)



CLI quirúrgica con tres comandos. Cada ejecución arranca de cero, sin historial.



```bash

python frontend.py refactor "ruta/archivo.jsx" "Tu instrucción"

python frontend.py fix "stack trace"        # o sin argumento: pegás el trace; o -p para portapapeles

python frontend.py deshacer "ruta/archivo.jsx"

```



Flujo de `refactor`:



1\. Lee el archivo (preservando CRLF/LF para no ensuciar `git diff`).

2\. Arma el prompt: reglas base + `CONVENTIONS.md` + mapa \*\*filtrado por relevancia\*\* + código.

3\. Estima tokens y pide confirmación si se pasa del límite.

4\. Envía al modelo por streaming y recibe \*\*bloques SEARCH/REPLACE\*\*.

5\. Aplica los bloques en memoria (todo o nada) y muestra el \*\*diff\*\* en rojo/verde.

6\. Avisa si el archivo se achica de forma sospechosa (posible recorte por alucinación).

7\. Pregunta `¿Aplicar estos cambios? \[y/N]` (por defecto, \*\*no\*\*).

8\. Si aceptás: \*\*backup\*\* en `.ai\_backups/` y recién ahí escribe.



\---



\## 🛡️ FASE 1 y 2: Seguridad y Precisión ✅ (implementadas)



\### Backups automáticos + `deshacer`

Cada escritura guarda antes una copia con timestamp en `.ai\_backups/`. `frontend.py deshacer archivo` muestra el diff contra el último backup y restaura (respaldando también el estado actual, así que el deshacer es reversible).



\### Diff visual con `rich`

Basado en `difflib.unified\_diff`: líneas eliminadas en rojo, agregadas en verde, más contador `+N -M`. Se muestra siempre antes de la confirmación.



\### Directivas globales (`CONVENTIONS.md`)

Si existe `CONVENTIONS.md` en la raíz del proyecto, su contenido se concatena al mensaje `system`. Plantilla sugerida:



```markdown

\# Convenciones del proyecto

\- Frontend: React funcional con hooks. Siempre funciones flecha, nunca clases.

\- Estilos: Tailwind; no CSS inline.

\- Backend: Spring Boot, inyección por constructor (no @Autowired en campos).

\- Nombres en inglés para código, comentarios en español.

\- No agregar dependencias nuevas sin que se pida explícitamente.

```



\### Salida en formato SEARCH/REPLACE

En lugar de pedir el archivo completo y extraerlo del Markdown:



```

<<<<<<< SEARCH

const total = items.length

=======

const total = items.filter(i => i.activo).length

>>>>>>> REPLACE

```



Ventajas: gasta muchos menos tokens de salida, el diff es trivial y es casi imposible que el modelo "pierda" código que no tocó.



\* El SEARCH debe coincidir una sola vez; si hay 0 o más de 1 coincidencia, \*\*no se aplica nada\*\* y se informa cuál bloque falló.

\* Hay un reintento tolerante a espacios al final de línea.

\* Si el modelo ignora el formato y devuelve el archivo completo en un bloque ```, se acepta como fallback con un aviso explícito. Si no hay ni bloques ni código, \*\*no se escribe nada\*\* (antes se guardaba el texto crudo).



\### Detalles pensados para tu hardware

\* `cache\_prompt: true` en el payload: llama.cpp reutiliza el prefijo del prompt entre ejecuciones.

\* El mensaje `system` ordena primero lo estático (reglas + convenciones) y después lo variable (mapa), para maximizar ese reuso.

\* El mapa se envía como \*\*texto compacto\*\*, no JSON indentado (menos tokens).



\---



\## 🧠 FASE 3: Optimización del Contexto ✅ (implementada)



\### Filtrado del mapa por relevancia

Reemplaza el filtro grueso por extensión/carpeta. Cada archivo del mapa recibe un puntaje respecto al archivo objetivo:



| Criterio | Puntos |

|---|---|

| El archivo objetivo lo importa | +5 |

| El archivo importa al objetivo | +4 |

| Su nombre aparece en la instrucción | +3 |

| Identificadores en común con la instrucción | hasta +3 |

| Está en la misma carpeta | +1 |



Se envían los de mayor puntaje hasta un presupuesto de \~6000 caracteres (máx. 30 archivos). La consola muestra `mapa 4/120 archivos` para que veas cuánto contexto se está usando.



\### Comando `fix` auto-guiado

Extrae del stack trace los pares `archivo:línea` (Java, Node y URLs del navegador), los resuelve contra el mapa, ignora `node\_modules` y librerías, toma el primer archivo del proyecto, le adjunta el fragmento alrededor de la línea fallida y pide un arreglo mínimo. Opciones: `--archivo/-a` para forzar el archivo, `--portapapeles/-p` para leer el trace copiado.



> En Windows pegar un trace multilínea como argumento entre comillas suele romperse. Por eso `fix` sin argumento lee por stdin (terminás con `Ctrl+Z` + `Enter`), o usá `-p`.



\---



\## ⚠️ Limitaciones conocidas



\* \*\*La relevancia es heurística.\*\* Resuelve imports por nombre de archivo, no por resolución real de módulos. Puede fallar con alias raros, archivos `index.js` o clases con el mismo nombre en distintos paquetes.

\* \*\*Edición de un solo archivo.\*\* Un cambio que toca controller + service + componente requiere varias ejecuciones.

\* \*\*`fix` usa el primer archivo del proyecto del trace\*\*, que no siempre es la causa raíz; el resto se lista como referencia.

\* \*\*Estimación de tokens aproximada\*\* (caracteres / 3).

\* \*\*API de tree-sitter:\*\* el código está escrito para `tree-sitter` ≥ 0.22. Con versiones viejas cae a regex.



\---



\## 🗺️ ROADMAP PENDIENTE



\### Fase 4: Más potencia (próximo)

1\. \*\*Integración con git:\*\* commit o stash automático previo en vez de (o además de) `.ai\_backups/`.

2\. \*\*Edición multi-archivo:\*\* un plan en dos pasos (la IA propone qué archivos tocar, después se edita cada uno con su diff y confirmación individual).

3\. \*\*Reintento automático:\*\* si un bloque SEARCH falla, reenviar al modelo el error y el archivo para que corrija solo ese bloque.

4\. \*\*Resolución real de imports:\*\* respetar `tsconfig` paths, `index.js` y paquetes Java para mejorar el ranking.

5\. \*\*Tests básicos\*\* de `aplicar\_bloques`, `filtrar\_mapa` y el parser de trazas (son funciones puras, fáciles de testear).



\### Fase 5: Evolución visual (opcional)

Estas fases no mejoran la calidad de las respuestas, por eso quedan al final.



\* \*\*Web UI con Streamlit:\*\* selector de archivo, input de instrucción y visor de diff con botones de confirmación, manteniendo la arquitectura stateless.

\* \*\*Visualizador de árbol interactivo:\*\* React Flow o D3.js leyendo `.ai\_map.json` (ahora incluye imports, así que se pueden dibujar dependencias reales entre archivos).



\---



\## ✅ Checklist de pruebas manuales



\* \[ ] Pedir un cambio de una sola variable: el diff muestra únicamente esa línea.

\* \[ ] Pedir un cambio sin mencionar framework: respeta `CONVENTIONS.md`.

\* \[ ] Editar un `.jsx`: el mapa enviado no incluye firmas de Spring Boot irrelevantes.

\* \[ ] Rechazar con `n`: el archivo queda intacto y no se crea backup.

\* \[ ] Aceptar y luego `deshacer`: el archivo vuelve al estado anterior.

\* \[ ] Pegar un stack trace de Java y otro de Node: `fix` detecta el archivo correcto.

\* \[ ] Apagar el servidor del modelo: el error es claro, sin traceback.

