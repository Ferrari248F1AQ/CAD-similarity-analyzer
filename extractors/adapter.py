"""
Adapter per convertire CADModelSignature (multi-CAD) in FeatureSignature
compatibile con il core (compute_similarity) basato su solid_edge_similarity_v2.

Strategia:
- Mappa i campi condivisi (metadati, conteggi, sequenze) dal modello multi-CAD
  alla struttura attesa da FeatureSignature.
- Garantisce valori di default per campi mancanti.
- Gestisce feature_types, feature_sequence, feature_names,
  sketches_data, geometry_2d_types, constraint_2d_types, ratios stilistici.
"""
from pathlib import Path
from typing import Dict, Any

try:
    # Import core dataclass
    from solid_edge_similarity_v2 import FeatureSignature
except ImportError:
    FeatureSignature = None

from .cad_signature import CADModelSignature


FEATURE_TYPE_MAP_GENERAL = {
    # Normalizzazioni comuni tra CAD diversi
    # (già normalizzate nell'estrattore, qui solo fallback di armonizzazione)
    'Extrude': 'Protrusion',
    'Boss-Extrude': 'Protrusion',
    'Pad': 'Protrusion',
    'Cut-Extrude': 'Cutout',
    'Pocket': 'Cutout',
    'Revolve': 'Revolution',
    'Shaft': 'Revolution',
    'Groove': 'RevolvedCutout',
    'Sweep': 'Sweep',
    'Loft': 'Loft',
    'Hole': 'Hole',
    'Fillet': 'Round',
    'Chamfer': 'Chamfer',
    'CircularPattern': 'CircularPattern',
    'RectangularPattern': 'RectangularPattern',
    'Mirror': 'Mirror',
}


def map_feature_types(feature_types: Dict[str, int]) -> Dict[str, int]:
    out = {}
    for k, v in feature_types.items():
        mapped = FEATURE_TYPE_MAP_GENERAL.get(k, k)
        out[mapped] = out.get(mapped, 0) + v
    return out


def build_feature_signature(sig: CADModelSignature) -> FeatureSignature:
    """Converte CADModelSignature in FeatureSignature (per compute_similarity)."""
    if FeatureSignature is None:
        raise ImportError("FeatureSignature non disponibile")

    # Metadati
    author = sig.author or sig.last_author or ""

    # Mappa tipi e sequenze
    feature_types = map_feature_types(sig.feature_types)
    feature_sequence = [FEATURE_TYPE_MAP_GENERAL.get(t, t) for t in sig.feature_sequence]
    feature_names = list(sig.feature_names or [])

    fs = FeatureSignature(
        filename=sig.filename or Path(sig.filepath).name,
        filepath=sig.filepath,
        file_hash=sig.file_hash or "",
        author=author,
        last_author=sig.last_author or "",
        company=sig.company or "",
        template=sig.template or "",
        feature_count=sig.feature_count,
        feature_types=feature_types,
        feature_sequence=feature_sequence,
        feature_names=feature_names,
        sketches_count=sig.sketches_count,
        sketches_data=[sk.__dict__ for sk in sig.sketches_data],
        total_2d_geometry_count=sig.total_2d_geometry_count,
        total_2d_constraint_count=sig.total_2d_constraint_count,
        geometry_2d_types=dict(sig.geometry_2d_types),
        constraint_2d_types=dict(sig.constraint_2d_types),
        constraint_to_geometry_ratio=sig.constraint_to_geometry_ratio,
    )

    # Rapporti stilistici
    fs.extrusion_ratio = sig.extrusion_ratio
    fs.cutout_ratio = sig.cutout_ratio
    fs.hole_ratio = sig.hole_ratio
    fs.round_chamfer_ratio = sig.round_chamfer_ratio

    # Stile naming
    fs.naming_style = sig.naming_style

    return fs


def build_feature_signatures(cad_sigs: list[CADModelSignature]) -> list[FeatureSignature]:
    return [build_feature_signature(s) for s in cad_sigs]

