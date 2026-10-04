# CONVENTIONS.md

## Reglas Generales
- **Idioma:** Todo el código, nombres de variables, métodos, clases y rutas debe escribirse en **inglés**.
- **Comentarios:** Las explicaciones, documentación (Javadocs/JSDocs) y comentarios internos deben escribirse en **español**.
- **Simplicidad:** Aplicá el principio KISS. Evitá sobreingeniería. No agregues dependencias nuevas al `package.json` o `pom.xml` a menos que se solicite explícitamente.
- **Formato:** Modificá estrictamente lo que se pide. Respetá la indentación actual del archivo.

## Backend (Java / Spring Boot)
- **Inyección de Dependencias:** Usá siempre inyección por constructor. Prohibido usar `@Autowired` en los campos (field injection).
- **Arquitectura REST:** Los controladores deben devolver `ResponseEntity<?>` y manejar los códigos de estado HTTP correctamente (200, 201, 400, 404).
- **Microservicios:** Si se interactúa con RabbitMQ o Eureka, mantené las colas y los nombres de los servicios tipados de forma segura (usá constantes, no strings mágicos).
- **Persistencia:** Usá Spring Data JPA. Evitá consultas N+1 utilizando `@EntityGraph` o consultas JPQL/Native explícitas cuando haya relaciones `OneToMany` o `ManyToMany`.
- **Estructura:** Separá la lógica en capas claras: `Controller` -> `Service` -> `Repository`. Usá DTOs para exponer datos; no expongas las Entidades de base de datos directamente al cliente.

## Frontend (Next.js / React)
- **Componentes:** Usá siempre componentes funcionales y Hooks. Prohibido usar componentes de clase.
- **Next.js:** Priorizá el App Router y los Server Components (`app/`). Usá la directiva `"use client"` únicamente cuando el componente requiera estado (`useState`), efectos (`useEffect`) o interactividad del DOM.
- **Estilos:** Usá **Tailwind CSS** para todo el estilizado. Prohibido usar CSS inline (`style={{...}}`) o archivos `.css` tradicionales a menos que sea estrictamente necesario para animaciones complejas.
- **Tipado:** Si el archivo es TypeScript (`.ts` o `.tsx`), definí interfaces claras para las props y respuestas de API. No uses `any`.
- **Estructura:** Extraé componentes reutilizables (botones, tarjetas, modales) a la carpeta `components/`. Mantení las páginas (`page.tsx`) limpias, delegando la carga visual a los componentes.