"""
CATIA V5 Extractor - Estrattore per file CATIA via COM.

Supporta:
- .CATPart (Part)
- .CATProduct (Assembly)
- .CATDrawing (Drawing) - solo metadati

Richiede CATIA V5 installato con licenza appropriata.

NOTA: CATIA ha un'API COM più complessa. Questo estrattore
fornisce una prima integrazione funzionante, ma potrebbe
non estrarre tutti i dettagli a causa di limitazioni API/licensing.
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
# MAPPING TIPI FEATURE CATIA
# ============================================================================

FEATURE_TYPE_MAP = {
    # Solids
    "Pad": "Protrusion",
    "Pocket": "Cutout",
    "Shaft": "Revolution",
    "Groove": "RevolvedCutout",
    "Rib": "Sweep",
    "Slot": "SweptCutout",
    "Loft": "Loft",
    "Remove Loft": "LoftedCutout",
    "Multi-sections Solid": "Loft",

    # Holes
    "Hole": "Hole",
    "HolePattern": "Hole",

    # Dress-up
    "EdgeFillet": "Round",
    "Chamfer": "Chamfer",
    "Draft": "Draft",
    "Shell": "Shell",
    "Thickness": "Shell",

    # Patterns
    "RectPattern": "RectangularPattern",
    "CircPattern": "CircularPattern",
    "UserPattern": "Pattern",
    "Mirror": "Mirror",
    "Symmetry": "Mirror",

    # Reference
    "Plane": "RefPlane",
    "Line": "RefAxis",
    "Point": "RefPoint",

    # Sketch
    "Sketch": "Sketch",
    "OpenBodySketch": "Sketch",
}

# Mapping vincoli CATIA
CONSTRAINT_TYPE_MAP = {
    "CATCstTypeReference": ("Ground", "geometrico", "Elemento fisso"),
    "CATCstTypeDistance": ("Distance", "dimensionale", "Distanza"),
    "CATCstTypeLength": ("Length", "dimensionale", "Lunghezza"),
    "CATCstTypeAngle": ("Angle", "dimensionale", "Angolo"),
    "CATCstTypeRadius": ("Radius", "dimensionale", "Raggio"),
    "CATCstTypeDiameter": ("Diameter", "dimensionale", "Diametro"),
    "CATCstTypeOn": ("Coincident", "geometrico", "Coincidenza"),
    "CATCstTypeTangent": ("Tangent", "geometrico", "Tangenza"),
    "CATCstTypePerpendicular": ("Perpendicular", "geometrico", "Perpendicolarità"),
    "CATCstTypeParallel": ("Parallel", "geometrico", "Parallelismo"),
    "CATCstTypeConcentric": ("Concentric", "geometrico", "Concentricità"),
    "CATCstTypeHorizontal": ("Horizontal", "geometrico", "Orizzontale"),
    "CATCstTypeVertical": ("Vertical", "geometrico", "Verticale"),
    "CATCstTypeCoincidence": ("Coincident", "geometrico", "Coincidenza"),
    "CATCstTypeFix": ("Ground", "geometrico", "Fisso"),
    "CATCstTypeSymmetry": ("Symmetric", "geometrico", "Simmetria"),
    "CATCstTypeMidPoint": ("MidPoint", "geometrico", "Punto medio"),
    "CATCstTypeEquidistant": ("Equal", "geometrico", "Equidistanza"),
}


class CATIAExtractor(BaseCADExtractor, COMExtractorMixin):
    """
    Estrattore per file CATIA V5.

    NOTA: CATIA richiede una licenza valida e potrebbe non esporre
    tutte le funzionalità via API COM. L'estrattore cerca di ottenere
    il massimo possibile, segnalando eventuali limitazioni nei warnings.
    """

    CAD_NAME = "CATIA"
    SUPPORTED_EXTENSIONS = [".catpart", ".catproduct", ".catdrawing"]
    VERSION = "1.0.0"
    COM_PROG_ID = "CATIA.Application"

    def __init__(self):
        super().__init__()
        self._created_new_app = False

    def _connect(self) -> bool:
        """Connette a CATIA."""
        try:
            self._app, self._created_new_app = self._get_or_create_app()

            if self._app:
                if self._created_new_app:
                    try:
                        self._app.Visible = False
                        self._app.DisplayFileAlerts = False
                    except Exception:
                        pass
                self._connected = True
                return True
            return False

        except ExtractionError:
            raise
        except Exception as e:
            self._add_warning(f"Errore connessione CATIA: {e}")
            return False

    def _disconnect(self):
        """Disconnette da CATIA."""
        if self._created_new_app and self._app:
            try:
                self._app.Quit()
            except Exception:
                pass
        self._app = None
        self._connected = False

    def _extract_from_document(self, filepath: Path) -> CADModelSignature:
        """Estrae signature da un documento CATIA."""
        doc = None
        was_already_open = False

        try:
            # Verifica se già aperto
            try:
                docs = self._app.Documents
                for i in range(1, docs.Count + 1):
                    d = docs.Item(i)
                    try:
                        if Path(d.FullName).resolve() == filepath.resolve():
                            doc = d
                            was_already_open = True
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            # Apri documento
            if not doc:
                try:
                    doc = self._app.Documents.Open(str(filepath))
                except Exception as e:
                    raise ExtractionError(
                        f"Impossibile aprire il file: {e}",
                        self.CAD_NAME,
                        str(filepath)
                    )

                if not doc:
                    raise ExtractionError(
                        "Documento non valido",
                        self.CAD_NAME,
                        str(filepath)
                    )

            # Estrai dati
            signature = self._extract_document_data(doc, filepath)

            return signature

        finally:
            if doc and not was_already_open:
                try:
                    doc.Close()
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

        # Segna estrazione parziale se CATIA non fornisce tutti i dati
        if sig.feature_count == 0 and sig.sketches_count == 0:
            sig.extraction_complete = False
            sig.extraction_warnings.append(
                "CATIA potrebbe non aver esposto tutti i dati via API. "
                "Verifica licenza e impostazioni."
            )

        return sig

    def _extract_metadata(self, doc, sig: CADModelSignature):
        """Estrae metadati del documento."""
        try:
            # Product per metadati
            try:
                product = doc.Product
                if product:
                    sig.title = getattr(product, 'PartNumber', '')
                    sig.comments = getattr(product, 'Nomenclature', '')
                    sig.author = getattr(product, 'DescriptionRef', '')
            except Exception:
                pass

            # Prova Properties
            try:
                # UserRefProperties
                props = doc.Product.UserRefProperties
                for i in range(1, props.Count + 1):
                    try:
                        prop = props.Item(i)
                        name = prop.Name
                        value = str(prop.Value)

                        if 'author' in name.lower():
                            sig.author = value
                        elif 'company' in name.lower():
                            sig.company = value
                    except Exception:
                        continue
            except Exception:
                pass

            # Versione
            try:
                sig.cad_version = f"CATIA V{self._app.SystemConfiguration.Version}"
            except Exception:
                pass

        except Exception as e:
            self._add_warning(f"Errore estrazione metadati: {e}")

    def _extract_features(self, doc, sig: CADModelSignature):
        """Estrae le feature 3D."""
        try:
            # Verifica se è un Part
            try:
                part = doc.Part
            except Exception:
                self._add_warning("Documento non è un CATPart, features 3D non disponibili")
                return

            if not part:
                return

            # Bodies contiene le feature
            bodies = None
            try:
                bodies = part.Bodies
            except Exception:
                pass

            feature_types = Counter()
            feature_sequence = []
            feature_names = []
            features_detailed = []
            order = 0

            def normalize_shape(shape):
                shape_type = type(shape).__name__
                if shape_type == 'CDispatch':
                    name = getattr(shape, 'Name', '') or ''
                    lname = name.lower()
                    mapped = None
                    for kw, mapped_type in NAME_KEYWORDS_MAP:
                        if kw in lname:
                            mapped = mapped_type
                            break
                    if mapped:
                        return mapped, name, 'CDispatch'
                    return 'Unknown', name or f'Feature_{order + 1}', 'CDispatch'
                return FEATURE_TYPE_MAP.get(shape_type, shape_type), getattr(shape, 'Name', f"Feature_{order + 1}"), shape_type

            if bodies:
                for b_idx in range(1, bodies.Count + 1):
                    try:
                        body = bodies.Item(b_idx)
                        shapes = body.Shapes

                        for s_idx in range(1, shapes.Count + 1):
                            try:
                                shape = shapes.Item(s_idx)

                                normalized_type, feat_name, original_type = normalize_shape(shape)

                                order += 1
                                feature_types[normalized_type] += 1
                                feature_sequence.append(normalized_type)
                                feature_names.append(feat_name)

                                features_detailed.append(FeatureData(
                                    name=feat_name,
                                    type=normalized_type,
                                    original_type=original_type,
                                    order=order
                                ))

                            except Exception:
                                continue
                    except Exception:
                        continue

            # Prova anche ShapeFactory per altri tipi
            try:
                factory = part.ShapeFactory
                if factory:
                    # Itera su collezioni specifiche
                    collections = [
                        ('Holes', 'Hole'),
                        ('Fillets', 'Round'),
                        ('Chamfers', 'Chamfer'),
                        ('Drafts', 'Draft'),
                    ]

                    for coll_name, feat_type in collections:
                        try:
                            coll = getattr(factory, coll_name, None)
                            if coll:
                                for i in range(1, coll.Count + 1):
                                    feat = coll.Item(i)
                                    feat_name = getattr(feat, 'Name', f"{feat_type}_{i}")

                                    if feat_name not in feature_names:
                                        order += 1
                                        feature_types[feat_type] += 1
                                        feature_sequence.append(feat_type)
                                        feature_names.append(feat_name)

                                        features_detailed.append(FeatureData(
                                            name=feat_name,
                                            type=feat_type,
                                            original_type=coll_name,
                                            order=order
                                        ))
                        except Exception:
                            continue
            except Exception:
                pass

            # Fallback: se nessuna feature trovata da bodies, prova direttamente part.Shapes
            if len(feature_sequence) == 0:
                try:
                    shapes = getattr(part, 'Shapes', None)
                    if shapes:
                        for s_idx in range(1, shapes.Count + 1):
                            try:
                                shape = shapes.Item(s_idx)
                                normalized_type, feat_name, original_type = normalize_shape(shape)
                                order += 1
                                feature_types[normalized_type] += 1
                                feature_sequence.append(normalized_type)
                                feature_names.append(feat_name)
                                features_detailed.append(FeatureData(
                                    name=feat_name,
                                    type=normalized_type,
                                    original_type=original_type,
                                    order=order
                                ))
                            except Exception:
                                continue
                except Exception:
                    pass

            # Se ancora vuoto, inserisci una feature Unknown per non restituire 0
            if len(feature_sequence) == 0:
                order = 1
                feature_types['Unknown'] = 1
                feature_sequence.append('Unknown')
                feature_names.append('Unknown')
                features_detailed.append(FeatureData(
                    name='Unknown',
                    type='Unknown',
                    original_type='Unknown',
                    order=order
                ))

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
            part = doc.Part
            if not part:
                return

            # Geometrical Sets / Sketches
            sketches = None
            try:
                # In CATIA gli sketch possono essere in diversi posti
                sketches = part.Sketches
            except Exception:
                pass

            if not sketches:
                try:
                    # Prova HybridBodies
                    hybrid_bodies = part.HybridBodies
                    if hybrid_bodies:
                        for hb_idx in range(1, hybrid_bodies.Count + 1):
                            hb = hybrid_bodies.Item(hb_idx)
                            if hasattr(hb, 'Sketches'):
                                sketches = hb.Sketches
                                break
                except Exception:
                    pass

            if not sketches:
                self._add_warning("Nessuno sketch trovato")
                return

            sketches_data = []
            total_geom = 0
            total_constr = 0
            geom_types = Counter()
            constr_types = Counter()

            for i in range(1, sketches.Count + 1):
                try:
                    sketch = sketches.Item(i)
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
            # In CATIA le geometrie sono in Geometry2D
            try:
                geom_2d = sketch.GeometricElements
                if geom_2d:
                    for i in range(1, geom_2d.Count + 1):
                        try:
                            geom = geom_2d.Item(i)
                            geom_type_name = type(geom).__name__

                            # Normalizza tipo
                            if 'Line' in geom_type_name:
                                normalized = 'Line'
                            elif 'Circle' in geom_type_name:
                                normalized = 'Circle'
                            elif 'Arc' in geom_type_name:
                                normalized = 'Arc'
                            elif 'Ellipse' in geom_type_name:
                                normalized = 'Ellipse'
                            elif 'Spline' in geom_type_name:
                                normalized = 'Spline'
                            elif 'Point' in geom_type_name:
                                normalized = 'Point'
                            else:
                                normalized = geom_type_name

                            geom_types[normalized] += 1
                            geom_detailed.append(GeometryData(
                                id=f"Geom_{i}",
                                type=normalized,
                                original_type=geom_type_name
                            ))
                        except Exception:
                            continue
            except Exception:
                pass

            # === VINCOLI ===
            try:
                constraints = sketch.Constraints
                if constraints:
                    for i in range(1, constraints.Count + 1):
                        try:
                            constr = constraints.Item(i)

                            # Tipo vincolo
                            constr_type = getattr(constr, 'Type', None)
                            constr_type_name = str(constr_type) if constr_type else type(constr).__name__

                            if constr_type_name in CONSTRAINT_TYPE_MAP:
                                type_name, category, description = CONSTRAINT_TYPE_MAP[constr_type_name]
                            else:
                                type_name = constr_type_name.replace("CATCstType", "")
                                category = "unknown"
                                description = ""

                            # Valore per vincoli dimensionali
                            value = None
                            if category == "dimensionale":
                                try:
                                    dim = constr.Dimension
                                    if dim:
                                        value = dim.Value
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

