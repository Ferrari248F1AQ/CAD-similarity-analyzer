# -*- coding: utf-8 -*-
"""
Test rapido per verificare che i pesi globali funzionino correttamente.
"""

import sys
from pathlib import Path

# Aggiungi il parent directory al path
sys.path.insert(0, str(Path(__file__).parent))

from solid_edge_similarity_v2 import load_weights, save_weights, DEFAULT_WEIGHTS

print("=" * 70)
print("TEST PESI GLOBALI")
print("=" * 70)

print("\n1️⃣ DEFAULT_WEIGHTS dal config.json:")
for k, v in DEFAULT_WEIGHTS.items():
    print(f"   {k:30s} = {v:.2f}")
print(f"   TOTALE: {sum(DEFAULT_WEIGHTS.values()):.2f}")

print("\n2️⃣ load_weights() (carica da cache o default):")
loaded = load_weights()
for k, v in loaded.items():
    print(f"   {k:30s} = {v:.2f}")
print(f"   TOTALE: {sum(loaded.values()):.2f}")

print("\n3️⃣ Modifico un peso e salvo...")
test_weights = dict(loaded)
test_weights['lcs_similarity'] = 0.30  # Aumento LCS al 30%
test_weights['author_match'] = 0.00    # Disabilito author
# Ribilancio per mantenere totale ~1.0
test_weights['bigram_similarity'] = 0.10
test_weights['trigram_similarity'] = 0.10

print("   Nuovi pesi:")
for k, v in test_weights.items():
    if v != loaded.get(k):
        print(f"   {k:30s} = {v:.2f} ⬅ MODIFICATO")
    else:
        print(f"   {k:30s} = {v:.2f}")
print(f"   TOTALE: {sum(test_weights.values()):.2f}")

ok = save_weights(test_weights)
if ok:
    print("\n✅ Pesi salvati con successo!")
else:
    print("\n❌ Errore nel salvataggio!")
    sys.exit(1)

print("\n4️⃣ Ricarico i pesi per verificare persistenza...")
reloaded = load_weights()
for k, v in reloaded.items():
    if abs(v - test_weights[k]) > 0.001:
        print(f"   ❌ {k:30s} = {v:.2f} (atteso {test_weights[k]:.2f})")
    else:
        print(f"   ✅ {k:30s} = {v:.2f}")

print(f"   TOTALE: {sum(reloaded.values()):.2f}")

print("\n5️⃣ Test compute_similarity con pesi personalizzati...")
from solid_edge_similarity_v2 import FeatureSignature, compute_similarity

# Crea due firme di test identiche
sig1 = FeatureSignature(
    filename="test1.par",
    filepath="C:\\test\\test1.par",
    file_hash="abc123"
)
sig1.feature_count = 10
sig1.feature_sequence = ['Protrusion', 'Cutout', 'Hole', 'Round']
sig1.feature_types = {'Protrusion': 4, 'Cutout': 3, 'Hole': 2, 'Round': 1}

sig2 = FeatureSignature(
    filename="test2.par",
    filepath="C:\\test\\test2.par",
    file_hash="def456"
)
sig2.feature_count = 10
sig2.feature_sequence = ['Protrusion', 'Cutout', 'Hole', 'Round']
sig2.feature_types = {'Protrusion': 4, 'Cutout': 3, 'Hole': 2, 'Round': 1}

sim = compute_similarity(sig1, sig2)
print(f"   Similarità overall: {sim['overall']:.2%}")
print(f"   LCS similarity: {sim['lcs_similarity']:.2%}")

if abs(sim['lcs_similarity'] - 1.0) < 0.01:  # Sequenze identiche → LCS = 100%
    print("   ✅ LCS calcolata correttamente!")
else:
    print(f"   ⚠️ LCS attesa ~100%, ottenuta {sim['lcs_similarity']:.2%}")

print("\n" + "=" * 70)
print("✅ TEST COMPLETATO!")
print("=" * 70)
print("\n💡 Se tutti i test sono OK, i pesi globali funzionano correttamente.")
print("   Puoi ora modificarli nell'interfaccia web e verranno applicati")
print("   a tutte le analisi e confronti!")

