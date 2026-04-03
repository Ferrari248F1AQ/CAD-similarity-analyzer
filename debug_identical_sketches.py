"""Test debug per capire perché sketch identici hanno similarità 94%"""
import sys
sys.path.insert(0, '.')

from solid_edge_similarity_v2 import compute_sketch_pair_similarity

# Simula due sketch IDENTICI
sketch1 = {
    'name': 'Sketch1',
    'geometry_count': 10,
    'geometry_types': {'Line2d': 6, 'Circle2d': 2, 'Arc2d': 2},
    'constraint_count': 15,
    'constraint_types': {'Coincident': 8, 'Perpendicular': 3, 'LinearDimension': 4},
    'parametric_frame': {
        'centroid': (50.0, 25.0),
        'axis_u': (1.0, 0.0),
        'axis_v': (0.0, 1.0),
        'extent_u': 0.8944,  # Pesi normalizzati
        'extent_v': 0.4472,
        'num_points': 10,
        'is_valid': True
    }
}

# Sketch identico (copia)
sketch2 = {
    'name': 'Sketch1',
    'geometry_count': 10,
    'geometry_types': {'Line2d': 6, 'Circle2d': 2, 'Arc2d': 2},
    'constraint_count': 15,
    'constraint_types': {'Coincident': 8, 'Perpendicular': 3, 'LinearDimension': 4},
    'parametric_frame': {
        'centroid': (50.0, 25.0),
        'axis_u': (1.0, 0.0),
        'axis_v': (0.0, 1.0),
        'extent_u': 0.8944,
        'extent_v': 0.4472,
        'num_points': 10,
        'is_valid': True
    }
}

print("=" * 60)
print("TEST: Due sketch IDENTICI")
print("=" * 60)

sim = compute_sketch_pair_similarity(sketch1, sketch2)
print(f"\nSimilarità calcolata: {sim:.4f} ({sim*100:.1f}%)")

if sim >= 0.999:
    print("[OK] Similarità = 100% come atteso!")
elif sim >= 0.94:
    print(f"[WARN] Similarità = {sim*100:.1f}% - dovrebbe essere 100%!")
else:
    print(f"[ERROR] Similarità troppo bassa: {sim*100:.1f}%")

# Debug dettagliato
pf1 = sketch1['parametric_frame']
pf2 = sketch2['parametric_frame']

u1 = pf1['axis_u']
v1 = pf1['axis_v']
u2 = pf2['axis_u']
v2 = pf2['axis_v']

dot_u = abs(u1[0] * u2[0] + u1[1] * u2[1])
dot_v = abs(v1[0] * v2[0] + v1[1] * v2[1])
orientation_sim = (dot_u + dot_v) / 2.0

print(f"\n--- Debug ---")
print(f"Orientation similarity: {orientation_sim:.4f}")

weight_u1 = pf1['extent_u']
weight_v1 = pf1['extent_v']
weight_u2 = pf2['extent_u']
weight_v2 = pf2['extent_v']

weights_dot_aligned = weight_u1 * weight_u2 + weight_v1 * weight_v2
weights_dot_swapped = weight_u1 * weight_v2 + weight_v1 * weight_u2
weights_dot = max(abs(weights_dot_aligned), abs(weights_dot_swapped))

print(f"Weight similarity (aligned): {weights_dot_aligned:.4f}")
print(f"Weight similarity (swapped): {weights_dot_swapped:.4f}")
print(f"Weight similarity (max): {weights_dot:.4f}")

# Geometrie
geom_types1 = sketch1.get('geometry_types', {})
geom_types2 = sketch2.get('geometry_types', {})
all_geom = set(geom_types1.keys()) | set(geom_types2.keys())
jaccard = len(set(geom_types1.keys()) & set(geom_types2.keys())) / len(all_geom)

count1 = sketch1.get('geometry_count', 0)
count2 = sketch2.get('geometry_count', 0)
count_sim = 1.0 - abs(count1 - count2) / max(count1, count2, 1)

constr_types1 = sketch1.get('constraint_types', {})
constr_types2 = sketch2.get('constraint_types', {})
all_constr = set(constr_types1.keys()) | set(constr_types2.keys())
constr_jaccard = len(set(constr_types1.keys()) & set(constr_types2.keys())) / len(all_constr)

geom_sim = 0.40 * jaccard + 0.30 * count_sim + 0.30 * constr_jaccard

print(f"Geometry similarity: {geom_sim:.4f}")
print(f"  - Jaccard types: {jaccard:.4f}")
print(f"  - Count sim: {count_sim:.4f}")
print(f"  - Constraint Jaccard: {constr_jaccard:.4f}")

# Calcolo finale
expected_sim = 0.25 * orientation_sim + 0.35 * weights_dot + 0.40 * geom_sim
print(f"\nCalcolo finale:")
print(f"  0.25 * {orientation_sim:.4f} + 0.35 * {weights_dot:.4f} + 0.40 * {geom_sim:.4f}")
print(f"  = {expected_sim:.4f}")

if abs(expected_sim - sim) > 0.001:
    print(f"[ERROR] Discrepanza nel calcolo!")
else:
    print(f"[OK] Calcolo corretto")
