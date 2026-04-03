#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Debug approfondito degli indici di similarità per le due sequenze fornite.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Sequenze fornite dall'utente
# P = ExtrudedProtrusion, C = ExtrudedCutout
A = ['P','P','C','P','C','P','P','C','C','C','C','C','P','P']  # 14 elementi
B = ['P','C','C','C','C','C','P','P','C','P','P','C','C','P','P']  # 15 elementi

print("=" * 70)
print("DEBUG INDICI DI SIMILARITÀ")
print("=" * 70)
print(f"\nSequenza A ({len(A)} feat): {' '.join(A)}")
print(f"Sequenza B ({len(B)} feat): {' '.join(B)}")

# ──────────────────────────────────────────────────────────────────
# 1. feature_count_similarity
# ──────────────────────────────────────────────────────────────────
diff = abs(len(A) - len(B))
if diff <= 1:
    fcs = 1.0
elif diff <= 3:
    fcs = 0.7
elif diff <= 5:
    fcs = 0.4
else:
    fcs = max(0.0, 1.0 - diff / max(len(A), len(B)))
print(f"\n1. feature_count_similarity = {fcs:.3f}  (|{len(A)}-{len(B)}|={diff})")

# ──────────────────────────────────────────────────────────────────
# 2. feature_type_similarity  (cosine tra conteggi P/C)
# ──────────────────────────────────────────────────────────────────
from collections import Counter
typesA = Counter(A)
typesB = Counter(B)
all_types = set(typesA) | set(typesB)
v1 = [typesA.get(t,0) for t in all_types]
v2 = [typesB.get(t,0) for t in all_types]
dot = sum(a*b for a,b in zip(v1,v2))
n1 = sum(a**2 for a in v1)**0.5
n2 = sum(b**2 for b in v2)**0.5
fts = dot / (n1*n2 + 1e-9)
print(f"\n2. feature_type_similarity  = {fts:.3f}")
print(f"   A: P={typesA['P']} C={typesA['C']}")
print(f"   B: P={typesB['P']} C={typesB['C']}")

# ──────────────────────────────────────────────────────────────────
# 3. style_similarity  (rapporti P e C)
# ──────────────────────────────────────────────────────────────────
extA = typesA['P'] / len(A)
extB = typesB['P'] / len(B)
cutA = typesA['C'] / len(A)
cutB = typesB['C'] / len(B)
style_diff = (abs(extA-extB) + abs(cutA-cutB)) / 2.0   # gli altri ratio sono 0
style_sim = 1.0 - min(style_diff, 1.0)
print(f"\n3. style_similarity         = {style_sim:.3f}")
print(f"   A: ext_ratio={extA:.3f}  cut_ratio={cutA:.3f}")
print(f"   B: ext_ratio={extB:.3f}  cut_ratio={cutB:.3f}")

# ──────────────────────────────────────────────────────────────────
# 4. bigram_similarity
# ──────────────────────────────────────────────────────────────────
def get_bigrams(seq):
    return [tuple(seq[i:i+2]) for i in range(len(seq)-1)]

bi_A = set(get_bigrams(A))
bi_B = set(get_bigrams(B))
inter = len(bi_A & bi_B)
union = len(bi_A | bi_B)
bigram_sim = inter / union if union else 0
print(f"\n4. bigram_similarity        = {bigram_sim:.3f}  ({inter}/{union})")
print(f"   A bigrams: {sorted(bi_A)}")
print(f"   B bigrams: {sorted(bi_B)}")
print(f"   Comuni:    {sorted(bi_A & bi_B)}")

# ──────────────────────────────────────────────────────────────────
# 5. trigram_similarity
# ──────────────────────────────────────────────────────────────────
def get_trigrams(seq):
    return set(tuple(seq[i:i+3]) for i in range(len(seq)-2))

tri_A = get_trigrams(A)
tri_B = get_trigrams(B)
inter3 = len(tri_A & tri_B)
union3 = len(tri_A | tri_B)
trigram_sim = inter3 / union3 if union3 else 0
print(f"\n5. trigram_similarity       = {trigram_sim:.3f}  ({inter3}/{union3})")
print(f"   Comuni: {sorted(tri_A & tri_B)}")

# ──────────────────────────────────────────────────────────────────
# 6. LCS – calcolo STANDARD e FUZZY
# ──────────────────────────────────────────────────────────────────
def lcs_dp(seq1, seq2):
    m, n = len(seq1), len(seq2)
    dp = [[0]*(n+1) for _ in range(m+1)]
    for i in range(1, m+1):
        for j in range(1, n+1):
            if seq1[i-1] == seq2[j-1]:
                dp[i][j] = dp[i-1][j-1] + 1
            else:
                dp[i][j] = max(dp[i-1][j], dp[i][j-1])
    return dp

def backtrack(dp, seq1, seq2):
    i, j = len(seq1), len(seq2)
    matches = []
    while i > 0 and j > 0:
        if seq1[i-1] == seq2[j-1]:
            matches.append((i-1, j-1, seq1[i-1]))
            i -= 1; j -= 1
        elif dp[i-1][j] >= dp[i][j-1]:
            i -= 1
        else:
            j -= 1
    matches.reverse()
    return matches

dp = lcs_dp(A, B)
lcs_len = dp[len(A)][len(B)]
matches = backtrack(dp, A, B)

lcs_standard = lcs_len / max(len(A), len(B))
print(f"\n6. LCS:")
print(f"   Lunghezza LCS = {lcs_len}  (max_len={max(len(A),len(B))})")
print(f"   lcs_standard  = {lcs_standard:.3f}  ({lcs_len}/{max(len(A),len(B))})")
print(f"   Match: {matches}")

# Fuzzy con parametri default (exponential, alpha=2.0, mix=0.7)
alpha = 2.0
fuzzy_function = 'exponential'
m_len, n_len = len(A), len(B)

total_weighted = 0.0
max_possible_weight = 0.0

print(f"\n   Calcolo peso fuzzy (exponential, alpha={alpha}):")
for pos1, pos2, elem in matches:
    norm_pos1 = pos1 / max(m_len - 1, 1)
    norm_pos2 = pos2 / max(n_len - 1, 1)
    norm_pos = (norm_pos1 + norm_pos2) / 2.0
    weight = math.exp(-alpha * norm_pos)
    total_weighted += weight
    print(f"   Match ({elem}) A[{pos1}] B[{pos2}] norm_pos={norm_pos:.3f} → w={weight:.4f}")

for idx in range(lcs_len):
    norm_pos = idx / max(lcs_len - 1, 1)
    weight = math.exp(-alpha * norm_pos)
    max_possible_weight += weight

lcs_fuzzy = total_weighted / max(max_possible_weight, 1e-9)
print(f"   total_weighted     = {total_weighted:.4f}")
print(f"   max_possible_weight= {max_possible_weight:.4f}")
print(f"   lcs_fuzzy          = {lcs_fuzzy:.4f}")

fuzzy_mix = 0.7
lcs_final = fuzzy_mix * lcs_fuzzy + (1 - fuzzy_mix) * lcs_standard
print(f"\n   lcs_similarity (mix={fuzzy_mix}*fuzzy + {1-fuzzy_mix}*standard)")
print(f"   = {fuzzy_mix}*{lcs_fuzzy:.4f} + {1-fuzzy_mix}*{lcs_standard:.4f}")
print(f"   = {lcs_final:.4f}  → {lcs_final*100:.1f}%")

# ──────────────────────────────────────────────────────────────────
# DIAGNOSI: perché LCS è così alto?
# ──────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("⚠️  DIAGNOSI DEL PROBLEMA")
print("=" * 70)
print(f"\nLa sequenza A contiene solo due tipi: P e C")
print(f"La sequenza B contiene solo due tipi: P e C")
print(f"\nL'LCS trova {lcs_len} elementi su {max(len(A),len(B))} = {lcs_standard*100:.1f}%")
print(f"\nIl VERO problema: sequenze binarie (solo P e C) producono LCS molto alti")
print(f"per definizione, perché ci sono POCHE combinazioni possibili.")
print(f"\nEsempio: due sequenze casuali di P/C hanno LCS atteso ≈ 85%")
print(f"(perché quasi ogni elemento P troverà un P corrispondente nell'altra sequenza)")

# Dimostralo: quanti P in comune possiamo allineare?
minP = min(typesA['P'], typesB['P'])
minC = min(typesA['C'], typesB['C'])
print(f"\n  min(PA,PB) = min({typesA['P']},{typesB['P']}) = {minP}  (P allineabili)")
print(f"  min(CA,CB) = min({typesA['C']},{typesB['C']}) = {minC}  (C allineabili)")
print(f"  Somma = {minP+minC}  (LCS teorico massimo su sequenze binarie)")
print(f"  LCS effettivo = {lcs_len}  ({'==' if lcs_len==minP+minC else '<'} max teorico)")

print(f"\n✅ CONCLUSIONE:")
print(f"   L'LCS dell'80% NON significa che i due pezzi sono simili all'80%!")
print(f"   Significa che le SEQUENZE BINARIE (P/C) hanno {lcs_standard*100:.1f}% di elementi in comune.")
print(f"   Ma qualsiasi due sequenze di ExtrudedProtrusion/ExtrudedCutout")
print(f"   tenderanno ad avere LCS alto per mera probabilità statistica.")
print(f"\n🔧 SOLUZIONE: ridurre drasticamente il peso di lcs_similarity")
print(f"   o usare metriche più discriminanti (es. ordine relativo, parametri geometrici).")

# ──────────────────────────────────────────────────────────────────
# SIMULAZIONE: LCS su sequenze casuali
# ──────────────────────────────────────────────────────────────────
import random
random.seed(42)
print(f"\n{'='*70}")
print("SIMULAZIONE: LCS medio su 1000 coppie casuali di lunghezza simile")
print("="*70)
lcs_values = []
for _ in range(1000):
    n1 = random.randint(12, 16)
    n2 = random.randint(12, 16)
    s1 = random.choices(['P','C'], k=n1)
    s2 = random.choices(['P','C'], k=n2)
    dp_r = lcs_dp(s1, s2)
    l = dp_r[n1][n2]
    lcs_values.append(l / max(n1, n2))

avg_lcs = sum(lcs_values) / len(lcs_values)
print(f"LCS standard medio su sequenze casuali P/C: {avg_lcs*100:.1f}%")
print(f"LCS minimo: {min(lcs_values)*100:.1f}%")
print(f"LCS massimo: {max(lcs_values)*100:.1f}%")
print(f"\n→ L'LCS su sequenze binarie (solo P/C) tende a {avg_lcs*100:.0f}% ANCHE per pezzi diversi!")
print(f"  Questo spiega il valore alto che vedi.")

# ──────────────────────────────────────────────────────────────────
# RIEPILOGO TUTTI GLI INDICI
# ──────────────────────────────────────────────────────────────────
print(f"\n{'='*70}")
print("RIEPILOGO INDICI (senza peso sketch/2D che dipendono dai file reali)")
print("="*70)
scores = {
    'feature_count_similarity': fcs,
    'feature_type_similarity': fts,
    'style_similarity': style_sim,
    'bigram_similarity': bigram_sim,
    'trigram_similarity': trigram_sim,
    'lcs_similarity': lcs_final,
}
weights_nominal = {
    'feature_count_similarity': 0.12,
    'feature_type_similarity':  0.12,
    'style_similarity':         0.07,
    'bigram_similarity':        0.14,
    'trigram_similarity':       0.14,
    'lcs_similarity':           0.20,
}
total_w = sum(weights_nominal.values())

print(f"\n{'Indice':<35} {'Score':>8}  {'Peso':>6}  {'Contributo':>10}")
print("-"*65)
weighted_sum = 0.0
for k, s in scores.items():
    w = weights_nominal.get(k, 0)
    contrib = s * (w / total_w)
    weighted_sum += contrib
    bar = '█' * int(s * 20)
    print(f"{k:<35} {s*100:>7.1f}%  {w:>6.2f}  {contrib*100:>9.2f}%  {bar}")

print("-"*65)
print(f"{'OVERALL (solo questi indici)':<35} {weighted_sum*100:>7.1f}%")
print(f"\nNota: mancano author_match, geometry_2d, constraint_2d, constraint_ratio")
print(f"che dipendono dai dati reali dei file .par")

