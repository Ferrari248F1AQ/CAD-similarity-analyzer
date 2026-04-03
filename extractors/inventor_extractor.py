"""
Autodesk Inventor Extractor - Estrattore per file Inventor via COM.

Supporta:
- .ipt (Part)
- .iam (Assembly)
- .idw (Drawing) - solo metadati

Richiede Autodesk Inventor installato.
"""

from pathlib import Path
from typing import Dict, List, Any, Optional
from collections import Counter
import traceback

from .base_extractor import BaseCADExtractor, COMExtractorMixin, ExtractionError
from .cad_signature import (
    CADModelSignature, SketchData, FeatureData,
    ConstraintData, GeometryData
)


# ============================================================================
# MAPPING TIPI FEATURE INVENTOR
# ============================================================================

FEATURE_TYPE_MAP = {
    # Extrusions
    "ExtrudeFeature": "Protrusion",
    "kExtrudeFeatureObject": "Protrusion",

    # Cutouts
    "CutFeature": "Cutout",
    "kCutFeatureObject": "Cutout",

    # Revolution
    "RevolveFeature": "Revolution",
    "kRevolveFeatureObject": "Revolution",

    # Sweep
    "SweepFeature": "Sweep",
    "kSweepFeatureObject": "Sweep",

    # Loft
    "LoftFeature": "Loft",
    "kLoftFeatureObject": "Loft",

    # Holes
    "HoleFeature": "Hole",
    "kHoleFeatureObject": "Hole",

    # Fillets & Chamfers
    "FilletFeature": "Round",
    "kFilletFeatureObject": "Round",
    "ChamferFeature": "Chamfer",
    "kChamferFeatureObject": "Chamfer",

    # Patterns
    "RectangularPatternFeature": "RectangularPattern",
    "kRectangularPatternFeatureObject": "RectangularPattern",
    "CircularPatternFeature": "CircularPattern",
    "kCircularPatternFeatureObject": "CircularPattern",
    "MirrorFeature": "Mirror",
    "kMirrorFeatureObject": "Mirror",

    # Shell
    "ShellFeature": "Shell",
    "kShellFeatureObject": "Shell",

    # Reference
    "WorkPlane": "RefPlane",
    "kWorkPlaneObject": "RefPlane",
    "WorkAxis": "RefAxis",
    "kWorkAxisObject": "RefAxis",
    "WorkPoint": "RefPoint",
    "kWorkPointObject": "RefPoint",

    # Sketch
    "PlanarSketch": "Sketch",
    "kPlanarSketchObject": "Sketch",
    "Sketch3D": "Sketch3D",
    "kSketch3DObject": "Sketch3D",
}

# Mapping vincoli Inventor
CONSTRAINT_TYPE_MAP = {
    # Geometrici
    "CoincidentConstraint": ("Coincident", "geometrico", "Coincidenza"),
    "TangentConstraint": ("Tangent", "geometrico", "Tangenza"),
    "PerpendicularConstraint": ("Perpendicular", "geometrico", "Perpendicolarità"),
    "ParallelConstraint": ("Parallel", "geometrico", "Parallelismo"),
    "ConcentricConstraint": ("Concentric", "geometrico", "Concentricità"),
    "HorizontalConstraint": ("Horizontal", "geometrico", "Orizzontale"),
    "VerticalConstraint": ("Vertical", "geometrico", "Verticale"),
    "CollinearConstraint": ("Collinear", "geometrico", "Collinearità"),
    "EqualConstraint": ("Equal", "geometrico", "Uguaglianza"),
    "SymmetricConstraint": ("Symmetric", "geometrico", "Simmetria"),
    "FixConstraint": ("Ground", "geometrico", "Fisso"),
    "SmoothConstraint": ("Smooth", "geometrico", "Lisciatura"),

    # Dimensionali
    "TwoPointDistanceDimConstraint": ("Distance", "dimensionale", "Distanza 2 punti"),
    "TwoLineAngleDimConstraint": ("Angle", "dimensionale", "Angolo"),
    "RadiusDimConstraint": ("Radius", "dimensionale", "Raggio"),
    "DiameterDimConstraint": ("Diameter", "dimensionale", "Diametro"),
    "LinearDimConstraint": ("Length", "dimensionale", "Lunghezza lineare"),
    "AngularDimConstraint": ("Angle", "dimensionale", "Angolo"),
    "OffsetDimConstraint": ("Offset", "dimensionale", "Offset"),
}


class InventorExtractor(BaseCADExtractor, COMExtractorMixin):
    """Estrattore per file Autodesk Inventor."""

    CAD_NAME = "Inventor"
    SUPPORTED_EXTENSIONS = [".ipt", ".iam", ".idw"]
    VERSION = "1.0.0"
    COM_PROG_ID = "Inventor.Application"

    def __init__(self):
        super().__init__()
        self._created_new_app = False

    def _connect(self) -> bool:
        """Connette a Inventor."""
        try:
            self._app, self._created_new_app = self._get_or_create_app()

            if self._app:
                if self._created_new_app:
                    try:
                        self._app.Visible = False
                        self._app.SilentOperation = True
                    except Exception:
                        pass
                self._connected = True
                return True
            return False

        except ExtractionError:
            raise
        except Exception as e:
            self._add_warning(f"Errore connessione Inventor: {e}")
            return False

    def _disconnect(self):
        """Disconnette da Inventor."""
        if self._created_new_app and self._app:
            try:
                self._app.Quit()
            except Exception:
                pass
        self._app = None
        self._connected = False

    def _extract_from_document(self, filepath: Path) -> CADModelSignature:
        """Estrae signature da un documento Inventor."""
        doc = None
        was_already_open = False

        try:
            # Verifica se già aperto
            try:
                for d in self._app.Documents:
                    if Path(d.FullFileName).resolve() == filepath.resolve():
                        doc = d
                        was_already_open = True
                        break
            except Exception:
                pass

            # Apri documento
            if not doc:
                doc = self._app.Documents.Open(str(filepath), False)  # False = no visible

                if not doc:
                    raise ExtractionError(
                        "Impossibile aprire il file",
                        self.CAD_NAME,
                        str(filepath)
                    )

            # Estrai dati
            signature = self._extract_document_data(doc, filepath)

            return signature

        finally:
            if doc and not was_already_open:
                try:
                    doc.Close(True)  # True = skip save
                except Exception:
                    pass

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
            # PropertySets
            try:
                prop_sets = doc.PropertySets

                # Summary Information
                try:
                    summary = prop_sets.Item("Inventor Summary Information")
                    sig.author = self._get_property_value(summary, "Author", "")
                    sig.title = self._get_property_value(summary, "Title", "")
                    sig.comments = self._get_property_value(summary, "Comments", "")
                except Exception:
                    pass

                # Document Summary
                try:
                    doc_summary = prop_sets.Item("Inventor Document Summary Information")
                    sig.company = self._get_property_value(doc_summary, "Company", "")
                except Exception:
                    pass

                # Design Tracking
                try:
                    tracking = prop_sets.Item("Design Tracking Properties")
                    sig.author = self._get_property_value(tracking, "Designer", sig.author)
                    sig.last_author = self._get_property_value(tracking, "Engineer", "")
                except Exception:
                    pass

            except Exception:
                pass

            # Versione
            try:
                sig.cad_version = doc.SoftwareVersion.DisplayVersion
            except Exception:
                pass

        except Exception as e:
            self._add_warning(f"Errore estrazione metadati: {e}")

    def _get_property_value(self, prop_set, name: str, default: Any = None) -> Any:
        """Ottiene il valore di una proprietà."""
        try:
            prop = prop_set.Item(name)
            return prop.Value if prop else default
        except Exception:
            return default

    def _extract_features(self, doc, sig: CADModelSignature):
        """Estrae le feature 3D."""
        try:
            # Verifica se è un Part document
            if not hasattr(doc, 'ComponentDefinition'):
                return

            comp_def = doc.ComponentDefinition
            if not comp_def:
                return

            # Features collection
            features = getattr(comp_def, 'Features', None)
            if not features:
                return

            feature_types = Counter()
            feature_sequence = []
            feature_names = []
            features_detailed = []
            order = 0

            # Itera sui tipi di feature
            feature_collections = [
                ('ExtrudeFeatures', 'Protrusion'),
                ('RevolveFeatures', 'Revolution'),
                ('HoleFeatures', 'Hole'),
                ('FilletFeatures', 'Round'),
                ('ChamferFeatures', 'Chamfer'),
                ('ShellFeatures', 'Shell'),
                ('SweepFeatures', 'Sweep'),
                ('LoftFeatures', 'Loft'),
                ('RectangularPatternFeatures', 'RectangularPattern'),
                ('CircularPatternFeatures', 'CircularPattern'),
                ('MirrorFeatures', 'Mirror'),
            ]

            for collection_name, feat_type in feature_collections:
                try:
                    collection = getattr(features, collection_name, None)
                    if collection:
                        for feat in collection:
                            try:
                                order += 1
                                feat_name = getattr(feat, 'Name', f"Feature_{order}")
                                is_suppressed = getattr(feat, 'Suppressed', False)

                                feature_types[feat_type] += 1
                                feature_sequence.append(feat_type)
                                feature_names.append(feat_name)

                                features_detailed.append(FeatureData(
                                    name=feat_name,
                                    type=feat_type,
                                    original_type=collection_name,
                                    order=order,
                                    is_suppressed=is_suppressed
                                ))
                            except Exception:
                                continue
                except Exception:
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
            comp_def = doc.ComponentDefinition
            if not comp_def:
                return

            sketches = getattr(comp_def, 'Sketches', None)
            if not sketches:
                return

            sketches_data = []
            total_geom = 0
            total_constr = 0
            geom_types = Counter()
            constr_types = Counter()

            for sketch in sketches:
                try:
                    sk_data = self._extract_single_sketch(sketch)
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
        """Estrae dati da un singolo sketch."""
        name = getattr(sketch, 'Name', 'Sketch')
        sk_data = SketchData(name=name)

        geom_types = Counter()
        geom_detailed = []
        constr_types = Counter()
        constr_detailed = []

        try:
            # === GEOMETRIE ===
            geometry_collections = [
                ('SketchLines', 'Line'),
                ('SketchArcs', 'Arc'),
                ('SketchCircles', 'Circle'),
                ('SketchEllipses', 'Ellipse'),
                ('SketchSplines', 'Spline'),
                ('SketchPoints', 'Point'),
            ]

            idx = 0
            for collection_name, geom_type in geometry_collections:
                try:
                    collection = getattr(sketch, collection_name, None)
                    if collection:
                        count = collection.Count
                        geom_types[geom_type] = count
                        for i in range(count):
                            idx += 1
                            geom_detailed.append(GeometryData(
                                id=f"{geom_type}_{idx}",
                                type=geom_type,
                                original_type=collection_name
                            ))
                except Exception:
                    continue

            # === VINCOLI ===
            # Geometric Constraints
            try:
                geo_constraints = sketch.GeometricConstraints
                if geo_constraints:
                    for i in range(1, geo_constraints.Count + 1):
                        try:
                            constr = geo_constraints.Item(i)
                            constr_type_name = type(constr).__name__

                            if constr_type_name in CONSTRAINT_TYPE_MAP:
                                type_name, category, description = CONSTRAINT_TYPE_MAP[constr_type_name]
                            else:
                                type_name = constr_type_name.replace("Constraint", "")
                                category = "geometrico"
                                description = ""

                            constr_types[type_name] += 1
                            constr_detailed.append(ConstraintData(
                                id=f"Geo_{i}",
                                type=type_name,
                                original_type=constr_type_name,
                                category=category,
                                description=description
                            ))
                        except Exception:
                            continue
            except Exception:
                pass

            # Dimensional Constraints
            try:
                dim_constraints = sketch.DimensionConstraints
                if dim_constraints:
                    for i in range(1, dim_constraints.Count + 1):
                        try:
                            constr = dim_constraints.Item(i)
                            constr_type_name = type(constr).__name__

                            if constr_type_name in CONSTRAINT_TYPE_MAP:
                                type_name, category, description = CONSTRAINT_TYPE_MAP[constr_type_name]
                            else:
                                type_name = constr_type_name.replace("DimConstraint", "")
                                category = "dimensionale"
                                description = ""

                            # Valore
                            value = None
                            try:
                                value = float(constr.ModelValue) * 10  # Converti in mm
                            except Exception:
                                pass

                            constr_types[type_name] += 1
                            constr_detailed.append(ConstraintData(
                                id=f"Dim_{i}",
                                type=type_name,
                                original_type=constr_type_name,
                                category=category,
                                description=description,
                                value=value
                            ))
                        except Exception:
                            continue
            except Exception:
                pass

        except Exception as e:
            self._add_warning(f"Errore sketch {name}: {e}")

        sk_data.geometry_count = sum(geom_types.values())
        sk_data.geometry_types = dict(geom_types)
        sk_data.geometry_detailed = geom_detailed
        sk_data.constraint_count = sum(constr_types.values())
        sk_data.constraint_types = dict(constr_types)
        sk_data.constraint_detailed = constr_detailed

        return sk_data

