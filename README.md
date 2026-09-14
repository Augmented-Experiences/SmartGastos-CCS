# SmartGastos (Pyme Ledger AI)

> Gestión inteligente de gastos y documentos contables para PyMEs — 100% local, sin internet. Producto **CCCE** (Cámara Colombiana de Comercio Electrónico).

## Descripción

**Pyme Ledger AI** es un plugin para [Pinokio](https://pinokio.computer) que permite a pequeñas y medianas empresas gestionar sus documentos contables con inteligencia artificial completamente offline. Extrae, clasifica y organiza facturas, boletas y comprobantes de forma automática usando modelos de IA locales (Ollama + LLaMA).

---

## Características Principales

### Ingesta Documental
- Carga de documentos PDF, JPG y PNG mediante drag & drop
- OCR automático con Tesseract y EasyOCR para documentos escaneados
- Extracción de campos clave: proveedor, RUT, folio, fecha, montos, IVA

### Clasificación Inteligente
- Clasificación automática por categoría contable (operaciones, marketing, impuestos, etc.)
- Asignación a centros de costo
- Detección de duplicados y anomalías
- Confianza de clasificación con indicador visual

### Revisión Asistida
- Bandeja de documentos pendientes de revisión
- Aprobación/rechazo con un clic
- Corrección manual de categorías y centros de costo
- Historial de revisiones

### Analítica y Reportes
- Dashboard con KPIs: gasto total, documentos procesados, pendientes
- Gráficos de gastos por categoría, proveedor y tendencias mensuales
- Alertas automáticas de anomalías y gastos inusuales
- Recomendaciones de ahorro y optimización

### Exportación
- CSV de documentos para análisis en Excel
- PDF de reportes con gráficos y análisis
- Libro contable en formato estándar

### Administración Maestra
- Gestión de múltiples empresas
- Configuración de categorías contables personalizadas
- Centros de costo configurables
- Reglas de clasificación por palabras clave

---

## Requisitos del Sistema

| Componente | Mínimo | Recomendado |
|---|---|---|
| RAM | 4 GB | 8 GB o más |
| Almacenamiento | 5 GB | 10 GB |
| CPU | 4 núcleos | 8 núcleos |
| GPU | No requerida | Opcional (acelera OCR) |
| SO | Windows 10, macOS 12, Ubuntu 20.04 | Cualquiera de los anteriores |

---

## Instalación

1. Instalar [Pinokio](https://pinokio.computer)
2. Abrir Pinokio y hacer clic en **Discover**
3. Buscar **Pyme Ledger AI** o pegar la URL del repositorio
4. Hacer clic en **Instalar** — el proceso es automático

El instalador se encarga de:
- Instalar Ollama (motor de IA local)
- Descargar el modelo de lenguaje apropiado según la RAM disponible
- Configurar el entorno Python con todas las dependencias
- Inicializar la base de datos SQLite

---

## Uso Rápido

### 1. Configurar Empresa
1. Ir a **Administración → Empresas**
2. Hacer clic en **Nueva Empresa**
3. Ingresar razón social, RUT y moneda base

### 2. Cargar Documentos
1. Ir a **Ingesta**
2. Arrastrar documentos al área de carga
3. El sistema los procesa automáticamente

### 3. Revisar Clasificaciones
1. Ir a **Revisión Asistida**
2. Verificar las clasificaciones sugeridas por la IA
3. Aprobar o corregir según corresponda

### 4. Analizar Gastos
1. Ir a **Dashboard** o **Analítica**
2. Seleccionar la empresa y el período
3. Ver gráficos y KPIs

### 5. Exportar Reportes
1. Ir a **Exportar**
2. Seleccionar formato (CSV, PDF o Libro Contable)
3. Descargar el archivo generado

---

## Arquitectura

```
pyme-ledger-ai/
├── server/
│   ├── app.py                    # Servidor FastAPI principal
│   ├── models.py                 # Modelos SQLAlchemy (SQLite)
│   ├── database.py               # Gestión de BD
│   ├── agents/
│   │   ├── ocr_agent.py          # Extracción de texto (Tesseract/EasyOCR)
│   │   ├── extractor_agent.py    # Extracción de campos (LLM)
│   │   ├── classifier_agent.py   # Clasificación de gastos (LLM + reglas)
│   │   └── auditor_agent.py      # Validación y detección de anomalías
│   ├── orchestration/
│   │   └── pipeline.py           # Orquestador del pipeline de procesamiento
│   └── analytics/
│       ├── analyzer.py           # Motor de analítica y KPIs
│       ├── recommender.py        # Motor de recomendaciones
│       └── exporter.py           # Exportación CSV/PDF
├── app/
│   └── index.html                # UI completa (HTML/CSS/JS)
├── defaults/
│   ├── agents.json               # Configuración de agentes
│   └── prompts/                  # Prompts del sistema
├── pinokio.js                    # Configuración del plugin
├── install.json                  # Script de instalación
├── start.json                    # Script de inicio
└── stop.json                     # Script de parada
```

---

## Modelos de IA Utilizados

| RAM disponible | Modelo | Tamaño |
|---|---|---|
| < 8 GB | llama3.2:1b | ~1.3 GB |
| 8-16 GB | llama3.2:3b | ~2.0 GB |
| > 16 GB | llama3.1:8b | ~4.7 GB |

---

## Privacidad y Seguridad

- **100% offline**: ningún dato sale del equipo
- **Sin telemetría**: no se envían datos a servidores externos
- **Datos locales**: toda la información se almacena en SQLite en el directorio del plugin
- **Código abierto**: todo el código es auditable

---

## Soporte

Para reportar problemas o sugerir mejoras, abrir un issue en el repositorio del proyecto.

---

## Licencia

MIT License — libre para uso comercial y personal.
