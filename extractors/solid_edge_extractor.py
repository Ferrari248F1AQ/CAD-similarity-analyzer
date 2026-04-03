"""
Solid Edge Extractor - Estrattore per file Solid Edge via COM.

Supporta:
- .par (Part)
- .psm (Sheet Metal Part)
- .asm (Assembly)
"""

from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from collections import Counter
import traceback
import math

from .base_extractor import BaseCADExtractor, COMExtractorMixin, ExtractionError
from .cad_signature import (
    CADModelSignature, SketchData, FeatureData,
    ConstraintData, GeometryData, SketchParametricFrame
)


# ============================================================================
# MAPPING TIPI FEATURE SOLID EDGE
# ============================================================================

FEATURE_TYPE_MAP = {
    # Protrusions
    462094706: "Protrusion",
    462094710: "RevolvedProtrusion",
    462094738: "Loft",
    462094766: "Sweep",

    # Cutouts
    462094714: "Cutout",
    462094718: "RevolvedCutout",
    462094742: "LoftedCutout",
    462094770: "SweptCutout",

    # Holes
    462094722: "Hole",

    # Modifiers
    462094726: "Round",
    462094730: "Chamfer",
    462094734: "Draft",
    462094746: "Rib",
    462094750: "Shell",
    462094754: "Lip",
    462094758: "Thin",
    462094762: "Thread",

    # Patterns
    -416228998: "CircularPattern",
    -127107951: "RectangularPattern",
    462094774: "Mirror",

    # Reference
    462094778: "RefPlane",
    462094782: "RefAxis",

    # Other
    462094786: "Offset",
    462094790: "Split",
    462094794: "Trim",
    462094798: "Extend",
    462094802: "Blend",
}

# Mapping vincoli 2D
CONSTRAINT_TYPE_MAP = {
    # Geometrici
    1166881792: ("Connect", "geometrico", "Connessione tra entità"),
    1166881793: ("Ground", "geometrico", "Entità fissa"),
    1166881794: ("Horizontal", "geometrico", "Linea orizzontale"),
    1166881795: ("Vertical", "geometrico", "Linea verticale"),
    1166881796: ("Parallel", "geometrico", "Linee parallele"),
    1166881797: ("Perpendicular", "geometrico", "Linee perpendicolari"),
    1166881798: ("Concentric", "geometrico", "Cerchi/archi concentrici"),
    1166881799: ("Tangent", "geometrico", "Tangenza"),
    1166881800: ("Equal", "geometrico", "Entità uguali"),
    1166881801: ("Symmetric", "geometrico", "Simmetria"),
    1166881802: ("Collinear", "geometrico", "Linee collineari"),
    1166881803: ("Coplanar", "geometrico", "Entità complanari"),
    1166881824: ("IntersectionPoint", "geometrico", "Punto di intersezione"),
    -1179755088: ("VerticalAlignment", "geometrico", "Allineamento verticale"),
    -401894992: ("HorizontalAlignment", "geometrico", "Allineamento orizzontale"),

    # Dimensionali
    1166881804: ("Distance", "dimensionale", "Distanza"),
    1166881805: ("Angle", "dimensionale", "Angolo"),
    1166881806: ("Radius", "dimensionale", "Raggio"),
    1166881807: ("Diameter", "dimensionale", "Diametro"),
    1166881808: ("Length", "dimensionale", "Lunghezza"),

    # Altri
    1166881810: ("Lock", "altro", "Blocco"),
    1166881811: ("MidPoint", "geometrico", "Punto medio"),
    1166881812: ("RigidSet", "geometrico", "Set rigido"),
    1166881820: ("Offset", "dimensionale", "Offset"),
}

# Mapping geometrie 2D
GEOMETRY_TYPE_MAP = {
    "Line2d": "Line",
    "Arc2d": "Arc",
    "Circle2d": "Circle",
    "Ellipse2d": "Ellipse",
    "BSplineCurve2d": "Spline",
    "Lines2d": "Line",
    "Arcs2d": "Arc",
    "Circles2d": "Circle",
    "Ellipses2d": "Ellipse",
    "BSplineCurves2d": "Spline",
}


# ============================================================================
# FUNZIONI PER FRAME PARAMETRICO (u,v) - Sistema di coordinate normalizzato
# ============================================================================

def compute_geometry_centroid_and_extent(geom: GeometryData) -> Tuple[Tuple[float, float], float, float]:
    """
    Calcola il baricentro geometrico e le estensioni (proiezioni) di una singola geometria.

    Per ogni tipo di geometria:
    - Linea: baricentro = punto medio, extent_x/y = |delta_x|, |delta_y|
    - Cerchio: baricentro = centro, extent_x = extent_y = 2*raggio (diametro)
    - Arco: baricentro = punto medio dell'arco (approssimato), extent = bounding box
    - Ellisse: baricentro = centro, extent = assi maggiore/minore (se disponibili)

    Returns:
        (centroid, extent_x, extent_y) dove extent è la "larghezza" della geometria lungo x e y
    """
    if geom.type == 'Line':
        if geom.start_point and geom.end_point:
            cx = (geom.start_point[0] + geom.end_point[0]) / 2
            cy = (geom.start_point[1] + geom.end_point[1]) / 2
            extent_x = abs(geom.end_point[0] - geom.start_point[0])
            extent_y = abs(geom.end_point[1] - geom.start_point[1])
            return ((cx, cy), extent_x, extent_y)

    elif geom.type == 'Circle':
        if geom.center_point and geom.radius:
            diameter = 2 * geom.radius
            return (geom.center_point, diameter, diameter)
        elif geom.center_point:
            # Raggio non disponibile, stima da altri dati se possibile
            return (geom.center_point, 1.0, 1.0)

    elif geom.type == 'Arc':
        if geom.center_point and geom.radius:
            # Per un arco, il baricentro è approssimato al centro
            # Le estensioni sono il diametro (caso peggiore)
            diameter = 2 * geom.radius
            return (geom.center_point, diameter, diameter)
        elif geom.start_point and geom.end_point:
            # Fallback: usa punti estremi
            cx = (geom.start_point[0] + geom.end_point[0]) / 2
            cy = (geom.start_point[1] + geom.end_point[1]) / 2
            extent_x = abs(geom.end_point[0] - geom.start_point[0])
            extent_y = abs(geom.end_point[1] - geom.start_point[1])
            return ((cx, cy), extent_x, extent_y)

    elif geom.type == 'Ellipse':
        if geom.center_point:
            # Per ellisse, senza info sugli assi, assumiamo simmetria
            return (geom.center_point, 1.0, 1.0)

    elif geom.type == 'Spline':
        if geom.start_point and geom.end_point:
            cx = (geom.start_point[0] + geom.end_point[0]) / 2
            cy = (geom.start_point[1] + geom.end_point[1]) / 2
            extent_x = abs(geom.end_point[0] - geom.start_point[0])
            extent_y = abs(geom.end_point[1] - geom.start_point[1])
            return ((cx, cy), extent_x, extent_y)

    # Fallback: prova a usare qualsiasi punto disponibile
    points = []
    if geom.start_point:
        points.append(geom.start_point)
    if geom.end_point:
        points.append(geom.end_point)
    if geom.center_point:
        points.append(geom.center_point)

    if points:
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        if len(points) > 1:
            extent_x = max(p[0] for p in points) - min(p[0] for p in points)
            extent_y = max(p[1] for p in points) - min(p[1] for p in points)
        else:
            extent_x = extent_y = 0.0
        return ((cx, cy), extent_x, extent_y)

    return ((0.0, 0.0), 0.0, 0.0)


def compute_sketch_weighted_centroid(geometries: List[GeometryData]) -> Tuple[float, float]:
    """
    Calcola il baricentro geometrico pesato dello sketch.

    Ogni geometria contribuisce al baricentro in base alla sua "importanza"
    (lunghezza per linee, perimetro per cerchi, etc.).
    Per semplicità, pesiamo ogni geometria ugualmente.
    """
    if not geometries:
        return (0.0, 0.0)

    total_cx = 0.0
    total_cy = 0.0
    valid_count = 0

    for geom in geometries:
        centroid, _, _ = compute_geometry_centroid_and_extent(geom)
        if centroid != (0.0, 0.0) or (geom.start_point or geom.end_point or geom.center_point):
            total_cx += centroid[0]
            total_cy += centroid[1]
            valid_count += 1

    if valid_count == 0:
        return (0.0, 0.0)

    return (total_cx / valid_count, total_cy / valid_count)


def compute_principal_axes(geometries: List[GeometryData], centroid: Tuple[float, float]) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """
    Calcola gli assi principali dello sketch usando PCA sui punti caratteristici.

    Returns:
        (axis_u, axis_v) - due vettori unitari ortogonali
    """
    # Raccogli tutti i punti caratteristici
    points = []
    for g in geometries:
        if g.start_point:
            points.append(g.start_point)
        if g.end_point:
            points.append(g.end_point)
        if g.center_point:
            points.append(g.center_point)

    if len(points) < 2:
        return ((1.0, 0.0), (0.0, 1.0))  # Assi default

    # Centra i punti
    cx, cy = centroid
    centered = [(p[0] - cx, p[1] - cy) for p in points]

    # Calcola matrice di covarianza 2x2
    n = len(centered)
    cov_xx = sum(p[0] * p[0] for p in centered) / n
    cov_yy = sum(p[1] * p[1] for p in centered) / n
    cov_xy = sum(p[0] * p[1] for p in centered) / n

    # Autovalori e autovettori
    trace = cov_xx + cov_yy
    det = cov_xx * cov_yy - cov_xy * cov_xy
    discriminant = max(0, trace * trace / 4 - det)

    sqrt_disc = math.sqrt(discriminant)
    lambda1 = trace / 2 + sqrt_disc

    # Autovettore per lambda1 (asse principale)
    if abs(cov_xy) > 1e-10:
        v1_x = lambda1 - cov_yy
        v1_y = cov_xy
    elif cov_xx >= cov_yy:
        v1_x, v1_y = 1.0, 0.0
    else:
        v1_x, v1_y = 0.0, 1.0

    # Normalizza
    norm1 = math.sqrt(v1_x * v1_x + v1_y * v1_y)
    if norm1 > 1e-10:
        v1_x /= norm1
        v1_y /= norm1
    else:
        v1_x, v1_y = 1.0, 0.0

    # v2 ortogonale a v1
    v2_x = -v1_y
    v2_y = v1_x

    return ((v1_x, v1_y), (v2_x, v2_y))


def compute_projection_weights(geometries: List[GeometryData], centroid: Tuple[float, float],
                                axis_u: Tuple[float, float], axis_v: Tuple[float, float]) -> Tuple[float, float]:
    """
    Calcola i pesi u e v come somma delle proiezioni normalizzate delle geometrie.

    Per ogni geometria, calcola quanto "si estende" lungo u e lungo v, poi somma
    tutte le estensioni e normalizza il vettore risultante.

    Esempio: cerchio raggio 1 centrato sull'origine
    - Proiezione su u = 2 (diametro)
    - Proiezione su v = 2 (diametro)
    - Normalizzato: (2, 2) / ||(2, 2)|| = (2/sqrt(8), 2/sqrt(8)) = (sqrt(2)/2, sqrt(2)/2)

    Returns:
        (weight_u, weight_v) - pesi normalizzati (norma = 1)
    """
    total_proj_u = 0.0
    total_proj_v = 0.0

    for geom in geometries:
        geom_centroid, extent_x, extent_y = compute_geometry_centroid_and_extent(geom)

        if extent_x == 0.0 and extent_y == 0.0:
            continue

        # Calcola la proiezione delle estensioni sugli assi u e v
        # L'estensione lungo u è quanto la geometria "si espande" nella direzione u
        # Per una geometria con extent (dx, dy), la proiezione su u è |dx * u_x + dy * u_y|
        # Ma questo non è corretto per estensioni...

        # Approccio corretto: per ogni geometria, calcola il bounding box proiettato su u e v
        # Per semplicità, usiamo le estensioni x e y e le proiettiamo

        # La proiezione dell'extent lungo un asse dipende dall'orientamento della geometria
        # Per una linea: la proiezione su u è |lunghezza * cos(angolo con u)|
        # Per un cerchio: la proiezione su u = diametro (sempre uguale)

        if geom.type == 'Circle':
            # Cerchio: proiezione uguale in tutte le direzioni = diametro
            total_proj_u += extent_x  # = diametro
            total_proj_v += extent_y  # = diametro

        elif geom.type == 'Line' and geom.start_point and geom.end_point:
            # Linea: proiezione = |vettore linea · asse|
            dx = geom.end_point[0] - geom.start_point[0]
            dy = geom.end_point[1] - geom.start_point[1]

            proj_u = abs(dx * axis_u[0] + dy * axis_u[1])
            proj_v = abs(dx * axis_v[0] + dy * axis_v[1])

            total_proj_u += proj_u
            total_proj_v += proj_v

        elif geom.type == 'Arc' and geom.radius:
            # Arco: approssima come cerchio
            diameter = 2 * geom.radius
            total_proj_u += diameter
            total_proj_v += diameter

        else:
            # Fallback: usa extent_x e extent_y direttamente
            # Proietta il bounding box sugli assi
            # extent_x contribuisce a u proporzionalmente a |u_x|
            # extent_y contribuisce a u proporzionalmente a |u_y|
            proj_u = extent_x * abs(axis_u[0]) + extent_y * abs(axis_u[1])
            proj_v = extent_x * abs(axis_v[0]) + extent_y * abs(axis_v[1])

            total_proj_u += proj_u
            total_proj_v += proj_v

    # Normalizza il vettore (weight_u, weight_v)
    norm = math.sqrt(total_proj_u * total_proj_u + total_proj_v * total_proj_v)

    if norm > 1e-10:
        weight_u = total_proj_u / norm
        weight_v = total_proj_v / norm
    else:
        weight_u = math.sqrt(2) / 2
        weight_v = math.sqrt(2) / 2

    return (weight_u, weight_v)


def compute_sketch_parametric_frame(geometries: List[GeometryData]) -> SketchParametricFrame:
    """
    Calcola il frame parametrico (u, v) per uno sketch.

    1. Calcola il baricentro geometrico dello sketch (media pesata dei baricentri delle geometrie)
    2. Calcola gli assi principali u e v usando PCA
    3. Calcola i pesi di u e v come proiezioni normalizzate delle geometrie

    Returns:
        SketchParametricFrame con centroid, axis_u, axis_v, extent_u (=weight_u), extent_v (=weight_v)
    """
    if not geometries:
        return SketchParametricFrame(
            centroid=(0.0, 0.0),
            axis_u=(1.0, 0.0),
            axis_v=(0.0, 1.0),
            extent_u=math.sqrt(2) / 2,
            extent_v=math.sqrt(2) / 2,
            num_points=0,
            is_valid=False
        )

    # 1. Calcola baricentro geometrico
    centroid = compute_sketch_weighted_centroid(geometries)

    # 2. Calcola assi principali
    axis_u, axis_v = compute_principal_axes(geometries, centroid)

    # 3. Calcola pesi normalizzati
    weight_u, weight_v = compute_projection_weights(geometries, centroid, axis_u, axis_v)

    # Conta punti per validazione
    num_points = sum(1 for g in geometries if g.start_point or g.end_point or g.center_point)

    return SketchParametricFrame(
        centroid=centroid,
        axis_u=axis_u,
        axis_v=axis_v,
        extent_u=weight_u,  # Ora è il peso normalizzato, non l'estensione
        extent_v=weight_v,
        num_points=num_points,
        is_valid=num_points >= 1
    )


# Funzioni legacy per compatibilità
def collect_characteristic_points(geometries: List[GeometryData]) -> List[Tuple[float, float]]:
    """Raccoglie tutti i punti caratteristici delle geometrie."""
    points = []
    for g in geometries:
        if g.start_point:
            points.append(g.start_point)
        if g.end_point:
            points.append(g.end_point)
        if g.center_point:
            points.append(g.center_point)
    return points


def compute_centroid(points: List[Tuple[float, float]]) -> Tuple[float, float]:
    """Calcola il baricentro (media) dei punti."""
    if not points:
        return (0.0, 0.0)
    n = len(points)
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n)


def compute_pca_2d(points: List[Tuple[float, float]], centroid: Tuple[float, float]) -> Tuple[Tuple[float, float], Tuple[float, float], float, float]:
    """Legacy: Calcola PCA 2D."""
    if len(points) < 2:
        return ((1.0, 0.0), (0.0, 1.0), 1.0, 1.0)

    cx, cy = centroid
    centered = [(p[0] - cx, p[1] - cy) for p in points]

    n = len(centered)
    cov_xx = sum(p[0] * p[0] for p in centered) / n
    cov_yy = sum(p[1] * p[1] for p in centered) / n
    cov_xy = sum(p[0] * p[1] for p in centered) / n

    trace = cov_xx + cov_yy
    det = cov_xx * cov_yy - cov_xy * cov_xy
    discriminant = max(0, trace * trace / 4 - det)

    sqrt_disc = math.sqrt(discriminant)
    lambda1 = trace / 2 + sqrt_disc
    lambda2 = trace / 2 - sqrt_disc

    if abs(cov_xy) > 1e-10:
        v1_x = lambda1 - cov_yy
        v1_y = cov_xy
    elif cov_xx >= cov_yy:
        v1_x, v1_y = 1.0, 0.0
    else:
        v1_x, v1_y = 0.0, 1.0

    norm1 = math.sqrt(v1_x * v1_x + v1_y * v1_y)
    if norm1 > 1e-10:
        v1_x /= norm1
        v1_y /= norm1

    v2_x, v2_y = -v1_y, v1_x

    extent_u = math.sqrt(max(lambda1, 0)) if lambda1 > 0 else 1.0
    extent_v = math.sqrt(max(lambda2, 0)) if lambda2 > 0 else 1.0

    return ((v1_x, v1_y), (v2_x, v2_y), max(extent_u, 1e-10), max(extent_v, 1e-10))


def transform_to_uv(point: Tuple[float, float], centroid: Tuple[float, float],
                    axis_u: Tuple[float, float], axis_v: Tuple[float, float],
                    extent_u: float, extent_v: float) -> Tuple[float, float]:
    """
    Trasforma un punto dal sistema di coordinate originale a coordinate (u, v) normalizzate.
    """
    # Centra rispetto al baricentro
    dx = point[0] - centroid[0]
    dy = point[1] - centroid[1]

    # Proietta sugli assi u e v
    proj_u = dx * axis_u[0] + dy * axis_u[1]
    proj_v = dx * axis_v[0] + dy * axis_v[1]

    # Normalizza per estensione
    u = proj_u / extent_u
    v = proj_v / extent_v

    return (u, v)


def apply_uv_transform_to_geometries(geometries: List[GeometryData], frame: SketchParametricFrame):
    """
    Applica la trasformazione (u, v) a tutte le geometrie in-place.
    """
    if not frame.is_valid:
        return

    for g in geometries:
        if g.start_point:
            g.start_point_uv = transform_to_uv(
                g.start_point, frame.centroid,
                frame.axis_u, frame.axis_v,
                frame.extent_u, frame.extent_v
            )
        if g.end_point:
            g.end_point_uv = transform_to_uv(
                g.end_point, frame.centroid,
                frame.axis_u, frame.axis_v,
                frame.extent_u, frame.extent_v
            )
        if g.center_point:
            g.center_point_uv = transform_to_uv(
                g.center_point, frame.centroid,
                frame.axis_u, frame.axis_v,
                frame.extent_u, frame.extent_v
            )


class SolidEdgeExtractor(BaseCADExtractor, COMExtractorMixin):
    """Estrattore per file Solid Edge."""

    CAD_NAME = "SolidEdge"
    SUPPORTED_EXTENSIONS = [".par", ".psm", ".asm"]
    VERSION = "2.0.0"
    COM_PROG_ID = "SolidEdge.Application"

    def __init__(self):
        super().__init__()
        self._created_new_app = False

    def _connect(self) -> bool:
        """Connette a Solid Edge."""
        try:
            self._app, self._created_new_app = self._get_or_create_app()
            self._connected = self._app is not None
            return self._connected
        except ExtractionError:
            raise
        except Exception as e:
            self._add_warning(f"Errore connessione: {e}")
            return False

    def _disconnect(self):
        """Disconnette da Solid Edge."""
        # Non chiudiamo Solid Edge se era già aperto
        if self._created_new_app and self._app:
            try:
                self._app.Quit()
            except Exception:
                pass
        self._app = None
        self._connected = False

    def _extract_from_document(self, filepath: Path) -> CADModelSignature:
        """Estrae signature da un documento Solid Edge."""
        doc = None
        was_already_open = False

        try:
            # Verifica se il file è già aperto
            for d in self._iterate_com_collection(self._app.Documents):
                try:
                    if Path(d.FullName).resolve() == filepath.resolve():
                        doc = d
                        was_already_open = True
                        break
                except Exception:
                    continue

            # Apri il documento se non già aperto
            if doc is None:
                doc = self._app.Documents.Open(str(filepath))

            # Estrai dati
            signature = self._extract_document_data(doc, filepath)

            return signature

        finally:
            # Chiudi solo se l'abbiamo aperto noi
            if doc and not was_already_open:
                try:
                    doc.Close(False)  # False = non salvare
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
            props = doc.Properties

            # SummaryInformation (PropertySet 1)
            try:
                summary = props.Item("SummaryInformation")
                sig.author = self._get_property_value(summary, "Author", "")
                sig.last_author = self._get_property_value(summary, "Last Author", "")
                sig.title = self._get_property_value(summary, "Title", "")
                sig.comments = self._get_property_value(summary, "Comments", "")
                sig.template = self._get_property_value(summary, "Template", "")
            except Exception as e:
                self._add_warning(f"Errore lettura SummaryInformation: {e}")

            # DocumentSummaryInformation (PropertySet 3)
            try:
                doc_summary = props.Item("DocumentSummaryInformation")
                sig.company = self._get_property_value(doc_summary, "Company", "")
            except Exception:
                pass

            # Versione CAD
            try:
                sig.cad_version = str(doc.CreatedVersion)
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
            models = doc.Models
            if models.Count == 0:
                return

            model = models.Item(1)
            features = model.Features

            feature_types = Counter()
            feature_sequence = []
            feature_names = []
            features_detailed = []

            for i in range(1, features.Count + 1):
                try:
                    feat = features.Item(i)

                    # Tipo feature
                    feat_type_id = getattr(feat, 'Type', 0)
                    feat_type = FEATURE_TYPE_MAP.get(feat_type_id, f"Unknown_{feat_type_id}")

                    # Nome feature
                    feat_name = getattr(feat, 'Name', f"Feature_{i}")

                    # Conta e aggiungi alla sequenza
                    feature_types[feat_type] += 1
                    feature_sequence.append(feat_type)
                    feature_names.append(feat_name)

                    # Dati dettagliati
                    features_detailed.append(FeatureData(
                        name=feat_name,
                        type=feat_type,
                        original_type=str(feat_type_id),
                        order=i
                    ))

                except Exception as e:
                    self._add_warning(f"Errore feature {i}: {e}")
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
            sketches = doc.Sketches
            if not sketches or sketches.Count == 0:
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

                    # Accumula totali
                    total_geom += sk_data.geometry_count
                    total_constr += sk_data.constraint_count

                    for gtype, count in sk_data.geometry_types.items():
                        geom_types[gtype] += count
                    for ctype, count in sk_data.constraint_types.items():
                        constr_types[ctype] += count

                except Exception as e:
                    self._add_warning(f"Errore sketch {i}: {e}")
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
        sk_data = SketchData(
            name=getattr(sketch, 'Name', 'Sketch')
        )

        geom_types = Counter()
        geom_detailed = []
        constr_types = Counter()
        constr_detailed = []

        # === GEOMETRIE ===
        # Prova ad accedere ai Profiles
        try:
            profiles = sketch.Profiles
            if profiles and profiles.Count > 0:
                for p_idx in range(1, profiles.Count + 1):
                    try:
                        profile = profiles.Item(p_idx)

                        # Linee
                        self._extract_geometry_collection(
                            profile, 'Lines2d', 'Line',
                            geom_types, geom_detailed
                        )
                        # Archi
                        self._extract_geometry_collection(
                            profile, 'Arcs2d', 'Arc',
                            geom_types, geom_detailed
                        )
                        # Cerchi
                        self._extract_geometry_collection(
                            profile, 'Circles2d', 'Circle',
                            geom_types, geom_detailed
                        )
                        # Ellissi
                        self._extract_geometry_collection(
                            profile, 'Ellipses2d', 'Ellipse',
                            geom_types, geom_detailed
                        )
                        # Spline
                        self._extract_geometry_collection(
                            profile, 'BSplineCurves2d', 'Spline',
                            geom_types, geom_detailed
                        )

                        # === VINCOLI (Relations2d) ===
                        try:
                            relations = profile.Relations2d
                            if relations:
                                for r_idx in range(1, relations.Count + 1):
                                    try:
                                        rel = relations.Item(r_idx)
                                        rel_type_id = getattr(rel, 'Type', 0)

                                        # Mappa il tipo
                                        if rel_type_id in CONSTRAINT_TYPE_MAP:
                                            type_name, category, description = CONSTRAINT_TYPE_MAP[rel_type_id]
                                        else:
                                            type_name = f"Constraint_{rel_type_id}"
                                            category = "unknown"
                                            description = ""

                                        # Valore (per vincoli dimensionali)
                                        value = None
                                        if category == "dimensionale":
                                            try:
                                                value = float(rel.Value) * 1000  # Converti in mm
                                            except Exception:
                                                pass

                                        constr_types[type_name] += 1
                                        constr_detailed.append(ConstraintData(
                                            id=f"R2d_{r_idx}",
                                            type=type_name,
                                            original_type=str(rel_type_id),
                                            category=category,
                                            description=description,
                                            value=value
                                        ))
                                    except Exception:
                                        continue
                        except Exception:
                            pass

                    except Exception:
                        continue
        except Exception:
            pass

        sk_data.geometry_count = sum(geom_types.values())
        sk_data.geometry_types = dict(geom_types)
        sk_data.geometry_detailed = geom_detailed
        sk_data.constraint_count = sum(constr_types.values())
        sk_data.constraint_types = dict(constr_types)
        sk_data.constraint_detailed = constr_detailed

        # === CALCOLO FRAME PARAMETRICO (u, v) ===
        # Dopo aver estratto le geometrie, calcola il sistema di riferimento normalizzato
        parametric_frame = compute_sketch_parametric_frame(geom_detailed)
        sk_data.parametric_frame = parametric_frame

        # Applica la trasformazione UV alle geometrie
        if parametric_frame.is_valid:
            apply_uv_transform_to_geometries(geom_detailed, parametric_frame)

        return sk_data

    def _extract_geometry_collection(self, profile, collection_name: str,
                                      type_name: str, geom_types: Counter,
                                      geom_detailed: List[GeometryData]):
        """Estrae una collezione di geometrie con coordinate 2D."""
        try:
            collection = getattr(profile, collection_name, None)
            if collection and collection.Count > 0:
                for j in range(1, collection.Count + 1):
                    try:
                        geom = collection.Item(j)

                        # Estrai coordinate in base al tipo
                        start_point = None
                        end_point = None
                        center_point = None
                        radius = None

                        if type_name == 'Line':
                            # Line2d ha StartPoint e EndPoint
                            try:
                                sp = geom.StartPoint
                                start_point = (float(sp.X), float(sp.Y))
                            except Exception:
                                pass
                            try:
                                ep = geom.EndPoint
                                end_point = (float(ep.X), float(ep.Y))
                            except Exception:
                                pass

                        elif type_name == 'Circle':
                            # Circle2d ha CenterPoint e Radius
                            try:
                                cp = geom.CenterPoint
                                center_point = (float(cp.X), float(cp.Y))
                            except Exception:
                                pass
                            try:
                                radius = float(geom.Radius)
                            except Exception:
                                pass

                        elif type_name == 'Arc':
                            # Arc2d ha StartPoint, EndPoint, CenterPoint, Radius
                            try:
                                sp = geom.StartPoint
                                start_point = (float(sp.X), float(sp.Y))
                            except Exception:
                                pass
                            try:
                                ep = geom.EndPoint
                                end_point = (float(ep.X), float(ep.Y))
                            except Exception:
                                pass
                            try:
                                cp = geom.CenterPoint
                                center_point = (float(cp.X), float(cp.Y))
                            except Exception:
                                pass
                            try:
                                radius = float(geom.Radius)
                            except Exception:
                                pass

                        elif type_name == 'Ellipse':
                            # Ellipse2d ha CenterPoint
                            try:
                                cp = geom.CenterPoint
                                center_point = (float(cp.X), float(cp.Y))
                            except Exception:
                                pass

                        elif type_name == 'Spline':
                            # BSplineCurve2d - prova a ottenere punti di controllo o estremi
                            try:
                                # Alcuni CAD espongono StartPoint/EndPoint
                                sp = geom.StartPoint
                                start_point = (float(sp.X), float(sp.Y))
                            except Exception:
                                pass
                            try:
                                ep = geom.EndPoint
                                end_point = (float(ep.X), float(ep.Y))
                            except Exception:
                                pass

                        geom_types[type_name] += 1
                        geom_detailed.append(GeometryData(
                            id=f"{type_name}_{j}",
                            type=type_name,
                            original_type=collection_name,
                            start_point=start_point,
                            end_point=end_point,
                            center_point=center_point,
                            radius=radius
                        ))
                    except Exception:
                        # Fallback senza coordinate
                        geom_types[type_name] += 1
                        geom_detailed.append(GeometryData(
                            id=f"{type_name}_{j}",
                            type=type_name,
                            original_type=collection_name
                        ))
        except Exception:
            pass

