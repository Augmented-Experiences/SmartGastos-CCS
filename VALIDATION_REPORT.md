# Informe final de corrección y validación — Pyme Ledger AI

**Fecha de validación:** 12 de agosto de 2026  
**Autor:** Manus AI  
**Alcance:** correcciones funcionales, controles Chile/CLP, IA local, deduplicación, interfaz y procesamiento de los tres ZIP entregados.

## Resultado ejecutivo

La implementación queda orientada exclusivamente a empresas de **Chile** y documentos contabilizados en **CLP**. El RUT ahora es opcional y no se usa un identificador compartido para representar su ausencia. Cada nueva empresa sin RUT recibe un UUID propio, eliminando el riesgo de sobrescritura de la empresa activa.

La carga documental bloquea duplicados exactos por SHA-256 **antes** de ejecutar OCR o clasificación. Los intentos se registran y aparecen en el dashboard, pero no se agregan a los montos contables. La interfaz explica los estados de revisión y distingue visualmente una categoría asignada de una pendiente. También se incorporó una importación ZIP segura, con informe por archivo admitido, duplicado, error o formato no compatible.

| Área | Resultado validado |
|---|---|
| Suite automatizada completa | **265 pruebas aprobadas** |
| Pruebas de aceptación nuevas | **10 pruebas aprobadas** |
| Validación de sintaxis de interfaz | JavaScript embebido validado por Node.js |
| ZIP `LlamaTalksTecWeek.zip` | **10/10** soportes admitidos procesados; 1 XLSX informado como no compatible |
| ZIP `LLaMATechBuenosAires-ARSAT.zip` | **6/6** soportes admitidos procesados; 1 EML y 1 XLSX informados como no compatibles |
| ZIP `Montevideo2025.zip` | **17/17** soportes admitidos procesados; 1 ZIP anidado y 2 XLSX informados como no compatibles |
| Errores en soportes admitidos | **0** |
| Valores `Infinity` o `NaN` persistidos | **0** |

## Cambios funcionales implementados

| Requisito | Implementación y comportamiento final |
|---|---|
| Empresa sin RUT | `rut` permite `NULL`; la búsqueda de una empresa existente se hace solo con RUT informado. La migración convierte el antiguo valor centinela a `NULL` y separa giro de régimen. |
| Solo Chile y CLP | API e interfaz restringen país a Chile, moneda base a CLP y moneda de documento a CLP. Los documentos que declaran otra moneda quedan **Pendientes** con una alerta de conversión/revisión. |
| Ledger AI accionable | Acciones estructuradas y confirmables para crear empresa, crear categoría, buscar gastos y sumar gastos válidos. Las mutaciones no se delegan al LLM. |
| Empresa activa y administración | “Empresa activa” muestra y permite cambiar el contexto de trabajo. “Administración” conserva empresas, categorías y reglas. |
| Anomalías locales | `IsolationForest` local se usa con muestras suficientes; mediana y MAD conforman el fallback determinista para conjuntos pequeños. Las alertas incluyen método, puntuación y razones. |
| Duplicados | SHA-256 se calcula inmediatamente después de validar la firma del archivo. El intento se registra y el dashboard cuenta duplicados bloqueados. |
| Estados y categorías | Documentos explica Pendiente, Aprobado, Rechazado y Duplicado. Cada tarjeta muestra “Asignada” o “Por clasificar”. |
| Recomendaciones | Incorporan giro, régimen, deducibilidad, regla IVA y tipo de documento mediante una capa de reglas trazable. |
| ProPyme y General | El contexto operativo diferencia ProPyme de General. Las recomendaciones mantienen un aviso de revisión humana y no generan determinaciones tributarias automáticas. |
| Métricas de IA | Se registran llamadas, tokens de entrada/salida reales de Ollama cuando existen, latencia, costo cloud de referencia y ahorro local estimado. |
| Seguridad de carga | Se valida extensión, MIME y firma binaria antes de procesar PDF e imágenes. Los ZIP rechazan rutas inseguras, exceso de archivos o tamaño descomprimido excesivo. |

## Validación con archivos reales

Los tres ZIP se procesaron mediante el endpoint de importación por lotes contra una base aislada. El informe JSON `VALIDATION_SUPPLIED_ZIPS.json` conserva el detalle de cada soporte procesado. Los XLSX, EML y ZIP anidados quedaron explícitamente informados como formatos no compatibles por el importador documental actual; no se presentaron de forma engañosa como facturas procesadas.

> Los documentos no CLP se mantienen como registros pendientes y no como gastos aprobados. El operador debe verificar una conversión y el respaldo antes de aprobarlos.

| ZIP | Admitidos | Procesados | Duplicados | Errores | Excluidos con motivo |
|---|---:|---:|---:|---:|---|
| LlamaTalksTecWeek | 10 | 10 | 0 | 0 | 1 XLSX |
| LLaMATechBuenosAires-ARSAT | 6 | 6 | 0 | 0 | 1 EML, 1 XLSX |
| Montevideo2025 | 17 | 17 | 0 | 0 | 1 ZIP anidado, 2 XLSX |
| **Total** | **33** | **33** | **0** | **0** | **6 archivos informados** |

## Consideraciones de operación

Las reglas ProPyme y General se exponen como orientación operacional, no como una liquidación tributaria. El SII describe ProPyme General como un régimen dirigido a Pymes que, como regla general, considera ingresos percibidos y gastos pagados; el régimen General aplica las reglas generales de renta líquida imponible. El propio SII señala que sus respuestas son referenciales y deben complementarse con los antecedentes de cada contribuyente y las instrucciones vigentes. [1] [2]

Los modelos de OCR y clasificación actúan localmente. Un documento con baja confianza, datos faltantes, moneda extranjera o importe no finito queda pendiente de revisión, preservando trazabilidad y evitando que una extracción incierta se apruebe automáticamente.

## Archivos principales añadidos

| Archivo | Propósito |
|---|---|
| `server/analytics/anomaly_detector.py` | Detección de anomalías con Isolation Forest local y fallback MAD. |
| `server/tax_rules.py` | Reglas operativas ProPyme/General, IVA, deducibilidad y giro. |
| `tests/test_chile_acceptance.py` | Pruebas de aceptación de los requisitos corregidos. |
| `scripts/validate_supplied_zips.py` | Validación reproducible de los tres ZIP entregados. |
| `VALIDATION_PLAN.md` | Criterios de aceptación y estrategia de pruebas. |

## Referencias

[1] [Servicio de Impuestos Internos — ¿En qué consiste el Régimen Pro Pyme General?](https://www.sii.cl/preguntas_frecuentes/declaracion_renta/001_140_7529.htm)  
[2] [Servicio de Impuestos Internos — Tipos de Regímenes Tributarios](https://www.sii.cl/destacados/modernizacion/tipos_regimenes_mt.html)
