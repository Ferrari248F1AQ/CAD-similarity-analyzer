# -*- coding: utf-8 -*-
"""
Script di test per verificare il confronto dei nomi personalizzati delle feature
"""

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from solid_edge_similarity_v2 import extract_signature, compute_similarity

# File di test menzionati dall'utente
file1_path = Path(r"C:\Users\emanu\OneDrive - Università degli Studi dell'Aquila\Università\Materiale ricerca\solid edge plagiarism detector\dataset\A013\A013_9.par")
file2_path = Path(r"C:\Users\emanu\OneDrive - Università degli Studi dell'Aquila\Università\Materiale ricerca\solid edge plagiarism detector\dataset\A013\A013_10.par")

print("=" * 80)
print("TEST: Confronto nomi personalizzati feature")
print("=" * 80)
print()

if not file1_path.exists():
    print(f"❌ File non trovato: {file1_path}")
    sys.exit(1)

if not file2_path.exists():
    print(f"❌ File non trovato: {file2_path}")
    sys.exit(1)

print(f"📄 File 1: {file1_path.name}")
print(f"📄 File 2: {file2_path.name}")
print()

# Estrai signatures
print("🔍 Estrazione signature...")
sig1 = extract_signature(file1_path)
sig2 = extract_signature(file2_path)

print(f"\n✅ File 1 - Feature trovate: {sig1.feature_count}")
print(f"   Feature sequence: {sig1.feature_sequence}")
print(f"   Feature names: {sig1.feature_names}")

print(f"\n✅ File 2 - Feature trovate: {sig2.feature_count}")
print(f"   Feature sequence: {sig2.feature_sequence}")
print(f"   Feature names: {sig2.feature_names}")

# Calcola similarità
print("\n" + "=" * 80)
print("📊 ANALISI SIMILARITÀ")
print("=" * 80)

sim = compute_similarity(sig1, sig2)

print(f"\n🎯 Similarità Complessiva: {sim['overall']*100:.1f}%")
print("\n📋 Dettaglio criteri:")
print("-" * 80)

# Ordina per importanza (peso)
weights = {
    'author_match': 0.03,
    'feature_count_similarity': 0.11,
    'feature_type_similarity': 0.11,
    'style_similarity': 0.07,
    'naming_similarity': 0.03,
    'bigram_similarity': 0.11,
    'trigram_similarity': 0.11,
    'lcs_similarity': 0.16,
    'feature_names_similarity': 0.09,  # ✨ NUOVO
    'geometry_2d_similarity': 0.07,
    'constraint_2d_similarity': 0.07,
    'constraint_ratio_similarity': 0.04,
}

descriptions = {
    'author_match': 'Autore match',
    'feature_count_similarity': 'Numero feature',
    'feature_type_similarity': 'Tipi feature',
    'style_similarity': 'Stile modellazione',
    'naming_similarity': 'Naming style',
    'bigram_similarity': 'Bigram (coppie)',
    'trigram_similarity': 'Trigram (triple)',
    'lcs_similarity': 'LCS (ordine)',
    'feature_names_similarity': '✨ Nomi feature personalizzati',
    'geometry_2d_similarity': 'Geometrie 2D',
    'constraint_2d_similarity': 'Vincoli 2D',
    'constraint_ratio_similarity': 'Rapporto vincoli/geom',
}

for key in weights.keys():
    if key in sim:
        value = sim[key] * 100
        weight = weights[key] * 100
        desc = descriptions[key]
        icon = "🔴" if value < 50 else "🟡" if value < 80 else "🟢"
        print(f"{icon} {desc:.<35} {value:>5.1f}%  (peso: {weight:>4.0f}%)")

print("\n" + "=" * 80)
print("NOTA: Se i file hanno le stesse feature ma con nomi diversi,")
print("      il criterio '✨ Nomi feature personalizzati' dovrebbe mostrare")
print("      una similarità BASSA, mentre gli altri criteri HIGH.")
print("=" * 80)

