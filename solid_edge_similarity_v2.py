# -*- coding: utf-8 -*-
"""
Analisi similaritÃ  file CAD Solid Edge basata su feature patterns.
V2: Include analisi sketch (entitÃ  geometriche 2D + vincoli).
"""

import json
import hashlib
import math
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass, field, asdict
from collections import Counter

# Per connessione COM diretta a Solid Edge
try:
    import win32com.client
    HAS_COM = True
except ImportError:
    HAS_COM = False
    print("WARNING: pywin32 not installed (pip install pywin32)")


# Mapping reale dei tipi feature di Solid Edge (basato su output diagnostico)
# âœ¨ AGGIORNATO con valori REALI trovati dai file A013, A014, A030, rectangular_pattern
FEATURE_TYPE_MAP = {
    # âœ¨ VALORI REALI trovati con debug
    462094706: "ExtrudedProtrusion",      # Protrusion (Estrusione)
    462094710: "RevolvedProtrusion",      # Revolution (Rivoluzione)
    462094714: "ExtrudedCutout",          # Cutout (Taglio)
    462094722: "Hole",                    # Hole (Foro)
    462094738: "Round",                   # Round (Raccordo)
    462094742: "Chamfer",                 # Chamfer (Smusso)

    # Pattern - âœ¨ Usato anche per CircularPattern e RectangularPattern (distinguiti per nome)
    -416228998: "Pattern",                # Pattern generico (il nome della feature dirÃ  se Circular o Rectangular)

    # Feature speciali
    66247736: "Mirror",                   # Mirror (Specchio)
    2057842144: "Loft",                   # Loft (Profilato)
    -2101194894: "Sweep",                 # Sweep (Estrusione lungo percorso)
    -1468087919: "Hole",                  # Hole alternativo

    # Sketch e piani
    1689979564: "Sketch",                 # Sketch
    732824896: "RefPlane",                # Piano di riferimento

    # Pattern specifici (per completezza)
    462094770: "CircularPattern",
    462094774: "RectangularPattern",
    462094778: "MirrorPattern",

    # Altri tipi (legacy o meno comuni)
    462094718: "RevolvedProtrusion",
    462094726: "RevolvedCutout",
    462094730: "Loft",
    462094734: "Sweep",
    462094746: "Draft",
    462094750: "Shell",
    462094754: "Rib",
    462094758: "Web",
    462094762: "Lip",
    462094766: "Thread",
    462094782: "ThinWall",
    462094786: "Thicken",
    462094790: "Boolean",
}

# Mapping tipi geometrici 2D
GEOMETRY_2D_TYPE_MAP = {
    0: "Unknown",
    1: "Point2d",
    2: "Line2d",
    3: "Circle2d",
    4: "Arc2d",
    5: "Ellipse2d",
    6: "EllipticalArc2d",
    7: "BSplineCurve2d",
    8: "Polyline2d",
    # Aggiungi altri tipi se necessario
}

# Mapping tipi vincoli 2D
# NOTA: I valori sono i VERI valori ritornati da Solid Edge COM API
# Mappati usando il file "Sketch cases.par" con 30 sketch di test
CONSTRAINT_2D_TYPE_MAP = {
    # âœ¨ VINCOLI GEOMETRICI (da Relations2d) - Valori REALI da Solid Edge
    768508992: "Coincidente",         # Coincidenza punti (17 occ.) - Sketch 1,7,10
    1679388272: "Concentrico",        # ConcentricitÃ  (1 occ.) - Sketch 2
    463670656: "Parallelo",           # Parallelismo (1 occ.) - Sketch 3
    273497200: "Connesso",            # Connessione/punto su curva (7 occ.) - Sketch 4,5,26
    -367594016: "Raccordo",            # Raccordo (1 occ.) - Sketch 6
    640124384: "Perpendicolare",      # PerpendicolaritÃ  (4 occ.) - Sketch 7,28
    -1337543808: "Simmetrico",        # Simmetria (2 occ.) - Sketch 9,23
    -1337543801: "Collineare",        # CollinearitÃ  (1 occ.) - Sketch 10
    709097856: "Tangente",            # Tangenza (3 occ.) - Sketch 12,29
    -280074960: "OrizzontaleVerticale", # Orizzontale/Verticale (3 occ.) - Sketch 13,14
    -83892864: "RigidSet",           # Set rigido interno (3 occ.) - Sketch 9,14
    769466240: "RigidSetGroup",      # Gruppo rigido (1 occ.) - Sketch 15
    296913277: "ProjectOffset",      # Proiezione/Offset (2 occ.) - Sketch 18,21
    -902087584: "Fisso",             # Punto fisso (1 occ.) - Sketch 22
    -1337543803: "Uguale",            # Uguaglianza (6 occ.) - Sketch 27,30
    1166881824: "PuntoIntersezione", # Punto di intersezione - Sketch 2
    -1179755088: "AllineamentoVerticale",   # Allineamento verticale
    -401894992: "AllineamentoOrizzontale",  # Allineamento orizzontale

    # âœ¨ VINCOLI DIMENSIONALI (da Dimensions)
    488188096: "LinearDimension",    # Quota lineare/raggio/angolo/diametro - TUTTI gli sketch quotati

    # Valori legacy (potrebbero non essere usati)
    0: "Unknown",
}

# âœ¨ MAPPING PARLANTE: Descrizioni dettagliate dei vincoli
CONSTRAINT_DESCRIPTIONS = {
    # âœ¨ VINCOLI GEOMETRICI - Valori REALI da Solid Edge
    768508992: {"categoria": "Geometrico", "descrizione": "Coincidenza di punti", "tipo": "geometrico"},
    1679388272: {"categoria": "Geometrico", "descrizione": "ConcentricitÃ  (stesso centro)", "tipo": "geometrico"},
    463670656: {"categoria": "Geometrico", "descrizione": "Parallelismo", "tipo": "geometrico"},
    273497200: {"categoria": "Geometrico", "descrizione": "Connessione/Punto su curva", "tipo": "geometrico"},
    -367594016: {"categoria": "Geometrico", "descrizione": "Raccordo (Fillet)", "tipo": "geometrico"},
    640124384: {"categoria": "Geometrico", "descrizione": "PerpendicolaritÃ  (90Â°)", "tipo": "geometrico"},
    -1337543808: {"categoria": "Geometrico", "descrizione": "Simmetria", "tipo": "geometrico"},
    -1337543801: {"categoria": "Geometrico", "descrizione": "CollinearitÃ ", "tipo": "geometrico"},
    709097856: {"categoria": "Geometrico", "descrizione": "Tangenza", "tipo": "geometrico"},
    -280074960: {"categoria": "Geometrico", "descrizione": "Allineamento orizzontale/verticale", "tipo": "geometrico"},
    -83892864: {"categoria": "Geometrico", "descrizione": "Set rigido (interno)", "tipo": "geometrico"},
    769466240: {"categoria": "Geometrico", "descrizione": "Gruppo rigido", "tipo": "geometrico"},
    296913277: {"categoria": "Geometrico", "descrizione": "Proiezione/Offset", "tipo": "geometrico"},
    -902087584: {"categoria": "Geometrico", "descrizione": "Punto fisso (Fixed)", "tipo": "geometrico"},
    -1337543803: {"categoria": "Geometrico", "descrizione": "Uguaglianza", "tipo": "geometrico"},
    1166881824: {"categoria": "Geometrico", "descrizione": "Punto di intersezione", "tipo": "geometrico"},
    -1179755088: {"categoria": "Geometrico", "descrizione": "Allineamento verticale (VerticalAlignment)", "tipo": "geometrico"},
    -401894992: {"categoria": "Geometrico", "descrizione": "Allineamento orizzontale (HorizontalAlignment)", "tipo": "geometrico"},

    # âœ¨ VINCOLI DIMENSIONALI
    488188096: {"categoria": "Dimensionale", "descrizione": "Quota (distanza/raggio/angolo/diametro)", "tipo": "dimensionale"},

    # Fallback
    0: {"categoria": "Sconosciuto", "descrizione": "Tipo di vincolo non identificato", "tipo": "unknown"},
}

def get_constraint_description(constraint_type_enum: int) -> dict:
    """
    Ritorna descrizione parlante di un vincolo.

    Returns:
        {
            'categoria': 'Geometrico' | 'Dimensionale' | 'Sconosciuto',
            'descrizione': 'Descrizione leggibile',
            'tipo': 'geometrico' | 'dimensionale' | 'unknown'
        }
    """
    if constraint_type_enum in CONSTRAINT_DESCRIPTIONS:
        return CONSTRAINT_DESCRIPTIONS[constraint_type_enum]

    # Se non trovato, ritorna un valore generico ma mostra il type
    return {
        "categoria": "Geometrico",
        "descrizione": f"Vincolo geometrico (tipo {constraint_type_enum})",
        "tipo": "geometrico"
    }


@dataclass
class SketchInfo:
    """Informazioni su un singolo sketch."""
    name: str = ""
    index: int = 0

    # Geometrie 2D
    geometry_count: int = 0
    geometry_types: Dict[str, int] = field(default_factory=dict)
    geometry_sequence: List[str] = field(default_factory=list)

    # Vincoli
    constraint_count: int = 0
    constraint_types: Dict[str, int] = field(default_factory=dict)
    constraint_sequence: List[str] = field(default_factory=list)

    # Dimensioni (valori)
    dimension_values: List[float] = field(default_factory=list)

    # ComplessitÃ 
    complexity_score: float = 0.0


@dataclass
class FeatureSignature:
    """Firma estratta da un file CAD."""
    filename: str
    filepath: str
    file_hash: str

    # Metadati documento (da Properties)
    author: str = ""
    last_author: str = ""
    company: str = ""
    creation_date: str = ""
    last_save_date: str = ""
    template: str = ""
    revision: str = ""

    # Feature analysis (3D)
    feature_count: int = 0
    feature_types: Dict[str, int] = field(default_factory=dict)
    feature_sequence: List[str] = field(default_factory=list)
    feature_names: List[str] = field(default_factory=list)

    # Collezioni specifiche
    extrusions_count: int = 0
    cutouts_count: int = 0
    holes_count: int = 0
    rounds_count: int = 0
    chamfers_count: int = 0
    sketches_count: int = 0

    # Pattern di modellazione (fingerprint stilistico)
    extrusion_ratio: float = 0.0
    cutout_ratio: float = 0.0
    hole_ratio: float = 0.0
    round_chamfer_ratio: float = 0.0

    # Sequenze caratteristiche (bigram di feature consecutive)
    common_sequences: List[Tuple[str, int]] = field(default_factory=list)

    # Stile naming
    naming_style: str = ""

    # === NUOVI CAMPI: Sketch Analysis ===
    sketches_data: List[Dict[str, Any]] = field(default_factory=list)

    # Aggregati sketch
    total_2d_geometry_count: int = 0
    total_2d_constraint_count: int = 0
    geometry_2d_types: Dict[str, int] = field(default_factory=dict)
    constraint_2d_types: Dict[str, int] = field(default_factory=dict)

    # Pattern 2D
    avg_geometry_per_sketch: float = 0.0
    avg_constraints_per_sketch: float = 0.0
    constraint_to_geometry_ratio: float = 0.0

    # âœ¨ Flag per segnalare istanza Solid Edge corrotta
    instance_corrupted: bool = False


def compute_file_hash(filepath: Path) -> str:
    """Calcola hash SHA256 del file."""
    sha = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha.update(chunk)
    return sha.hexdigest()[:16]


def get_feature_type_name(type_enum: int, feature_name: str = None) -> str:
    """
    Converte l'enum Type di Solid Edge in nome leggibile.
    Per Pattern, usa il nome della feature per distinguere Circular da Rectangular.
    """
    base_name = FEATURE_TYPE_MAP.get(type_enum, f"Unknown_{type_enum}")

    # âœ¨ SPECIALE: Distingui Pattern usando il nome della feature
    if type_enum == -416228998 and feature_name:  # Pattern type
        name_lower = feature_name.lower()
        if 'circular' in name_lower or 'circle' in name_lower:
            return "CircularPattern"
        elif 'rectangular' in name_lower or 'rect' in name_lower:
            return "RectangularPattern"

    return base_name


def get_geometry_2d_type_name(type_enum: int) -> str:
    """Converte tipo geometria 2D."""
    return GEOMETRY_2D_TYPE_MAP.get(type_enum, f"Geom2D_{type_enum}")


def get_constraint_2d_type_name(type_enum: int) -> str:
    """Converte tipo vincolo 2D."""
    return CONSTRAINT_2D_TYPE_MAP.get(type_enum, f"Constraint_{type_enum}")


# ============================================================================
# FUNZIONI PER FRAME PARAMETRICO SKETCH (u,v)
# ============================================================================

def compute_geometry_centroid_and_projections(geom_type: str, geom_data: Dict) -> Tuple[Tuple[float, float], float, float]:
    """
    Calcola baricentro e proiezioni di una geometria per il frame parametrico.

    Returns:
        (centroid, proj_x, proj_y) - baricentro e proiezioni sugli assi
    """
    start = geom_data.get('start_point')
    end = geom_data.get('end_point')
    center = geom_data.get('center_point')
    radius = geom_data.get('radius')

    if geom_type in ('Line', 'Line2d'):
        if start and end:
            cx = (start[0] + end[0]) / 2
            cy = (start[1] + end[1]) / 2
            proj_x = abs(end[0] - start[0])
            proj_y = abs(end[1] - start[1])
            return ((cx, cy), proj_x, proj_y)

    elif geom_type in ('Circle', 'Circle2d'):
        if center and radius:
            diameter = 2 * radius
            return (center, diameter, diameter)
        elif center:
            return (center, 1.0, 1.0)

    elif geom_type in ('Arc', 'Arc2d'):
        if center and radius:
            diameter = 2 * radius
            return (center, diameter, diameter)
        elif start and end:
            cx = (start[0] + end[0]) / 2
            cy = (start[1] + end[1]) / 2
            proj_x = abs(end[0] - start[0])
            proj_y = abs(end[1] - start[1])
            return ((cx, cy), proj_x, proj_y)

    # Fallback
    return ((0.0, 0.0), 0.0, 0.0)


def compute_sketch_frame_from_geometries(geometries: List[Dict]) -> Dict:
    """
    Calcola il frame parametrico (u,v) di uno sketch dalle sue geometrie.

    Returns:
        Dict con centroid, axis_u, axis_v, extent_u, extent_v, is_valid
    """
    if not geometries:
        return {
            'centroid': (0.0, 0.0),
            'axis_u': (1.0, 0.0),
            'axis_v': (0.0, 1.0),
            'extent_u': math.sqrt(2) / 2,
            'extent_v': math.sqrt(2) / 2,
            'num_points': 0,
            'is_valid': False
        }

    # Raccogli centroidi e proiezioni
    centroids = []
    total_proj_x = 0.0
    total_proj_y = 0.0

    for geom in geometries:
        geom_type = geom.get('type', '')
        centroid, proj_x, proj_y = compute_geometry_centroid_and_projections(geom_type, geom)

        if centroid != (0.0, 0.0) or proj_x > 0 or proj_y > 0:
            centroids.append(centroid)
            total_proj_x += proj_x
            total_proj_y += proj_y

    if not centroids:
        return {
            'centroid': (0.0, 0.0),
            'axis_u': (1.0, 0.0),
            'axis_v': (0.0, 1.0),
            'extent_u': math.sqrt(2) / 2,
            'extent_v': math.sqrt(2) / 2,
            'num_points': 0,
            'is_valid': False
        }

    # Baricentro
    cx = sum(p[0] for p in centroids) / len(centroids)
    cy = sum(p[1] for p in centroids) / len(centroids)

    # Assi principali (PCA semplificato - per ora usa assi standard)
    # TODO: implementare PCA completo se necessario
    axis_u = (1.0, 0.0)
    axis_v = (0.0, 1.0)

    # Calcola PCA se abbiamo abbastanza punti
    if len(centroids) >= 2:
        centered = [(p[0] - cx, p[1] - cy) for p in centroids]
        n = len(centered)
        cov_xx = sum(p[0] * p[0] for p in centered) / n
        cov_yy = sum(p[1] * p[1] for p in centered) / n
        cov_xy = sum(p[0] * p[1] for p in centered) / n

        trace = cov_xx + cov_yy
        det = cov_xx * cov_yy - cov_xy * cov_xy
        discriminant = max(0, trace * trace / 4 - det)
        sqrt_disc = math.sqrt(discriminant)
        lambda1 = trace / 2 + sqrt_disc

        if abs(cov_xy) > 1e-10:
            v1_x = lambda1 - cov_yy
            v1_y = cov_xy
        elif cov_xx >= cov_yy:
            v1_x, v1_y = 1.0, 0.0
        else:
            v1_x, v1_y = 0.0, 1.0

        norm1 = math.sqrt(v1_x * v1_x + v1_y * v1_y)
        if norm1 > 1e-10:
            axis_u = (v1_x / norm1, v1_y / norm1)
            axis_v = (-axis_u[1], axis_u[0])

    # Pesi normalizzati
    # Proietta le estensioni totali sugli assi u e v
    proj_u = total_proj_x * abs(axis_u[0]) + total_proj_y * abs(axis_u[1])
    proj_v = total_proj_x * abs(axis_v[0]) + total_proj_y * abs(axis_v[1])

    norm = math.sqrt(proj_u * proj_u + proj_v * proj_v)
    if norm > 1e-10:
        weight_u = proj_u / norm
        weight_v = proj_v / norm
    else:
        weight_u = math.sqrt(2) / 2
        weight_v = math.sqrt(2) / 2

    return {
        'centroid': (cx, cy),
        'axis_u': axis_u,
        'axis_v': axis_v,
        'extent_u': weight_u,
        'extent_v': weight_v,
        'num_points': len(centroids),
        'is_valid': len(centroids) >= 1
    }


def extract_geometry_coordinates(profile, geom_type: str, index: int) -> Dict:
    """
    Estrae le coordinate di una geometria da un profile Solid Edge.

    Returns:
        Dict con type, id, start_point, end_point, center_point, radius
    """
    geom_data = {
        'type': geom_type,
        'id': f'{geom_type}_{index}',
        'start_point': None,
        'end_point': None,
        'center_point': None,
        'radius': None
    }

    def _to_point(value):
        if value is None:
            return None
        try:
            if hasattr(value, 'X') and hasattr(value, 'Y'):
                return (float(value.X), float(value.Y))
        except Exception:
            pass
        try:
            if isinstance(value, (tuple, list)) and len(value) >= 2:
                return (float(value[0]), float(value[1]))
        except Exception:
            pass
        return None

    def _get_point(entity, candidates):
        for name in candidates:
            try:
                attr = getattr(entity, name)
            except Exception:
                continue

            # property style
            try:
                pt = _to_point(attr)
                if pt:
                    return pt
            except Exception:
                pass

            # method style
            if callable(attr):
                try:
                    ret = attr()
                    pt = _to_point(ret)
                    if pt:
                        return pt
                except Exception:
                    pass
        return None

    def _get_radius(entity):
        for name in ('Radius', 'radius'):
            try:
                return float(getattr(entity, name))
            except Exception:
                continue
        return None

    try:
        if geom_type == 'Line2d':
            lines = profile.Lines2d
            if index <= lines.Count:
                line = lines.Item(index)
                geom_data['start_point'] = _get_point(line, ('StartPoint', 'StartPoint2d', 'GetStartPoint'))
                geom_data['end_point'] = _get_point(line, ('EndPoint', 'EndPoint2d', 'GetEndPoint'))
                if geom_data['start_point'] is None:
                    try:
                        geom_data['start_point'] = (float(line.X1), float(line.Y1))
                    except Exception:
                        pass
                if geom_data['end_point'] is None:
                    try:
                        geom_data['end_point'] = (float(line.X2), float(line.Y2))
                    except Exception:
                        pass

        elif geom_type == 'Circle2d':
            circles = profile.Circles2d
            if index <= circles.Count:
                circle = circles.Item(index)
                geom_data['center_point'] = _get_point(circle, ('CenterPoint', 'CenterPoint2d', 'GetCenterPoint'))
                if geom_data['center_point'] is None:
                    try:
                        geom_data['center_point'] = (float(circle.XCenter), float(circle.YCenter))
                    except Exception:
                        pass
                geom_data['radius'] = _get_radius(circle)

        elif geom_type == 'Arc2d':
            arcs = profile.Arcs2d
            if index <= arcs.Count:
                arc = arcs.Item(index)
                geom_data['start_point'] = _get_point(arc, ('StartPoint', 'StartPoint2d', 'GetStartPoint'))
                geom_data['end_point'] = _get_point(arc, ('EndPoint', 'EndPoint2d', 'GetEndPoint'))
                geom_data['center_point'] = _get_point(arc, ('CenterPoint', 'CenterPoint2d', 'GetCenterPoint'))
                if geom_data['start_point'] is None:
                    try:
                        geom_data['start_point'] = (float(arc.X1), float(arc.Y1))
                    except Exception:
                        pass
                if geom_data['end_point'] is None:
                    try:
                        geom_data['end_point'] = (float(arc.X2), float(arc.Y2))
                    except Exception:
                        pass
                if geom_data['center_point'] is None:
                    try:
                        geom_data['center_point'] = (float(arc.XCenter), float(arc.YCenter))
                    except Exception:
                        pass
                geom_data['radius'] = _get_radius(arc)
    except Exception:
        pass

    return geom_data


def extract_sketch_entities(sketch, sketch_index: int) -> Dict[str, Any]:
    """Estrae entitÃ  geometriche e vincoli da uno sketch con dettagli puntali."""
    sketch_data = {
        'name': '',
        'index': sketch_index,
        'geometry_count': 0,
        'geometry_types': {},
        'geometry_sequence': [],
        'geometry_detailed': [],  # âœ¨ NUOVO: Lista dettagliata entitÃ 
        'constraint_count': 0,
        'constraint_types': {},
        'constraint_sequence': [],
        'constraint_detailed': [],  # âœ¨ NUOVO: Lista dettagliata vincoli
        'dimension_values': [],
        'errors': []
    }

    try:
        sketch_data['name'] = getattr(sketch, 'Name', f'Sketch_{sketch_index}')
    except:
        sketch_data['name'] = f'Sketch_{sketch_index}'

    # Estrai geometrie 2D
    geometry_counter = Counter()
    try:
        # Prova Profile (per sketch di feature)
        profiles = None
        try:
            profiles = sketch.Profiles
        except:
            pass

        if profiles and hasattr(profiles, 'Count') and profiles.Count > 0:
            for p_idx in range(1, profiles.Count + 1):
                try:
                    profile = profiles.Item(p_idx)
                    # Ogni profilo ha Lines2d, Arcs2d, Circles2d, etc.

                    # Lines
                    try:
                        lines = profile.Lines2d
                        for i in range(1, lines.Count + 1):
                            geometry_counter['Line2d'] += 1
                            sketch_data['geometry_sequence'].append('Line2d')
                            # âœ¨ Aggiungi dettagli
                            geom_detail = {
                                'type': 'Line2d',
                                'index': i,
                                'profile_index': p_idx,
                                'id': f'Line2d_{p_idx}_{i}'
                            }
                            geom_detail.update(extract_geometry_coordinates(profile, 'Line2d', i))
                            sketch_data['geometry_detailed'].append(geom_detail)
                    except:
                        pass

                    # Arcs
                    try:
                        arcs = profile.Arcs2d
                        for i in range(1, arcs.Count + 1):
                            geometry_counter['Arc2d'] += 1
                            sketch_data['geometry_sequence'].append('Arc2d')
                            geom_detail = {
                                'type': 'Arc2d',
                                'index': i,
                                'profile_index': p_idx,
                                'id': f'Arc2d_{p_idx}_{i}'
                            }
                            geom_detail.update(extract_geometry_coordinates(profile, 'Arc2d', i))
                            sketch_data['geometry_detailed'].append(geom_detail)
                    except:
                        pass

                    # Circles
                    try:
                        circles = profile.Circles2d
                        for i in range(1, circles.Count + 1):
                            geometry_counter['Circle2d'] += 1
                            sketch_data['geometry_sequence'].append('Circle2d')
                            geom_detail = {
                                'type': 'Circle2d',
                                'index': i,
                                'profile_index': p_idx,
                                'id': f'Circle2d_{p_idx}_{i}'
                            }
                            geom_detail.update(extract_geometry_coordinates(profile, 'Circle2d', i))
                            sketch_data['geometry_detailed'].append(geom_detail)
                    except:
                        pass

                    # Ellipses
                    try:
                        ellipses = profile.Ellipses2d
                        for i in range(1, ellipses.Count + 1):
                            geometry_counter['Ellipse2d'] += 1
                            sketch_data['geometry_sequence'].append('Ellipse2d')
                            sketch_data['geometry_detailed'].append({
                                'type': 'Ellipse2d',
                                'index': i,
                                'profile_index': p_idx,
                                'id': f'Ellipse2d_{p_idx}_{i}'
                            })
                    except:
                        pass

                    # BSplineCurves
                    try:
                        splines = profile.BSplineCurves2d
                        for i in range(1, splines.Count + 1):
                            geometry_counter['BSpline2d'] += 1
                            sketch_data['geometry_sequence'].append('BSpline2d')
                            sketch_data['geometry_detailed'].append({
                                'type': 'BSpline2d',
                                'index': i,
                                'profile_index': p_idx,
                                'id': f'BSpline2d_{p_idx}_{i}'
                            })
                    except:
                        pass

                except Exception as e:
                    sketch_data['errors'].append(f"Profile {p_idx}: {str(e)}")

        # Prova anche direttamente sullo sketch
        try:
            if hasattr(sketch, 'Lines2d'):
                lines = sketch.Lines2d
                if hasattr(lines, 'Count'):
                    for i in range(1, lines.Count + 1):
                        geometry_counter['Line2d'] += 1
                        sketch_data['geometry_sequence'].append('Line2d')
                        geom_detail = {
                            'type': 'Line2d',
                            'index': i,
                            'profile_index': 0,
                            'id': f'Line2d_{i}'
                        }
                        geom_detail.update(extract_geometry_coordinates(sketch, 'Line2d', i))
                        sketch_data['geometry_detailed'].append(geom_detail)
        except:
            pass

        try:
            if hasattr(sketch, 'Arcs2d'):
                arcs = sketch.Arcs2d
                if hasattr(arcs, 'Count'):
                    for i in range(1, arcs.Count + 1):
                        geometry_counter['Arc2d'] += 1
                        sketch_data['geometry_sequence'].append('Arc2d')
                        geom_detail = {
                            'type': 'Arc2d',
                            'index': i,
                            'profile_index': 0,
                            'id': f'Arc2d_{i}'
                        }
                        geom_detail.update(extract_geometry_coordinates(sketch, 'Arc2d', i))
                        sketch_data['geometry_detailed'].append(geom_detail)
        except:
            pass

        try:
            if hasattr(sketch, 'Circles2d'):
                circles = sketch.Circles2d
                if hasattr(circles, 'Count'):
                    for i in range(1, circles.Count + 1):
                        geometry_counter['Circle2d'] += 1
                        sketch_data['geometry_sequence'].append('Circle2d')
                        geom_detail = {
                            'type': 'Circle2d',
                            'index': i,
                            'profile_index': 0,
                            'id': f'Circle2d_{i}'
                        }
                        geom_detail.update(extract_geometry_coordinates(sketch, 'Circle2d', i))
                        sketch_data['geometry_detailed'].append(geom_detail)
        except:
            pass

    except Exception as e:
        sketch_data['errors'].append(f"Geometry extraction: {str(e)}")

    sketch_data['geometry_types'] = dict(geometry_counter)
    sketch_data['geometry_count'] = sum(geometry_counter.values())

    # Estrai vincoli
    constraint_counter = Counter()
    try:
        # âœ¨ FIX: I vincoli sono nel Profile, non direttamente nello sketch!
        # Primo, prova da Profile.Relations2d
        profile_constraint_count = 0
        try:
            profiles = sketch.Profiles
            if profiles and hasattr(profiles, 'Count') and profiles.Count > 0:
                for p_idx in range(1, profiles.Count + 1):
                    try:
                        profile = profiles.Item(p_idx)
                        
                        # Estrai vincoli dal profile
                        if hasattr(profile, 'Relations2d'):
                            relations = profile.Relations2d
                            if relations and hasattr(relations, 'Count'):
                                for i in range(1, relations.Count + 1):
                                    try:
                                        rel = relations.Item(i)
                                        rel_type = getattr(rel, 'Type', 0)
                                        type_name = get_constraint_2d_type_name(rel_type)
                                        constraint_counter[type_name] += 1
                                        sketch_data['constraint_sequence'].append(type_name)
                                        profile_constraint_count += 1

                                        # âœ¨ Aggiungi dettagli vincolo
                                        constraint_desc = get_constraint_description(rel_type)
                                        constraint_detail = {
                                            'type': type_name,
                                            'index': i,
                                            'profile_index': p_idx,
                                            'id': f'{type_name}_{p_idx}_{i}',
                                            'value': None,
                                            'categoria': constraint_desc['categoria'],
                                            'descrizione': constraint_desc['descrizione'],
                                            'tipo': constraint_desc['tipo']
                                        }

                                        # Se Ã¨ un vincolo dimensionale, estrai il valore
                                        if hasattr(rel, 'Value'):
                                            try:
                                                val = float(rel.Value)
                                                sketch_data['dimension_values'].append(val)
                                                constraint_detail['value'] = val
                                            except:
                                                pass

                                        sketch_data['constraint_detailed'].append(constraint_detail)
                                    except:
                                        pass

                        # âœ¨ NUOVO: Estrai anche le DIMENSIONI (quote) da profile.Dimensions
                        if hasattr(profile, 'Dimensions'):
                            dimensions = profile.Dimensions
                            if dimensions and hasattr(dimensions, 'Count'):
                                for i in range(1, dimensions.Count + 1):
                                    try:
                                        dim = dimensions.Item(i)
                                        dim_type = getattr(dim, 'Type', 488188096)  # Default: LinearDimension
                                        type_name = get_constraint_2d_type_name(dim_type)
                                        constraint_counter[type_name] += 1
                                        sketch_data['constraint_sequence'].append(type_name)
                                        profile_constraint_count += 1

                                        # Estrai il valore della dimensione
                                        dim_value = None
                                        try:
                                            dim_value = float(dim.Value)
                                            # Converti da metri a millimetri per leggibilitÃ 
                                            dim_value_mm = dim_value * 1000
                                            sketch_data['dimension_values'].append(dim_value_mm)
                                        except:
                                            pass

                                        # Aggiungi dettagli
                                        constraint_desc = get_constraint_description(dim_type)
                                        dim_name = getattr(dim, 'Name', f'Dimension_{i}')
                                        constraint_detail = {
                                            'type': type_name,
                                            'index': i,
                                            'profile_index': p_idx,
                                            'id': f'{type_name}_{p_idx}_{i}',
                                            'name': dim_name,
                                            'value': dim_value_mm if dim_value else None,
                                            'value_raw': dim_value,
                                            'categoria': constraint_desc['categoria'],
                                            'descrizione': constraint_desc['descrizione'],
                                            'tipo': constraint_desc['tipo']
                                        }

                                        sketch_data['constraint_detailed'].append(constraint_detail)
                                    except:
                                        pass
                    except:
                        pass
        except:
            pass

        # Se non trovato nei profile, prova direttamente sketch.Relations2d (vecchio metodo)
        if profile_constraint_count == 0:
            try:
                relations = sketch.Relations2d
                if relations and hasattr(relations, 'Count'):
                    for i in range(1, relations.Count + 1):
                        try:
                            rel = relations.Item(i)
                            rel_type = getattr(rel, 'Type', 0)
                            type_name = get_constraint_2d_type_name(rel_type)
                            constraint_counter[type_name] += 1
                            sketch_data['constraint_sequence'].append(type_name)

                            constraint_desc = get_constraint_description(rel_type)
                            constraint_detail = {
                                'type': type_name,
                                'index': i,
                                'id': f'{type_name}_{i}',
                                'value': None,
                                'categoria': constraint_desc['categoria'],
                                'descrizione': constraint_desc['descrizione'],
                                'tipo': constraint_desc['tipo']
                            }

                            if hasattr(rel, 'Value'):
                                try:
                                    val = float(rel.Value)
                                    sketch_data['dimension_values'].append(val)
                                    constraint_detail['value'] = val
                                except:
                                    pass

                            sketch_data['constraint_detailed'].append(constraint_detail)
                        except:
                            pass
            except:
                pass

        # Prova anche sketch.Constraints se sketch.Relations2d non ha nulla
        if len(constraint_counter) == 0:
            try:
                constraints = sketch.Constraints
                if hasattr(constraints, 'Count'):
                    for i in range(1, constraints.Count + 1):
                        try:
                            c = constraints.Item(i)
                            c_type = getattr(c, 'Type', 0)
                            type_name = get_constraint_2d_type_name(c_type)
                            if type_name not in constraint_counter:
                                constraint_counter[type_name] += 1
                                sketch_data['constraint_sequence'].append(type_name)

                                constraint_desc = get_constraint_description(c_type)
                                constraint_detail = {
                                    'type': type_name,
                                    'index': i,
                                    'id': f'{type_name}_{i}',
                                    'value': None,
                                    'categoria': constraint_desc['categoria'],
                                    'descrizione': constraint_desc['descrizione'],
                                    'tipo': constraint_desc['tipo']
                                }

                                if hasattr(c, 'Value'):
                                    try:
                                        val = float(c.Value)
                                        constraint_detail['value'] = val
                                    except:
                                        pass

                                sketch_data['constraint_detailed'].append(constraint_detail)
                        except:
                            pass
            except:
                pass

    except Exception as e:
        sketch_data['errors'].append(f"Constraint extraction: {str(e)}")

    sketch_data['constraint_types'] = dict(constraint_counter)
    sketch_data['constraint_count'] = sum(constraint_counter.values())

    # âœ¨ NUOVO: Calcola frame parametrico basato sulle geometrie estratte
    # Prima, estrai le coordinate delle geometrie (se non giÃ  fatto)
    geometries_with_coords = []
    try:
        profiles = None
        try:
            profiles = sketch.Profiles
        except:
            pass

        if profiles and hasattr(profiles, 'Count') and profiles.Count > 0:
            for p_idx in range(1, profiles.Count + 1):
                try:
                    profile = profiles.Item(p_idx)

                    # Estrai coordinate linee
                    try:
                        lines = profile.Lines2d
                        for i in range(1, lines.Count + 1):
                            geom_coords = extract_geometry_coordinates(profile, 'Line2d', i)
                            if geom_coords.get('start_point') or geom_coords.get('end_point'):
                                geometries_with_coords.append(geom_coords)
                    except:
                        pass

                    # Estrai coordinate cerchi
                    try:
                        circles = profile.Circles2d
                        for i in range(1, circles.Count + 1):
                            geom_coords = extract_geometry_coordinates(profile, 'Circle2d', i)
                            if geom_coords.get('center_point'):
                                geometries_with_coords.append(geom_coords)
                    except:
                        pass

                    # Estrai coordinate archi
                    try:
                        arcs = profile.Arcs2d
                        for i in range(1, arcs.Count + 1):
                            geom_coords = extract_geometry_coordinates(profile, 'Arc2d', i)
                            if geom_coords.get('center_point') or geom_coords.get('start_point'):
                                geometries_with_coords.append(geom_coords)
                    except:
                        pass
                except:
                    pass
    except:
        pass

    # Calcola frame parametrico
    if geometries_with_coords:
        sketch_data['parametric_frame'] = compute_sketch_frame_from_geometries(geometries_with_coords)
    else:
        # Fallback: frame invalido
        sketch_data['parametric_frame'] = {
            'centroid': (0.0, 0.0),
            'axis_u': (1.0, 0.0),
            'axis_v': (0.0, 1.0),
            'extent_u': math.sqrt(2) / 2,
            'extent_v': math.sqrt(2) / 2,
            'num_points': 0,
            'is_valid': False
        }

    return sketch_data


def extract_all_sketches(doc) -> List[Dict[str, Any]]:
    """Estrae tutti gli sketch dal documento."""
    sketches_data = []

    try:
        sketches_coll = doc.Sketches
        if hasattr(sketches_coll, 'Count'):
            for i in range(1, sketches_coll.Count + 1):
                try:
                    sketch = sketches_coll.Item(i)
                    sketch_info = extract_sketch_entities(sketch, i)
                    sketches_data.append(sketch_info)
                except Exception as e:
                    sketches_data.append({
                        'name': f'Sketch_{i}',
                        'index': i,
                        'error': str(e)
                    })
    except Exception as e:
        pass

    # Prova anche ProfileSets
    try:
        profile_sets = doc.ProfileSets
        if hasattr(profile_sets, 'Count'):
            for i in range(1, profile_sets.Count + 1):
                try:
                    ps = profile_sets.Item(i)
                    ps_name = getattr(ps, 'Name', f'ProfileSet_{i}')

                    # Ogni ProfileSet contiene Profiles
                    profiles = ps.Profiles
                    for j in range(1, profiles.Count + 1):
                        profile = profiles.Item(j)

                        profile_data = {
                            'name': f"{ps_name}_Profile_{j}",
                            'index': len(sketches_data) + 1,
                            'geometry_count': 0,
                            'geometry_types': {},
                            'geometry_sequence': [],
                            'constraint_count': 0,
                            'constraint_types': {},
                            'constraint_sequence': [],
                            'from_profile_set': True
                        }

                        geom_counter = Counter()

                        # Lines2d
                        try:
                            lines = profile.Lines2d
                            for k in range(1, lines.Count + 1):
                                geom_counter['Line2d'] += 1
                                profile_data['geometry_sequence'].append('Line2d')
                        except:
                            pass

                        # Arcs2d
                        try:
                            arcs = profile.Arcs2d
                            for k in range(1, arcs.Count + 1):
                                geom_counter['Arc2d'] += 1
                                profile_data['geometry_sequence'].append('Arc2d')
                        except:
                            pass

                        # Circles2d
                        try:
                            circles = profile.Circles2d
                            for k in range(1, circles.Count + 1):
                                geom_counter['Circle2d'] += 1
                                profile_data['geometry_sequence'].append('Circle2d')
                        except:
                            pass

                        profile_data['geometry_types'] = dict(geom_counter)
                        profile_data['geometry_count'] = sum(geom_counter.values())

                        if profile_data['geometry_count'] > 0:
                            sketches_data.append(profile_data)

                except Exception as e:
                    pass
    except:
        pass

    return sketches_data


def extract_properties_via_com(doc) -> Dict[str, Any]:
    """Estrae le proprietÃ  del documento (Author, Company, ecc.) via COM."""
    props_data = {}

    try:
        properties = doc.Properties
        for i in range(1, properties.Count + 1):
            try:
                prop_set = properties.Item(i)
                set_name = getattr(prop_set, 'Name', f'Set_{i}')

                if set_name == 'SummaryInformation':
                    for j in range(1, prop_set.Count + 1):
                        try:
                            p = prop_set.Item(j)
                            pname = getattr(p, 'Name', '')
                            pval = getattr(p, 'Value', '')

                            if pname == 'Author':
                                props_data['author'] = str(pval) if pval else ''
                            elif pname == 'Last Author':
                                props_data['last_author'] = str(pval) if pval else ''
                            elif pname == 'Template':
                                props_data['template'] = str(pval) if pval else ''
                            elif pname == 'Revision Number':
                                props_data['revision'] = str(pval) if pval else ''
                            elif pname == 'Origination Date':
                                props_data['creation_date'] = str(pval) if pval else ''
                            elif pname == 'Last Save Date':
                                props_data['last_save_date'] = str(pval) if pval else ''
                        except:
                            pass

                elif set_name == 'DocumentSummaryInformation':
                    for j in range(1, prop_set.Count + 1):
                        try:
                            p = prop_set.Item(j)
                            pname = getattr(p, 'Name', '')
                            pval = getattr(p, 'Value', '')

                            if pname == 'Company':
                                props_data['company'] = str(pval) if pval else ''
                        except:
                            pass

                elif set_name == 'ExtendedSummaryInformation':
                    for j in range(1, prop_set.Count + 1):
                        try:
                            p = prop_set.Item(j)
                            pname = getattr(p, 'Name', '')
                            pval = getattr(p, 'Value', '')

                            if pname == 'Username':
                                props_data['username'] = str(pval) if pval else ''
                        except:
                            pass
            except:
                pass
    except Exception as e:
        props_data['error'] = str(e)

    return props_data


def extract_features_via_com(filepath: Path, app=None, timeout_seconds=30) -> Dict[str, Any]:
    """Estrae feature tramite COM automation con gestione robusta degli errori."""
    if not HAS_COM:
        return {'error': 'pywin32 non installato'}

    # âœ¨ CRITICO: Assicura che COM sia inizializzato in questo thread
    try:
        import pythoncom
        pythoncom.CoInitialize()  # Ãˆ sicuro chiamarlo piÃ¹ volte - Ãˆ un no-op se giÃ  inizializzato
    except Exception:
        pass  # Ignora errori - potrebbe essere giÃ  inizializzato

    data = {
        'properties': {},
        'feature_list': [],
        'feature_types': Counter(),
        'collections': {},
        'sketches_data': [],
    }
    doc = None

    try:
        # Connessione a Solid Edge
        if app is None:
            try:
                app = win32com.client.GetActiveObject("SolidEdge.Application")
                print("    ðŸ“Œ Connected to existing Solid Edge")
            except:
                app = win32com.client.Dispatch("SolidEdge.Application")
                app.Visible = True
                print("    ðŸ†• Started new Solid Edge")

        # âœ¨ VERIFICA VALIDITÃ€ ISTANZA
        try:
            docs_count = app.Documents.Count
            print(f"    âœ“ Solid Edge valid (docs: {docs_count})")
        except Exception as e:
            print(f"    âŒ Solid Edge instance INVALID: {type(e).__name__}: {e}")
            return {
                'error': f'Solid Edge instance corrupted: {e}',
                'instance_corrupted': True
            }

        app.DisplayAlerts = False

        ext = filepath.suffix.lower()

        if ext in ['.par', '.psm']:
            # âœ¨ APERTURA DIRETTA - Gli oggetti COM non possono essere usati cross-thread
            # Se un file Ã¨ bloccato, Solid Edge potrebbe rimanere in attesa, ma questo Ã¨
            # gestito dal timeout generale dell'analisi e dal restart automatico
            try:
                doc = app.Documents.Open(str(filepath))
            except Exception as e:
                return {
                    'error': f'Failed to open document: {e}',
                    'open_failed': True
                }

            if doc is None:
                return {'error': 'Failed to open document (doc is None)'}


            # Estrai proprietÃ  documento
            data['properties'] = extract_properties_via_com(doc)

            # Estrai feature da tutti i model presenti nel documento.
            try:
                models_count = doc.Models.Count
            except Exception:
                models_count = 0

            data['models_count'] = models_count

            if models_count > 0:
                collections_to_check = [
                    'ExtrudedProtrusions',
                    'ExtrudedCutouts',
                    'Holes',
                    'Rounds',
                    'Chamfers',
                    'RevolvedProtrusions',
                    'RevolvedCutouts',
                    'Sweeps',
                    'Lofts',
                    'CircularPatterns',
                    'RectangularPatterns',
                    'MirrorPatterns',
                    'Shells',
                    'Drafts',
                    'Threads',
                    'Ribs',
                    'Webs',
                    'BooleanFeatures',
                ]
                collection_maxima = Counter()
                features_seen = set()
                feature_index = 0

                for model_idx in range(1, models_count + 1):
                    try:
                        model = doc.Models.Item(model_idx)
                    except Exception as e:
                        data.setdefault('model_errors', []).append({
                            'model_index': model_idx,
                            'error': str(e),
                        })
                        continue

                    try:
                        features_coll = model.Features
                        for local_idx in range(1, features_coll.Count + 1):
                            try:
                                feat = features_coll.Item(local_idx)
                                feat_name = str(getattr(feat, 'Name', f'Feature_{local_idx}'))
                                feat_type_enum = int(getattr(feat, 'Type', 0) or 0)
                                feat_type = get_feature_type_name(feat_type_enum, feat_name)
                                feature_key = (model_idx, feat_name, feat_type_enum, feat_type)

                                if feature_key in features_seen:
                                    continue

                                features_seen.add(feature_key)
                                feature_index += 1
                                data['feature_list'].append({
                                    'index': feature_index,
                                    'model_index': model_idx,
                                    'name': feat_name,
                                    'type': feat_type,
                                    'type_enum': feat_type_enum,
                                })
                                data['feature_types'][feat_type] += 1
                            except Exception as e:
                                feature_index += 1
                                data['feature_list'].append({
                                    'index': feature_index,
                                    'model_index': model_idx,
                                    'name': f'Feature_{local_idx}',
                                    'type': 'Error',
                                    'error': str(e)
                                })
                    except Exception as e:
                        data.setdefault('model_errors', []).append({
                            'model_index': model_idx,
                            'error': str(e),
                            'context': 'Features',
                        })

                    for coll_name in collections_to_check:
                        try:
                            coll = getattr(model, coll_name)
                            if hasattr(coll, 'Count'):
                                collection_maxima[coll_name] = max(collection_maxima[coll_name], int(coll.Count))
                        except:
                            pass

                data['collections'] = dict(collection_maxima)

            # Conta sketches
            try:
                data['sketches_count'] = doc.Sketches.Count
            except:
                data['sketches_count'] = 0

            # Estrai dati dettagliati sketch
            data['sketches_data'] = extract_all_sketches(doc)

            doc.Close(False)

        elif ext == '.asm':
            doc = app.Documents.Open(str(filepath))
            data['properties'] = extract_properties_via_com(doc)

            try:
                data['occurrences_count'] = doc.Occurrences.Count
            except:
                pass

            try:
                data['relations_count'] = doc.Relations3d.Count
            except:
                pass

            doc.Close(False)

    except Exception as e:
        data['error'] = str(e)
        import traceback
        data['traceback'] = traceback.format_exc()

    finally:
        # âœ¨ CHIUSURA FORZATA - Garantisce che il documento venga sempre chiuso
        if doc:
            try:
                doc.Close(SaveChanges=False)
            except:
                # Se Close() fallisce, prova metodi alternativi
                try:
                    doc.Close(False)
                except:
                    pass
                try:
                    # Force quit del documento tramite app
                    if app and hasattr(app, 'Documents'):
                        for i in range(app.Documents.Count, 0, -1):
                            try:
                                app.Documents.Item(i).Close(False)
                            except:
                                pass
                except:
                    pass

    return data


def analyze_naming_style(feature_names: List[str]) -> str:
    """Analizza lo stile di naming delle feature."""
    if not feature_names:
        return "unknown"

    # Conta pattern
    patterns = Counter()
    for name in feature_names:
        if '_' in name and name.split('_')[-1].isdigit():
            patterns['default_numbered'] += 1  # Es: ExtrudedProtrusion_1
        elif name[0].isupper() and '_' not in name:
            patterns['camelcase'] += 1
        elif name.lower().startswith(('feat', 'feature')):
            patterns['generic'] += 1
        else:
            patterns['custom'] += 1

    if patterns:
        return patterns.most_common(1)[0][0]
    return "unknown"


def compute_feature_sequences(feature_types: List[str]) -> List[Tuple[str, int]]:
    """Calcola i bigram (coppie consecutive) di feature piÃº comuni."""
    if len(feature_types) < 2:
        return []

    bigrams = []
    for i in range(len(feature_types) - 1):
        bigram = f"{feature_types[i]} â†’ {feature_types[i+1]}"
        bigrams.append(bigram)

    return Counter(bigrams).most_common(5)


def extract_signature(filepath: Path, app=None) -> FeatureSignature:
    """Estrae la firma completa da un file Solid Edge."""

    sig = FeatureSignature(
        filename=filepath.name,
        filepath=str(filepath),
        file_hash=compute_file_hash(filepath)
    )

    # Estrai tutto via COM
    data = extract_features_via_com(filepath, app=app)

    # âœ¨ Controlla se l'istanza Ã¨ corrotta
    if data.get('instance_corrupted', False):
        print(f"    âš ï¸ Errore: {data.get('error', 'Instance corrupted')}")
        sig.instance_corrupted = True  # Flag per forzare restart
        return sig

    if 'error' in data:
        print(f"    âš ï¸ Errore: {data['error']}")
        return sig

    # ProprietÃ  documento
    props = data.get('properties', {})
    sig.author = props.get('author', '') or props.get('username', '')
    sig.last_author = props.get('last_author', '')
    sig.company = props.get('company', '')
    sig.creation_date = props.get('creation_date', '')
    sig.last_save_date = props.get('last_save_date', '')
    sig.template = props.get('template', '')
    sig.revision = props.get('revision', '')

    # Feature
    feature_list = data.get('feature_list', [])
    sig.feature_count = len(feature_list)
    sig.feature_types = dict(data.get('feature_types', {}))
    sig.feature_sequence = [f['type'] for f in feature_list]
    sig.feature_names = [f['name'] for f in feature_list]

    # Collezioni specifiche
    colls = data.get('collections', {})
    feature_types = Counter(sig.feature_types)
    sig.extrusions_count = feature_types.get('ExtrudedProtrusion', colls.get('ExtrudedProtrusions', 0))
    sig.cutouts_count = feature_types.get('ExtrudedCutout', colls.get('ExtrudedCutouts', 0))
    sig.holes_count = feature_types.get('Hole', colls.get('Holes', 0))
    sig.rounds_count = feature_types.get('Round', colls.get('Rounds', 0))
    sig.chamfers_count = feature_types.get('Chamfer', colls.get('Chamfers', 0))
    sig.sketches_count = data.get('sketches_count', 0)

    # Calcola rapporti
    total = sig.feature_count or 1
    sig.extrusion_ratio = sig.extrusions_count / total
    sig.cutout_ratio = sig.cutouts_count / total
    sig.hole_ratio = sig.holes_count / total
    sig.round_chamfer_ratio = (sig.rounds_count + sig.chamfers_count) / total

    # Sequenze
    sig.common_sequences = compute_feature_sequences(sig.feature_sequence)

    # Stile naming
    sig.naming_style = analyze_naming_style(sig.feature_names)

    # === DATI SKETCH ===
    sig.sketches_data = data.get('sketches_data', [])

    # Aggrega dati sketch
    all_geom_types = Counter()
    all_constraint_types = Counter()
    total_geom = 0
    total_constr = 0

    for sk in sig.sketches_data:
        total_geom += sk.get('geometry_count', 0)
        total_constr += sk.get('constraint_count', 0)
        all_geom_types.update(sk.get('geometry_types', {}))
        all_constraint_types.update(sk.get('constraint_types', {}))

    sig.total_2d_geometry_count = total_geom
    sig.total_2d_constraint_count = total_constr
    sig.geometry_2d_types = dict(all_geom_types)
    sig.constraint_2d_types = dict(all_constraint_types)

    # Pattern 2D
    num_sketches = len(sig.sketches_data) or 1
    sig.avg_geometry_per_sketch = total_geom / num_sketches
    sig.avg_constraints_per_sketch = total_constr / num_sketches
    sig.constraint_to_geometry_ratio = total_constr / max(total_geom, 1)

    # âœ¨ SERIALIZZAZIONE JSON - Assicura che i dati siano JSON-serializzabili
    # Questo Ã¨ importante per la trasmissione via API
    try:
        sig.sketches_data = json.loads(json.dumps(sig.sketches_data, default=str))
    except Exception as e:
        print(f"âš ï¸ Errore serializzazione sketch_data: {e}")
        # Continua comunque, i dati potrebbero funzionare

    # âœ¨ PERSISTENZA: Salva i dati sketch in cache
    if sig.sketches_data:
        save_sketch_data(str(filepath), sig.sketches_data)

    return sig


# ========== GESTIONE PESI GLOBALI ==========

def get_weights_filepath() -> Path:
    """Path del file pesi globali nella cache utente."""
    p = Path.home() / '.cache' / 'solid_edge_similarity'
    p.mkdir(parents=True, exist_ok=True)
    return p / 'weights.json'


def get_config_filepath() -> Path:
    """Path del file config.json nella directory del progetto."""
    return Path(__file__).parent / 'config.json'


def load_config() -> dict:
    """Carica la configurazione dal file config.json."""
    try:
        config_path = get_config_filepath()
        if config_path.exists():
            # âœ¨ Controlla se il file Ã¨ vuoto
            if config_path.stat().st_size == 0:
                print(f"âš ï¸ Config file is empty: {config_path}")
                return get_default_config()

            with open(config_path, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content.strip():
                    print(f"âš ï¸ Config file contains only whitespace: {config_path}")
                    return get_default_config()

                try:
                    return json.loads(content)
                except json.JSONDecodeError as je:
                    print(f"âŒ Invalid JSON in config file: {config_path}")
                    print(f"   Error: {je}")
                    print(f"   Using default configuration")
                    return get_default_config()
    except Exception as e:
        print(f"âš ï¸ Errore caricamento config.json: {e}")

    # Fallback se config non esiste
    return get_default_config()


def get_default_config() -> dict:
    """Restituisce la configurazione di default."""
    return {
        'default_weights': {
            'author_match': 0.02,
            'feature_count_similarity': 0.05,
            'feature_type_similarity': 0.11,
            'style_similarity': 0.04,
            'bigram_similarity': 0.02,
            'trigram_similarity': 0.03,
            'lcs_similarity': 0.25,
            'feature_names_similarity': 0.02,
            'geometry_2d_similarity': 0.26,
            'constraint_2d_similarity': 0.11,
            'constraint_ratio_similarity': 0.09,
            'sketch_parametric_similarity': 0.00  # Nuovo: similaritÃ  basata su frame (u,v)
        }
    }


# Carica i default weights dal config
_config = load_config()
DEFAULT_WEIGHTS = _config.get('default_weights', {
    'author_match': 0.02,
    'feature_count_similarity': 0.05,
    'feature_type_similarity': 0.11,
    'style_similarity': 0.04,
    'bigram_similarity': 0.02,
    'trigram_similarity': 0.03,
    'lcs_similarity': 0.25,
    'feature_names_similarity': 0.02,
    'geometry_2d_similarity': 0.26,
    'constraint_2d_similarity': 0.11,
    'constraint_ratio_similarity': 0.09,
    'sketch_parametric_similarity': 0.00,  # SimilaritÃ  basata su frame (u,v)
    # âœ¨ Parametri fuzzy LCS
    'lcs_fuzzy_enabled': True,
    'lcs_fuzzy_function': 'exponential',
    'lcs_fuzzy_alpha': 2.0,
    'lcs_fuzzy_mix': 0.7,
    # âœ¨ Parametri fuzzy combination
    'fuzzy_combination_enabled': False,
    'fuzzy_combination_method': 'gaussian',
    'fuzzy_combination_penalty': 0.3,
    'fuzzy_combination_boost': 0.15
})


def load_weights() -> dict:
    """Carica i pesi globali dalla cache; restituisce i DEFAULT_WEIGHTS se non presenti."""
    try:
        wf = get_weights_filepath()
        if wf.exists():
            # âœ¨ Controlla se il file Ã¨ vuoto
            if wf.stat().st_size == 0:
                print(f"âš ï¸ Weights file is empty: {wf}")
                return dict(DEFAULT_WEIGHTS)

            with open(wf, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content.strip():
                    print(f"âš ï¸ Weights file contains only whitespace: {wf}")
                    return dict(DEFAULT_WEIGHTS)

                try:
                    data = json.loads(content)
                except json.JSONDecodeError as je:
                    print(f"âŒ Invalid JSON in weights file: {wf}")
                    print(f"   Error: {je}")
                    print(f"   Renaming corrupted file...")
                    # Rinomina il file corrotto
                    import time
                    backup_path = wf.with_suffix(f'.json.corrupted.{int(time.time())}')
                    wf.rename(backup_path)
                    print(f"   Corrupted file backed up to: {backup_path}")
                    return dict(DEFAULT_WEIGHTS)

            # âœ… Converti in float solo i pesi numerici, non i parametri fuzzy
            out = {}
            for k in DEFAULT_WEIGHTS:
                value = data.get(k, DEFAULT_WEIGHTS[k])
                # Se Ã¨ un parametro fuzzy (lcs_fuzzy_* o fuzzy_combination_*), mantieni il tipo originale
                if k.startswith('lcs_fuzzy_') or k.startswith('fuzzy_combination_'):
                    out[k] = value
                else:
                    # Altrimenti converti in float (Ã¨ un peso numerico)
                    out[k] = float(value)
            return out
    except Exception as e:
        print(f"âš ï¸ Errore load_weights: {e}")
    return dict(DEFAULT_WEIGHTS)


def save_weights(weights: dict) -> bool:
    """Salva i pesi globali sulla cache; ritorna True se OK."""
    try:
        wf = get_weights_filepath()

        # âœ… Prepara dati: converti in float solo i pesi numerici, non i parametri fuzzy
        data_to_save = {}
        for k in DEFAULT_WEIGHTS:
            value = weights.get(k, DEFAULT_WEIGHTS[k])
            # Se Ã¨ un parametro fuzzy (lcs_fuzzy_* o fuzzy_combination_*), salvalo cosÃ¬ com'Ã¨
            if k.startswith('lcs_fuzzy_') or k.startswith('fuzzy_combination_'):
                data_to_save[k] = value
            else:
                # Altrimenti converti in float (Ã¨ un peso numerico)
                data_to_save[k] = float(value)

        with open(wf, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, indent=2)
        print(f"âœ… Pesi salvati: {wf}")
        return True
    except Exception as e:
        print(f"âš ï¸ Errore save_weights: {e}")
        return False


# ============================================================================
# SIMILARITA SKETCH TOPOLOGICA - Grafo di primitive 2D e loro connessioni
# ============================================================================

def _to_point2d(value: Any) -> Optional[Tuple[float, float]]:
    try:
        if value is None:
            return None
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            x = float(value[0])
            y = float(value[1])
            if math.isfinite(x) and math.isfinite(y):
                return (x, y)
    except Exception:
        pass
    return None


def _distance_2d(p1: Tuple[float, float], p2: Tuple[float, float]) -> float:
    return math.hypot(float(p1[0]) - float(p2[0]), float(p1[1]) - float(p2[1]))


def _ratio_similarity(v1: float, v2: float) -> float:
    a = max(0.0, float(v1))
    b = max(0.0, float(v2))
    if a <= 1e-12 and b <= 1e-12:
        return 1.0
    return max(0.0, min(1.0, 1.0 - abs(a - b) / max(a, b, 1.0)))


def _counter_jaccard(counts1: Dict[str, int], counts2: Dict[str, int]) -> float:
    keys = set(counts1.keys()) | set(counts2.keys())
    if not keys:
        return 1.0
    inter = sum(min(int(counts1.get(k, 0)), int(counts2.get(k, 0))) for k in keys)
    union = sum(max(int(counts1.get(k, 0)), int(counts2.get(k, 0))) for k in keys)
    return inter / union if union > 0 else 1.0


def _resample_sorted_values(sorted_vals: List[float], m: int) -> List[float]:
    if not sorted_vals or m <= 0:
        return []
    n = len(sorted_vals)
    if n == m:
        return list(sorted_vals)
    if m == 1:
        return [sorted_vals[n // 2]]
    sampled = []
    for i in range(m):
        pos = i * (n - 1) / (m - 1)
        idx = int(round(pos))
        idx = max(0, min(n - 1, idx))
        sampled.append(sorted_vals[idx])
    return sampled


def _raw_profile_similarity(values1: List[float], values2: List[float]) -> float:
    vals1 = sorted(float(v) for v in values1 if float(v) > 1e-12)
    vals2 = sorted(float(v) for v in values2 if float(v) > 1e-12)
    if not vals1 and not vals2:
        return 1.0
    if not vals1 or not vals2:
        return 0.0
    m = max(1, min(len(vals1), len(vals2)))
    a = _resample_sorted_values(vals1, m)
    b = _resample_sorted_values(vals2, m)
    sims = []
    for x, y in zip(a, b):
        sims.append(_ratio_similarity(x, y))
    count_ratio = min(len(vals1), len(vals2)) / max(len(vals1), len(vals2), 1)
    return 0.75 * (sum(sims) / max(len(sims), 1)) + 0.25 * count_ratio


def _normalized_measure_profile_similarity(values1: List[float], values2: List[float]) -> float:
    vals1 = sorted(float(v) for v in values1 if math.isfinite(float(v)) and float(v) > 1e-9)
    vals2 = sorted(float(v) for v in values2 if math.isfinite(float(v)) and float(v) > 1e-9)
    if not vals1 and not vals2:
        return 1.0
    if not vals1 or not vals2:
        return 0.0

    def _median_base(sorted_vals: List[float]) -> float:
        n = len(sorted_vals)
        if n % 2 == 1:
            return sorted_vals[n // 2]
        return 0.5 * (sorted_vals[n // 2 - 1] + sorted_vals[n // 2])

    base1 = max(_median_base(vals1), 1e-9)
    base2 = max(_median_base(vals2), 1e-9)
    norm1 = [v / base1 for v in vals1]
    norm2 = [v / base2 for v in vals2]
    return _raw_profile_similarity(norm1, norm2)


def _extract_geometry_measure(geom: Dict[str, Any]) -> Optional[float]:
    geom_type = str(geom.get('type', ''))
    start = _to_point2d(geom.get('start_point'))
    end = _to_point2d(geom.get('end_point'))
    radius = geom.get('radius')

    try:
        if geom_type in ('Line2d', 'Arc2d') and start and end:
            return _distance_2d(start, end)
        if geom_type in ('Circle2d', 'Arc2d') and radius is not None:
            r = abs(float(radius))
            if math.isfinite(r) and r > 1e-9:
                return r
    except Exception:
        pass
    return None


def _build_sketch_topology_signature(sketch_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    cached = sketch_data.get('_topology_signature')
    if isinstance(cached, dict):
        return cached

    geometry_entries = sketch_data.get('geometry_detailed') or []
    if not geometry_entries:
        return None

    point_candidates: List[Tuple[float, float]] = []
    primitives: List[Dict[str, Any]] = []

    for geom in geometry_entries:
        geom_type = str(geom.get('type', ''))
        profile_index = int(geom.get('profile_index', 0) or 0)
        start = _to_point2d(geom.get('start_point'))
        end = _to_point2d(geom.get('end_point'))
        center = _to_point2d(geom.get('center_point'))
        measure = _extract_geometry_measure(geom)

        if geom_type in ('Line2d', 'Arc2d') and start and end:
            point_candidates.extend([start, end])
            primitives.append({
                'kind': 'edge',
                'type': geom_type,
                'profile_index': profile_index,
                'points': (start, end),
                'measure': measure
            })
        elif geom_type in ('Circle2d', 'Ellipse2d'):
            if center:
                point_candidates.append(center)
            primitives.append({
                'kind': 'closed',
                'type': geom_type,
                'profile_index': profile_index,
                'points': tuple(),
                'measure': measure
            })

    if not primitives:
        return None

    if point_candidates:
        xs = [p[0] for p in point_candidates]
        ys = [p[1] for p in point_candidates]
        bbox_diag = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    else:
        bbox_diag = 0.0
    merge_tol = max(1e-6, bbox_diag * 1e-3)

    nodes: List[Tuple[float, float]] = []

    def _get_node_index(pt: Tuple[float, float]) -> int:
        for idx, existing in enumerate(nodes):
            if _distance_2d(existing, pt) <= merge_tol:
                return idx
        nodes.append(pt)
        return len(nodes) - 1

    edges: List[Tuple[int, int, str]] = []
    degree_counter: Counter = Counter()
    adjacency: Dict[int, set] = {}
    component_edge_counter: Counter = Counter()
    primitive_type_counts: Counter = Counter()
    profile_primitive_counts: Counter = Counter()
    closed_rimitives_count = 0
    measure_profile: List[float] = []

    for primitive in primitives:
        primitive_type_counts[primitive['type']] += 1
        profile_primitive_counts[int(primitive.get('profile_index', 0) or 0)] += 1
        measure = primitive.get('measure')
        if measure is not None and math.isfinite(float(measure)) and float(measure) > 1e-9:
            measure_profile.append(float(measure))

        if primitive['kind'] == 'edge':
            p1, p2 = primitive['points']
            n1 = _get_node_index(p1)
            n2 = _get_node_index(p2)
            edges.append((n1, n2, primitive['type']))
            if n1 == n2:
                degree_counter[n1] += 2
            else:
                degree_counter[n1] += 1
                degree_counter[n2] += 1
                adjacency.setdefault(n1, set()).add(n2)
                adjacency.setdefault(n2, set()).add(n1)
        else:
            closed_rimitives_count += 1

    open_components = 0
    component_sizes: List[float] = []
    visited = set()
    edge_map: Dict[int, List[Tuple[int, int, str]]] = {}
    for a, b, edge_type in edges:
        edge_map.setdefault(a, []).append((a, b, edge_type))
        edge_map.setdefault(b, []).append((a, b, edge_type))

    for start_node in set(list(adjacency.keys()) + list(degree_counter.keys())):
        if start_node in visited:
            continue
        stack = [start_node]
        nodes_in_component = set()
        edges_in_component = set()
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            nodes_in_component.add(node)
            for edge in edge_map.get(node, []):
                edges_in_component.add(edge)
            for neigh in adjacency.get(node, set()):
                if neigh not in visited:
                    stack.append(neigh)
        if nodes_in_component:
            open_components += 1
            component_edge_counter[open_components] = len(edges_in_component)
            component_sizes.append(float(len(edges_in_component)))

    component_sizes.extend([1.0] * closed_rimitives_count)

    node_degree_hist = Counter()
    for _, degree in degree_counter.items():
        if degree <= 1:
            node_degree_hist['deg1'] += 1
        elif degree == 2:
            node_degree_hist['deg2'] += 1
        else:
            node_degree_hist['deg3p'] += 1

    open_endpoint_count = int(node_degree_hist.get('deg1', 0))
    junction_count = int(node_degree_hist.get('deg3p', 0))
    cycle_rank = max(0, len(edges) - len(degree_counter) + open_components) + closed_rimitives_count

    signature = {
        'primitive_type_counts': dict(primitive_type_counts),
        'primitive_count': int(sum(primitive_type_counts.values())),
        'node_degree_hist': dict(node_degree_hist),
        'component_count': int(open_components + closed_rimitives_count),
        'open_components': int(open_components),
        'closed_primitive_count': int(closed_rimitives_count),
        'open_endpoint_count': open_endpoint_count,
        'junction_count': junction_count,
        'cycle_rank': int(cycle_rank),
        'profile_count': int(len(profile_primitive_counts)),
        'component_sizes': sorted(component_sizes),
        'measure_profile': sorted(measure_profile),
        'is_valid': True
    }
    sketch_data['_topology_signature'] = signature
    return signature


def compute_sketch_pair_similarity(sk1_data: Dict, sk2_data: Dict) -> Optional[float]:
    """
    Calcola la similarita tra due sketch usando una firma topologica.

    La metrica usa un grafo di primitive 2D ricostruito dalle coordinate COM:
    primitive, connessioni tra estremi, componenti, cicli, endpoint aperti,
    giunzioni e profili di misura normalizzati.
    """
    sig1 = _build_sketch_topology_signature(sk1_data)
    sig2 = _build_sketch_topology_signature(sk2_data)
    if not sig1 or not sig2:
        return None

    type_sim = _counter_jaccard(sig1.get('primitive_type_counts', {}), sig2.get('primitive_type_counts', {}))
    degree_sim = _counter_jaccard(sig1.get('node_degree_hist', {}), sig2.get('node_degree_hist', {}))
    if not sig1.get('node_degree_hist') and not sig2.get('node_degree_hist'):
        degree_sim = 0.5
    component_profile_sim = _raw_profile_similarity(sig1.get('component_sizes', []), sig2.get('component_sizes', []))
    measure_profile_sim = _normalized_measure_profile_similarity(
        sig1.get('measure_profile', []), sig2.get('measure_profile', [])
    )

    scalar_topology_sim = (
        0.20 * _ratio_similarity(sig1.get('component_count', 0), sig2.get('component_count', 0)) +
        0.20 * _ratio_similarity(sig1.get('cycle_rank', 0), sig2.get('cycle_rank', 0)) +
        0.16 * _ratio_similarity(sig1.get('open_endpoint_count', 0), sig2.get('open_endpoint_count', 0)) +
        0.12 * _ratio_similarity(sig1.get('junction_count', 0), sig2.get('junction_count', 0)) +
        0.18 * _ratio_similarity(sig1.get('closed_primitive_count', 0), sig2.get('closed_primitive_count', 0)) +
        0.06 * _ratio_similarity(sig1.get('profile_count', 0), sig2.get('profile_count', 0)) +
        0.08 * _ratio_similarity(sig1.get('primitive_count', 0), sig2.get('primitive_count', 0))
    )

    similarity = (
        0.28 * type_sim +
        0.18 * degree_sim +
        0.28 * scalar_topology_sim +
        0.16 * component_profile_sim +
        0.10 * measure_profile_sim
    )
    return max(0.0, min(1.0, similarity))


def compute_sketch_geometry_similarity(sk1_data: Dict, sk2_data: Dict) -> float:
    """
    Calcola similaritÃ  tra sketch basandosi solo su geometrie e vincoli.
    Usato come fallback quando il frame parametrico non Ã¨ valido.
    """
    # Tipi geometrici
    geom_types1 = sk1_data.get('geometry_types', {})
    geom_types2 = sk2_data.get('geometry_types', {})

    all_geom = set(geom_types1.keys()) | set(geom_types2.keys())
    if all_geom:
        jaccard = len(set(geom_types1.keys()) & set(geom_types2.keys())) / len(all_geom)
    else:
        jaccard = 0.5

    # Conteggio geometrie
    count1 = sk1_data.get('geometry_count', 0)
    count2 = sk2_data.get('geometry_count', 0)
    count_sim = 1.0 - abs(count1 - count2) / max(count1, count2, 1)

    # Tipi vincoli
    constr_types1 = sk1_data.get('constraint_types', {})
    constr_types2 = sk2_data.get('constraint_types', {})

    all_constr = set(constr_types1.keys()) | set(constr_types2.keys())
    if all_constr:
        constr_jaccard = len(set(constr_types1.keys()) & set(constr_types2.keys())) / len(all_constr)
    else:
        constr_jaccard = 0.5

    return 0.40 * jaccard + 0.30 * count_sim + 0.30 * constr_jaccard


def compute_sketch_dimension_profile_similarity(sk1_data: Dict, sk2_data: Dict) -> float:
    """
    SimilaritÃ  del profilo dimensionale dello sketch, robusta alla scala assoluta.

    Usa i valori quota (dimension_values), normalizzati sul valore mediano
    per confrontare la forma (rapporti) piu che le dimensioni assolute.
    """
    def _clean(vals):
        out = []
        for v in vals or []:
            try:
                fv = abs(float(v))
                if math.isfinite(fv) and fv > 1e-9:
                    out.append(fv)
            except Exception:
                pass
        out.sort()
        return out

    def _resample(sorted_vals, m):
        if not sorted_vals or m <= 0:
            return []
        n = len(sorted_vals)
        if n == m:
            return list(sorted_vals)
        if m == 1:
            return [sorted_vals[n // 2]]
        sampled = []
        for i in range(m):
            pos = i * (n - 1) / (m - 1)
            idx = int(round(pos))
            idx = max(0, min(n - 1, idx))
            sampled.append(sorted_vals[idx])
        return sampled

    vals1 = _clean(sk1_data.get('dimension_values', []))
    vals2 = _clean(sk2_data.get('dimension_values', []))

    if not vals1 and not vals2:
        return 0.5
    if not vals1 or not vals2:
        return 0.2

    def _median_base(sorted_vals):
        n = len(sorted_vals)
        if n % 2 == 1:
            return sorted_vals[n // 2]
        return 0.5 * (sorted_vals[n // 2 - 1] + sorted_vals[n // 2])

    base1 = max(_median_base(vals1), 1e-9)
    base2 = max(_median_base(vals2), 1e-9)
    norm1 = [v / base1 for v in vals1]
    norm2 = [v / base2 for v in vals2]

    m = max(1, min(len(norm1), len(norm2)))
    a = _resample(norm1, m)
    b = _resample(norm2, m)

    point_sims = []
    for x, y in zip(a, b):
        denom = max(abs(x), abs(y), 1e-9)
        sim = 1.0 - abs(x - y) / denom
        point_sims.append(max(0.0, min(1.0, sim)))

    shape_sim = sum(point_sims) / max(len(point_sims), 1)
    count_ratio = min(len(norm1), len(norm2)) / max(len(norm1), len(norm2), 1)

    return 0.75 * shape_sim + 0.25 * count_ratio


def match_sketches_greedy(sketches1: List[Dict], sketches2: List[Dict]) -> List[Tuple[int, int, float]]:
    """
    Effettua il matching greedy tra gli sketch di due modelli.

    Strategia:
    - Prende il modello con meno sketch
    - Per ogni sketch di quel modello, trova il miglior match nell'altro
    - Ogni sketch puÃ² essere matchato una sola volta

    Returns:
        Lista di (idx1, idx2, similarity) per ogni coppia matchata
    """
    if not sketches1 or not sketches2:
        return []

    # Assicura che sketches1 sia quello con meno elementi
    swapped = False
    if len(sketches1) > len(sketches2):
        sketches1, sketches2 = sketches2, sketches1
        swapped = True

    # Calcola matrice di similaritÃ 
    n1 = len(sketches1)
    n2 = len(sketches2)
    similarity_matrix = []

    for i in range(n1):
        row = []
        for j in range(n2):
            sim = compute_sketch_pair_similarity(sketches1[i], sketches2[j])
            row.append(sim if sim is not None else -1.0)
        similarity_matrix.append(row)

    # Matching greedy
    matches = []
    used_j = set()

    for i in range(n1):
        best_j = -1
        best_sim = -1

        for j in range(n2):
            if j not in used_j and similarity_matrix[i][j] > best_sim:
                best_sim = similarity_matrix[i][j]
                best_j = j

        if best_j >= 0 and best_sim >= 0.0:
            if swapped:
                matches.append((best_j, i, best_sim))
            else:
                matches.append((i, best_j, best_sim))
            used_j.add(best_j)

    return matches


def compute_sketch_parametric_similarity(sig1: 'FeatureSignature', sig2: 'FeatureSignature') -> Tuple[Optional[float], List[Tuple[int, int, float]]]:
    """
    Calcola la similarita complessiva tra sketch usando una firma topologica.

    Algoritmo:
    1. Prende min(num_sketch1, num_sketch2) come numero di coppie
    2. Matcha ogni sketch del modello minore al corrispondente piÃ¹ simile
    3. La similaritÃ  totale Ã¨ la media delle similaritÃ  delle coppie

    Returns:
        (similarity_score, list_of_matches)
    """
    sk1 = sig1.sketches_data
    sk2 = sig2.sketches_data

    if not sk1 or not sk2:
        return None, []  # Criterio non valutabile

    # Converti in dizionari se necessario (per compatibilitÃ )
    if sk1 and hasattr(sk1[0], '__dict__'):
        sk1 = [s.__dict__ if hasattr(s, '__dict__') else s for s in sk1]
    if sk2 and hasattr(sk2[0], '__dict__'):
        sk2 = [s.__dict__ if hasattr(s, '__dict__') else s for s in sk2]

    # Se disponibili, preferisci gli sketch nativi e ignora i ProfileSet duplicati.
    # Questo evita di "contare due volte" la stessa geometria del profilo.
    native_sk1 = [s for s in sk1 if not isinstance(s, dict) or not s.get('from_profile_set')]
    native_sk2 = [s for s in sk2 if not isinstance(s, dict) or not s.get('from_profile_set')]
    if native_sk1:
        sk1 = native_sk1
    if native_sk2:
        sk2 = native_sk2

    def _is_topology_ready(sk_data):
        if not isinstance(sk_data, dict):
            return False
        topo = _build_sketch_topology_signature(sk_data)
        return bool(topo and topo.get('is_valid'))

    # Nessun fallback: consideriamo solo sketch con firma topologica costruibile.
    sk1 = [s for s in sk1 if _is_topology_ready(s)]
    sk2 = [s for s in sk2 if _is_topology_ready(s)]
    if not sk1 or not sk2:
        return None, []

    n1 = len(sk1)
    n2 = len(sk2)

    # âœ¨ FIX DEFINITIVO per file identici:
    # Se stesso numero di sketch, prova ANCHE matching posizionale (i con i)
    # e prendi il massimo tra greedy e posizionale
    if n1 == n2:
        # Matching posizionale: sketch i con sketch i
        positional_matches = []
        positional_total = 0.0
        for i in range(n1):
            sim = compute_sketch_pair_similarity(sk1[i], sk2[i])
            positional_matches.append((i, i, sim))
            positional_total += sim
        positional_avg = positional_total / n1 if n1 > 0 else 0.5

        # Matching greedy
        greedy_matches = match_sketches_greedy(sk1, sk2)
        greedy_avg = sum(m[2] for m in greedy_matches) / len(greedy_matches) if greedy_matches else 0.5

        # Prendi il matching migliore
        if positional_avg >= greedy_avg:
            matches = positional_matches
            avg_similarity = positional_avg
        else:
            matches = greedy_matches
            avg_similarity = greedy_avg

        count_ratio = 1.0  # Stesso numero
    else:
        # Effettua matching greedy (numero diverso di sketch)
        matches = match_sketches_greedy(sk1, sk2)

        if not matches:
            return None, []

        avg_similarity = sum(m[2] for m in matches) / len(matches)
        count_ratio = min(n1, n2) / max(n1, n2, 1)

    # Combina: 80% match similarity + 20% count ratio penalty
    final_similarity = 0.80 * avg_similarity + 0.20 * count_ratio

    return final_similarity, matches


def compute_raw_scores(sig1: FeatureSignature, sig2: FeatureSignature,
                       lcs_fuzzy_config: dict = None) -> dict:
    """
    Estrae i punteggi grezzi per ogni criterio di similaritÃ  (FASE LENTA).

    Questa funzione confronta due firme CAD e restituisce uno score 0..1
    per ogni criterio. NON applica pesi nÃ© fuzzy combination.
    Il risultato puÃ² essere ri-combinato istantaneamente con combine_scores().

    Args:
        sig1, sig2: Firme da confrontare
        lcs_fuzzy_config: Configurazione fuzzy per LCS (opzionale).
                         Se None, carica dalla config globale.
                         Chiavi: lcs_fuzzy_enabled, lcs_fuzzy_function, lcs_fuzzy_alpha, lcs_fuzzy_mix

    Returns:
        dict con chiavi:
            - criteri numerici (str -> float 0..1): 'author_match', 'feature_count_similarity', ...
            - metadati (str prefissati '_' -> any): '_sketch_matches', ...
    """
    scores = {}

    def _multiset_jaccard(counts1: Dict[str, int], counts2: Dict[str, int]) -> float:
        keys = set(counts1.keys()) | set(counts2.keys())
        if not keys:
            return 0.0
        inter = sum(min(int(counts1.get(k, 0)), int(counts2.get(k, 0))) for k in keys)
        union = sum(max(int(counts1.get(k, 0)), int(counts2.get(k, 0))) for k in keys)
        return inter / union if union > 0 else 0.0

    def _normalize_name_text(name: str) -> str:
        n = unicodedata.normalize('NFKD', str(name or ''))
        n = ''.join(ch for ch in n if not unicodedata.combining(ch))
        n = n.strip().lower()
        n = re.sub(r'[^a-z0-9]+', ' ', n)
        n = re.sub(r'\s+', ' ', n).strip()
        return n

    def _compact_alnum(text: str) -> str:
        return re.sub(r'[^a-z0-9]+', '', text or '')

    def _is_default_feature_name(name: str, feature_type: str = '') -> bool:
        n = _normalize_name_text(name)
        if not n:
            return True
        compact = _compact_alnum(n)
        if not compact:
            return True

        default_roots = {
            # English / canonical feature roots
            'extrudedprotrusion', 'revolvedprotrusion', 'extrudedcutout', 'revolvedcutout',
            'loftedprotrusion', 'loftedcutout', 'sweptprotrusion', 'sweptcutout',
            'protrusion', 'cutout', 'hole', 'round', 'chamfer', 'pattern',
            'circularpattern', 'rectangularpattern', 'mirror', 'mirrorcopy', 'mirrorbody',
            'loft', 'sweep', 'sketch', 'refplane', 'plane', 'draft', 'shell',
            'rib', 'web', 'lip', 'thread', 'thinwall', 'thicken', 'boolean',
            'slot', 'facemove', 'normalcutout', 'feature', 'feat',
            # Other common auto-generated roots observed in datasets
            'blend', 'offset', 'resizehole', 'faceset', 'thinregion', 'deleteface',
            'copiedpart', 'scalebody', 'bodyfeature', 'union', 'subtract', 'deleteregion',
            'facerotate', 'helixprotrusion', 'helixcutout', 'lipgroove', 'base',
            # Italian roots often generated by localized environments
            'protrusione', 'protrusioneestrusa', 'scavo', 'scavoestrusione', 'taglio',
            'foro', 'raccordo', 'smusso', 'schizzo', 'piano', 'specchio',
            'filettatura', 'campitura', 'rotondo', 'spoglia', 'ripetizione',
            'estrusione', 'protrusionerivoluzione', 'copiaspeculare',
        }
        if feature_type:
            ft = _normalize_name_text(feature_type)
            ft_compact = _compact_alnum(ft)
            if ft_compact:
                default_roots.add(ft_compact)
            for token in ft.split():
                if len(token) >= 4:
                    default_roots.add(_compact_alnum(token))

        generic_suffixes = ('', 'copy', 'body', 'feature', 'feat', 'operation', 'op', 'item')
        for root in default_roots:
            rr = _compact_alnum(root)
            if not rr:
                continue
            for suffix in generic_suffixes:
                if re.fullmatch(rf'{re.escape(rr)}{re.escape(suffix)}\d*', compact):
                    return True

        # Generic software-assigned naming like "Feature 12"
        if re.fullmatch(r'(feature|feat)\d*', compact):
            return True

        # "Root + number" with spaced tokens, e.g. "Foro 1", "Normal Cutout 2"
        tokens = [t for t in n.split() if t]
        non_numeric_tokens = [t for t in tokens if not t.isdigit()]
        if tokens and len(tokens) <= 4 and any(t.isdigit() for t in tokens):
            base = _compact_alnum(' '.join(non_numeric_tokens))
            if base in default_roots:
                return True

        return False

    def _normalize_custom_name(name: str) -> str:
        n = _normalize_name_text(name)
        tokens = re.findall(r'[a-z0-9]+', n)
        tokens = [t for t in tokens if not t.isdigit()]
        stop_tokens = {
            'feature', 'feat', 'copy', 'body', 'normal', 'operation', 'op',
            'item', 'part', 'cad',
        }
        tokens = [t for t in tokens if t not in stop_tokens]
        return ' '.join(tokens)

    def _collect_custom_names(signature: FeatureSignature) -> List[str]:
        names = list(signature.feature_names or [])
        types = list(signature.feature_sequence or [])
        out: List[str] = []
        seen = set()
        for idx, raw_name in enumerate(names):
            ftype = types[idx] if idx < len(types) else ''
            if _is_default_feature_name(raw_name, ftype):
                continue
            norm = _normalize_custom_name(raw_name)
            if not norm:
                continue
            if norm in seen:
                continue
            seen.add(norm)
            out.append(norm)
        return out

    def _name_tokens(name_norm: str) -> set:
        return {t for t in re.findall(r'[a-z0-9]+', name_norm or '') if t and not t.isdigit()}

    def _name_match_score(name_a: str, name_b: str) -> float:
        a = _normalize_custom_name(name_a)
        b = _normalize_custom_name(name_b)
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0

        # Regex-friendly matching: ignore separators and optional trailing indexes.
        a_compact = _compact_alnum(a)
        b_compact = _compact_alnum(b)
        if a_compact and a_compact == b_compact:
            return 0.98

        parts_a = [re.escape(t) for t in a.split() if t]
        parts_b = [re.escape(t) for t in b.split() if t]
        if parts_a:
            pat_a = re.compile(r'^' + r'[\s_\-]*'.join(parts_a) + r'(?:[\s_\-]*\d+)?$')
            if pat_a.fullmatch(b):
                return 0.95
        if parts_b:
            pat_b = re.compile(r'^' + r'[\s_\-]*'.join(parts_b) + r'(?:[\s_\-]*\d+)?$')
            if pat_b.fullmatch(a):
                return 0.95

        tok_a = _name_tokens(a)
        tok_b = _name_tokens(b)
        if not tok_a or not tok_b:
            return 0.0
        overlap = len(tok_a & tok_b) / max(len(tok_a), len(tok_b), 1)
        if overlap >= 0.80 and len(tok_a & tok_b) >= 2:
            return min(0.90, overlap)
        return 0.0

    def _count_custom_name_matches(custom_names_1: List[str], custom_names_2: List[str]) -> int:
        if not custom_names_1 or not custom_names_2:
            return 0
        used_j = set()
        matches = 0
        for name_a in custom_names_1:
            best_j = -1
            best_score = 0.0
            for j, name_b in enumerate(custom_names_2):
                if j in used_j:
                    continue
                score = _name_match_score(name_a, name_b)
                if score > best_score:
                    best_score = score
                    best_j = j
            if best_j >= 0 and best_score >= 0.80:
                used_j.add(best_j)
                matches += 1
        return matches

    # 1. Stesso autore dichiarato
    if sig1.author and sig2.author:
        scores['author_match'] = 1.0 if sig1.author.lower().strip() == sig2.author.lower().strip() else 0.0
    else:
        scores['author_match'] = 0.0

    # 2. Similarita numero di feature (FCS)
    # Coerente con il paper: f(|F1-F2|) monotona decrescente nel range [0,1].
    # Esempio: 5 vs 6 => 1 - 1/6 = 0.833 (83.3%), non 100%.
    diff_count = abs(sig1.feature_count - sig2.feature_count)
    max_count = max(sig1.feature_count, sig2.feature_count, 1)
    scores['feature_count_similarity'] = max(0.0, 1.0 - (diff_count / max_count))

    # 3. SimilaritÃ  distribuzione feature types (cosine similarity)
    all_types = set(sig1.feature_types.keys()) | set(sig2.feature_types.keys())
    if all_types:
        vec1 = [sig1.feature_types.get(t, 0) for t in all_types]
        vec2 = [sig2.feature_types.get(t, 0) for t in all_types]
        dot = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a ** 2 for a in vec1) ** 0.5
        norm2 = sum(b ** 2 for b in vec2) ** 0.5
        scores['feature_type_similarity'] = dot / (norm1 * norm2 + 1e-9)
    else:
        scores['feature_type_similarity'] = 0.5

    # 4. SimilaritÃ  rapporti stilistici
    style_diff = (
        abs(sig1.extrusion_ratio - sig2.extrusion_ratio) +
        abs(sig1.cutout_ratio - sig2.cutout_ratio) +
        abs(sig1.hole_ratio - sig2.hole_ratio) +
        abs(sig1.round_chamfer_ratio - sig2.round_chamfer_ratio)
    ) / 4.0
    scores['style_similarity'] = 1.0 - min(style_diff, 1.0)

    # 5. BIGRAM - Calcola TUTTI i bigram dalla sequenza originale
    def get_bigrams(seq):
        if len(seq) < 2:
            return []
        return [tuple(seq[i:i+2]) for i in range(len(seq) - 1)]

    bi1 = get_bigrams(sig1.feature_sequence)
    bi2 = get_bigrams(sig2.feature_sequence)

    if bi1 or bi2:
        set1 = set(bi1)
        set2 = set(bi2)
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        scores['bigram_similarity'] = intersection / union if union > 0 else 0.0
    else:
        scores['bigram_similarity'] = 0.5

    # 7. Sequenze comuni - TRIGRAM (3 feature consecutive nello stesso ordine)
    def get_trigrams(seq):
        if len(seq) < 3:
            return set()
        return set(tuple(seq[i:i+3]) for i in range(len(seq) - 2))

    tri1 = get_trigrams(sig1.feature_sequence)
    tri2 = get_trigrams(sig2.feature_sequence)

    if tri1 or tri2:
        intersection = len(tri1 & tri2)
        union = len(tri1 | tri2)
        scores['trigram_similarity'] = intersection / union if union > 0 else 0.0
    else:
        scores['trigram_similarity'] = 0.5

    # 8. Longest Common Subsequence (LCS) con LOGICA FUZZY OPZIONALE
    def lcs_length_fuzzy(seq1, seq2, use_fuzzy=True, fuzzy_function='exponential', alpha=2.0):
        """
        Calcola LCS con peso fuzzy opzionale (SIMMETRICO).
        Le feature INIZIALI pesano di piÃ¹ (piÃ¹ difficili da spostare).
        """
        if not seq1 or not seq2:
            return 0, 0.0

        m, n = len(seq1), len(seq2)

        # Matrice DP standard per LCS
        dp = [[0] * (n + 1) for _ in range(m + 1)]
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if seq1[i-1] == seq2[j-1]:
                    dp[i][j] = dp[i-1][j-1] + 1
                else:
                    dp[i][j] = max(dp[i-1][j], dp[i][j-1])

        lcs_len = dp[m][n]

        if not use_fuzzy or lcs_len == 0:
            return lcs_len, lcs_len / max(m, n, 1)

        # Backtrack per trovare posizioni match
        i, j = m, n
        matches = []
        while i > 0 and j > 0:
            if seq1[i-1] == seq2[j-1]:
                matches.append((i-1, j-1, seq1[i-1]))
                i -= 1
                j -= 1
            elif dp[i-1][j] >= dp[i][j-1]:
                i -= 1
            else:
                j -= 1
        matches.reverse()

        if not matches:
            return 0, 0.0

        # Calcola peso fuzzy SIMMETRICO
        total_weighted = 0.0
        max_possible_weight = 0.0

        for pos1, pos2, elem in matches:
            norm_pos1 = pos1 / max(m - 1, 1)
            norm_pos2 = pos2 / max(n - 1, 1)
            norm_pos = (norm_pos1 + norm_pos2) / 2.0

            if fuzzy_function == 'linear':
                weight = 1.0 - norm_pos
            elif fuzzy_function == 'exponential':
                weight = math.exp(-alpha * norm_pos)
            elif fuzzy_function == 'logarithmic':
                weight = 1.0 - math.log(1.0 + alpha * norm_pos) / math.log(1.0 + alpha)
            else:
                weight = 1.0 - norm_pos

            total_weighted += weight

        for idx in range(lcs_len):
            norm_pos = idx / max(lcs_len - 1, 1)
            if fuzzy_function == 'linear':
                weight = 1.0 - norm_pos
            elif fuzzy_function == 'exponential':
                weight = math.exp(-alpha * norm_pos)
            elif fuzzy_function == 'logarithmic':
                weight = 1.0 - math.log(1.0 + alpha * norm_pos) / math.log(1.0 + alpha)
            else:
                weight = 1.0 - norm_pos
            max_possible_weight += weight

        fuzzy_score = total_weighted / max(max_possible_weight, 1e-9)
        return lcs_len, fuzzy_score

    # Carica configurazione fuzzy LCS
    if lcs_fuzzy_config is not None:
        lcs_cfg = lcs_fuzzy_config
    else:
        lcs_cfg = load_weights()

    use_lcs_fuzzy = lcs_cfg.get('lcs_fuzzy_enabled', True)
    fuzzy_function = lcs_cfg.get('lcs_fuzzy_function', 'exponential')
    fuzzy_alpha = lcs_cfg.get('lcs_fuzzy_alpha', 2.0)
    fuzzy_mix = lcs_cfg.get('lcs_fuzzy_mix', 0.7)

    lcs_len, lcs_fuzzy = lcs_length_fuzzy(
        sig1.feature_sequence, sig2.feature_sequence,
        use_fuzzy=use_lcs_fuzzy, fuzzy_function=fuzzy_function, alpha=fuzzy_alpha
    )
    max_len = max(len(sig1.feature_sequence), len(sig2.feature_sequence), 1)

    lcs_standard = lcs_len / max_len
    if use_lcs_fuzzy:
        lcs_score = fuzzy_mix * lcs_fuzzy + (1 - fuzzy_mix) * lcs_standard
    else:
        lcs_score = lcs_standard
    scores['lcs_similarity'] = max(0.0, min(1.0, float(lcs_score)))

    # 9. SimilaritÃ  nomi feature (SOLO nomi personalizzati/rinominati)
    custom_names_1 = _collect_custom_names(sig1)
    custom_names_2 = _collect_custom_names(sig2)
    if custom_names_1 and custom_names_2:
        matched_custom_names = _count_custom_name_matches(custom_names_1, custom_names_2)
        max_len_names = max(len(custom_names_1), len(custom_names_2), 1)
        scores['feature_names_similarity'] = matched_custom_names / max_len_names
    else:
        # Mantieni sempre un valore numerico coerente (0.0) e usa _unavailable_criteria
        # per gestire l'esclusione statistica.
        scores['feature_names_similarity'] = 0.0
        unavailable = list(scores.get('_unavailable_criteria', []))
        unavailable.append('feature_names_similarity')
        scores['_unavailable_criteria'] = list(dict.fromkeys(unavailable))

    # === CRITERI SKETCH 2D ===

    # Helper locale: frazione di sketch della firma che hanno almeno un vincolo
    def _constraint_coverage(sig: FeatureSignature) -> float:
        sketches = getattr(sig, 'sketches_data', None) or []
        if not sketches:
            return 0.0
        with_constraints = sum(
            1 for sk in sketches
            if isinstance(sk, dict) and int(sk.get('constraint_count', 0) or 0) > 0
        )
        return with_constraints / len(sketches)

    # 10. Similarita geometrie 2D su multiset (coerente con 2DGS del paper)
    scores['geometry_2d_similarity'] = _multiset_jaccard(sig1.geometry_2d_types, sig2.geometry_2d_types)

    # 11 & 12: vincoli disponibili solo se almeno l'80% degli sketch di ENTRAMBE
    # le firme ha constraint_count > 0 (stessa logica di feature_names_similarity).
    _cov1 = _constraint_coverage(sig1)
    _cov2 = _constraint_coverage(sig2)
    _constraints_available = (_cov1 >= 0.80) and (_cov2 >= 0.80)

    # 11. Similarita vincoli 2D su multiset (coerente con 2DCS del paper)
    # 12. Similarita rapporto vincoli/geometrie
    _unavailable_buf = list(scores.get('_unavailable_criteria', []))
    # Calcola sempre i raw score: la disponibilita/statistical reliability e' separata.
    scores['constraint_2d_similarity'] = _multiset_jaccard(
        sig1.constraint_2d_types, sig2.constraint_2d_types
    )
    ratio_diff = abs(sig1.constraint_to_geometry_ratio - sig2.constraint_to_geometry_ratio)
    scores['constraint_ratio_similarity'] = max(0.0, 1.0 - ratio_diff)
    if not _constraints_available:
        _unavailable_buf.append('constraint_2d_similarity')
        _unavailable_buf.append('constraint_ratio_similarity')
    if _unavailable_buf:
        scores['_unavailable_criteria'] = list(dict.fromkeys(_unavailable_buf))

    # Metadato diagnostico: copertura vincoli per coppia (debug/UI)
    scores['_constraint_coverage'] = {'sig1': round(_cov1, 3), 'sig2': round(_cov2, 3)}

    # 13. Similarita sketch parametrica (u, v)
    sketch_param_sim, sketch_matches = compute_sketch_parametric_similarity(sig1, sig2)
    unavailable = list(scores.get('_unavailable_criteria', []))
    if sketch_param_sim is None:
        # Mantieni valore numerico anche quando non disponibile.
        scores['sketch_parametric_similarity'] = 0.0
        unavailable.append('sketch_parametric_similarity')
    else:
        scores['sketch_parametric_similarity'] = float(max(0.0, min(1.0, sketch_param_sim)))
    if unavailable:
        # Dedup preservando ordine
        scores['_unavailable_criteria'] = list(dict.fromkeys(unavailable))
    scores['_sketch_matches'] = sketch_matches

    return scores


def combine_scores(raw_scores: dict, weights: dict, exclusion_policy: dict | None = None) -> dict:
    """
    Combina i punteggi grezzi con i pesi e la fuzzy logic (FASE VELOCE).

    Questa funzione Ã¨ puramente aritmetica: prende i punteggi giÃ  calcolati
    da compute_raw_scores() e applica la combinazione pesata, con opzionale
    fuzzy penalty per criteri disomogenei.

    PuÃ² essere chiamata ripetutamente con pesi diversi SENZA ricalcolare i raw scores.

    Args:
        raw_scores: dict da compute_raw_scores() (chiavi criterio -> float 0..1, + metadati _*)
        weights: dict completo dei pesi (inclusi parametri fuzzy_combination_*)
        exclusion_policy: policy opzionale per stabilire quando un criterio e' escluso
            dal calcolo. Formato:
              {
                'enabled': bool,
                'exclude_if_unavailable': bool,
                'exclude_if_missing_or_non_finite': bool,
                'force_excluded': list[str],
                'force_included': list[str],
              }

    Returns:
        dict con tutti gli score originali + 'overall' + metriche fuzzy debug (_fuzzy_*)
    """
    # Copia gli score originali (non mutare l'input)
    scores = dict(raw_scores)

    # Parametri fuzzy combination
    use_fuzzy = weights.get('fuzzy_combination_enabled', False)
    fuzzy_method = weights.get('fuzzy_combination_method', 'gaussian')
    fuzzy_penalty = weights.get('fuzzy_combination_penalty', 0.3)
    fuzzy_boost = weights.get('fuzzy_combination_boost', 0.15)

    # Estrai solo i pesi numerici (esclude parametri fuzzy, lcs_fuzzy, e metadati _*)
    numeric_weights = {
        k: v for k, v in weights.items()
        if isinstance(v, (int, float))
        and not k.startswith('lcs_fuzzy_')
        and not k.startswith('fuzzy_combination_')
        and not k.startswith('_')
    }

    # Coerenza con paper: peso AM effettivo = AM * w_AM
    am_score = max(0.0, min(1.0, float(scores.get('author_match', 0.0))))
    if 'author_match' in numeric_weights:
        numeric_weights['author_match'] = float(numeric_weights['author_match']) * am_score

    # Criteri non valutabili (es. sketch_parametric senza frame valido)
    unavailable_criteria = set()
    raw_unavailable = scores.get('_unavailable_criteria', [])
    if isinstance(raw_unavailable, list):
        unavailable_criteria = set(str(x) for x in raw_unavailable)

    # Policy configurabile per l'esclusione criteri.
    policy = exclusion_policy if isinstance(exclusion_policy, dict) else {}
    policy_enabled = bool(policy.get('enabled', True))
    exclude_if_unavailable = bool(policy.get('exclude_if_unavailable', True))
    exclude_if_missing_or_non_finite = bool(policy.get('exclude_if_missing_or_non_finite', True))
    force_excluded = set(str(x) for x in (policy.get('force_excluded', []) or []) if str(x).strip())
    force_included = set(str(x) for x in (policy.get('force_included', []) or []) if str(x).strip())
    force_included = force_included - force_excluded

    # Modalita' legacy: comportamento storico immutato.
    if not policy_enabled:
        exclude_if_unavailable = True
        exclude_if_missing_or_non_finite = True
        force_excluded = set()
        force_included = set()

    # Usa SOLO i criteri effettivamente disponibili, poi rinormalizza.
    active_criteria = []
    excluded_criteria = []
    for k, w in numeric_weights.items():
        if w <= 0.0:
            excluded_criteria.append(k)
            continue
        if k in force_excluded:
            excluded_criteria.append(k)
            continue
        forced_included = k in force_included
        if (k in unavailable_criteria) and exclude_if_unavailable and (not forced_included):
            excluded_criteria.append(k)
            continue

        s_raw = scores.get(k, None)
        try:
            s_val = float(s_raw)
        except Exception:
            s_val = float('nan')

        if not math.isfinite(s_val):
            if exclude_if_missing_or_non_finite and (not forced_included):
                excluded_criteria.append(k)
                continue
            # Se la policy non esclude i missing/non-finite, includi come 0.
            s_val = 0.0

        if not math.isfinite(s_val):
            excluded_criteria.append(k)
            continue
        s_val = max(0.0, min(1.0, s_val))
        scores[k] = s_val
        active_criteria.append((k, float(w), s_val))

    scores['_active_criteria'] = [k for (k, _, _) in active_criteria]
    scores['_excluded_criteria'] = list(dict.fromkeys(excluded_criteria))

    if not use_fuzzy:
        # Combinazione lineare classica solo sui criteri disponibili.
        # I pesi vengono rinormalizzati sul sottoinsieme attivo.
        total_weight = sum(w for (_, w, _) in active_criteria) if active_criteria else 0.0
        if total_weight <= 0:
            scores['overall'] = 0.0
        else:
            linear_num = 0.0
            for _, w, s_val in active_criteria:
                linear_num += s_val * (w / total_weight)
            scores['overall'] = max(0.0, min(1.0, linear_num))
    else:
        # â”€â”€ Fuzzy combination: PREMIA coerenza, PENALIZZA discordanza â”€â”€
        #
        # Approccio: calcoliamo un "coherence score" che misura quanto i
        # criteri concordano tra loro (pesato per importanza).
        #   - Coerenza alta (tutti alti O tutti bassi) â†’ coherence â‰ˆ 1
        #   - Coerenza bassa (alcuni alti, altri bassi) â†’ coherence â‰ˆ 0
        #
        # Il risultato finale Ã¨ un MIX tra similaritÃ  lineare e coherence-adjusted:
        #   overall = linear * (1 - sensitivity) + linear * coherence * sensitivity
        #
        # Effetto:
        #   - Plagio vero (linear=0.94, coherenceâ‰ˆ0.95): overall SALE
        #   - Falso positivo (linear=0.65, coherenceâ‰ˆ0.3): overall SCENDE
        #   - Non plagio (linear=0.40): overall resta ~invariato (Ã¨ giÃ  basso)
        #
        # Tre metodi determinano come la weighted_std â†’ coherence:
        #   - triangular: transizione lineare (semplice)
        #   - gaussian:   transizione morbida (consigliato)
        #   - sugeno:     transizione lenta, tollera piÃ¹ varianza
        import numpy as np

        individual_scores = [s for (_, _, s) in active_criteria]
        weighted_scores = [s * w for (_, w, s) in active_criteria]
        weights_list = [w for (_, w, _) in active_criteria if w > 0.0]

        if not individual_scores:
            scores['overall'] = 0.0
        else:
            total_weight = sum(weights_list)
            linear_similarity = sum(weighted_scores) / total_weight if total_weight > 0 else 0.0

            scores_array = np.array(individual_scores)
            norm_weights = np.array(weights_list) / total_weight
            mean_score = float(np.average(scores_array, weights=norm_weights))

            # Deviazione standard pesata
            weighted_variance = float(np.average((scores_array - mean_score) ** 2, weights=norm_weights))
            weighted_std = weighted_variance ** 0.5

            # â”€â”€ Coherence: 1 = criteri perfettamente concordi, 0 = discordanti â”€â”€
            # I 3 metodi mappano weighted_std â†’ coherence in modi diversi:
            #
            # triangular: coherence = max(0, 1 - std/0.35)
            #   â†’ lineare, cala subito anche con poca varianza
            #
            # gaussian: coherence = exp(-4 * stdÂ²)
            #   â†’ curva morbida, tollera std fino a ~0.15, poi cala
            #   â†’ consigliato
            #
            # sugeno: coherence = 1 / (1 + 8 * stdÂ²)
            #   â†’ molto tollerante, cala lentamente
            #
            if fuzzy_method == 'triangular':
                coherence = max(0.0, 1.0 - weighted_std / 0.35)
            elif fuzzy_method == 'gaussian':
                coherence = float(np.exp(-4.0 * weighted_std ** 2))
            else:  # sugeno
                coherence = 1.0 / (1.0 + 8.0 * weighted_std ** 2)

            # â”€â”€ Mix finale: boost + penalty indipendenti â”€â”€
            #
            # Due leve separate:
            #   - boost:   premia coerenza â†’ spinge verso l'alto i plagi veri
            #   - penalty: penalizza incoerenza â†’ spinge verso il basso i falsi positivi
            #
            # Formula:
            #   factor = 1 + boost * coherence - penalty * (1 - coherence)
            #
            # coherence=1 (plagio vero):    factor = 1 + boost         â†’ SALE
            # coherence=0 (discordante):    factor = 1 - penalty       â†’ SCENDE
            # coherence=0.5 (medio):        factor = 1 + boost/2 - penalty/2
            #
            # Con boost=0.15, penalty=0.30:
            #   plagio vero (cohâ‰ˆ0.92):  factor = 1 + 0.138 - 0.024 = 1.114 â†’ +11%
            #   falso positivo (cohâ‰ˆ0.3): factor = 1 + 0.045 - 0.21  = 0.835 â†’ -16%
            #   non plagio (coh=1):       factor = 1 + 0.15  - 0     = 1.15  â†’ invariato (lin basso)

            boost_component = fuzzy_boost * coherence
            penalty_component = fuzzy_penalty * (1.0 - coherence)
            fuzzy_factor = 1.0 + boost_component - penalty_component

            scores['overall'] = max(0.0, min(1.0, linear_similarity * fuzzy_factor))

            # Debug metrics
            scores['_fuzzy_method'] = fuzzy_method
            scores['_fuzzy_weighted_std'] = weighted_std
            scores['_fuzzy_weighted_mean'] = mean_score
            scores['_fuzzy_coherence'] = coherence
            scores['_fuzzy_factor'] = fuzzy_factor
            scores['_fuzzy_boost'] = boost_component
            scores['_fuzzy_penalty'] = penalty_component
            scores['_fuzzy_linear'] = linear_similarity

    return scores


def compute_similarity(sig1: FeatureSignature, sig2: FeatureSignature, custom_weights: dict = None) -> Dict[str, float]:
    """Computes overall similarity by combining raw scores with weights (2-phase pipeline)."""
    """
    Calcola la similaritÃ  tra due firme con pipeline a 2 fasi:
    1) compute_raw_scores: estrae metriche grezze (LENTO, dipende dai dati)
    2) combine_scores: combina metriche con pesi (VELOCE, aritmetica)

    Args:
        sig1, sig2: Feature signatures da confrontare
        custom_weights: Pesi custom (opzionale). Se None, carica da config.json
    """
    weights = custom_weights if custom_weights is not None else load_weights()

    # Fase 1: estrazione score (LENTA - dipende dai dati delle firme)
    raw_scores = compute_raw_scores(sig1, sig2, lcs_fuzzy_config=weights)

    # Fase 2: combinazione (VELOCE - solo aritmetica)
    return combine_scores(raw_scores, weights)


def _is_signature_extraction_failed(sig: FeatureSignature) -> bool:
    """Rileva fallimenti reali senza scartare file con soli sketch/metadata."""
    if getattr(sig, 'instance_corrupted', False):
        return True

    has_features = bool(getattr(sig, 'feature_count', 0) > 0)
    has_sketch_payload = bool(
        getattr(sig, 'sketches_data', None)
        or getattr(sig, 'total_2d_geometry_count', 0) > 0
        or getattr(sig, 'total_2d_constraint_count', 0) > 0
        or getattr(sig, 'sketches_count', 0) > 0
    )
    has_metadata = bool(
        (getattr(sig, 'author', '') or '').strip()
        or (getattr(sig, 'last_author', '') or '').strip()
        or (getattr(sig, 'company', '') or '').strip()
        or (getattr(sig, 'creation_date', '') or '').strip()
        or (getattr(sig, 'last_save_date', '') or '').strip()
        or (getattr(sig, 'template', '') or '').strip()
        or (getattr(sig, 'revision', '') or '').strip()
    )

    return not (has_features or has_sketch_payload or has_metadata)


def analyze_directory(directory: Path, use_com: bool = True) -> List[FeatureSignature]:
    """Analizza tutti i file Solid Edge in una directory con gestione robusta degli errori."""
    extensions = {'.par', '.psm', '.asm'}
    signatures = []
    app = None
    consecutive_failures = 0
    MAX_CONSECUTIVE_FAILURES = 5  # Riavvia Solid Edge dopo 5 fallimenti consecutivi
    total_restarts = 0
    MAX_TOTAL_RESTARTS = 3  # âœ… NUOVO: Limite massimo di restart totali

    # âœ¨ CRITICO: Inizializza COM all'inizio per questo thread
    if use_com and HAS_COM:
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass  # OK se giÃ  inizializzato

    if use_com and HAS_COM:
        try:
            app = win32com.client.GetActiveObject("SolidEdge.Application")
            print("  âœ“ Connesso a Solid Edge esistente")
        except:
            app = win32com.client.Dispatch("SolidEdge.Application")
            app.Visible = True
            print("  âœ“ Avviato Solid Edge")
        app.DisplayAlerts = False

    for filepath in directory.rglob('*'):
        if filepath.suffix.lower() in extensions:
            print(f"  Analizzando: {filepath.name}")
            try:
                sig = extract_signature(filepath, app=app)

                # âœ¨ CONTROLLO ISTANZA CORROTTA - Restart immediato
                if hasattr(sig, 'instance_corrupted') and sig.instance_corrupted:
                    print(f"    âŒ Solid Edge instance corrupted!")

                    # Verifica limite restart
                    if total_restarts >= MAX_TOTAL_RESTARTS:
                        print(f"\n    âŒ Reached maximum restart limit ({MAX_TOTAL_RESTARTS})")
                        print("    âš ï¸ Solid Edge may be unstable. Stopping COM automation.")
                        print("    ðŸ’¡ Please restart Solid Edge manually and try again.")
                        use_com = False
                        app = None
                        consecutive_failures = 0
                        continue

                    print("    ðŸ”„ Forcing immediate restart...")
                    app = restart_solid_edge(app)
                    total_restarts += 1
                    consecutive_failures = 0

                    if app is None:
                        print("    âŒ Failed to restart Solid Edge. Disabling COM automation.")
                        use_com = False
                        consecutive_failures = 0
                        continue

                    # Riprova l'estrazione con la nuova istanza
                    print(f"    ðŸ”„ Retrying: {filepath.name}")
                    sig = extract_signature(filepath, app=app)

                # ✅ Controlla se l'estrazione Ã¨ davvero fallita
                if _is_signature_extraction_failed(sig):
                    consecutive_failures += 1
                    print(f"    ⚠️ Extraction failed ({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})")
                else:
                    # Reset counter on success
                    consecutive_failures = 0
                    signatures.append(sig)
                    print(f"    ✅ Dati sketch salvati: {get_cache_filepath(filepath)}")

                # âš ï¸ Se troppi fallimenti consecutivi, riavvia Solid Edge
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    # âœ… Verifica limite restart
                    if total_restarts >= MAX_TOTAL_RESTARTS:
                        print(f"\n    âŒ Reached maximum restart limit ({MAX_TOTAL_RESTARTS})")
                        print("    âš ï¸ Solid Edge may be unstable. Stopping COM automation.")
                        print("    ðŸ’¡ Please restart Solid Edge manually and try again.")
                        use_com = False
                        app = None
                        consecutive_failures = 0
                        continue

                    print(f"\n    âš ï¸ {consecutive_failures} consecutive failures detected!")
                    print("    ðŸ”„ Restarting Solid Edge to recover...")
                    app = restart_solid_edge(app)
                    total_restarts += 1
                    consecutive_failures = 0

                    if app is None:
                        print("    âŒ Failed to restart Solid Edge. Disabling COM automation.")
                        use_com = False

            except Exception as e:
                consecutive_failures += 1
                print(f"    âš ï¸ Errore: {e}")

                # Riavvia dopo troppi errori
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES and use_com:
                    if total_restarts >= MAX_TOTAL_RESTARTS:
                        print(f"\n    âŒ Reached maximum restart limit ({MAX_TOTAL_RESTARTS})")
                        print("    âš ï¸ Disabling COM automation.")
                        use_com = False
                        app = None
                        consecutive_failures = 0
                        continue

                    print(f"\n    âš ï¸ {consecutive_failures} consecutive failures!")
                    print("    ðŸ”„ Attempting to restart Solid Edge...")
                    app = restart_solid_edge(app)
                    total_restarts += 1
                    consecutive_failures = 0

    print(f"\nðŸ“Š Analysis complete:")
    print(f"   - Successfully analyzed: {len(signatures)} files")
    print(f"   - Total restarts performed: {total_restarts}")

    return signatures


def find_similar_authors(signatures: List[FeatureSignature], threshold: float = 0.7) -> List[Tuple[FeatureSignature, FeatureSignature, float, Dict]]:
    """Trova coppie di file potenzialmente dello stesso autore."""
    similar_pairs = []

    for i, sig1 in enumerate(signatures):
        for sig2 in signatures[i + 1:]:
            folder1 = Path(sig1.filepath).parent if sig1.filepath else None
            folder2 = Path(sig2.filepath).parent if sig2.filepath else None

            if folder1 and folder2 and folder1 == folder2:
                continue

            sim = compute_similarity(sig1, sig2)
            if sim['overall'] >= threshold:
                similar_pairs.append((sig1, sig2, sim['overall'], sim))

    return sorted(similar_pairs, key=lambda x: -x[2])


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Analisi similaritÃ  file CAD Solid Edge v2")
    parser.add_argument('--input', type=Path, required=True, help='Directory o file da analizzare')
    parser.add_argument('--compare', type=Path, help='Secondo file/directory da confrontare')
    parser.add_argument('--use-com', action='store_true', default=True, help='Usa COM automation')
    parser.add_argument('--threshold', type=float, default=0.7, help='Soglia similaritÃ  (0-1)')
    parser.add_argument('--output', type=Path, help='Salva report JSON')
    parser.add_argument('--verbose', '-v', action='store_true', help='Output dettagliato')
    parser.add_argument('--web', action='store_true', help='Avvia interfaccia web')

    args = parser.parse_args()

    if args.web:
        from webapp.app import run_server
        run_server()
        return

    print("\nðŸ” ANALISI SIMILARITÃ€ FILE CAD SOLID EDGE v2\n")

    signatures = []

    if args.input.is_dir():
        print(f"ðŸ“ Scansione directory: {args.input}")
        signatures.extend(analyze_directory(args.input, use_com=args.use_com))
    else:
        sig = extract_signature(args.input)
        signatures.append(sig)

    if args.compare:
        if args.compare.is_dir():
            print(f"ðŸ“ Scansione directory comparazione: {args.compare}")
            signatures.extend(analyze_directory(args.compare, use_com=args.use_com))
        else:
            sig = extract_signature(args.compare)
            signatures.append(sig)

    print(f"\nâœ… File analizzati: {len(signatures)}\n")

    # Mostra info firme
    if args.verbose:
        for sig in signatures:
            parent_folder = Path(sig.filepath).parent.name if sig.filepath else "(sconosciuto)"

            print(f"ðŸ“„ {sig.filename}")
            print(f"   ðŸ“ Cartella: {parent_folder}")
            print(f"   Autore: {sig.author or '(non disponibile)'}")
            print(f"   Feature 3D count: {sig.feature_count}")
            print(f"   Feature types: {sig.feature_types}")
            print(f"   Sketches: {sig.sketches_count}")
            print(f"   Geometrie 2D totali: {sig.total_2d_geometry_count}")
            print(f"   Tipi geometrie 2D: {sig.geometry_2d_types}")
            print(f"   Vincoli 2D totali: {sig.total_2d_constraint_count}")
            print(f"   Tipi vincoli 2D: {sig.constraint_2d_types}")
            print(f"   Rapporto vincoli/geom: {sig.constraint_to_geometry_ratio:.2f}")
            print()

    # Trova coppie simili
    if len(signatures) >= 2:
        print("ðŸ”— ANALISI SIMILARITÃ€\n")
        similar = find_similar_authors(signatures, threshold=args.threshold)

        if similar:
            print(f"Coppie con similaritÃ  â‰¥ {args.threshold:.0%}:\n")
            for sig1, sig2, score, details in similar:
                folder1 = Path(sig1.filepath).parent.name if sig1.filepath else "?"
                folder2 = Path(sig2.filepath).parent.name if sig2.filepath else "?"

                print(f"  â€¢ [{folder1}] {sig1.filename} ({sig1.feature_count} feat3D, {sig1.total_2d_geometry_count} geom2D)")
                print(f"    â†” [{folder2}] {sig2.filename} ({sig2.feature_count} feat3D, {sig2.total_2d_geometry_count} geom2D)")
                print(f"    SimilaritÃ  totale: {score:.1%}")
                if args.verbose:
                    print(f"    - Feature count: {details['feature_count_similarity']:.0%}")
                    print(f"    - Feature types: {details['feature_type_similarity']:.0%}")
                    print(f"    - Trigram: {details['trigram_similarity']:.0%}")
                    print(f"    - LCS: {details['lcs_similarity']:.0%}")
                    print(f"    - Bigram: {details['bigram_similarity']:.0%}")
                    print(f"    - Geom 2D: {details['geometry_2d_similarity']:.0%}")
                    print(f"    - Vincoli 2D: {details['constraint_2d_similarity']:.0%}")
                print()
        else:
            print(f"Nessuna coppia con similaritÃ  â‰¥ {args.threshold:.0%}")

    # Salva report
    if args.output:
        report = {
            'signatures': [asdict(s) for s in signatures],
            'similar_pairs': [
                {
                    'file1': sig1.filename,
                    'folder1': Path(sig1.filepath).parent.name,
                    'file2': sig2.filename,
                    'folder2': Path(sig2.filepath).parent.name,
                    'similarity': sc,
                    'details': det
                }
                for sig1, sig2, sc, det in find_similar_authors(signatures, 0.5)
            ],
        }
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nðŸ’¾ Report salvato: {args.output}")


# âœ¨ PERSISTENZA DEI DATI SKETCH
import os
from datetime import datetime

CACHE_DIR = Path.home() / '.cache' / 'solid_edge_similarity'
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Versione dello schema cache - incrementa quando cambia il contenuto persistito degli sketch
CACHE_SCHEMA_VERSION = 4  # v4: geometry_detailed con coordinate + sketch similarity topologica

def get_cache_filepath(original_filepath: Path) -> Path:
    """Genera percorso cache basato su hash del file."""
    file_hash = hashlib.md5(str(original_filepath).encode()).hexdigest()
    return CACHE_DIR / f'sketch_data_{file_hash}.json'

def save_sketch_data(filepath: str, sketch_data_list: List[Dict]) -> bool:
    """Salva i dati degli sketch in cache JSON."""
    try:
        cache_path = get_cache_filepath(Path(filepath))
        cache_content = {
            'schema_version': CACHE_SCHEMA_VERSION,  # âœ¨ NUOVO: versione schema
            'filepath': filepath,
            'file_hash': hashlib.md5(str(filepath).encode()).hexdigest(),
            'timestamp': datetime.now().isoformat(),
            'sketches': sketch_data_list,
            'total_sketches': len(sketch_data_list),
            'total_geometries': sum(s.get('geometry_count', 0) for s in sketch_data_list),
            'total_constraints': sum(s.get('constraint_count', 0) for s in sketch_data_list)
        }

        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(cache_content, f, indent=2, ensure_ascii=False)

        print(f"âœ… Dati sketch salvati: {cache_path}")
        return True
    except Exception as e:
        print(f"âš ï¸ Errore salvataggio sketch: {e}")
        return False

def load_sketch_data(filepath: str) -> List[Dict] | None:
    """Carica i dati degli sketch da cache."""
    try:
        cache_path = get_cache_filepath(Path(filepath))

        if not cache_path.exists():
            return None

        # âœ¨ Controlla se il file Ã¨ vuoto
        if cache_path.stat().st_size == 0:
            print(f"âš ï¸ Cache file is empty, deleting: {cache_path}")
            cache_path.unlink()
            return None

        with open(cache_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip():
                print(f"âš ï¸ Cache file contains only whitespace, deleting: {cache_path}")
                cache_path.unlink()
                return None

            try:
                cache_content = json.loads(content)
            except json.JSONDecodeError as je:
                print(f"âŒ Invalid JSON in cache file: {cache_path}")
                print(f"   Error: {je}")
                print(f"   Deleting corrupted cache file...")
                cache_path.unlink()
                return None

        sketches = cache_content.get('sketches', [])

        # âœ¨ NUOVO: Verifica versione schema
        cached_version = cache_content.get('schema_version', 0)
        if cached_version < CACHE_SCHEMA_VERSION:
            print(f"âš ï¸ Cache outdated (schema v{cached_version} < v{CACHE_SCHEMA_VERSION}), deleting: {cache_path}")
            cache_path.unlink()
            return None

        print(f"âœ… Dati sketch caricati da cache: {cache_path}")
        return sketches
    except Exception as e:
        print(f"âš ï¸ Errore caricamento sketch: {e}")
        return None
    except Exception as e:
        print(f"âš ï¸ Errore caricamento sketch: {e}")
        return None

def get_sketch_cache_info(filepath: str) -> Dict | None:
    """Restituisce informazioni sulla cache degli sketch."""
    try:
        cache_path = get_cache_filepath(Path(filepath))

        if not cache_path.exists():
            return None

        # âœ¨ Controlla se il file Ã¨ vuoto
        if cache_path.stat().st_size == 0:
            print(f"âš ï¸ Cache info file is empty, deleting: {cache_path}")
            cache_path.unlink()
            return None

        with open(cache_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip():
                print(f"âš ï¸ Cache info file contains only whitespace, deleting: {cache_path}")
                cache_path.unlink()
                return None

            try:
                cache_content = json.loads(content)
            except json.JSONDecodeError as je:
                print(f"âŒ Invalid JSON in cache info file: {cache_path}")
                print(f"   Error: {je}")
                print(f"   Deleting corrupted cache file...")
                cache_path.unlink()
                return None

        return {
            'cached': True,
            'timestamp': cache_content.get('timestamp'),
            'total_sketches': cache_content.get('total_sketches', 0),
            'total_geometries': cache_content.get('total_geometries', 0),
            'total_constraints': cache_content.get('total_constraints', 0),
            'cache_path': str(cache_path)
        }
    except:
        return None


def restart_solid_edge(app=None):
    """
    Riavvia Solid Edge quando si blocca.
    Chiude tutte le istanze e ne crea una nuova.
    """
    if not HAS_COM:
        return None

    import time
    import subprocess
    import gc

    print("    ðŸ”„ Restarting Solid Edge...")

    # 1. Rilascia l'oggetto COM correttamente
    if app:
        try:
            app.Quit()
        except:
            pass
        # âœ… IMPORTANTE: Rilascia il riferimento COM
        del app
        app = None

    # âœ… FORCE garbage collection per rilasciare tutti i riferimenti COM
    gc.collect()
    time.sleep(1)

    # 2. Termina TUTTI i processi Solid Edge (potrebbe esserci piÃ¹ di un'istanza)
    try:
        subprocess.run(['taskkill', '/F', '/IM', 'edge.exe'],
                      capture_output=True, timeout=10)
        print("    â³ Waiting for process to terminate...")
        time.sleep(3)  # âœ… Aumentato a 3 secondi
    except Exception as e:
        print(f"    âš ï¸ taskkill error: {e}")

    # 3. Verifica che il processo sia morto
    max_wait = 10
    for i in range(max_wait):
        try:
            result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq edge.exe'],
                                  capture_output=True, text=True, timeout=5)
            if 'edge.exe' not in result.stdout:
                print(f"    âœ“ Solid Edge process terminated after {i+1}s")
                break  # Processo morto
        except:
            break
        time.sleep(1)

    # Attendi un po' extra per essere sicuri
    time.sleep(2)

    # 4. âœ¨ REINIZIALIZZA COM NEL THREAD CORRENTE (FIX CRITICO)
    try:
        import pythoncom
        # Prima verifica se COM Ãˆ giÃ  inizializzato
        try:
            pythoncom.CoUninitialize()  # Rilascia COM esistente
        except Exception:
            pass  # OK se non era inizializzato
        time.sleep(0.5)
        pythoncom.CoInitialize()    # Reinizializza COM
        print("    ðŸ”§ COM re-initialized in current thread")
    except Exception as e:
        print(f"    âš ï¸ COM re-init warning: {e}")
        # âœ¨ Tenta di inizializzare comunque
        try:
            import pythoncom
            pythoncom.CoInitialize()
        except Exception:
            pass

    # 5. Crea nuova istanza DIRETTAMENTE (no threading - COM non supporta cross-thread)
    try:
        # âœ¨ CRITICO: Reimporta win32com dopo CoInitialize
        import win32com.client

        print("    ðŸ†• Creating Solid Edge instance (timeout: 30s)...")
        # âœ¨ Prova prima a connettersi a istanza esistente
        try:
            new_app = win32com.client.GetActiveObject("SolidEdge.Application")
            print("    ðŸ“Œ Found existing Solid Edge instance")
        except:
            # Se non esiste, crea nuova istanza
            new_app = win32com.client.Dispatch("SolidEdge.Application")
            print("    ðŸ†• Created new Solid Edge instance")

        new_app.Visible = True
        new_app.DisplayAlerts = False

        # Attendi che l'app sia pronta
        time.sleep(2)

        # Verifica validitÃ 
        try:
            _ = new_app.Documents.Count
            print("    âœ… Solid Edge restarted successfully")
            return new_app
        except Exception as e:
            print(f"    âŒ New instance not valid: {e}")
            try:
                new_app.Quit()
            except:
                pass
            return None

    except Exception as e:
        print(f"    âŒ Failed to create instance: {e}")
        return None
