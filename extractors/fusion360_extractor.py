"""
Fusion 360 Extractor - Estrattore per file Fusion 360 via API nativa.

Supporta:
- .f3d (Fusion 360 Design)
- .f3z (Fusion 360 Archive)

NOTA IMPORTANTE:
Fusion 360 NON supporta l'apertura diretta di file .f3d/.f3z via API esterna.
I file devono essere aperti all'interno dell'applicazione Fusion 360.

Questo estrattore può funzionare in DUE modalità:
1. COME ADD-IN di Fusion 360 (dentro l'app)
2. VIA API CLOUD (richiede autenticazione Autodesk Forge)

Per la modalità standalone, questo estrattore analizza la struttura
del file .f3d/.f3z (che è uno ZIP contenente JSON) senza richiedere
l'applicazione Fusion 360.
"""

from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import Counter
import traceback
import json
import zipfile
import tempfile
import os

from .base_extractor import BaseCADExtractor, ExtractionError
from .cad_signature import (
    CADModelSignature, SketchData, FeatureData,
    ConstraintData, GeometryData
)


# Flag per indicare se adsk è disponibile (dentro Fusion 360)
ADSK_AVAILABLE = False
adsk = None

def _init_adsk():
    """Inizializza moduli Autodesk se disponibili (dentro Fusion 360)."""
    global ADSK_AVAILABLE, adsk

    if ADSK_AVAILABLE:
        return True

    try:
        import adsk.core
        import adsk.fusion
        adsk = type('adsk', (), {'core': adsk.core, 'fusion': adsk.fusion})()
        ADSK_AVAILABLE = True
        return True
    except ImportError:
        return False


# ============================================================================
# MAPPING TIPI FEATURE FUSION 360
# ============================================================================

FEATURE_TYPE_MAP = {
    # Extrusions
    "ExtrudeFeature": "Protrusion",
    "Extrude": "Protrusion",
    "NewBodyExtrude": "Protrusion",
    "CutExtrude": "Cutout",
    "JoinExtrude": "Protrusion",
    "IntersectExtrude": "Cutout",

    # Revolution
    "RevolveFeature": "Revolution",
    "Revolve": "Revolution",

    # Sweep
    "SweepFeature": "Sweep",
    "Sweep": "Sweep",

    # Loft
    "LoftFeature": "Loft",
    "Loft": "Loft",

    # Holes
    "HoleFeature": "Hole",
    "Hole": "Hole",

    # Fillets & Chamfers
    "FilletFeature": "Round",
    "Fillet": "Round",
    "ChamferFeature": "Chamfer",
    "Chamfer": "Chamfer",

    # Patterns
    "RectangularPatternFeature": "RectangularPattern",
    "CircularPatternFeature": "CircularPattern",
    "MirrorFeature": "Mirror",

    # Shell
    "ShellFeature": "Shell",
    "Shell": "Shell",

    # Reference
    "ConstructionPlane": "RefPlane",
    "ConstructionAxis": "RefAxis",
    "ConstructionPoint": "RefPoint",

    # Sketch
    "Sketch": "Sketch",
}

# Mapping vincoli Fusion 360
CONSTRAINT_TYPE_MAP = {
    "coincidentConstraint": ("Coincident", "geometrico", "Coincidenza"),
    "collinearConstraint": ("Collinear", "geometrico", "Collinearità"),
    "concentricConstraint": ("Concentric", "geometrico", "Concentricità"),
    "horizontalConstraint": ("Horizontal", "geometrico", "Orizzontale"),
    "verticalConstraint": ("Vertical", "geometrico", "Verticale"),
    "parallelConstraint": ("Parallel", "geometrico", "Parallelismo"),
    "perpendicularConstraint": ("Perpendicular", "geometrico", "Perpendicolarità"),
    "tangentConstraint": ("Tangent", "geometrico", "Tangenza"),
    "equalConstraint": ("Equal", "geometrico", "Uguaglianza"),
    "symmetryConstraint": ("Symmetric", "geometrico", "Simmetria"),
    "midPointConstraint": ("MidPoint", "geometrico", "Punto medio"),
    "fixConstraint": ("Ground", "geometrico", "Fisso"),

    # Dimensionali
    "sketchDimension": ("Distance", "dimensionale", "Quota"),
    "sketchLinearDimension": ("Length", "dimensionale", "Lunghezza"),
    "sketchAngularDimension": ("Angle", "dimensionale", "Angolo"),
    "sketchRadialDimension": ("Radius", "dimensionale", "Raggio"),
    "sketchDiameterDimension": ("Diameter", "dimensionale", "Diametro"),
    "sketchOffsetDimension": ("Offset", "dimensionale", "Offset"),
}


class Fusion360Extractor(BaseCADExtractor):
    """
    Estrattore per file Fusion 360.

    Supporta due modalità:
    1. Standalone: Analizza la struttura del file .f3d/.f3z (ZIP + JSON)
    2. Add-in: Usa l'API nativa dentro Fusion 360 (richiede adsk modules)

    La modalità standalone estrae meno dettagli ma non richiede Fusion 360.
    """

    CAD_NAME = "Fusion360"
    SUPPORTED_EXTENSIONS = [".f3d", ".f3z"]
    VERSION = "1.0.0"

    def __init__(self, use_native_api: bool = True):
        """
        Args:
            use_native_api: Se True, prova a usare l'API nativa adsk.
                            Se False, usa solo l'analisi del file ZIP.
        """
        super().__init__()
        self._use_native_api = use_native_api
        self._app = None

    @property
    def is_available(self) -> bool:
        """
        Fusion 360 è sempre "disponibile" in modalità standalone
        perché analizza direttamente la struttura del file.
        """
        return True

    def _connect(self) -> bool:
        """Connette a Fusion 360 (se disponibile) o usa modalità standalone."""
        if self._use_native_api and _init_adsk():
            try:
                self._app = adsk.core.Application.get()
                self._connected = True
                return True
            except Exception:
                pass

        # Modalità standalone sempre disponibile
        self._connected = True
        return True

    def _disconnect(self):
        """Disconnette (noop per modalità standalone)."""
        self._app = None
        self._connected = False

    def _extract_from_document(self, filepath: Path) -> CADModelSignature:
        """Estrae signature da un file Fusion 360."""
        # Se siamo dentro Fusion 360 con API nativa
        if self._app and ADSK_AVAILABLE:
            return self._extract_via_native_api(filepath)

        # Altrimenti usa analisi del file ZIP
        return self._extract_via_file_analysis(filepath)

    def _extract_via_native_api(self, filepath: Path) -> CADModelSignature:
        """Estrae usando l'API nativa di Fusion 360 (dentro l'app)."""
        sig = CADModelSignature()

        try:
            # Apri documento
            doc = self._app.documents.open(str(filepath))
            if not doc:
                raise ExtractionError(
                    "Impossibile aprire il documento",
                    self.CAD_NAME,
                    str(filepath)
                )

            design = doc.products.itemByProductType('DesignProductType')
            if not design:
                self._add_warning("Nessun design trovato nel documento")
                return sig

            # === METADATI ===
            sig.title = design.rootComponent.name
            sig.author = doc.createdBy if hasattr(doc, 'createdBy') else ''

            # === FEATURE 3D ===
            self._extract_features_native(design, sig)

            # === SKETCH 2D ===
            self._extract_sketches_native(design, sig)

            return sig

        except Exception as e:
            raise ExtractionError(
                f"Errore API nativa: {e}",
                self.CAD_NAME,
                str(filepath)
            )

    def _extract_features_native(self, design, sig: CADModelSignature):
        """Estrae features usando API nativa."""
        try:
            root_comp = design.rootComponent
            timeline = design.timeline

            feature_types = Counter()
            feature_sequence = []
            feature_names = []
            features_detailed = []

            for i in range(timeline.count):
                try:
                    tl_obj = timeline.item(i)
                    entity = tl_obj.entity

                    if not entity:
                        continue

                    # Tipo
                    entity_type = type(entity).__name__
                    normalized_type = FEATURE_TYPE_MAP.get(entity_type, entity_type)

                    # Nome
                    feat_name = entity.name if hasattr(entity, 'name') else f"Feature_{i}"

                    feature_types[normalized_type] += 1
                    feature_sequence.append(normalized_type)
                    feature_names.append(feat_name)

                    features_detailed.append(FeatureData(
                        name=feat_name,
                        type=normalized_type,
                        original_type=entity_type,
                        order=i + 1,
                        is_suppressed=tl_obj.isSuppressed if hasattr(tl_obj, 'isSuppressed') else False
                    ))

                except Exception:
                    continue

            sig.feature_count = len(feature_sequence)
            sig.feature_types = feature_types
            sig.feature_sequence = feature_sequence
            sig.feature_names = feature_names
            sig.features_detailed = features_detailed

        except Exception as e:
            self._add_warning(f"Errore estrazione features native: {e}")

    def _extract_sketches_native(self, design, sig: CADModelSignature):
        """Estrae sketch usando API nativa."""
        try:
            root_comp = design.rootComponent
            sketches = root_comp.sketches

            sketches_data = []
            total_geom = 0
            total_constr = 0
            geom_types = Counter()
            constr_types = Counter()

            for i in range(sketches.count):
                try:
                    sketch = sketches.item(i)
                    sk_data = self._extract_single_sketch_native(sketch)
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
            self._add_warning(f"Errore estrazione sketches native: {e}")

    def _extract_single_sketch_native(self, sketch) -> SketchData:
        """Estrae dati da uno sketch usando API nativa."""
        name = sketch.name if hasattr(sketch, 'name') else 'Sketch'
        sk_data = SketchData(name=name)

        geom_types = Counter()
        geom_detailed = []
        constr_types = Counter()
        constr_detailed = []

        try:
            # Geometrie
            curves = sketch.sketchCurves

            # Linee
            for j in range(curves.sketchLines.count):
                geom_types['Line'] += 1
                geom_detailed.append(GeometryData(id=f"Line_{j}", type='Line'))

            # Archi
            for j in range(curves.sketchArcs.count):
                geom_types['Arc'] += 1
                geom_detailed.append(GeometryData(id=f"Arc_{j}", type='Arc'))

            # Cerchi
            for j in range(curves.sketchCircles.count):
                geom_types['Circle'] += 1
                geom_detailed.append(GeometryData(id=f"Circle_{j}", type='Circle'))

            # Ellissi
            for j in range(curves.sketchEllipses.count):
                geom_types['Ellipse'] += 1
                geom_detailed.append(GeometryData(id=f"Ellipse_{j}", type='Ellipse'))

            # Spline
            for j in range(curves.sketchFittedSplines.count):
                geom_types['Spline'] += 1
                geom_detailed.append(GeometryData(id=f"Spline_{j}", type='Spline'))

            # Vincoli geometrici
            constraints = sketch.geometricConstraints
            for j in range(constraints.count):
                try:
                    constr = constraints.item(j)
                    constr_type_name = type(constr).__name__

                    if constr_type_name in CONSTRAINT_TYPE_MAP:
                        type_name, category, description = CONSTRAINT_TYPE_MAP[constr_type_name]
                    else:
                        type_name = constr_type_name
                        category = "geometrico"
                        description = ""

                    constr_types[type_name] += 1
                    constr_detailed.append(ConstraintData(
                        id=f"GeoC_{j}",
                        type=type_name,
                        original_type=constr_type_name,
                        category=category,
                        description=description
                    ))
                except Exception:
                    continue

            # Vincoli dimensionali
            dimensions = sketch.sketchDimensions
            for j in range(dimensions.count):
                try:
                    dim = dimensions.item(j)
                    dim_type_name = type(dim).__name__

                    if dim_type_name in CONSTRAINT_TYPE_MAP:
                        type_name, category, description = CONSTRAINT_TYPE_MAP[dim_type_name]
                    else:
                        type_name = dim_type_name.replace('SketchDimension', '')
                        category = "dimensionale"
                        description = ""

                    value = None
                    try:
                        value = dim.value
                    except Exception:
                        pass

                    constr_types[type_name] += 1
                    constr_detailed.append(ConstraintData(
                        id=f"DimC_{j}",
                        type=type_name,
                        original_type=dim_type_name,
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

    # ========================================================================
    # MODALITÀ STANDALONE: Analisi diretta del file ZIP
    # ========================================================================

    def _extract_via_file_analysis(self, filepath: Path) -> CADModelSignature:
        """
        Estrae dati analizzando direttamente la struttura del file .f3d/.f3z.

        I file Fusion 360 sono archivi ZIP contenenti:
        - manifest.json: Metadati del documento
        - *.f3d: File binari con i dati del design
        - *.json: Struttura del modello (a volte)
        """
        sig = CADModelSignature()
        sig.extraction_warnings.append(
            "Modalità standalone: estrazione limitata dalla struttura del file. "
            "Per estrazione completa, usa Fusion 360 con questo script come add-in."
        )

        try:
            # Verifica se è un file ZIP valido
            if not zipfile.is_zipfile(str(filepath)):
                raise ExtractionError(
                    "Il file non è un archivio Fusion 360 valido",
                    self.CAD_NAME,
                    str(filepath)
                )

            with zipfile.ZipFile(str(filepath), 'r') as zf:
                # Lista file nell'archivio
                file_list = zf.namelist()

                # === METADATI da manifest.json ===
                if 'manifest.json' in file_list:
                    try:
                        with zf.open('manifest.json') as f:
                            manifest = json.load(f)
                            self._extract_metadata_from_manifest(manifest, sig)
                    except Exception as e:
                        self._add_warning(f"Errore lettura manifest: {e}")

                # === Cerca file JSON con struttura design ===
                for fname in file_list:
                    if fname.endswith('.json') and fname != 'manifest.json':
                        try:
                            with zf.open(fname) as f:
                                data = json.load(f)
                                self._extract_from_json_structure(data, sig)
                        except Exception:
                            continue

                # Se non abbiamo trovato nulla, analizza struttura file
                if sig.feature_count == 0:
                    sig.feature_count = len([f for f in file_list if '.f3d' in f or 'brep' in f.lower()])
                    sig.extraction_complete = False
                    sig.extraction_warnings.append(
                        "Struttura interna non leggibile. Conteggio feature stimato."
                    )

            return sig

        except zipfile.BadZipFile:
            raise ExtractionError(
                "File corrotto o non è un archivio Fusion 360 valido",
                self.CAD_NAME,
                str(filepath)
            )
        except Exception as e:
            raise ExtractionError(
                f"Errore analisi file: {e}",
                self.CAD_NAME,
                str(filepath)
            )

    def _extract_metadata_from_manifest(self, manifest: Dict, sig: CADModelSignature):
        """Estrae metadati dal manifest.json."""
        try:
            sig.title = manifest.get('name', '')
            sig.author = manifest.get('author', '')
            sig.comments = manifest.get('description', '')

            # Versione
            version_info = manifest.get('version', {})
            if isinstance(version_info, dict):
                sig.cad_version = version_info.get('fusion', '')

        except Exception as e:
            self._add_warning(f"Errore parsing manifest: {e}")

    def _extract_from_json_structure(self, data: Dict, sig: CADModelSignature):
        """Estrae dati dalla struttura JSON interna (se disponibile)."""
        try:
            # Cerca timeline/features
            if 'timeline' in data:
                timeline = data['timeline']
                for item in timeline:
                    if isinstance(item, dict):
                        feat_type = item.get('type', item.get('entityType', 'Unknown'))
                        feat_name = item.get('name', '')

                        normalized = FEATURE_TYPE_MAP.get(feat_type, feat_type)
                        sig.feature_types[normalized] = sig.feature_types.get(normalized, 0) + 1
                        sig.feature_sequence.append(normalized)
                        sig.feature_names.append(feat_name)

                sig.feature_count = len(sig.feature_sequence)

            # Cerca sketches
            if 'sketches' in data:
                sketches = data['sketches']
                for sk in sketches:
                    if isinstance(sk, dict):
                        sk_data = SketchData(name=sk.get('name', 'Sketch'))

                        # Geometrie
                        curves = sk.get('curves', [])
                        for curve in curves:
                            curve_type = curve.get('type', 'Unknown')
                            if 'line' in curve_type.lower():
                                sk_data.geometry_types['Line'] = sk_data.geometry_types.get('Line', 0) + 1
                            elif 'arc' in curve_type.lower():
                                sk_data.geometry_types['Arc'] = sk_data.geometry_types.get('Arc', 0) + 1
                            elif 'circle' in curve_type.lower():
                                sk_data.geometry_types['Circle'] = sk_data.geometry_types.get('Circle', 0) + 1

                        sk_data.geometry_count = sum(sk_data.geometry_types.values())

                        # Vincoli
                        constraints = sk.get('constraints', [])
                        for c in constraints:
                            c_type = c.get('type', 'Unknown')
                            if c_type in CONSTRAINT_TYPE_MAP:
                                type_name = CONSTRAINT_TYPE_MAP[c_type][0]
                            else:
                                type_name = c_type
                            sk_data.constraint_types[type_name] = sk_data.constraint_types.get(type_name, 0) + 1

                        sk_data.constraint_count = sum(sk_data.constraint_types.values())

                        sig.sketches_data.append(sk_data)
                        sig.total_2d_geometry_count += sk_data.geometry_count
                        sig.total_2d_constraint_count += sk_data.constraint_count

                sig.sketches_count = len(sig.sketches_data)

        except Exception as e:
            self._add_warning(f"Errore parsing struttura JSON: {e}")

