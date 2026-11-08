# Series 76 Technical Assistant — Propuesta de sistema

Nov 3, 2026 · @Samuel Castro Carmona

## Resumen y principios de diseño

El sistema trata las tablas del PDF como datos estructurados en PostgreSQL, no como texto para RAG. El agente LangChain solo consulta esos datos mediante tools tipadas, y la app dibuja los números directamente desde la salida de las tools. Así, ningún número que ve el usuario pasa por la redacción del LLM.

Principios que guían cada decisión:

1. **Los datos mandan, el LLM conversa.** El LLM interpreta la intención, elige la tool y redacta. Los números viajan en un campo estructurado (`products`) que sale de la base de datos.
2. **Determinismo donde importa.** La ingesta, el filtrado y el ranking son código Python con tests. El LLM no filtra ni ordena actuadores.
3. **Fallar con información.** "No existe", "existe pero sin datos", "ambiguo" y "ningún modelo cumple" son respuestas distintas, cada una con su tipo.
4. **Trazabilidad completa.** Cada dato apunta a su tabla y página de origen. Cada respuesta guarda sus tool calls para la pantalla de debug.
5. **Presupuesto como requisito.** Desarrollo con LLM gratuito o simulado; la key de OpenRouter solo para validación final.

Alcance de los datos: 7 tablas y 68 part numbers (11 + 16 + 11 weatherproof; 5 + 5 + 10 + 10 explosionproof), de los cuales 4 aparecen como N/A.

## Arquitectura general

Cuatro piezas: la app Android, un backend FastAPI con el agente, una instancia de PostgreSQL con dos áreas de datos y una ingesta que se ejecuta una sola vez. Todo el backend se levanta con `docker compose up`.

&#91;embedded content: arquitectura · app, backend, Postgres, LLM e ingesta\]

Las tools y el catálogo (resaltados) son el único camino hacia los números. El LLM recibe sus resultados, pero la app dibuja las specs desde `products`, que arma el backend.

## Modelo de datos e ingesta

Tres tablas normalizadas, porque el torque depende del tipo de servicio y la velocidad y las corrientes dependen de la frecuencia. Las tools leen las tres tablas con el ORM a través de un repositorio de consultas parametrizadas (ver `docs/adr/0003-tool-logic-in-python.md`).

```sql
CREATE TABLE actuators (
  part_number         TEXT PRIMARY KEY,   -- '763B00-11320000/A' (sin '*')
  part_key            TEXT UNIQUE,        -- normalizado: '763B0011320000A'
  series_size         SMALLINT,           -- 3 (CHECK 1..7)
  variant             TEXT,               -- 'B' (CHECK A|B|C|X|Y)
  enclosure           TEXT,               -- 'weatherproof' | 'explosionproof'
  certifications      TEXT[],             -- {CSA,CE,UKCA}; {CE,UKCA} en tamaños 6 y 7; {CSA} en XP
  csa_certified       BOOLEAN,            -- false para tamaños 6 y 7 (*)
  hazardous_locations TEXT[],             -- clases de las notas XP; NULL en weatherproof
  voltage_v           SMALLINT,           -- 24 | 110 | 220
  phase               SMALLINT,           -- 1 | 3 | NULL (exactamente en 24V)
  current_type        TEXT,               -- 'AC' | 'DC' | 'AC/DC'
  motor_power_w       INT,                -- NULL en filas N/A
  data_available      BOOLEAN,            -- false en filas N/A
  source_table        TEXT, source_page SMALLINT
);  -- + índice GIN pg_trgm sobre part_key

CREATE TABLE service_ratings (
  part_number     TEXT REFERENCES actuators ON DELETE CASCADE,
  service_type    TEXT,  -- 'on_off' | 'modulating' | 'unspecified' (trifásico)
  torque_inlb     INT, torque_nm INT, duty_cycle_pct SMALLINT,
  cycles_per_hour INT,   -- on_off y unspecified
  starts_per_hour INT,   -- modulating y unspecified
  PRIMARY KEY (part_number, service_type)
);

CREATE TABLE electrical_ratings (
  id              INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  part_number     TEXT REFERENCES actuators ON DELETE CASCADE,
  frequency_hz    SMALLINT,   -- 60 | 50 | NULL en 24V
  speed_s INT, fla_a NUMERIC(5,2), lra_a NUMERIC(5,2),
  UNIQUE NULLS NOT DISTINCT (part_number, frequency_hz)
);
```

Los conjuntos cerrados son `CHECK`, no tipos `ENUM` de Postgres. Las filas N/A solo existen en `actuators`: no tienen filas en `service_ratings` ni en `electrical_ratings`, así que un `LEFT JOIN` las devuelve con todas las specs a `NULL`. Ver `docs/adr/0001-catalog-schema.md`.

**Decisiones de modelado que conviene defender:**

- `NULL` significa "no publicado"; nunca se usa 0.
- El trifásico usa `service_type = 'unspecified'` porque la tabla no distingue on/off de modulante. El agente lo dice en lugar de asumir.
- Se filtra en la unidad que dio el usuario (in-lb o Nm), porque los Nm de la tabla están redondeados.
- `part_key` normalizado (sin guiones, espacios ni barra, en mayúsculas) permite encontrar "763b00 11320000 a".
- Se habilita `pg_trgm` para sugerir part numbers parecidos. `pgvector` no se usa para los números; se justifica en el README.

**Pipeline de ingesta (`python -m ingest.ingest {extract|build|load}`):**

El PDF de datos es un escaneo (cada página es una imagen), así que `pdfplumber` no extrae ninguna celda y un OCR local comete demasiados errores. La extracción se hace con visión y queda aislada en una etapa offline cuyo resultado se versiona. Ver `docs/adr/0002-vision-extraction.md`.

1. **Especificación declarativa por tabla** (`tables.yaml`): página, encapsulado, voltaje, fase, tipo de corriente, servicios presentes y mapeo de columnas. Son solo 7 tablas, así que esto es más robusto que inferir la estructura.
2. **`extract`** (manual, solo la ejecuta el humano, con red): `pdfplumber` rasteriza cada página a 300 dpi y un modelo de visión (`INGEST_LLM_PROVIDER` = `google` con Gemini en Google AI Studio, u `openrouter` con su propia clave `INGEST_OPENROUTER_API_KEY`; modelo en `INGEST_LLM_MODEL`, `temperature=0`) devuelve cada tabla en un esquema JSON estricto generado desde el layout. Las celdas se copian tal cual se imprimen; las celdas combinadas de Motor Power se devuelven como rangos de filas. Se ejecuta dos veces: ambas salidas se guardan en `data/extracted/<tabla>.json` y el diff celda a celda en `data/extracted/discrepancies.md`.
3. **`build`** (determinista, sin red): lee `data/extracted/` y `data/corrections.yaml` (correcciones revisadas por el humano contra el PDF; obligatorias para cada celda que difiere entre las dos ejecuciones). Limpia separadores de miles, asteriscos y letras en negrita partidas; propaga Motor Power sobre su rango; marca las filas `N/A`; deriva `series_size`, `variant` y `part_key` del part number.
4. **Validación** con Pydantic y chequeos cruzados: tipos, sufijo del part number según la tabla, variante según el encapsulado, 68 part numbers únicos, filas por tabla, filas N/A, in-lb vs Nm (factor 8,85, tolerancia 5 %, porque algunos pares impresos se desvían 2–4 %) y FLA ≤ LRA (hay filas impresas con FLA = LRA).
5. **Salida revisable:** `data/processed/actuators.csv` y `catalog.json`, versionados en el repo y reproducibles byte a byte.
6. **`load`:** carga idempotente en Postgres (upsert) desde `catalog.json`, ejecutada al arrancar con Docker Compose si la base está vacía. Docker y los tests nunca llaman al modelo de visión.

**Tests de ingesta:** 68 part numbers; 4 sin datos; 5 sin CSA; y unos 10 valores verificados a mano. Por ejemplo, `765Y30-11300000/A` da 25,900 in-lb on/off y `763C00-11330C00/A` da 5,300 in-lb.

## Agente LangChain

Un agente con tool calling (`create_agent` de LangChain sobre LangGraph) y tres tools con argumentos tipados en Pydantic. No se permite SQL generado por el LLM: cada tool ejecuta consultas parametrizadas.

| Tool | Entrada | Salida | Casos que resuelve |
| --- | --- | --- | --- |
| `get_actuator` | `part_number` (texto libre) | `status`: found · partial\_match · not\_found · no\_data; specs completas; sugerencias | Consulta puntual, normalización, "¿quisiste decir…?", consultas parciales como "763C" |
| `search_actuators` | enclosure, voltage\_v, phase, current\_type, service\_type, min\_torque + unidad, max\_speed\_s, min\_duty\_cycle\_pct, csa\_required, series\_size, frequency\_hz, limit | candidatos ordenados; si no hay, `blocking_filters` y `closest_alternatives` | Recomendación, requisitos imposibles, alternativas |
| `list_catalog_options` | campo opcional | valores disponibles y rangos (p. ej., torque máximo por encapsulado) | Ambigüedad, "¿qué voltajes hay?" |

**Lógica de `search_actuators` (Python, no LLM):**

La unidad de búsqueda es el par (actuador, tipo de servicio), porque el torque depende del servicio. Las filas N/A nunca coinciden. Ver `docs/adr/0003-tool-logic-in-python.md`.

1. Filtros duros: encapsulado, alimentación, servicio, certificación y tamaño. Una alimentación AC/DC sirve a peticiones AC y DC. Pedir `on_off` o `modulating` excluye el trifásico (`unspecified`), que siempre lleva el aviso `service_type_unspecified`.
2. Voltaje con tolerancia de ±10% (nota 3 de la tabla). Si el usuario tiene 230V, aplica la tabla de 220V y devuelve `tolerance_applied = true`.
3. Torque mínimo en la columna de la unidad pedida, sin convertir. Velocidad a la frecuencia pedida o, sin ella, la más lenta publicada. Orden por menor margen sobre lo requerido, luego por velocidad.
4. Sin resultados: relaja un filtro a la vez (primero torque, velocidad y duty; al final CSA y encapsulado) y devuelve `blocking_filters` y `closest_alternatives` marcadas con `meets_requirements = false` y los filtros que no cumplen. Si ningún filtro por sí solo desbloquea la búsqueda, busca la combinación más pequeña que sí lo haga (pares, luego tríos…) y lo marca con `multiple_filters_blocking = true`.

**System prompt (puntos clave):**

- Nunca escribir un número técnico que no venga de una tool en esta conversación.
- Si faltan voltaje, servicio o encapsulado para recomendar, preguntar con opciones concretas en lugar de asumir.
- Mencionar condiciones relevantes: sin certificación CSA, trifásico sin servicio especificado, 24V modulante solo DC, frecuencia 50/60 Hz.
- Responder en el idioma del usuario.

**Garantía de números, en tres capas:**

1. **Estructura:** el campo `products` de la respuesta se construye en el backend con la salida de las tools, no con el texto del LLM. La app dibuja las tarjetas desde ahí.
2. **Prompt:** el LLM tiene instrucciones de no citar números fuera de las tools.
3. **Verificación:** un validador extrae los números del `answer` y comprueba que existan en los resultados de tools del turno o de la sesión. Si alguno no aparece, se regenera una vez con un aviso; si persiste, se eliminan los números del texto y se remite a las tarjetas.

**Contexto entre turnos:** el historial que recibe el modelo es solo el texto de los últimos turnos; los resultados de tools no se reenvían. La sesión guarda `active_parts` (los últimos part numbers mostrados) y `shown_parts`, que se inyectan en el system prompt, así "¿y cuánto consume?" se resuelve llamando de nuevo a la tool sin repetir números del historial. "Consume" se responde con Motor Power (W) y la FLA como contexto.

**Middleware y límites:** prompt dinámico con los part numbers activos, registro de uso por llamada al modelo, `ModelCallLimitMiddleware` y `ToolCallLimitMiddleware` con `exit_behavior="error"`, registro de cada tool call (argumentos, resultado, latencia, error) con mensajes genéricos ante fallos, y `ModelFallbackMiddleware` si hay `FALLBACK_MODEL`. Los límites son por turno: el reintento del validador comparte el presupuesto. `response_type`, `products`, `clarification`, `highlight_fields`, `suggestions` y `warnings` se derivan en el backend de los resultados tipados de las tools. Ver `docs/adr/0004-agent-and-conversation-service.md`.

## API REST

La respuesta separa lo que ve el usuario (`answer`, `products`, `clarification`) de lo interno, que va en un endpoint de trazas aparte. Así el chat nunca recibe nombres de tools.

```json
POST /api/conversation
{ "session_id": "opcional", "query": "Necesito 1,500 Nm a 220V monofásico, modulante, weatherproof" }

200 OK
{
  "session_id": "7f3c…",
  "message_id": "a91e…",
  "answer": "Varios modelos cumplen. El de menor margen sobre lo pedido es…",
  "response_type": "recommendation",
  "products": [
    {
      "part_number": "765A00-11320000/A",
      "enclosure": "weatherproof",
      "power_supply": { "voltage_v": 220, "phase": 1, "current_type": "AC" },
      "certifications": ["CSA", "CE", "UKCA"],
      "service": [ { "type": "modulating", "torque": {"in_lb": 15000, "nm": 1700}, "duty_cycle_pct": 35, "starts_per_hour": 840 } ],
      "electrical": [ { "frequency_hz": 60, "speed_s": 88, "fla_a": 3.3, "lra_a": 3.8 }, { "frequency_hz": 50, "speed_s": 106, "fla_a": 3.2, "lra_a": 3.9 } ],
      "motor_power_w": 180,
      "notes": [],
      "source": { "table": "Weatherproof 220V 1ph", "page": 1 }
    }
  ],
  "highlight_fields": ["torque", "speed_s"],
  "clarification": null,
  "suggestions": [],
  "warnings": []
}
```

Cada producto lleva además `series_size`, `variant`, `csa_certified`, `hazardous_locations`, `data_available`, `matched_service` (el servicio con el que coincidió una búsqueda), `meets_requirements` (`false` en las alternativas más cercanas) y `unmet`. `warnings` resume avisos del turno: `tolerance_applied`, `service_type_unspecified`, `not_csa_certified`, `no_published_data`, `multiple_filters_blocking`, `numbers_removed` y `tool_error`.

`response_type` toma uno de estos valores: `detail`, `recommendation`, `comparison`, `clarification`, `no_match`, `not_found`, `no_data` o `error`. La app elige la vista según este campo. `clarification` lleva `{ question, options[] }` y la app lo muestra como chips.

| Método | Ruta | Auth | Uso |
| --- | --- | --- | --- |
| POST | `/api/auth/login` | pública | `{ email, password }` → `{ access_token, token_type: "bearer", user: { email, role } }` |
| GET | `/api/auth/me` | token | `{ email, role }` del usuario actual |
| POST | `/api/conversation` | token | Turno de chat (contrato fijo). Un `session_id` de otro usuario → 404 |
| GET | `/api/sessions` | token | Sesiones del usuario: id, título, fecha de actualización, número de mensajes |
| GET | `/api/sessions/{id}/messages` | token | Historial con `products` por mensaje, para reconstruir las tarjetas |
| DELETE | `/api/sessions/{id}` | token | Borrar conversación (extra útil en la app) |
| GET | `/api/messages/{id}/trace` | developer | Debug: tool calls, argumentos, resultados, modelo, tokens, costo, latencia, de cualquier mensaje |
| GET | `/health` | pública | Estado de la base de datos y configuración del LLM |

Cambio respecto a lo sugerido: la traza vive en `/api/messages/{id}/trace` y no dentro de la respuesta del chat. Esto mantiene liviano el payload del usuario. La ruta exige el rol `developer` (ver [ADR 0006](adr/0006-auth-and-roles.md)).

**Autenticación:** todas las rutas `/api/*` salvo el login exigen `Authorization: Bearer <token>`. El token es un JWT HS256 firmado con `JWT_SECRET` (claims `sub`, `role`, `exp`; dura `JWT_EXPIRE_MINUTES`, 30 por defecto). En cada petición se carga el usuario de la base, y el rol que vale es el guardado allí. Una sesión de otro usuario responde 404, igual que una inexistente, para no revelar que existe.

Errores con un formato único `{ "error": { "code", "message" } }`: 400 (`empty_query`, `query_too_long`, `invalid_request`), 401 (`invalid_credentials`, `not_authenticated`, `invalid_token`, `token_expired`, con `WWW-Authenticate: Bearer`), 403 (`forbidden`), 404 (`session_not_found`, `message_not_found`, `not_found`), 405 (`method_not_allowed`), 429 (`too_many_attempts`, con `Retry-After`, tras `LOGIN_MAX_ATTEMPTS` logins fallidos en `LOGIN_WINDOW_MINUTES` por IP y email), 503 (`llm_unavailable`) cuando el proveedor LLM falla y 503 (`budget_exceeded`) cuando se agotó el tope diario, y 500 (`internal_error`) sin detalles. Un fallo del LLM nunca deja la sesión a medias. `/health` es la excepción: es un informe de estado y devuelve su propio cuerpo también con 503 cuando la base no responde.

## Persistencia de conversaciones y trazas

Tablas propias en la misma instancia de Postgres, en lugar de un checkpointer opaco. Así la misma fuente sirve para reanudar el agente, listar sesiones en la app y alimentar el debug.

```sql
CREATE TABLE users (
  id UUID PRIMARY KEY, email TEXT UNIQUE,   -- en minúsculas
  password_hash TEXT,                       -- Argon2 (pwdlib)
  role TEXT,                                -- 'engineer' | 'developer'
  created_at TIMESTAMPTZ
);
CREATE TABLE sessions (
  id UUID PRIMARY KEY, user_id UUID NOT NULL REFERENCES users ON DELETE CASCADE,
  title TEXT, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ,
  state JSONB          -- { active_parts: [...], last_filters: {...} }
);
CREATE TABLE messages (
  id UUID PRIMARY KEY, session_id UUID REFERENCES sessions ON DELETE CASCADE,
  role TEXT,           -- 'user' | 'assistant'
  content TEXT,
  payload JSONB,       -- products, response_type, clarification, warnings; internal_error (solo traza)
  status TEXT,         -- 'ok' | 'error' (fallo del proveedor)
  created_at TIMESTAMPTZ
);
CREATE TABLE tool_calls (
  id UUID PRIMARY KEY, message_id UUID REFERENCES messages ON DELETE CASCADE,
  seq SMALLINT, tool_name TEXT, args JSONB, result JSONB,
  latency_ms INT, error TEXT
);
CREATE TABLE llm_usage (
  id INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  message_id UUID REFERENCES messages ON DELETE CASCADE, model TEXT,
  input_tokens INT, output_tokens INT, cost_usd NUMERIC(10,6)  -- NULL si el proveedor no lo informa
);
```

**Reconstrucción del contexto en cada turno:** se cargan los últimos N turnos (configurable con `HISTORY_TURNS`, por defecto 10) como texto, sin los turnos fallidos. Los resultados de tools no se reenvían; `state.active_parts` y `state.shown_parts` se inyectan en el system prompt para resolver referencias como "ese" o "el segundo". El validador numérico sí usa los resultados de tools guardados de toda la sesión.

**Título de la sesión:** se genera a partir de la primera consulta sin llamar al LLM (por ejemplo, el part number detectado o los primeros 60 caracteres). Ahorra una llamada por conversación.

**Consistencia:** el turno lee en una sesión corta, ejecuta el agente sin retener conexión y se guarda en una transacción al final (mensaje del usuario, tool calls, respuesta y uso). Si el LLM falla en una sesión existente, se guarda el mensaje del usuario con `status = 'error'` y su traza para que la app pueda reintentar; un reintento exitoso de la misma consulta reutiliza ese mensaje en lugar de duplicarlo; en una conversación nueva no se guarda nada, porque la respuesta 503 no puede devolver el `session_id`.

## Selección de modelos y presupuesto

Un solo modelo económico con buen tool calling para el agente basta, porque el trabajo pesado lo hace el código. No hacen falta embeddings.

| Tarea | Variable de entorno | Criterio de elección | Candidatos (verificar precio actual en OpenRouter) |
| --- | --- | --- | --- |
| Agente (tool calling + redacción) | `AGENT_MODEL` | Tool calling fiable, bajo costo por token, buen español | Familias tipo Gemini Flash, GPT mini o Claude Haiku |
| Respaldo si el principal falla | `FALLBACK_MODEL` | Proveedor distinto | Otro de la lista anterior |
| Extracción por visión del PDF escaneado (solo desarrollo, la ejecuta el humano) | `INGEST_LLM_PROVIDER`, `INGEST_LLM_MODEL` | Visión con salida estructurada; se ejecuta pocas veces y su salida se versiona | Gemini vía Google AI Studio (gratis, 20 peticiones/día por modelo) u OpenRouter con clave propia |
| Transcripción de voz (opcional) | — | Sin costo | Reconocimiento de voz del dispositivo |

Decisión: no se usan embeddings ni pgvector. Los datos son 68 filas con campos tipados, y la búsqueda por filtros es exacta y explicable. pgvector solo tendría sentido para el texto del summary, que cabe completo en el prompt.

**Control del gasto:**

- `LLM_PROVIDER=openrouter|google|fake`. El modo `fake` responde con un guion fijo y llama tools reales: sirve para tests y CI sin gastar.
- `max_tokens` acotado (1500 por defecto, ver ADR 0005) y temperatura 0.
- Historial limitado y resultados de tools compactos (solo los campos necesarios).
- Registro de costo por mensaje en `llm_usage` y un tope diario configurable (`DAILY_BUDGET_USD`, sin valor = sin tope). Antes de llamar al modelo se suma `cost_usd` del día UTC; si ya alcanzó el tope, el turno responde 503 `budget_exceeded` y no guarda nada. El costo que el proveedor no informa cuenta como cero, y borrar una conversación borra también su uso (limitación aceptada: es un freno de gasto, no contabilidad).
- Prompt caching del proveedor cuando esté disponible, con el system prompt estable al inicio.
- Un set de unas 30 preguntas de evaluación que se ejecuta con la key real solo 2 o 3 veces en total. Costo estimado: menos de USD 1 (estimación propia; depende del modelo elegido).

## App móvil

Android con Kotlin y Jetpack Compose. El emulador corre en Windows, macOS y Linux, lo que facilita que el evaluador reproduzca la app.

**Arquitectura:** MVVM con un solo módulo. Retrofit + kotlinx.serialization para la API, ViewModels con `StateFlow<UiState>` (`Loading`, `Content`, `Error`), inyección manual (un `AppContainer` en la clase `Application`; con pocas pantallas no hace falta Hilt) y Navigation Compose con rutas tipadas. La URL del backend se configura en `local.properties` (`API_BASE_URL`, por defecto `http://10.0.2.2:8000`).

**Sesión** ([ADR 0007](adr/0007-app-session-storage.md)): un `SessionManager` de ámbito de aplicación expone un único estado (`Checking`, `SignedOut`, `SignedIn(user)`, `VerifyFailed(error)`) y la raíz de la UI muestra una pantalla por estado. Al abrir la app, un token guardado se valida con `GET /api/auth/me` antes de entrar: un 401 lo descarta y lleva al Login, y cualquier otro fallo ofrece Reintentar o Cerrar sesión. Un interceptor de OkHttp añade `Authorization: Bearer` (salvo en el login) y, ante cualquier 401, cierra la sesión. Cada estado con pantallas tiene su propio `ViewModelStore`, que se borra al salir de él: tras cerrar sesión no queda en memoria nada del usuario anterior ni pila a la que volver. El token, el email y el rol se guardan cifrados con DataStore + Tink (clave en Android Keystore) y se excluyen de los backups; la contraseña no se guarda. La entrada al Debug dependerá solo de `user.isDeveloper`.

| Pantalla | Contenido | Estados de carga y error |
| --- | --- | --- |
| Conversaciones | Lista con título, fecha relativa y número de mensajes (el título ya son los primeros caracteres de la primera consulta); botón "Nueva conversación"; deslizar para borrar, con diálogo de confirmación; pull to refresh | Skeleton al cargar; vacío con llamada a la acción; error con "Reintentar"; si falla un refresco o un borrado, la lista sigue visible y un snackbar ofrece "Reintentar" |
| Chat | Burbujas; tarjetas de producto; tabla comparativa; chips de aclaración; chips de sugerencias | Indicador "escribiendo"; mensaje fallido con reintento; banner sin conexión |
| Debug | Por cada respuesta: tool calls en orden, argumentos y resultado en JSON plegable, modelo, tokens, costo y latencia | Carga bajo demanda; solo para el rol developer (403 para engineer) |
| Login | Email y contraseña con mostrar/ocultar; botón deshabilitado mientras carga. Menú de cuenta en Conversaciones con el email y "Cerrar sesión" | Error de credenciales (401); demasiados intentos (429); sin conexión o servidor caído; un 401 en cualquier pantalla lleva al login |
| Verificación | Al abrir con token guardado: indicador mientras se valida con `/me` | Si falla por red: "No se pudo verificar tu sesión" con Reintentar y Cerrar sesión |

**Cómo se muestran los productos, según `response_type`:**

- `detail`: una tarjeta con encabezado (part number, encapsulado y alimentación como chips), una cuadrícula de specs con unidades y una tabla pequeña 60 Hz / 50 Hz.
- `recommendation` o `comparison`: tabla comparativa con la columna de specs fija y una columna por modelo con scroll horizontal. Se resaltan los campos de `highlight_fields` y el mejor valor de cada fila.
- `no_match` o `not_found`: tarjeta de aviso con las alternativas como chips que envían la consulta al tocarlos.
- Avisos como "Sin certificación CSA" se muestran como badges en la tarjeta.

**Errores en la app:** el repositorio convierte cada fallo en un `AppError` y la UI muestra un texto fijo por caso, nunca el `message` del backend: sin conexión (el dispositivo no tiene red), el servidor no respondió a tiempo, no se pudo conectar (servidor caído), error del servidor (otro 5xx), asistente no disponible (503 `llm_unavailable`) y límite diario alcanzado (503 `budget_exceeded`). Todos ofrecen "Reintentar". Un 4xx no lo ofrece, porque repetir la misma petición fallaría igual. Un reintento reenvía la misma consulta a la misma sesión. Si el fallo fue `llm_unavailable`, el backend reutiliza el mensaje guardado con `status = 'error'`. Tras un timeout o una conexión cortada, el backend puede haber terminado y guardado el turno igualmente, así que en una sesión existente la app relee el historial antes de reenviar y, si el turno ya está, lo muestra en lugar de duplicarlo. En una conversación nueva no hay `session_id` que consultar: el reintento reenvía y, en el peor caso, crea una segunda sesión.

**Fixtures:** los JSON de `android/app/src/test/resources/` se generan desde el API real con `make android-fixtures` (LLM fake, base desechable) y alimentan los tests y los `@Preview`. Un test del backend falla si quedan desactualizados.

El modelo redacta el `answer` en Markdown y la burbuja lo renderiza (negrita, listas, encabezados) con `multiplatform-markdown-renderer`. El texto del `answer` nunca lleva tablas; los números se leen desde `products`. Esto cumple el requisito de no mostrar nombres de tools ni detalles internos.

**Acceso al debug:** mantener presionada una respuesta del asistente abre su traza. Esta opción solo existe si el modo desarrollador está activo: se activa tocando 7 veces el título de la pantalla de Conversaciones, y en builds `debug` viene activo por defecto. Se documenta en el README.

## Casos borde y pruebas

Cada caso borde tiene un `response_type` esperado y un test automatizado. La mayoría se prueba sin LLM, directamente contra las tools.

| Entrada de ejemplo | Situación | Comportamiento esperado | `response_type` |
| --- | --- | --- | --- |
| `761A00-11380000/A` | Formato válido, no existe (es el ejemplo del summary) | Decir que no existe y sugerir parecidos | `not_found` |
| `761a00 11300000 a` | Formato informal | Normalizar y encontrar `761A00-11300000/A` | `detail` |
| "763C" | Coincide con 3 modelos (110V, 220V, trifásico) | Preguntar con las 3 opciones | `clarification` |
| `762A00-11330C00/A` | Listado como N/A | Existe, pero sin datos publicados | `no_data` |
| "24V AC modulante" | El modulante de 24V es solo DC | Indicarlo; ofrecer 24V DC modulante o 24V AC/DC on/off | `no_match` |
| "Explosionproof de 30,000 in-lb" | Máximo disponible: 25,900 in-lb | Indicar el máximo y la alternativa más cercana | `no_match` |
| "Tamaño 7 con CSA" | Tamaños 6 y 7 no tienen CSA | Explicar el conflicto | `no_match` |
| "Tengo 230V" | Dentro de 220V ±10% | Usar la tabla de 220V y decirlo | `recommendation` |
| "¿Y cuánto consume?" tras una consulta | Seguimiento | Usar el part number activo; potencia del motor y FLA | `detail` |
| "Compara 764A y 764B a 110V" | Comparación | Tabla comparativa | `comparison` |
| "Recomiéndame uno" | Faltan requisitos | Pedir voltaje, servicio, torque y encapsulado | `clarification` |
| "¿Cuál es el precio?" | Dato que no está en las tablas | Decir que no está disponible | `detail` o `no_data` |
| Fuera de tema o inyección de prompt | — | Sin llamar a tools: rechazo breve y redirección al catálogo | `clarification` |

**Niveles de prueba:**

1. **Ingesta** (pytest, sin LLM): 68 filas, valores puntuales verificados a mano, N/A y CSA.
2. **Tools** (pytest + Postgres en Docker): normalización, coincidencias parciales, filtros, tolerancia de voltaje, relajación de filtros.
3. **API** (pytest + `LLM_PROVIDER=fake`): contrato, sesiones, persistencia tras reinicio y formato de errores.
4. **Evaluación del agente** (`eval/cases.jsonl`, unas 36 preguntas generadas desde `catalog.json` más casos manuales; `eval/run_eval.py` por la API): se miden la tool elegida, sus argumentos, los part numbers esperados, el `response_type`, que todos los números del `answer` existan en los resultados, la correferencia en seguimientos, la latencia p95 y el costo por turno. 20 casos `fast` corren gratis (`make eval-fast`); la suite completa con la key real requiere `--confirm-paid` y se ejecuta pocas veces por el presupuesto.
5. **App:** tests de ViewModel con un repositorio falso y una prueba manual de los flujos principales.

## Opcionales, repositorio, plan y riesgos

**Opcionales recomendados** (bajo costo, buena impresión):

- **Login:** implementado en el backend (ADR 0006). JWT firmado con `JWT_SECRET`, contraseñas con Argon2, 3 usuarios precargados por un seed (`python -m app.db.seed`) y documentados en el README. Los roles son `engineer` y `developer`, y solo el developer ve las trazas. `sessions.user_id` filtra todas las consultas. No hay modo sin autenticación. La app tiene pantalla de login y guarda la sesión cifrada (ADR 0007).
- **Voz:** `SpeechRecognizer` de Android en el dispositivo. El texto transcrito se muestra editable antes de enviarlo. No gasta presupuesto ni requiere cambios en el backend.

**Estructura del repositorio:**

```
/backend
  app/ (api, agent, tools, db, services, schemas)
  ingest/ (ingest.py, tables.yaml, extract.py, build.py, validate.py, load.py)
  data/raw/  data/extracted/  data/corrections.yaml  data/processed/
  tests/  eval/
  Dockerfile  pyproject.toml
/android
/.claude/          configuración del asistente, como pide la prueba
docker-compose.yml  .env.example  README.md
```

`docker compose up` levanta Postgres, ejecuta migraciones e ingesta si la base está vacía, y arranca FastAPI en el puerto 8000.

**Plan de trabajo (7 días):**

1. Esquema, `tables.yaml`, ingesta y tests de ingesta.
2. Tools y tests de tools con Postgres en Docker.
3. Agente, endpoint de conversación, persistencia y trazas, con `LLM_PROVIDER=fake` y Google.
4. Casos borde, validador de números y primera ejecución de la evaluación con OpenRouter.
5. App: Conversaciones y Chat con tarjetas.
6. App: tabla comparativa, Debug, estados de error; login y voz si hay tiempo.
7. README, prueba desde un clon limpio y evaluación final.

**Riesgos y mitigación:**

| Riesgo | Mitigación |
| --- | --- |
| Agotar los USD 10 | Modo `fake`, desarrollo con Google AI Studio, tope diario, evaluación con pocas ejecuciones |
| Errores de extracción del PDF | Especificación por tabla, CSV revisable, tests con valores verificados y diff con extracción por LLM |
| El LLM cita números incorrectos | `products` desde el backend y validador de números |
| El evaluador no logra reproducir | Un solo comando con Docker, ingesta automática y prueba desde un clon limpio |
| El modelo elige mal la tool | Descripciones de tools precisas, ejemplos en el prompt y evaluación del agente |

**Preguntas probables en la entrevista:** por qué no RAG; por qué tablas normalizadas; cómo se garantiza que no se inventen números; qué pasa con el trifásico sin tipo de servicio; cómo escalaría a miles de productos (índices, más tablas en `tables.yaml`, y pgvector solo para texto libre).
