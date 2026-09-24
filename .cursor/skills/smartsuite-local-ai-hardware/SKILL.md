---
name: smartsuite-local-ai-hardware
description: >-
  Contrato SmartSuite CCS para IA local en desktop Tauri+sidecar: perfiles RAM,
  un LLM de texto por PC, RapidOCR, moondream opcional, bloqueo bajo 7 GB y
  aviso 8-12 GB. Usar al tocar Ollama, agentes, splash, hardware, smartsuite.config.json,
  o al portar SmartGastos a SmartRedes u otra herramienta SmartSuite.
---

# SmartSuite: hardware, RAM y un modelo por PC

Referencia viva: este repo (`SmartGastos-CCS`, rama `kit`). Copiar el contrato, no reinventar umbrales.

UI y copy de producto: **español**. No mutar SQLite/datos del usuario. Versión de instalador: `desktop/smartsuite.config.json`.

## Contrato (no negociar por herramienta)

1. **Un LLM de texto por máquina** = el del perfil de RAM. Extractor, clasificador, auditor, recomendador y chat usan ese modelo. Distintos *prompts*, no distintos binarios.
2. **Un motor OCR** (RapidOCR). No es un LLM. No Tesseract como motor de escritorio.
3. **Visión opcional** (`moondream`) solo en Completo/Máximo (16 GB+). En 8–12 GB el paso visual se omite.
4. **Pull solo del perfil**: modelo del tramo + `extraModels` de ese tramo. Nunca bajar 1b+3b+8b+moondream a todos.
5. En desktop (`RUN_BY_TAURI` o sidecar frozen), `resolve_ollama_model` **prefiere `OLLAMA_MODEL`** aunque haya un 8b viejo en `/api/tags`.
6. Medir RAM con `sysinfo` `total_memory` (Windows 8 GB kit ≈ 7.8 GB). Umbrales **exclusivos**: `gb < maxRamGb`.

## Tabla de perfiles (copiar tal cual)

`maxRamGb` es techo exclusivo. El último tramo usa `0`.

| RAM reportada | id | label | modelo | extraModels |
|---|---|---|---|---|
| `< 7` | liviano | Liviano | llama3.2:1b | [] — **bloquear app** |
| `< 13` | estandar | Estándar | llama3.2:3b | [] — **aviso, dejar entrar** |
| `< 24` | completo | Completo | llama3.2:3b | [moondream] |
| resto | maximo | Máximo | llama3.1:8b | [moondream] |

Acceso:

```json
"access": { "blockBelowGb": 7, "warnBelowGb": 13 }
```

- RAM `<= 0` (detección fallida): **fail open** (no bloquear).
- Override: `SMARTSUITE_ALLOW_LOW_RAM=1` (alias por producto opcional, p.ej. `SMARTGASTOS_ALLOW_LOW_RAM`).
- Bloqueo en **splash** (phase `blocked`, `can_continue=false`). No pull de Ollama. Copy: se midieron X GB, se necesitan al menos 8 GB.
- Aviso persistente en el panel si `7 <= RAM < 13`: perfil Estándar, 3b + RapidOCR, cerrar Chrome/Teams, PDF grandes más lentos. **No avisar en 16 GB** (Completo).

## Config y launcher

En `desktop/smartsuite.config.json`: `ollama.tiers` con `id`, `label`, `model`, `extraModels` por tramo (no `extraModels` global). `configure.mjs` las copia a `src-tauri/appconfig.json`.

Rust (`main.rs`):

- `profile_for_ram()` elige el tramo; pull `profile.model` luego `profile.extra_models`.
- Sidecar env: `OLLAMA_MODEL`, `OLLAMA_PROFILE`, `OLLAMA_URL`.
- Persist: `ollama/active_model.txt` + `ollama/active_profile.json` (`id`, `label`, `model`, `extraModels`, `ramGb`, `access`).

Python: `hardware_profile.py` con la misma tabla + `select_profile` + `access_for_ram`. El sidecar no es la fuente de verdad del pull; sí debe **obedecer** el perfil.

## Agentes (UI)

- OCR: motor `rapidocr`, selector bloqueado.
- Visión: `moondream` solo si el perfil trae extras; si no, “no disponible en este perfil”.
- Texto: mostrar “modelo del perfil”, no un combo libre de 8b en un PC de 8 GB.
- `agents_config.json` viejo: migrar `tesseract`→`rapidocr`; extractor `moondream`→modelo del perfil.

## Calidad 3b (expectativa)

3b + RapidOCR **sí** extrae/clasifica JSON de boletas con texto limpio. Se degrada en layouts raros, montos mal leídos y en recomendaciones/chat. El 1b del Liviano no es producto: por eso se bloquea `< 7 GB`.

## Checklist al portar

- [ ] Mismos umbrales 7 / 13 / 24 / 0
- [ ] `access.blockBelowGb` / `warnBelowGb`
- [ ] Splash `phase === 'blocked'` no entra a la app
- [ ] Un solo pull de texto + extras del tramo
- [ ] Desktop pin a `OLLAMA_MODEL` para agentes de texto
- [ ] Visión omitida sin moondream
- [ ] UI en español; override documentado
- [ ] Tests de 6.9→block/liviano, 7.0→estandar, 8→3b sin moon, 16→moon, 32→8b
