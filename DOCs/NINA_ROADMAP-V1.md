\# Documentación y Roadmap: Motor de Pair Programming Local 🚀



\*\*Filosofía del Proyecto:\*\* Herramienta de \*Coworking\* (Pair Programming) 100% local y stateless. Diseñada específicamente para maximizar la velocidad de inferencia, proteger la caché (para hardware con restricciones de VRAM como una RTX 4060 con 8GB) y dotar a la IA de conocimiento total del contexto del proyecto sin arrastrar historiales de chat masivos.



\## 🏗️ ARQUITECTURA ACTUAL (Lo que ya construimos ✅)



El sistema se compone de dos scripts principales de Python que trabajan en conjunto para resolver el problema de contexto en LLMs locales.



\### 1. El Vigía / Generador de AST (`generar\_mapa.py`)



Un proceso en segundo plano que mantiene a la IA informada sobre la arquitectura del proyecto sin gastar tokens.



\* \*\*Qué hace:\*\* Escanea el proyecto (ignorando carpetas pesadas como `node\_modules` o `target`), extrae las firmas de métodos, clases y componentes (usando Regex para Java, JS, TS, JSX) y las guarda en un archivo ligero llamado `.ai\_map.json`.



\* \*\*Watcher Integrado:\*\* Utiliza la librería `watchdog`. Se queda ejecutando en la terminal y re-escanea automáticamente solo cuando detecta que guardaste un archivo nuevo o modificado (`Ctrl+S` en tu editor).



\* \*\*Cómo ejecutarlo:\*\*

&#x20; Abrir una pestaña en la terminal en la raíz de tu proyecto y dejarlo corriendo:



&#x20; ```

&#x20; python "Ruta\\A\\Tus\\Scripts\\generar\_mapa.py"

&#x20; 

&#x20; ```



\### 2. El Ejecutor Stateless (`frontend.py`)



La herramienta CLI quirúrgica que invoca a la IA.



\* \*\*Qué hace:\*\* Toma la ruta de un archivo y un prompt de tu parte. Carga el código de ese archivo, le inyecta el `.ai\_map.json` actual, y lo envía al modelo Qwen local mediante \*streaming\* HTTP (formato OpenAI compatible con LM Studio/llama.cpp).



\* \*\*Stateless (Sin estado):\*\* Se ejecuta, hace el trabajo, y se cierra. No guarda historial de chat, previniendo el colapso de la memoria KV Cache de la GPU.



\* \*\*Inyección Automática:\*\* Captura la respuesta de la IA, extrae solo el código fuente (quitando el formato Markdown) y te pregunta por consola `(y/n)` si querés sobreescribir el archivo original.



\* \*\*Cómo ejecutarlo:\*\*

&#x20; En otra pestaña de tu terminal:



&#x20; ```

&#x20; python "Ruta\\A\\Tus\\Scripts\\frontend.py" "ruta\\al\\archivo.jsx" "Tu instrucción para la IA"

&#x20; 

&#x20; ```



\## 🛡️ FASE 2: Seguridad y Precisión (Próximos Pasos)



\### 1. Directivas Globales (Estilo Aider)



Evitar repetir instrucciones en la terminal. El modelo debe leer un archivo de reglas estáticas.



\* \*\*Implementación:\*\*



&#x20; 1. Crear un archivo `CONVENTIONS.md` en la raíz de cada proyecto.



&#x20; 2. Modificar `frontend.py` para que busque este archivo y concatene su contenido dentro del `"role": "system"`.



\* \*\*Prueba:\*\* Pedir un cambio sin especificar el lenguaje/framework en la terminal y verificar que el LLM utilice las reglas del `.md` (ej. usar siempre funciones flecha).



\### 2. Diffing Visual en la Terminal (Seguridad Anti-Alucinaciones)



Evitar que el script borre código útil si el LLM se equivoca y recorta funciones existentes.



\* \*\*Implementación:\*\*



&#x20; 1. Integrar la librería nativa `difflib` de Python.



&#x20; 2. Usar la librería `rich` para imprimir las diferencias: líneas eliminadas en rojo (`-`), líneas agregadas en verde (`+`).



&#x20; 3. Mostrar este diff \*antes\* del prompt `¿Querés sobreescribir el archivo original? \[y/N]`.



\* \*\*Prueba:\*\* Pedirle a la IA que cambie una sola variable. El diff en la consola debe mostrar únicamente esa línea cambiada, no el archivo entero.



\## 🧠 FASE 3: Optimización del Contexto



\### 3. Filtrado Quirúrgico del RepoMap



Actualmente se envía todo el `.ai\_map.json`. Para ahorrar tokens y acelerar la IA, hay que enviarle solo el mapa relevante.



\* \*\*Implementación:\*\*



&#x20; 1. En `frontend.py`, detectar la extensión del archivo objetivo.



&#x20; 2. Si es `.java`, filtrar el JSON y enviar solo las rutas que contengan `/backend`.



&#x20; 3. Si es `.jsx`/`.tsx`, enviar solo el mapa de `/frontend`.



\* \*\*Prueba:\*\* Verificar que el prompt enviado al editar un componente de UI no incluya las firmas de los repositorios de Spring Boot.



\### 4. Comando "Fix Error" Auto-guiado



Pasar de pedir modificaciones a delegar la lectura de logs de error.



\* \*\*Implementación:\*\*



&#x20; 1. Agregar un nuevo comando CLI: `python frontend.py fix "stack trace pegado de la consola"`.



&#x20; 2. Usar Expresiones Regulares para que el script detecte de qué archivo viene el error (ej: buscar `at com.lwt.backend...:45`).



&#x20; 3. Abrir ese archivo automáticamente y mandarlo al LLM con el error.



\## 🌐 FASE 4: Evolución Visual y Web



\### 5. Interfaz Gráfica Minimalista (Web UI)



Salir de la terminal hacia una UI más amigable, pero manteniendo la arquitectura stateless.



\* \*\*Implementación:\*\* Usar \*\*Streamlit\*\* (en Python) para armar una interfaz web local:



&#x20; \* Panel Izquierdo: Selector rápido de archivo.



&#x20; \* Centro: Input para la instrucción y botón de "Generar".



&#x20; \* Derecho: Visor de código con el Diff visual y botones de confirmación.



\### 6. Visualizador de Árbol Interactivo



Ver físicamente cómo el AST (Abstract Syntax Tree) entiende tu proyecto.



\* \*\*Implementación:\*\*



&#x20; 1. Crear una página simple en tu frontend.



&#x20; 2. Usar `React Flow` o `D3.js` para leer tu archivo `.ai\_map.json` en tiempo real.



&#x20; 3. Renderizar nodos interactivos (Controladores apuntando a Servicios).

