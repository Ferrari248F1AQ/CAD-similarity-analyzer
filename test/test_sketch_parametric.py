"""
Test delle funzionalita di similarita sketch parametrica.
"""

import sys
import math
from pathlib import Path

# Aggiungi il path del progetto
project_path = Path(__file__).parent
sys.path.insert(0, str(project_path))

# Test 1: Import delle strutture dati
print("=" * 60)
print("TEST 1: Import strutture dati")
print("=" * 60)

try:
    from extractors.cad_signature import (
        SketchParametricFrame,
        GeometryData,
        SketchData,
        CADModelSignature
    )
    print("[OK] Import cad_signature OK")
except Exception as e:
    print(f"[ERROR] Errore import cad_signature: {e}")
    sys.exit(1)

# Test 2: Creazione SketchParametricFrame
print("\n" + "=" * 60)
print("TEST 2: Creazione SketchParametricFrame")
print("=" * 60)

try:
    pf = SketchParametricFrame(
        centroid=(10.0, 20.0),
        axis_u=(0.866, 0.5),   # Ruotato di 30 gradi
        axis_v=(-0.5, 0.866),
        extent_u=50.0,
        extent_v=30.0,
        num_points=15,
        is_valid=True
    )
    print(f"[OK] SketchParametricFrame creato:")
    print(f"  - centroid: {pf.centroid}")
    print(f"  - axis_u: {pf.axis_u}")
    print(f"  - axis_v: {pf.axis_v}")
    print(f"  - extent_u: {pf.extent_u}")
    print(f"  - extent_v: {pf.extent_v}")
    print(f"  - num_points: {pf.num_points}")
    print(f"  - is_valid: {pf.is_valid}")
except Exception as e:
    print(f"[ERROR] Errore creazione SketchParametricFrame: {e}")

# Test 3: Creazione GeometryData con coordinate
print("\n" + "=" * 60)
print("TEST 3: Creazione GeometryData con coordinate")
print("=" * 60)

try:
    geom = GeometryData(
        id="Line_1",
        type="Line",
        original_type="Lines2d",
        start_point=(0.0, 0.0),
        end_point=(100.0, 50.0),
        start_point_uv=(-1.0, -0.5),
        end_point_uv=(1.0, 0.5)
    )
    print(f"[OK] GeometryData creato:")
    print(f"  - id: {geom.id}")
    print(f"  - type: {geom.type}")
    print(f"  - start_point: {geom.start_point}")
    print(f"  - end_point: {geom.end_point}")
    print(f"  - start_point_uv: {geom.start_point_uv}")
    print(f"  - end_point_uv: {geom.end_point_uv}")
except Exception as e:
    print(f"[ERROR] Errore creazione GeometryData: {e}")

# Test 4: Import funzioni PCA
print("\n" + "=" * 60)
print("TEST 4: Import funzioni PCA da solid_edge_extractor")
print("=" * 60)

try:
    from extractors.solid_edge_extractor import (
        collect_characteristic_points,
        compute_centroid,
        compute_pca_2d,
        transform_to_uv,
        compute_sketch_parametric_frame,
        apply_uv_transform_to_geometries
    )
    print("[OK] Import funzioni PCA OK")
except Exception as e:
    print(f"[ERROR] Errore import funzioni PCA: {e}")
    print("  Tentativo fallback...")

# Test 5: Test calcolo PCA
print("\n" + "=" * 60)
print("TEST 5: Test calcolo frame parametrico")
print("=" * 60)

try:
    # Test A: Rettangolo 100x50
    print("\n--- Test A: Rettangolo 100x50 ---")
    rect_geometries = [
        GeometryData(id="L1", type="Line", start_point=(0.0, 0.0), end_point=(100.0, 0.0)),
        GeometryData(id="L2", type="Line", start_point=(100.0, 0.0), end_point=(100.0, 50.0)),
        GeometryData(id="L3", type="Line", start_point=(100.0, 50.0), end_point=(0.0, 50.0)),
        GeometryData(id="L4", type="Line", start_point=(0.0, 50.0), end_point=(0.0, 0.0)),
    ]

    frame_rect = compute_sketch_parametric_frame(rect_geometries)
    print(f"[OK] Frame rettangolo:")
    print(f"  - centroid: {frame_rect.centroid}")
    print(f"  - axis_u: {frame_rect.axis_u}")
    print(f"  - axis_v: {frame_rect.axis_v}")
    print(f"  - weight_u (extent_u): {frame_rect.extent_u:.4f}")
    print(f"  - weight_v (extent_v): {frame_rect.extent_v:.4f}")
    print(f"  - is_valid: {frame_rect.is_valid}")

    # Test B: Cerchio raggio 1 centrato sull'origine
    print("\n--- Test B: Cerchio raggio 1 centrato su origine ---")
    circle_geometries = [
        GeometryData(id="C1", type="Circle", center_point=(0.0, 0.0), radius=1.0),
    ]

    frame_circle = compute_sketch_parametric_frame(circle_geometries)
    print(f"[OK] Frame cerchio:")
    print(f"  - centroid: {frame_circle.centroid}")
    print(f"  - axis_u: {frame_circle.axis_u}")
    print(f"  - axis_v: {frame_circle.axis_v}")
    print(f"  - weight_u: {frame_circle.extent_u:.4f}")
    print(f"  - weight_v: {frame_circle.extent_v:.4f}")

    # Verifica: per un cerchio, weight_u = weight_v = sqrt(2)/2 = 0.7071
    expected = math.sqrt(2) / 2
    if abs(frame_circle.extent_u - expected) < 0.01 and abs(frame_circle.extent_v - expected) < 0.01:
        print(f"  [OK] Pesi corretti! Atteso sqrt(2)/2 = {expected:.4f}")
    else:
        print(f"  [WARN] Pesi diversi da sqrt(2)/2 = {expected:.4f}")

    # Test C: Due cerchi identici
    print("\n--- Test C: Due cerchi identici ---")
    circle2_geometries = [
        GeometryData(id="C2", type="Circle", center_point=(10.0, 20.0), radius=1.0),
    ]
    frame_circle2 = compute_sketch_parametric_frame(circle2_geometries)

    # Calcola similarita
    from solid_edge_similarity_v2 import compute_sketch_pair_similarity

    sk1 = {'parametric_frame': {
        'centroid': frame_circle.centroid,
        'axis_u': frame_circle.axis_u,
        'axis_v': frame_circle.axis_v,
        'extent_u': frame_circle.extent_u,
        'extent_v': frame_circle.extent_v,
        'is_valid': frame_circle.is_valid
    }, 'geometry_count': 1, 'geometry_types': {'Circle': 1}, 'constraint_count': 0, 'constraint_types': {}}

    sk2 = {'parametric_frame': {
        'centroid': frame_circle2.centroid,
        'axis_u': frame_circle2.axis_u,
        'axis_v': frame_circle2.axis_v,
        'extent_u': frame_circle2.extent_u,
        'extent_v': frame_circle2.extent_v,
        'is_valid': frame_circle2.is_valid
    }, 'geometry_count': 1, 'geometry_types': {'Circle': 1}, 'constraint_count': 0, 'constraint_types': {}}

    sim_circles = compute_sketch_pair_similarity(sk1, sk2)
    print(f"[OK] Similarita due cerchi identici (centri diversi): {sim_circles:.4f}")
    if sim_circles > 0.95:
        print("  [OK] Alta similarita come atteso!")

    # Test D: Cerchio vs Rettangolo
    print("\n--- Test D: Cerchio vs Rettangolo ---")
    sk_rect = {'parametric_frame': {
        'centroid': frame_rect.centroid,
        'axis_u': frame_rect.axis_u,
        'axis_v': frame_rect.axis_v,
        'extent_u': frame_rect.extent_u,
        'extent_v': frame_rect.extent_v,
        'is_valid': frame_rect.is_valid
    }, 'geometry_count': 4, 'geometry_types': {'Line': 4}, 'constraint_count': 0, 'constraint_types': {}}

    sim_diff = compute_sketch_pair_similarity(sk1, sk_rect)
    print(f"[OK] Similarita cerchio vs rettangolo: {sim_diff:.4f}")
    if sim_diff < 0.8:
        print("  [OK] Bassa similarita come atteso (forme diverse)!")

except Exception as e:
    import traceback
    print(f"[ERROR] Errore test frame: {e}")
    traceback.print_exc()

# Test 6: Import funzioni di similarita
print("\n" + "=" * 60)
print("TEST 6: Import funzioni di similarita sketch parametrica")
print("=" * 60)

try:
    from solid_edge_similarity_v2 import (
        compute_sketch_pair_similarity,
        compute_sketch_geometry_similarity,
        match_sketches_greedy,
        compute_sketch_parametric_similarity,
        DEFAULT_WEIGHTS
    )
    print("[OK] Import funzioni similarita OK")
    has_weight = 'sketch_parametric_similarity' in DEFAULT_WEIGHTS
    print(f"[OK] sketch_parametric_similarity in DEFAULT_WEIGHTS: {has_weight}")
    print(f"  Peso: {DEFAULT_WEIGHTS.get('sketch_parametric_similarity', 'NON TROVATO')}")
except Exception as e:
    print(f"[ERROR] Errore import funzioni similarita: {e}")

# Test 7: Test matching sketch
print("\n" + "=" * 60)
print("TEST 7: Test matching sketch")
print("=" * 60)

try:
    # Crea sketch simulati
    sketch1 = {
        'name': 'Sketch1',
        'geometry_count': 4,
        'geometry_types': {'Line': 4},
        'constraint_count': 2,
        'constraint_types': {'Perpendicular': 2},
        'parametric_frame': {
            'centroid': (50.0, 25.0),
            'axis_u': (1.0, 0.0),
            'axis_v': (0.0, 1.0),
            'extent_u': 50.0,
            'extent_v': 25.0,
            'num_points': 8,
            'is_valid': True
        }
    }

    sketch2 = {
        'name': 'Sketch2',
        'geometry_count': 4,
        'geometry_types': {'Line': 4},
        'constraint_count': 2,
        'constraint_types': {'Perpendicular': 2},
        'parametric_frame': {
            'centroid': (100.0, 50.0),  # Diverso ma stessa forma
            'axis_u': (0.707, 0.707),   # Ruotato di 45 gradi
            'axis_v': (-0.707, 0.707),
            'extent_u': 50.0,
            'extent_v': 25.0,
            'num_points': 8,
            'is_valid': True
        }
    }

    # Calcola similarita coppia
    sim = compute_sketch_pair_similarity(sketch1, sketch2)
    print(f"[OK] Similarita coppia sketch: {sim:.4f}")

    # Test matching
    sketches1 = [sketch1]
    sketches2 = [sketch2]
    matches = match_sketches_greedy(sketches1, sketches2)
    print(f"[OK] Match trovati: {matches}")

except Exception as e:
    import traceback
    print(f"[ERROR] Errore test matching: {e}")
    traceback.print_exc()

print("\n" + "=" * 60)
print("TEST COMPLETATI")
print("=" * 60)
