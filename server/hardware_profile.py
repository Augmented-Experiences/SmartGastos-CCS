"""Perfiles de hardware para elegir modelo Ollama según la RAM."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

# Tramos alineados con desktop/smartsuite.config.json (max_ram_gb exclusivo: RAM < umbral).
# 8 GB típico (~7.8–8.5) entra en Estándar: llama3.2:3b sin moondream.
TIERS: List[Dict] = [
    {
        "max_ram_gb": 7,
        "id": "liviano",
        "label": "Liviano",
        "model": "llama3.2:1b",
        "extra_models": [],
    },
    {
        "max_ram_gb": 13,
        "id": "estandar",
        "label": "Estándar",
        "model": "llama3.2:3b",
        "extra_models": [],
    },
    {
        "max_ram_gb": 24,
        "id": "completo",
        "label": "Completo",
        "model": "llama3.2:3b",
        "extra_models": ["moondream"],
    },
    {
        "max_ram_gb": 0,
        "id": "maximo",
        "label": "Máximo",
        "model": "llama3.1:8b",
        "extra_models": ["moondream"],
    },
]


def select_profile(ram_gb: float) -> Dict:
    """Elige el perfil cuyo umbral es el primero mayor que la RAM detectada."""
    try:
        gb = float(ram_gb)
    except (TypeError, ValueError):
        gb = 0.0
    for tier in TIERS:
        max_gb = float(tier.get("max_ram_gb") or 0)
        if max_gb > 0 and gb < max_gb:
            return dict(tier)
    return dict(TIERS[-1])


def profile_marker_path(data_dir: Optional[str] = None) -> Path:
    root = Path(data_dir or os.environ.get("DATA_DIR") or "data")
    return root / "ollama" / "active_profile.json"


def load_active_profile(data_dir: Optional[str] = None) -> Optional[Dict]:
    path = profile_marker_path(data_dir)
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("model"):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def describe_profile(profile: Dict, ram_gb: Optional[float] = None) -> str:
    label = profile.get("label") or profile.get("id") or "Auto"
    model = profile.get("model") or ""
    extras = profile.get("extra_models") or profile.get("extraModels") or []
    vision = "RapidOCR + moondream" if extras else "RapidOCR"
    ram = f"{ram_gb:g} GB · " if ram_gb else ""
    return f"{ram}perfil {label} · {model} · {vision}"
