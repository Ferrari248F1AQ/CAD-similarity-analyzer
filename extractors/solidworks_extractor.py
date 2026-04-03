"""
SolidWorks Extractor - Estrattore per file SolidWorks via COM.

Supporta:
- .sldprt (Part)
- .sldasm (Assembly)
- .slddrw (Drawing) - solo metadati

Richiede SolidWorks installato con API attive.
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
# MAPPING TIPI FEATURE SOLIDWORKS
# ============================================================================

# SolidWorks Feature Type Names (stringa, non numerico)
FEATURE_TYPE_MAP = {
    # Protrusions/Extrudes
    "Extrusion": "Protrusion",
    "Boss-Extrude": "Protrusion",
    "Extrude": "Protrusion",
    "BaseExtrude": "Protrusion",
    "BossExtrude": "Protrusion",

    # Cutouts
    "Cut-Extrude": "Cutout",
    "CutExtrude": "Cutout",
    "Extrude-Cut": "Cutout",
    "ExtrudeCut": "Cutout",

    # Revolution
    "Revolve": "Revolution",
    "Boss-Revolve": "Revolution",
    "Cut-Revolve": "RevolvedCutout",
    "RevolveCut": "RevolvedCutout",

    # Sweep
    "Sweep": "Sweep",
    "Boss-Sweep": "Sweep",
    "Cut-Sweep": "SweptCutout",

    # Loft
    "Loft": "Loft",
    "Boss-Loft": "Loft",
    "Cut-Loft": "LoftedCutout",

    # Holes
    "Hole": "Hole",
    "HoleWzd": "Hole",
    "HoleWizard": "Hole",
    "SimpleHole": "Hole",

    # Fillets & Chamfers
    "Fillet": "Round",
    "FilletFeature": "Round",
    "Chamfer": "Chamfer",
    "ChamferFeature": "Chamfer",

    # Patterns
    "LPattern": "RectangularPattern",
    "LinearPattern": "RectangularPattern",
    "CirPattern": "CircularPattern",
    "CircularPattern": "CircularPattern",
    "Mirror": "Mirror",
    "MirrorPattern": "Mirror",

    # Shell & Draft
    "Shell": "Shell",
    "Draft": "Draft",

    # Reference
    "RefPlane": "RefPlane",
    "ReferencePlane": "RefPlane",
    "RefAxis": "RefAxis",
    "ReferenceAxis": "RefAxis",

    # Sketch
    "ProfileFeature": "Sketch",
    "3DSketch": "Sketch3D",
}

# Mapping vincoli SolidWorks
CONSTRAINT_TYPE_MAP = {
    # Geometrici
    "swConstraintType_COINCIDENT": ("Coincident", "geometrico", "Coincidenza"),
    "swConstraintType_CONCENTRIC": ("Concentric", "geometrico", "Concentricità"),
    "swConstraintType_COLINEAR": ("Collinear", "geometrico", "Collinearità"),
    "swConstraintType_PARALLEL": ("Parallel", "geometrico", "Parallelismo"),
    "swConstraintType_PERPENDICULAR": ("Perpendicular", "geometrico", "Perpendicolarità"),
    "swConstraintType_HORIZONTAL": ("Horizontal", "geometrico", "Orizzontale"),
    "swConstraintType_VERTICAL": ("Vertical", "geometrico", "Verticale"),
    "swConstraintType_TANGENT": ("Tangent", "geometrico", "Tangenza"),
    "swConstraintType_MIDPOINT": ("MidPoint", "geometrico", "Punto medio"),
    "swConstraintType_SYMMETRIC": ("Symmetric", "geometrico", "Simmetria"),
    "swConstraintType_EQUAL": ("Equal", "geometrico", "Uguaglianza"),
    "swConstraintType_FIX": ("Ground", "geometrico", "Fisso"),

    # Dimensionali (numeri)
    0: ("Distance", "dimensionale", "Distanza"),
    1: ("Angle", "dimensionale", "Angolo"),
    2: ("Radius", "dimensionale", "Raggio"),
    3: ("Diameter", "dimensionale", "Diametro"),
    4: ("Length", "dimensionale", "Lunghezza"),
}


class SolidWorksExtractor(BaseCADExtractor, COMExtractorMixin):
    """Estrattore per file SolidWorks."""

    CAD_NAME = "SolidWorks"
    SUPPORTED_EXTENSIONS = [".sldprt", ".sldasm", ".slddrw"]
    VERSION = "1.0.0"
    COM_PROG_ID = "SldWorks.Application"

    # Costanti SolidWorks
    SW_DOC_PART = 1
    SW_DOC_ASSEMBLY = 2
    SW_DOC_DRAWING = 3

    def __init__(self):
        super().__init__()
        self._created_new_app = False

    def _connect(self) -> bool:
        """Connette a SolidWorks."""
        try:
            self._app, self._created_new_app = self._get_or_create_app()

            if self._app:
                # Rendi visibile se appena creato
                if self._created_new_app:
                    try:
                        self._app.Visible = False  # Nascondi per velocità
                    except Exception:
                        pass
                self._connected = True
                return True
            return False

        except ExtractionError:
            raise
        except Exception as e:
            self._add_warning(f"Errore connessione SolidWorks: {e}")
            return False

    def _disconnect(self):
        """Disconnette da SolidWorks."""
        if self._created_new_app and self._app:
            try:
                self._app.ExitApp()
            except Exception:
                pass
        self._app = None
        self._connected = False

    def _extract_from_document(self, filepath: Path) -> CADModelSignature:
        """Estrae signature da un documento SolidWorks."""
        doc = None
        was_already_open = False

        try:
            # Determina tipo documento
            ext = filepath.suffix.lower()
            if ext == ".sldprt":
                doc_type = self.SW_DOC_PART
            elif ext == ".sldasm":
                doc_type = self.SW_DOC_ASSEMBLY
            else:
                doc_type = self.SW_DOC_DRAWING

            # Verifica se già aperto
            try:
                doc = self._app.GetOpenDocument(str(filepath))
                if doc:
                    was_already_open = True
            except Exception:
                pass

            # Apri documento
            if not doc:
                errors = 0
                warnings = 0
                doc = self._app.OpenDoc6(
                    str(filepath),
                    doc_type,
                    1,  # swOpenDocOptions_Silent
                    "",
                    errors,
                    warnings
                )

                if not doc:
                    raise ExtractionError(
                        f"Impossibile aprire il file. Errori: {errors}",
                        self.CAD_NAME,
                        str(filepath)
                    )

            # Estrai dati
            signature = self._extract_document_data(doc, filepath)

            return signature

        finally:
            if doc and not was_already_open:
                try:
                    self._app.CloseDoc(str(filepath))
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
            # Custom Properties
            try:
                custom_info = doc.CustomInfo2("", "Author")
                if custom_info:
                    sig.author = custom_info
            except Exception:
                pass

            try:
                custom_info = doc.CustomInfo2("", "Company")
                if custom_info:
                    sig.company = custom_info
            except Exception:
                pass

            try:
                custom_info = doc.CustomInfo2("", "Title")
                if custom_info:
                    sig.title = custom_info
            except Exception:
                pass

            # Summary Info
            try:
                ext = doc.Extension
                if ext:
                    summary = ext.get_CustomPropertyManager("")
                    if summary:
                        sig.author = summary.Get("Author") or sig.author
                        sig.company = summary.Get("Company") or sig.company
            except Exception:
                pass

        except Exception as e:
            self._add_warning(f"Errore estrazione metadati: {e}")

    def _extract_features(self, doc, sig: CADModelSignature):
        """Estrae le feature 3D."""
        try:
            feat_mgr = doc.FeatureManager
            if not feat_mgr:
                return

            # Ottieni feature tree
            features = feat_mgr.GetFeatures(True)  # True = solo visibili
            if not features:
                return

            feature_types = Counter()
            feature_sequence = []
            feature_names = []
            features_detailed = []
            order = 0

            for feat in features:
                try:
                    if not feat:
                        continue

                    # Tipo feature (stringa in SolidWorks)
                    feat_type_name = feat.GetTypeName2()
                    if not feat_type_name:
                        continue

                    # Salta feature di sistema
                    if feat_type_name in ["OriginProfileFeature", "MaterialFolder",
                                          "RefPlane", "MateGroup", "HistoryFolder"]:
                        continue

                    # Mappa il tipo
                    normalized_type = FEATURE_TYPE_MAP.get(feat_type_name, feat_type_name)

                    # Nome feature
                    feat_name = feat.Name or f"Feature_{order}"

                    # Verifica se soppressa
                    is_suppressed = feat.IsSuppressed2(1, [])[0] if hasattr(feat, 'IsSuppressed2') else False

                    order += 1
                    feature_types[normalized_type] += 1
                    feature_sequence.append(normalized_type)
                    feature_names.append(feat_name)

                    features_detailed.append(FeatureData(
                        name=feat_name,
                        type=normalized_type,
                        original_type=feat_type_name,
                        order=order,
                        is_suppressed=is_suppressed
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
            feat_mgr = doc.FeatureManager
            if not feat_mgr:
                return

            features = feat_mgr.GetFeatures(True)
            if not features:
                return

            sketches_data = []
            total_geom = 0
            total_constr = 0
            geom_types = Counter()
            constr_types = Counter()

            for feat in features:
                try:
                    if not feat:
                        continue

                    # Cerca solo feature di tipo Sketch
                    feat_type = feat.GetTypeName2()
                    if feat_type not in ["ProfileFeature", "3DSketch", "Sketch"]:
                        continue

                    # Ottieni sketch
                    sketch = feat.GetSpecificFeature2()
                    if not sketch:
                        continue

                    sk_data = self._extract_single_sketch(sketch, feat.Name)
                    sketches_data.append(sk_data)

                    # Accumula totali
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

    def _extract_single_sketch(self, sketch, name: str) -> SketchData:
        """Estrae dati da un singolo sketch."""
        sk_data = SketchData(name=name or "Sketch")

        geom_types = Counter()
        geom_detailed = []
        constr_types = Counter()
        constr_detailed = []

        try:
            # === GEOMETRIE ===
            # Linee
            try:
                lines = sketch.GetLines()
                if lines:
                    count = len(lines) // 7  # Ogni linea = 7 double
                    geom_types["Line"] = count
                    for i in range(count):
                        geom_detailed.append(GeometryData(
                            id=f"Line_{i}",
                            type="Line"
                        ))
            except Exception:
                pass

            # Archi e cerchi
            try:
                arcs = sketch.GetArcs()
                if arcs:
                    count = len(arcs) // 8  # Ogni arco = 8 double
                    # Distingui cerchi completi da archi
                    geom_types["Arc"] = count
                    for i in range(count):
                        geom_detailed.append(GeometryData(
                            id=f"Arc_{i}",
                            type="Arc"
                        ))
            except Exception:
                pass

            # Spline
            try:
                splines = sketch.GetSplines()
                if splines:
                    count = len(splines) if isinstance(splines, list) else 1
                    geom_types["Spline"] = count
                    for i in range(count):
                        geom_detailed.append(GeometryData(
                            id=f"Spline_{i}",
                            type="Spline"
                        ))
            except Exception:
                pass

            # === VINCOLI (Constraints/Relations) ===
            try:
                relations = sketch.GetRelationManager()
                if relations:
                    rel_count = relations.GetRelationsCount(0)  # 0 = all
                    for i in range(rel_count):
                        try:
                            rel = relations.GetRelation(i)
                            if rel:
                                rel_type = rel.GetRelationType()
                                # Mappa tipo
                                type_name = f"Constraint_{rel_type}"
                                category = "geometrico"
                                description = ""

                                constr_types[type_name] += 1
                                constr_detailed.append(ConstraintData(
                                    id=f"Rel_{i}",
                                    type=type_name,
                                    original_type=str(rel_type),
                                    category=category,
                                    description=description
                                ))
                        except Exception:
                            continue
            except Exception:
                pass

            # Prova anche Sketch.GetConstraintsCount
            try:
                constr_count = sketch.GetConstraintsCount()
                if constr_count > 0 and not constr_types:
                    constr_types["Unknown"] = constr_count
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

