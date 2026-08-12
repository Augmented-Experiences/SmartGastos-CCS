# Plan de corrección y validación — Pyme Ledger AI

## Alcance y línea base

La revisión inicial confirmó que el plugin procesa documentos mediante una aplicación FastAPI con interfaz autocontenida para Pinokio. La suite actual arroja **252 pruebas aprobadas y 2 fallidas**; estas últimas corresponden a la validación de tipos MIME de archivos maliciosos y se incorporan al alcance de corrección para no degradar la seguridad existente.

Los tres conjuntos documentales proporcionados contienen PDF, imágenes, mensajes `.eml` y planillas `.xlsx`. La interfaz de carga actual admite solamente PDF e imágenes. La validación final debe demostrar una importación controlada de cada ZIP: se extraerán los soportes permitidos y las planillas se utilizarán para contrastar el número y los totales de gastos cuando sea posible. Los archivos `.eml` y `.xlsx` no se deben presentar engañosamente como formatos de factura compatibles hasta que exista un importador específico.

## Criterios de aceptación

| ID | Requisito verificable | Estado inicial | Evidencia final esperada |
|---|---|---|---|
| AC-01 | Dos empresas creadas sin RUT reciben identificadores internos diferentes; ninguna altera a la otra. | Fallido: el RUT centinela `00.000.000-0` es único y dispara actualización. | Prueba de API y respuesta con `created=true` para cada empresa. |
| AC-02 | Solo se aceptan empresas chilenas, RUT chileno opcional y moneda base CLP. | Fallido: API e interfaz permiten países y monedas extra. | Pruebas de validación HTTP 422 y controles UI limitados a Chile/CLP. |
| AC-03 | Ledger AI permite crear empresa, crear categoría y consultar gasto individual o agregado, con confirmación para mutaciones. | Parcial: solo responde texto/gráficos. | Pruebas de comandos estructurados y acciones visibles en la conversación. |
| AC-04 | “Empresa activa” abre un selector/resumen de contexto, mientras “Administración” conserva la gestión completa. | Fallido: ambos disparan `renderAdmin`. | Prueba estática/UI y navegación funcional diferenciada. |
| AC-05 | Las anomalías se basan en un algoritmo local de aprendizaje no supervisado y devuelven puntuación, método y razones. | Fallido: existen solo reglas fijas. | Pruebas con datos reales/sintéticos deterministas y respuesta de alertas enriquecida. |
| AC-06 | Una segunda carga del mismo archivo se identifica antes del procesamiento costoso y no crea ni duplica el gasto contable. | Parcial: detección tardía y estado inconsistente. | Pruebas de hash, respuesta 409/resultado duplicado y una sola fila contable. |
| AC-07 | Dashboard y Documentos reflejan duplicados históricos y potenciales sin incluir su importe dos veces en gastos válidos. | Fallido: depende de un estado que no se persiste. | Pruebas de KPIs, alerta y filtro de duplicados. |
| AC-08 | Documentos explica Pendiente, Aprobado, Rechazado y Duplicado, y muestra categoría asignada/sin asignar de manera inequívoca. | Parcial: faltan leyenda y filtro de duplicados. | Pruebas de interfaz y de serialización API. |
| AC-09 | Recomendaciones emplea giro, régimen tributario, deducibilidad y regla IVA de categoría. | Fallido: esos campos casi no participan. | Pruebas de recomendación tributaria diferenciada. |
| AC-10 | ProPyme y General aplican reglas explícitas y auditables, sin afirmar determinaciones tributarias definitivas. | Ausente. | Pruebas de matriz de reglas y explicación en la respuesta. |
| AC-11 | Métricas de agentes registran llamadas, tokens de entrada/salida, latencia, costo cloud de referencia y ahorro local. | Parcial: una ruta de Ollama solo cuenta llamadas. | Pruebas de contabilización para ambas rutas y panel UI detallado. |
| AC-12 | Los tres ZIP se ejecutan por un flujo de importación de lote reproducible y todos los archivos admitidos se procesan o quedan documentados como no compatibles. | No validado. | Informe automático por ZIP, archivo y resultado, más contraste con planillas. |

## Estrategia de pruebas

Las pruebas unitarias cubrirán validadores, identificación de empresa, reglas ProPyme/General, deduplicación, detección de anomalías y contabilidad de uso de IA. Las pruebas de integración cubrirán los endpoints FastAPI, el pipeline de carga y el dashboard. Se añadirá una validación estática de la interfaz para las restricciones Chile/CLP, la navegación separada y las etiquetas de estado.

Las pruebas de ingestión de los ZIP utilizarán exclusivamente los documentos reales entregados. No se sustituirán por datos inventados. Para impedir que un OCR de baja calidad produzca falsos negativos, el criterio será que cada archivo admisible llegue a un resultado persistido o a un resultado de duplicado explícito, con trazabilidad por archivo. Las planillas solo se usarán como control de referencia y no como fuente para alterar resultados extraídos.

## Decisiones de diseño previstas

La identificación de empresas sin RUT pasará a ser un identificador interno único y no un valor centinela compartido. El sistema conservará el RUT nulo y normalizará una clave de deduplicación solo cuando exista un RUT válido. La deduplicación se hará por SHA-256 antes de arrancar el pipeline; los duplicados quedarán registrados como eventos/alertas sin duplicar los gastos contabilizados.

El análisis de anomalías combinará Isolation Forest local, cuando exista una muestra suficiente, con un fallback estadístico robusto basado en mediana y MAD. Su salida incluirá método, puntuación, severidad y factores del caso. Las reglas contables de categoría se consolidarán en una capa dedicada que use `regla_iva`, deducibilidad, giro y régimen; se etiquetarán como orientación operativa y requerirán revisión humana para decisiones tributarias.
