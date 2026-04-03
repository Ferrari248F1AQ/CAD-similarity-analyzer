"""
CADModelSignature - Struttura dati universale per firme CAD multi-piattaforma.

Questa dataclass rappresenta il "contratto" comune che tutti gli estrattori
devono rispettare, indipendentemente dal CAD di origine.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from collections import Counter
from datetime import datetime


@dataclass
class FeatureData:
    """Dati di una singola feature 3D."""
    name: str                          # Nome della feature nell'albero
    type: str                          # Tipo normalizzato (Protrusion, Cutout, Hole, etc.)
    original_type: str = ""            # Tipo originale dal CAD
    order: int = 0                     # Posizione nell'albero delle operazioni
    is_suppressed: bool = False        # Feature soppressa/disattivata
    parameters: Dict[str, Any] = field(default_factory=dict)  # Parametri dimensionali


@dataclass
class ConstraintData:
    """Dati di un vincolo 2D."""
    id: str                            # Identificatore univoco
    type: str                          # Tipo normalizzato
    original_type: str = ""            # Tipo originale dal CAD
    category: str = "unknown"          # geometrico, dimensionale, altro
    description: str = ""              # Descrizione human-readable
    value: Optional[float] = None      # Valore (per vincoli dimensionali)


@dataclass
class GeometryData:
    """Dati di un'entità geometrica 2D."""
    id: str                            # Identificatore univoco
    type: str                          # Tipo normalizzato (Line, Circle, Arc, etc.)
    original_type: str = ""            # Tipo originale dal CAD
    # Coordinate 2D (nel sistema di riferimento locale dello sketch)
    start_point: Optional[Tuple[float, float]] = None  # Punto iniziale (Line, Arc)
    end_point: Optional[Tuple[float, float]] = None    # Punto finale (Line, Arc)
    center_point: Optional[Tuple[float, float]] = None # Centro (Circle, Arc, Ellipse)
    radius: Optional[float] = None                      # Raggio (Circle, Arc)
    # Coordinate parametriche normalizzate (u, v)
    start_point_uv: Optional[Tuple[float, float]] = None
    end_point_uv: Optional[Tuple[float, float]] = None
    center_point_uv: Optional[Tuple[float, float]] = None


@dataclass
class SketchParametricFrame:
    """
    Sistema di riferimento parametrico normalizzato per uno sketch.
    Definito da baricentro O e assi ortogonali u, v (da PCA).
    """
    # Baricentro geometrico (media dei punti caratteristici)
    centroid: Tuple[float, float] = (0.0, 0.0)
    # Asse principale u (primo autovettore PCA)
    axis_u: Tuple[float, float] = (1.0, 0.0)
    # Asse secondario v (secondo autovettore PCA, ortogonale a u)
    axis_v: Tuple[float, float] = (0.0, 1.0)
    # Estensione (scala) lungo u e v (per normalizzazione)
    extent_u: float = 1.0
    extent_v: float = 1.0
    # Numero di punti usati per il calcolo
    num_points: int = 0
    # Validità del frame (False se troppi pochi punti)
    is_valid: bool = False


@dataclass
class SketchData:
    """Dati di uno sketch 2D."""
    name: str                          # Nome dello sketch
    geometry_count: int = 0            # Numero totale di entità geometriche
    constraint_count: int = 0          # Numero totale di vincoli
    geometry_types: Dict[str, int] = field(default_factory=dict)    # Conteggio per tipo
    constraint_types: Dict[str, int] = field(default_factory=dict)  # Conteggio per tipo
    geometry_detailed: List[GeometryData] = field(default_factory=list)
    constraint_detailed: List[ConstraintData] = field(default_factory=list)
    # Sistema di riferimento parametrico (u, v)
    parametric_frame: Optional[SketchParametricFrame] = None


@dataclass
class CADModelSignature:
    """
    Firma universale di un modello CAD.

    Questa struttura è il "contratto" che tutti gli estrattori devono rispettare.
    Ogni campo ha un valore di default sensato per gestire CAD con API limitate.

    Regole per i campi mancanti:
    - Stringhe: "" (stringa vuota)
    - Numeri: 0 o 0.0
    - Liste: []
    - Dict: {}
    - Booleani: False
    - Optional: None
    """

    # === IDENTIFICAZIONE ===
    filepath: str = ""                 # Path completo del file
    filename: str = ""                 # Nome del file
    cad_type: str = ""                 # "SolidEdge", "SolidWorks", "Inventor", etc.
    file_extension: str = ""           # ".par", ".sldprt", etc.
    file_hash: str = ""                # Hash MD5 per identificare modifiche

    # === METADATI ===
    author: str = ""                   # Autore del file
    last_author: str = ""              # Ultimo utente che ha salvato
    company: str = ""                  # Azienda
    title: str = ""                    # Titolo documento
    comments: str = ""                 # Commenti
    template: str = ""                 # Template usato
    created_date: Optional[datetime] = None
    modified_date: Optional[datetime] = None
    cad_version: str = ""              # Versione del CAD usato

    # === FEATURE 3D ===
    feature_count: int = 0             # Numero totale feature
    feature_types: Dict[str, int] = field(default_factory=dict)  # Conteggio per tipo
    feature_sequence: List[str] = field(default_factory=list)    # Sequenza tipi nell'ordine
    feature_names: List[str] = field(default_factory=list)       # Nomi personalizzati
    features_detailed: List[FeatureData] = field(default_factory=list)

    # === SKETCH 2D ===
    sketches_count: int = 0
    total_2d_geometry_count: int = 0
    total_2d_constraint_count: int = 0
    geometry_2d_types: Dict[str, int] = field(default_factory=dict)
    constraint_2d_types: Dict[str, int] = field(default_factory=dict)
    sketches_data: List[SketchData] = field(default_factory=list)
    constraint_to_geometry_ratio: float = 0.0

    # === STILE MODELLAZIONE ===
    extrusion_ratio: float = 0.0       # % feature di tipo extrusion
    cutout_ratio: float = 0.0          # % feature di tipo cutout
    hole_ratio: float = 0.0            # % feature di tipo hole
    revolution_ratio: float = 0.0      # % feature di tipo revolution
    round_chamfer_ratio: float = 0.0   # % feature round/chamfer
    pattern_ratio: float = 0.0         # % feature pattern (circular/rectangular)
    naming_style: str = "default"      # "default", "custom", "mixed"

    # === QUALITÀ ESTRAZIONE ===
    extraction_complete: bool = True   # False se alcuni dati non sono stati estratti
    extraction_warnings: List[str] = field(default_factory=list)
    extraction_errors: List[str] = field(default_factory=list)
    extraction_timestamp: Optional[datetime] = None
    extractor_version: str = "1.0.0"

    def __post_init__(self):
        """Calcola campi derivati dopo l'inizializzazione."""
        self._calculate_ratios()
        self._determine_naming_style()
        self._calculate_constraint_ratio()
        if self.extraction_timestamp is None:
            self.extraction_timestamp = datetime.now()

    def _calculate_ratios(self):
        """Calcola i rapporti stilistici."""
        if self.feature_count == 0:
            return

        total = self.feature_count

        # Mappa tipi a categorie
        extrusion_types = {'Protrusion', 'ExtrudedProtrusion', 'Extrude', 'Boss-Extrude', 'Pad'}
        cutout_types = {'Cutout', 'ExtrudedCutout', 'Cut-Extrude', 'Pocket'}
        hole_types = {'Hole', 'SimpleHole', 'CounterBore', 'CounterSink'}
        revolution_types = {'Revolution', 'RevolvedProtrusion', 'RevolvedCutout', 'Revolve', 'Shaft'}
        round_chamfer_types = {'Round', 'Chamfer', 'Fillet', 'EdgeBlend'}
        pattern_types = {'CircularPattern', 'RectangularPattern', 'LinearPattern', 'MirrorPattern', 'Pattern'}

        for feat_type, count in self.feature_types.items():
            ratio = count / total
            if feat_type in extrusion_types:
                self.extrusion_ratio += ratio
            elif feat_type in cutout_types:
                self.cutout_ratio += ratio
            elif feat_type in hole_types:
                self.hole_ratio += ratio
            elif feat_type in revolution_types:
                self.revolution_ratio += ratio
            elif feat_type in round_chamfer_types:
                self.round_chamfer_ratio += ratio
            elif feat_type in pattern_types:
                self.pattern_ratio += ratio

    def _determine_naming_style(self):
        """Determina lo stile di naming delle feature."""
        if not self.feature_names:
            self.naming_style = "unknown"
            return

        # Conta feature con nomi personalizzati (non default)
        default_patterns = [
            r'^Sketch\d*$', r'^Protrusion\d*$', r'^Cutout\d*$', r'^Hole\d*$',
            r'^Extrude\d*$', r'^Revolve\d*$', r'^Pattern\d*$', r'^Fillet\d*$',
            r'^Chamfer\d*$', r'^Round\d*$', r'^Boss-Extrude\d*$', r'^Cut-Extrude\d*$'
        ]

        import re
        custom_count = 0
        for name in self.feature_names:
            is_default = any(re.match(pat, name, re.IGNORECASE) for pat in default_patterns)
            if not is_default:
                custom_count += 1

        ratio = custom_count / len(self.feature_names) if self.feature_names else 0

        if ratio < 0.1:
            self.naming_style = "default"
        elif ratio > 0.7:
            self.naming_style = "custom"
        else:
            self.naming_style = "mixed"

    def _calculate_constraint_ratio(self):
        """Calcola il rapporto vincoli/geometrie."""
        if self.total_2d_geometry_count > 0:
            self.constraint_to_geometry_ratio = (
                self.total_2d_constraint_count / self.total_2d_geometry_count
            )

    def to_dict(self) -> Dict[str, Any]:
        """Converte la signature in dizionario (per JSON)."""
        result = {
            'filepath': self.filepath,
            'filename': self.filename,
            'cad_type': self.cad_type,
            'file_extension': self.file_extension,
            'file_hash': self.file_hash,
            'author': self.author,
            'last_author': self.last_author,
            'company': self.company,
            'title': self.title,
            'template': self.template,
            'cad_version': self.cad_version,
            'feature_count': self.feature_count,
            'feature_types': dict(self.feature_types),
            'feature_sequence': list(self.feature_sequence),
            'feature_names': list(self.feature_names),
            'sketches_count': self.sketches_count,
            'total_2d_geometry_count': self.total_2d_geometry_count,
            'total_2d_constraint_count': self.total_2d_constraint_count,
            'geometry_2d_types': dict(self.geometry_2d_types),
            'constraint_2d_types': dict(self.constraint_2d_types),
            'constraint_to_geometry_ratio': self.constraint_to_geometry_ratio,
            'extrusion_ratio': self.extrusion_ratio,
            'cutout_ratio': self.cutout_ratio,
            'hole_ratio': self.hole_ratio,
            'revolution_ratio': self.revolution_ratio,
            'round_chamfer_ratio': self.round_chamfer_ratio,
            'pattern_ratio': self.pattern_ratio,
            'naming_style': self.naming_style,
            'extraction_complete': self.extraction_complete,
            'extraction_warnings': self.extraction_warnings,
            'extraction_errors': self.extraction_errors,
            'extractor_version': self.extractor_version,
        }

        # Converti sketches_data in lista di dict
        result['sketches_data'] = [
            {
                'name': sk.name,
                'geometry_count': sk.geometry_count,
                'constraint_count': sk.constraint_count,
                'geometry_types': dict(sk.geometry_types),
                'constraint_types': dict(sk.constraint_types),
                'geometry_detailed': [
                    {
                        'id': g.id,
                        'type': g.type,
                        'start_point': g.start_point,
                        'end_point': g.end_point,
                        'center_point': g.center_point,
                        'radius': g.radius,
                        'start_point_uv': g.start_point_uv,
                        'end_point_uv': g.end_point_uv,
                        'center_point_uv': g.center_point_uv,
                    } for g in sk.geometry_detailed
                ],
                'constraint_detailed': [
                    {'id': c.id, 'type': c.type, 'category': c.category,
                     'description': c.description, 'value': c.value}
                    for c in sk.constraint_detailed
                ],
                'parametric_frame': {
                    'centroid': sk.parametric_frame.centroid,
                    'axis_u': sk.parametric_frame.axis_u,
                    'axis_v': sk.parametric_frame.axis_v,
                    'extent_u': sk.parametric_frame.extent_u,
                    'extent_v': sk.parametric_frame.extent_v,
                    'num_points': sk.parametric_frame.num_points,
                    'is_valid': sk.parametric_frame.is_valid,
                } if sk.parametric_frame else None
            }
            for sk in self.sketches_data
        ]

        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'CADModelSignature':
        """Crea una signature da dizionario."""
        # Estrai sketches_data
        sketches = []
        for sk_data in data.get('sketches_data', []):
            # Ricostruisci parametric_frame se presente
            pf_data = sk_data.get('parametric_frame')
            parametric_frame = None
            if pf_data:
                parametric_frame = SketchParametricFrame(
                    centroid=tuple(pf_data.get('centroid', (0.0, 0.0))),
                    axis_u=tuple(pf_data.get('axis_u', (1.0, 0.0))),
                    axis_v=tuple(pf_data.get('axis_v', (0.0, 1.0))),
                    extent_u=pf_data.get('extent_u', 1.0),
                    extent_v=pf_data.get('extent_v', 1.0),
                    num_points=pf_data.get('num_points', 0),
                    is_valid=pf_data.get('is_valid', False),
                )

            sketches.append(SketchData(
                name=sk_data.get('name', ''),
                geometry_count=sk_data.get('geometry_count', 0),
                constraint_count=sk_data.get('constraint_count', 0),
                geometry_types=sk_data.get('geometry_types', {}),
                constraint_types=sk_data.get('constraint_types', {}),
                geometry_detailed=[
                    GeometryData(
                        id=g['id'],
                        type=g['type'],
                        start_point=tuple(g['start_point']) if g.get('start_point') else None,
                        end_point=tuple(g['end_point']) if g.get('end_point') else None,
                        center_point=tuple(g['center_point']) if g.get('center_point') else None,
                        radius=g.get('radius'),
                        start_point_uv=tuple(g['start_point_uv']) if g.get('start_point_uv') else None,
                        end_point_uv=tuple(g['end_point_uv']) if g.get('end_point_uv') else None,
                        center_point_uv=tuple(g['center_point_uv']) if g.get('center_point_uv') else None,
                    )
                    for g in sk_data.get('geometry_detailed', [])
                ],
                constraint_detailed=[
                    ConstraintData(
                        id=c['id'], type=c['type'],
                        category=c.get('category', 'unknown'),
                        description=c.get('description', ''),
                        value=c.get('value')
                    )
                    for c in sk_data.get('constraint_detailed', [])
                ],
                parametric_frame=parametric_frame,
            ))

        return cls(
            filepath=data.get('filepath', ''),
            filename=data.get('filename', ''),
            cad_type=data.get('cad_type', ''),
            file_extension=data.get('file_extension', ''),
            file_hash=data.get('file_hash', ''),
            author=data.get('author', ''),
            last_author=data.get('last_author', ''),
            company=data.get('company', ''),
            title=data.get('title', ''),
            template=data.get('template', ''),
            cad_version=data.get('cad_version', ''),
            feature_count=data.get('feature_count', 0),
            feature_types=Counter(data.get('feature_types', {})),
            feature_sequence=data.get('feature_sequence', []),
            feature_names=data.get('feature_names', []),
            sketches_count=data.get('sketches_count', 0),
            total_2d_geometry_count=data.get('total_2d_geometry_count', 0),
            total_2d_constraint_count=data.get('total_2d_constraint_count', 0),
            geometry_2d_types=Counter(data.get('geometry_2d_types', {})),
            constraint_2d_types=Counter(data.get('constraint_2d_types', {})),
            sketches_data=sketches,
            extraction_complete=data.get('extraction_complete', True),
            extraction_warnings=data.get('extraction_warnings', []),
            extraction_errors=data.get('extraction_errors', []),
            extractor_version=data.get('extractor_version', '1.0.0'),
        )

