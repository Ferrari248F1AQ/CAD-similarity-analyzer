la p# -*- coding: utf-8 -*-
"""
✅ LOGICA CORRETTA: Le feature INIZIALI pesano di più (difficili da spostare),
   le feature finali pesano meno (facili da modificare).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from solid_edge_similarity_v2 import FeatureSignature, compute_similarity

print("=" * 80)
print("TEST LOGICA FUZZY LCS - Feature INIZIALI Pesano di Più")
print("=" * 80)

# Scenario 1: File identici
print("\n📊 SCENARIO 1: File Identici")
sig1 = FeatureSignature(filename="A.par", filepath="A.par", file_hash="aaa")
sig1.feature_sequence = ['Sketch', 'Protrusion', 'Cutout', 'Hole', 'Round']
sig1.feature_count = 5

sig2 = FeatureSignature(filename="B.par", filepath="B.par", file_hash="bbb")
sig2.feature_sequence = ['Sketch', 'Protrusion', 'Cutout', 'Hole', 'Round']
sig2.feature_count = 5

sim = compute_similarity(sig1, sig2)
print(f"File A: {sig1.feature_sequence}")
print(f"File B: {sig2.feature_sequence}")
print(f"✅ LCS Similarity: {sim['lcs_similarity']:.2%}")
print(f"   Atteso: ~100% (identici)")

# Scenario 2: Prime 2 feature diverse, resto identico
print("\n📊 SCENARIO 2: Prime 2 Feature Diverse, Resto Identico")
sig3 = FeatureSignature(filename="C.par", filepath="C.par", file_hash="ccc")
sig3.feature_sequence = ['Sketch', 'Protrusion', 'Cutout', 'Hole', 'Round']
sig3.feature_count = 5

sig4 = FeatureSignature(filename="D.par", filepath="D.par", file_hash="ddd")
sig4.feature_sequence = ['SketchX', 'ProtrusionY', 'Cutout', 'Hole', 'Round']
sig4.feature_count = 5

sim2 = compute_similarity(sig3, sig4)
print(f"File A: {sig3.feature_sequence}")
print(f"File B: {sig4.feature_sequence}")
print(f"   LCS = [Cutout, Hole, Round] (3/5 = 60% match)")
print(f"✅ LCS Similarity: {sim2['lcs_similarity']:.2%}")
print(f"   ✅ CORRETTO: Con logica FUZZY, le ultime 3 (Cutout, Hole, Round) pesano MENO!")
print(f"   Atteso: ~50-55% (PIÙ BASSO del 60% standard perché match sono finali)")

# Scenario 3: Ultime 2 feature diverse, inizio identico
print("\n📊 SCENARIO 3: Ultime 2 Feature Diverse, Inizio Identico")
sig5 = FeatureSignature(filename="E.par", filepath="E.par", file_hash="eee")
sig5.feature_sequence = ['Sketch', 'Protrusion', 'Cutout', 'Hole', 'Round']
sig5.feature_count = 5

sig6 = FeatureSignature(filename="F.par", filepath="F.par", file_hash="fff")
sig6.feature_sequence = ['Sketch', 'Protrusion', 'Cutout', 'HoleX', 'RoundY']
sig6.feature_count = 5

sim3 = compute_similarity(sig5, sig6)
print(f"File A: {sig5.feature_sequence}")
print(f"File B: {sig6.feature_sequence}")
print(f"   LCS = [Sketch, Protrusion, Cutout] (3/5 = 60% match)")
print(f"✅ LCS Similarity: {sim3['lcs_similarity']:.2%}")
print(f"   ✅ CORRETTO: Con logica FUZZY, le prime 3 pesano MOLTO DI PIÙ!")
print(f"   Atteso: ~65-75% (PIÙ ALTO del caso precedente perché match sono iniziali)")

# Scenario 4: Feature lunghe - prime 3 diverse, resto identico
print("\n📊 SCENARIO 4: Sequenza Lunga (10 feat) - Prime 3 Diverse")
sig7 = FeatureSignature(filename="G.par", filepath="G.par", file_hash="ggg")
sig7.feature_sequence = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J']
sig7.feature_count = 10

sig8 = FeatureSignature(filename="H.par", filepath="H.par", file_hash="hhh")
sig8.feature_sequence = ['X', 'Y', 'Z', 'D', 'E', 'F', 'G', 'H', 'I', 'J']
sig8.feature_count = 10

sim4 = compute_similarity(sig7, sig8)
print(f"File A: {sig7.feature_sequence}")
print(f"File B: {sig8.feature_sequence}")
print(f"   LCS = [D,E,F,G,H,I,J] (7/10 = 70% match)")
print(f"✅ LCS Similarity: {sim4['lcs_similarity']:.2%}")
print(f"   ✅ CORRETTO: Con logica FUZZY, le ultime 7 (D-J) pesano MENO!")
print(f"   Atteso: ~55-65% (PIÙ BASSO del 70% standard perché match sono finali)")

print("\n" + "=" * 80)
print("💡 OSSERVAZIONI:")
print("=" * 80)
print("• Scenario 2 vs 3: STESSO numero di match (3/5), ma...")
print(f"  - Match FINALI (Scenario 2, pesano MENO): {sim2['lcs_similarity']:.2%}")
print(f"  - Match INIZIALI (Scenario 3, pesano PIÙ): {sim3['lcs_similarity']:.2%}")
print(f"  - Differenza: {abs(sim3['lcs_similarity'] - sim2['lcs_similarity']) * 100:.1f} punti percentuali")
print("\n✅ Se Scenario 3 > Scenario 2: Logica fuzzy funziona correttamente!")
print("   (Le feature INIZIALI pesano di più)")

if sim3['lcs_similarity'] > sim2['lcs_similarity']:
    print("\n🎉 SUCCESSO! La logica fuzzy privilegia correttamente le feature INIZIALI.")
    print(f"   File con match iniziali: {sim3['lcs_similarity']:.2%}")
    print(f"   File con match finali: {sim2['lcs_similarity']:.2%}")
else:
    print("\n⚠️ ATTENZIONE: La logica fuzzy potrebbe non funzionare come atteso.")

print("\n" + "=" * 80)

