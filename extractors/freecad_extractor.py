"""
FreeCAD Extractor - Estrattore per file FreeCAD via Python API nativa.

Supporta:
- .FCStd (FreeCAD Standard)
- .fcstd (case insensitive)

FreeCAD è open source e ha un'API Python nativa molto completa.
Questo estrattore può funzionare sia come modulo FreeCAD che standalone.
"""

from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import Counter
import traceback
import sys
import os

from .base_extractor import BaseCADExtractor, ExtractionError
from .cad_signature import (
    CADModelSignature, SketchData, FeatureData,
    ConstraintData, GeometryData
)


# Flag per indicare se FreeCAD è disponibile
FREECAD_AVAILABLE = False
FreeCAD = None
FreeCADGui = None

def _init_freecad():
    """Inizializza FreeCAD se disponibile."""
    global FREECAD_AVAILABLE, FreeCAD, FreeCADGui

    if FREECAD_AVAILABLE:
        return True

    # Prova a importare FreeCAD
    try:
        import FreeCAD as FC
        FreeCAD = FC
        FREECAD_AVAILABLE = True

        # Prova anche FreeCADGui (opzionale)
        try:
            import FreeCADGui as FCGui
            FreeCADGui = FCGui
        except ImportError:
            pass

        return True

    except ImportError:
        pass

    # Prova percorsi comuni di FreeCAD
    freecad_paths = [
        r"C:\Program Files\FreeCAD 0.21\bin",
        r"C:\Program Files\FreeCAD 0.20\bin",
        r"C:\Program Files\FreeCAD\bin",
        r"/usr/lib/freecad/lib",
        r"/usr/lib/freecad-python3/lib",
        r"/Applications/FreeCAD.app/Contents/Resources/lib",
    ]

    for path in freecad_paths:
        if os.path.exists(path):
            if path not in sys.path:
                sys.path.append(path)
            try:
                import FreeCAD as FC
                FreeCAD = FC
                FREECAD_AVAILABLE = True
                return True
            except ImportError:
                continue

    return False


# ============================================================================
# MAPPING TIPI FEATURE FREECAD
# ============================================================================

FEATURE_TYPE_MAP = {
    # Part Design
    "Pad": "Protrusion",
    "PartDesign::Pad": "Protrusion",
    "Pocket": "Cutout",
    "PartDesign::Pocket": "Cutout",
    "Revolution": "Revolution",
    "PartDesign::Revolution": "Revolution",
    "Groove": "RevolvedCutout",
    "PartDesign::Groove": "RevolvedCutout",
    "AdditivePipe": "Sweep",
    "PartDesign::AdditivePipe": "Sweep",
    "SubtractivePipe": "SweptCutout",
    "PartDesign::SubtractivePipe": "SweptCutout",
    "AdditiveLoft": "Loft",
    "PartDesign::AdditiveLoft": "Loft",
    "SubtractiveLoft": "LoftedCutout",
    "PartDesign::SubtractiveLoft": "LoftedCutout",

    # Holes
    "Hole": "Hole",
    "PartDesign::Hole": "Hole",

    # Dress-up
    "Fillet": "Round",
    "PartDesign::Fillet": "Round",
    "Chamfer": "Chamfer",
    "PartDesign::Chamfer": "Chamfer",
    "Draft": "Draft",
    "PartDesign::Draft": "Draft",
    "Thickness": "Shell",
    "PartDesign::Thickness": "Shell",

    # Patterns
    "LinearPattern": "RectangularPattern",
    "PartDesign::LinearPattern": "RectangularPattern",
    "PolarPattern": "CircularPattern",
    "PartDesign::PolarPattern": "CircularPattern",
    "Mirrored": "Mirror",
    "PartDesign::Mirrored": "Mirror",
    "MultiTransform": "Pattern",
    "PartDesign::MultiTransform": "Pattern",

    # Sketch
    "Sketch": "Sketch",
    "Sketcher::SketchObject": "Sketch",
    "SketchObject": "Sketch",

    # Reference
    "Plane": "RefPlane",
    "PartDesign::Plane": "RefPlane",
    "Line": "RefAxis",
    "PartDesign::Line": "RefAxis",
    "Point": "RefPoint",
    "PartDesign::Point": "RefPoint",

    # Part primitives
    "Box": "Protrusion",
    "Part::Box": "Protrusion",
    "Cylinder": "Revolution",
    "Part::Cylinder": "Revolution",
    "Sphere": "Revolution",
    "Part::Sphere": "Revolution",
    "Cone": "Revolution",
    "Part::Cone": "Revolution",
    "Torus": "Revolution",
    "Part::Torus": "Revolution",
}

# Mapping vincoli Sketcher
CONSTRAINT_TYPE_MAP = {
    # Geometrici
    "Coincident": ("Coincident", "geometrico", "Coincidenza"),
    "PointOnObject": ("Coincident", "geometrico", "Punto su oggetto"),
    "Vertical": ("Vertical", "geometrico", "Verticale"),
    "Horizontal": ("Horizontal", "geometrico", "Orizzontale"),
    "Parallel": ("Parallel", "geometrico", "Parallelismo"),
    "Perpendicular": ("Perpendicular", "geometrico", "Perpendicolarità"),
    "Tangent": ("Tangent", "geometrico", "Tangenza"),
    "Equal": ("Equal", "geometrico", "Uguaglianza"),
    "Symmetric": ("Symmetric", "geometrico", "Simmetria"),
    "Block": ("Ground", "geometrico", "Blocco"),

    # Dimensionali
    "Distance": ("Distance", "dimensionale", "Distanza"),
    "DistanceX": ("Distance", "dimensionale", "Distanza X"),
    "DistanceY": ("Distance", "dimensionale", "Distanza Y"),
    "Radius": ("Radius", "dimensionale", "Raggio"),
    "Diameter": ("Diameter", "dimensionale", "Diametro"),
    "Angle": ("Angle", "dimensionale", "Angolo"),
    "InternalAngle": ("Angle", "dimensionale", "Angolo interno"),
    "Lock": ("Ground", "geometrico", "Fisso"),
}


class FreeCADExtractor(BaseCADExtractor):
    """
    Estrattore per file FreeCAD.

    Utilizza l'API Python nativa di FreeCAD, non richiede COM.
    Può funzionare su Windows, Linux e macOS.
    """

    CAD_NAME = "FreeCAD"
    SUPPORTED_EXTENSIONS = [".fcstd"]
    VERSION = "1.0.0"

    def __init__(self):
        super().__init__()
        self._doc = None

    @property
    def is_available(self) -> bool:
        """Verifica se FreeCAD è disponibile."""
        return _init_freecad()

    def _connect(self) -> bool:
        """Inizializza FreeCAD."""
        if _init_freecad():
            self._connected = True
            return True
        return False

    def _disconnect(self):
        """Chiude documento FreeCAD."""
        if self._doc and FreeCAD:
            try:
                FreeCAD.closeDocument(self._doc.Name)
            except Exception:
                pass
        self._doc = None
        self._connected = False

    def _extract_from_document(self, filepath: Path) -> CADModelSignature:
        """Estrae signature da un documento FreeCAD."""
        if not _init_freecad():
            raise ExtractionError(
                "FreeCAD non disponibile. Installalo o aggiungi il percorso a sys.path",
                self.CAD_NAME,
                str(filepath)
            )

        try:
            # Apri documento
            self._doc = FreeCAD.openDocument(str(filepath))

            if not self._doc:
                raise ExtractionError(
                    "Impossibile aprire il documento",
                    self.CAD_NAME,
                    str(filepath)
                )

            # Estrai dati
            signature = self._extract_document_data(self._doc, filepath)

            return signature

        except ExtractionError:
            raise
        except Exception as e:
            raise ExtractionError(
                f"Errore durante l'estrazione: {e}",
                self.CAD_NAME,
                str(filepath)
            )

    def _extract_document_data(self, doc, filepath: Path) -> CADModelSignature:
        """Estrae tutti i dati dal documento."""
        sig = CADModelSignature()

        # === METADATI ===
        self._extract_metadata(doc, sig)

        # === FEATURE 3D ===
        self._extract_features(doc, sig)

        # === SKETCH 2D ===
        self._extract_sketches(doc, sig)

        return sig

    def _extract_metadata(self, doc, sig: CADModelSignature):
        """Estrae metadati del documento."""
        try:
            # FreeCAD standard properties
            sig.author = getattr(doc, 'CreatedBy', '') or ''
            sig.last_author = getattr(doc, 'LastModifiedBy', '') or ''
            sig.company = getattr(doc, 'Company', '') or ''
            sig.title = getattr(doc, 'Label', '') or doc.Name
            sig.comments = getattr(doc, 'Comment', '') or ''

            # Versione
            if FreeCAD:
                try:
                    sig.cad_version = FreeCAD.Version()[0] + '.' + FreeCAD.Version()[1]
                except Exception:
                    pass

        except Exception as e:
            self._add_warning(f"Errore estrazione metadati: {e}")

    def _extract_features(self, doc, sig: CADModelSignature):
        """Estrae le feature 3D."""
        try:
            feature_types = Counter()
            feature_sequence = []
            feature_names = []
            features_detailed = []
            order = 0

            # Itera su tutti gli oggetti nel documento
            for obj in doc.Objects:
                try:
                    obj_type = obj.TypeId

                    # Salta oggetti non-feature
                    if obj_type in ['App::Origin', 'App::Line', 'App::Plane',
                                    'App::DocumentObjectGroup', 'Spreadsheet::Sheet']:
                        continue

                    # Salta sketch (li processiamo separatamente)
                    if 'Sketch' in obj_type:
                        continue

                    # Normalizza tipo
                    normalized_type = FEATURE_TYPE_MAP.get(obj_type, None)
                    if not normalized_type:
                        # Prova nome classe
                        class_name = obj_type.split('::')[-1] if '::' in obj_type else obj_type
                        normalized_type = FEATURE_TYPE_MAP.get(class_name, class_name)

                    # Nome
                    feat_name = obj.Label or obj.Name

                    order += 1
                    feature_types[normalized_type] += 1
                    feature_sequence.append(normalized_type)
                    feature_names.append(feat_name)

                    features_detailed.append(FeatureData(
                        name=feat_name,
                        type=normalized_type,
                        original_type=obj_type,
                        order=order,
                        is_suppressed=not getattr(obj, 'Visibility', True)
                    ))

                except Exception as e:
                    continue

            sig.feature_count = len(feature_sequence)
            sig.feature_types = feature_types
            sig.feature_sequence = feature_sequence
            sig.feature_names = feature_names
            sig.features_detailed = features_detailed

        except Exception as e:
            self._add_warning(f"Errore estrazione features: {e}")
            sig.extraction_errors.append(f"Features: {e}")

    def _extract_sketches(self, doc, sig: CADModelSignature):
        """Estrae gli sketch 2D."""
        try:
            sketches_data = []
            total_geom = 0
            total_constr = 0
            geom_types = Counter()
            constr_types = Counter()

            # Trova tutti gli sketch
            for obj in doc.Objects:
                try:
                    obj_type = obj.TypeId

                    if 'Sketch' not in obj_type:
                        continue

                    sk_data = self._extract_single_sketch(obj)
                    sketches_data.append(sk_data)

                    total_geom += sk_data.geometry_count
                    total_constr += sk_data.constraint_count

                    for gtype, count in sk_data.geometry_types.items():
                        geom_types[gtype] += count
                    for ctype, count in sk_data.constraint_types.items():
                        constr_types[ctype] += count

                except Exception:
                    continue

            sig.sketches_count = len(sketches_data)
            sig.sketches_data = sketches_data
            sig.total_2d_geometry_count = total_geom
            sig.total_2d_constraint_count = total_constr
            sig.geometry_2d_types = geom_types
            sig.constraint_2d_types = constr_types

        except Exception as e:
            self._add_warning(f"Errore estrazione sketches: {e}")

    def _extract_single_sketch(self, sketch) -> SketchData:
        """Estrae dati da un singolo sketch FreeCAD."""
        name = getattr(sketch, 'Label', None) or getattr(sketch, 'Name', 'Sketch')
        sk_data = SketchData(name=name)

        geom_types = Counter()
        geom_detailed = []
        constr_types = Counter()
        constr_detailed = []

        try:
            # === GEOMETRIE ===
            geometry = getattr(sketch, 'Geometry', [])

            for i, geom in enumerate(geometry):
                try:
                    geom_type_name = type(geom).__name__

                    # Normalizza tipo
                    if 'LineSegment' in geom_type_name or geom_type_name == 'Line':
                        normalized = 'Line'
                    elif 'Circle' in geom_type_name:
                        normalized = 'Circle'
                    elif 'Arc' in geom_type_name:
                        normalized = 'Arc'
                    elif 'Ellipse' in geom_type_name:
                        normalized = 'Ellipse'
                    elif 'BSpline' in geom_type_name or 'Spline' in geom_type_name:
                        normalized = 'Spline'
                    elif 'Point' in geom_type_name:
                        normalized = 'Point'
                    else:
                        normalized = geom_type_name

                    # Salta geometrie di costruzione (opzionale)
                    # if hasattr(geom, 'Construction') and geom.Construction:
                    #     continue

                    geom_types[normalized] += 1
                    geom_detailed.append(GeometryData(
                        id=f"Geom_{i}",
                        type=normalized,
                        original_type=geom_type_name
                    ))
                except Exception:
                    continue

            # === VINCOLI ===
            constraints = getattr(sketch, 'Constraints', [])

            for i, constr in enumerate(constraints):
                try:
                    # In FreeCAD, il tipo è una stringa
                    constr_type_name = constr.Type

                    if constr_type_name in CONSTRAINT_TYPE_MAP:
                        type_name, category, description = CONSTRAINT_TYPE_MAP[constr_type_name]
                    else:
                        type_name = constr_type_name
                        category = "unknown"
                        description = ""

                    # Valore per vincoli dimensionali
                    value = None
                    if category == "dimensionale":
                        try:
                            value = constr.Value
                        except Exception:
                            pass

                    constr_types[type_name] += 1
                    constr_detailed.append(ConstraintData(
                        id=f"Cst_{i}",
                        type=type_name,
                        original_type=constr_type_name,
                        category=category,
                        description=description,
                        value=value
                    ))
                except Exception:
                    continue

        except Exception as e:
            self._add_warning(f"Errore sketch {name}: {e}")

        sk_data.geometry_count = sum(geom_types.values())
        sk_data.geometry_types = dict(geom_types)
        sk_data.geometry_detailed = geom_detailed
        sk_data.constraint_count = sum(constr_types.values())
        sk_data.constraint_types = dict(constr_types)
        sk_data.constraint_detailed = constr_detailed

        return sk_data

