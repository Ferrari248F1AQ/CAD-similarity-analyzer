#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Debug metodico degli indici di similarita su componenti sintetici.

Obiettivo:
- creare almeno 20 coppie sintetiche a partire da elementi reali del dataset cache;
- confrontare gli score calcolati da compute_raw_scores() con un "oracolo" indipendente;
- produrre un report leggibile + JSON.

Uso:
    py debug/debug_synthetic_indices.py
    py debug/debug_synthetic_indices.py --cases 30 --seed 7
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from collections import Counter
from dataclasses import fields
from pathlib import Path
from typing import Dict, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solid_edge_similarity_v2 import FeatureSignature, compute_raw_scores


NUMERIC_KEYS = [
    "author_match",
    "feature_count_similarity",
    "feature_type_similarity",
    "style_similarity",
    "bigram_similarity",
    "trigram_similarity",
    "lcs_similarity",
    "feature_names_similarity",
    "geometry_2d_similarity",
    "constraint_2d_similarity",
    "constraint_ratio_similarity",
    "sketch_parametric_similarity",
]

DEFAULT_NAME_ROOTS = (
    "extrudedprotrusion", "revolvedprotrusion", "extrudedcutout", "hole",
    "round", "chamfer", "pattern", "circularpattern", "rectangularpattern",
    "mirror", "loft", "sweep", "sketch", "refplane", "draft", "shell",
    "rib", "web", "lip", "thread", "thinwall", "thicken", "boolean",
    "feature", "feat",
    "protrusione", "taglio", "foro", "raccordo", "smusso", "schizzo", "piano",
)


def _results_dir() -> Path:
    return Path.home() / ".cache" / "cad_similarity_analyzer" / "results"


def _load_latest_signatures() -> Tuple[Path, List[dict]]:
    rdir = _results_dir()
    files = sorted(rdir.glob("analysis_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        raise FileNotFoundError(f"Nessun file analysis_*.json trovato in {rdir}")
    latest = files[0]
    with latest.open("r", encoding="utf-8") as f:
        data = json.load(f)
    sigs = data.get("signatures", [])
    if not sigs:
        raise RuntimeError(f"Il file {latest} non contiene signatures")
    return latest, sigs


def _safe_counter(d: Dict[str, int] | None) -> Dict[str, int]:
    if not isinstance(d, dict):
        return {}
    out = {}
    for k, v in d.items():
        try:
            iv = int(v)
        except Exception:
            iv = 0
        if iv > 0:
            out[str(k)] = iv
    return out


def _build_component(template: dict, idx: int, rng: random.Random) -> FeatureSignature:
    # prendi una sequenza realistica dal dataset reale
    seq = list(template.get("feature_sequence") or [])
    if not seq:
        seq = ["Sketch", "ExtrudedProtrusion", "ExtrudedCutout", "Hole", "Round"]
    if len(seq) > 20:
        seq = seq[:20]

    ft = Counter(seq)
    names = list(template.get("feature_names") or [])
    if not names:
        names = [f"Feature_{i+1}" for i in range(len(seq))]

    sig = FeatureSignature(
        filename=f"SYN_{idx:03d}.par",
        filepath=f"SYN://{idx:03d}",
        file_hash=f"syn{idx:03d}",
    )
    sig.author = (template.get("author") or "").strip()
    sig.feature_sequence = seq
    sig.feature_count = len(seq)
    sig.feature_types = dict(ft)
    sig.feature_names = names[:]

    # stile: copia valori reali quando disponibili
    sig.extrusion_ratio = float(template.get("extrusion_ratio", 0.0) or 0.0)
    sig.cutout_ratio = float(template.get("cutout_ratio", 0.0) or 0.0)
    sig.hole_ratio = float(template.get("hole_ratio", 0.0) or 0.0)
    sig.round_chamfer_ratio = float(template.get("round_chamfer_ratio", 0.0) or 0.0)

    sig.geometry_2d_types = _safe_counter(template.get("geometry_2d_types"))
    sig.constraint_2d_types = _safe_counter(template.get("constraint_2d_types"))
    sig.constraint_to_geometry_ratio = float(template.get("constraint_to_geometry_ratio", 0.0) or 0.0)

    # Per tenere oracolo semplice: sketch param disattivato (0.0 atteso)
    sig.sketches_data = []
    return sig


def _mutate_component(base: FeatureSignature, idx: int, rng: random.Random) -> FeatureSignature:
    # copia shallow sufficiente (i campi usati vengono riassegnati)
    b = FeatureSignature(filename=base.filename, filepath=base.filepath, file_hash=base.file_hash)
    for f in fields(FeatureSignature):
        setattr(b, f.name, getattr(base, f.name))

    b.filename = f"{base.filename[:-4]}_M.par" if base.filename.endswith(".par") else base.filename + "_M"
    b.filepath = base.filepath + "_M"
    b.file_hash = base.file_hash + "m"
    b.feature_sequence = list(base.feature_sequence)
    b.feature_names = list(base.feature_names)
    b.feature_types = dict(base.feature_types)
    b.geometry_2d_types = dict(base.geometry_2d_types)
    b.constraint_2d_types = dict(base.constraint_2d_types)

    mode = idx % 8
    if mode == 0:
        # stesso modello (controllo baseline)
        pass
    elif mode == 1:
        b.author = (base.author + "_X") if base.author else "synthetic_author"
    elif mode == 2:
        b.feature_sequence.append("Round")
    elif mode == 3 and len(b.feature_sequence) > 2:
        b.feature_sequence = b.feature_sequence[:-1]
    elif mode == 4 and len(b.feature_sequence) > 3:
        i = rng.randint(0, len(b.feature_sequence) - 2)
        b.feature_sequence[i], b.feature_sequence[i + 1] = b.feature_sequence[i + 1], b.feature_sequence[i]
    elif mode == 5:
        if b.geometry_2d_types:
            k = next(iter(b.geometry_2d_types.keys()))
            b.geometry_2d_types[k] = max(1, b.geometry_2d_types[k] - 1)
        else:
            b.geometry_2d_types = {"Line2d": 3, "Circle2d": 1}
    elif mode == 6:
        if b.constraint_2d_types:
            k = next(iter(b.constraint_2d_types.keys()))
            b.constraint_2d_types[k] += 1
        else:
            b.constraint_2d_types = {"Coincidente": 2}
    else:
        b.feature_names = [n + "_custom" for n in b.feature_names[: max(1, len(b.feature_names) // 2)]]

    # riallinea campi derivati
    b.feature_count = len(b.feature_sequence)
    b.feature_types = dict(Counter(b.feature_sequence))
    return b


def _custom_name_set(feature_names: List[str]) -> set:
    def is_default(name: str) -> bool:
        n = (name or "").strip().lower()
        if not n:
            return True
        if re.match(r"^(feature|feat)[\s_\-]*\d+$", n):
            return True
        for root in DEFAULT_NAME_ROOTS:
            if re.match(rf"^{re.escape(root)}[\s_\-]*\d*$", n):
                return True
        return False
    return {(n or "").strip().lower() for n in feature_names if (n or "").strip() and not is_default(n)}


def _multiset_jaccard(c1: Dict[str, int], c2: Dict[str, int]) -> float:
    keys = set(c1.keys()) | set(c2.keys())
    if not keys:
        return 0.0
    inter = sum(min(int(c1.get(k, 0)), int(c2.get(k, 0))) for k in keys)
    union = sum(max(int(c1.get(k, 0)), int(c2.get(k, 0))) for k in keys)
    return inter / union if union > 0 else 0.0


def _jaccard_ngrams(seq1: List[str], seq2: List[str], n: int) -> float:
    def grams(seq: List[str]) -> set:
        if len(seq) < n:
            return set()
        return {tuple(seq[i:i + n]) for i in range(len(seq) - n + 1)}
    g1, g2 = grams(seq1), grams(seq2)
    if not g1 and not g2:
        return 0.5
    union = len(g1 | g2)
    return (len(g1 & g2) / union) if union > 0 else 0.0


def _lcs_len(seq1: List[str], seq2: List[str]) -> int:
    m, n = len(seq1), len(seq2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if seq1[i - 1] == seq2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[m][n]


def _lcs_fuzzy_blended(seq1: List[str], seq2: List[str], use_fuzzy: bool, fuzzy_function: str, alpha: float, mix: float) -> float:
    if not seq1 or not seq2:
        return 0.0

    m, n = len(seq1), len(seq2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if seq1[i - 1] == seq2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    lcs_len = dp[m][n]
    lcs_standard = lcs_len / max(m, n, 1)
    if (not use_fuzzy) or lcs_len == 0:
        return lcs_standard

    i, j = m, n
    matches: List[Tuple[int, int]] = []
    while i > 0 and j > 0:
        if seq1[i - 1] == seq2[j - 1]:
            matches.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    matches.reverse()

    def w(norm_pos: float) -> float:
        if fuzzy_function == "linear":
            return 1.0 - norm_pos
        if fuzzy_function == "exponential":
            return math.exp(-alpha * norm_pos)
        if fuzzy_function == "logarithmic":
            return 1.0 - math.log(1.0 + alpha * norm_pos) / math.log(1.0 + alpha)
        return 1.0 - norm_pos

    total_weighted = 0.0
    for pos1, pos2 in matches:
        norm_pos1 = pos1 / max(m - 1, 1)
        norm_pos2 = pos2 / max(n - 1, 1)
        norm_pos = (norm_pos1 + norm_pos2) / 2.0
        total_weighted += w(norm_pos)

    max_possible_weight = 0.0
    for idx in range(lcs_len):
        norm_pos = idx / max(lcs_len - 1, 1)
        max_possible_weight += w(norm_pos)

    lcs_fuzzy = total_weighted / max(max_possible_weight, 1e-9)
    return mix * lcs_fuzzy + (1.0 - mix) * lcs_standard


def _cosine_counts(d1: Dict[str, int], d2: Dict[str, int]) -> float:
    keys = set(d1.keys()) | set(d2.keys())
    if not keys:
        return 0.5
    dot = sum(d1.get(k, 0) * d2.get(k, 0) for k in keys)
    n1 = math.sqrt(sum((d1.get(k, 0) ** 2) for k in keys))
    n2 = math.sqrt(sum((d2.get(k, 0) ** 2) for k in keys))
    return dot / (n1 * n2 + 1e-9)


def _oracle_scores(a: FeatureSignature, b: FeatureSignature) -> Dict[str, float]:
    out = {}
    out["author_match"] = 1.0 if a.author and b.author and a.author.strip().lower() == b.author.strip().lower() else 0.0
    out["feature_count_similarity"] = max(0.0, 1.0 - abs(a.feature_count - b.feature_count) / max(a.feature_count, b.feature_count, 1))
    out["feature_type_similarity"] = _cosine_counts(a.feature_types, b.feature_types)
    style_diff = (
        abs(a.extrusion_ratio - b.extrusion_ratio) +
        abs(a.cutout_ratio - b.cutout_ratio) +
        abs(a.hole_ratio - b.hole_ratio) +
        abs(a.round_chamfer_ratio - b.round_chamfer_ratio)
    ) / 4.0
    out["style_similarity"] = 1.0 - min(style_diff, 1.0)
    out["bigram_similarity"] = _jaccard_ngrams(a.feature_sequence, b.feature_sequence, 2)
    out["trigram_similarity"] = _jaccard_ngrams(a.feature_sequence, b.feature_sequence, 3)
    out["lcs_similarity"] = _lcs_len(a.feature_sequence, b.feature_sequence) / max(len(a.feature_sequence), len(b.feature_sequence), 1)
    n1, n2 = _custom_name_set(a.feature_names), _custom_name_set(b.feature_names)
    out["feature_names_similarity"] = len(n1 & n2) / max(len(n1), len(n2), 1)
    out["geometry_2d_similarity"] = _multiset_jaccard(a.geometry_2d_types, b.geometry_2d_types)
    out["constraint_2d_similarity"] = _multiset_jaccard(a.constraint_2d_types, b.constraint_2d_types)
    out["constraint_ratio_similarity"] = max(0.0, 1.0 - abs(a.constraint_to_geometry_ratio - b.constraint_to_geometry_ratio))
    out["sketch_parametric_similarity"] = 0.0  # sketches_data vuoti per costruzione
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=20, help="Numero di coppie sintetiche da verificare (default: 20)")
    ap.add_argument("--seed", type=int, default=42, help="Seed random riproducibile")
    ap.add_argument("--tol", type=float, default=1e-6, help="Tolleranza numerica")
    ap.add_argument("--skip-fuzzy", action="store_true", help="Salta la verifica extra LCS fuzzy")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    latest_file, real_sigs = _load_latest_signatures()
    real_sigs = [s for s in real_sigs if (s.get("feature_sequence") or [])]
    if len(real_sigs) < 5:
        raise RuntimeError("Dataset reale insufficiente per generare componenti sintetici")

    cases = []
    for i in range(args.cases):
        tpl = real_sigs[rng.randrange(0, len(real_sigs))]
        a = _build_component(tpl, i, rng)
        b = _mutate_component(a, i, rng)
        cases.append((a, b))

    fuzzy_off = {"lcs_fuzzy_enabled": False, "lcs_fuzzy_function": "linear", "lcs_fuzzy_alpha": 2.0, "lcs_fuzzy_mix": 0.0}
    mismatches = []
    total_checks = 0
    max_err = 0.0

    for ci, (a, b) in enumerate(cases, start=1):
        calc = compute_raw_scores(a, b, lcs_fuzzy_config=fuzzy_off)
        oracle = _oracle_scores(a, b)
        for k in NUMERIC_KEYS:
            av = float(calc.get(k, 0.0))
            ev = float(oracle.get(k, 0.0))
            err = abs(av - ev)
            total_checks += 1
            max_err = max(max_err, err)
            if err > args.tol:
                mismatches.append({
                    "case": ci,
                    "metric": k,
                    "actual": av,
                    "expected": ev,
                    "abs_error": err,
                    "a_file": a.filename,
                    "b_file": b.filename,
                })

    fuzzy_mismatches = []
    fuzzy_total_checks = 0
    fuzzy_max_err = 0.0
    fuzzy_configs = [
        {"name": "none", "lcs_fuzzy_enabled": False, "lcs_fuzzy_function": "linear", "lcs_fuzzy_alpha": 2.0, "lcs_fuzzy_mix": 0.0},
        {"name": "linear_06", "lcs_fuzzy_enabled": True, "lcs_fuzzy_function": "linear", "lcs_fuzzy_alpha": 2.0, "lcs_fuzzy_mix": 0.6},
        {"name": "exp_07", "lcs_fuzzy_enabled": True, "lcs_fuzzy_function": "exponential", "lcs_fuzzy_alpha": 2.0, "lcs_fuzzy_mix": 0.7},
        {"name": "log_05", "lcs_fuzzy_enabled": True, "lcs_fuzzy_function": "logarithmic", "lcs_fuzzy_alpha": 2.0, "lcs_fuzzy_mix": 0.5},
    ]
    if not args.skip_fuzzy:
        for cfg in fuzzy_configs:
            for ci, (a, b) in enumerate(cases, start=1):
                calc = compute_raw_scores(a, b, lcs_fuzzy_config=cfg)
                ev = _lcs_fuzzy_blended(
                    a.feature_sequence,
                    b.feature_sequence,
                    bool(cfg["lcs_fuzzy_enabled"]),
                    str(cfg["lcs_fuzzy_function"]),
                    float(cfg["lcs_fuzzy_alpha"]),
                    float(cfg["lcs_fuzzy_mix"]),
                )
                av = float(calc.get("lcs_similarity", 0.0))
                err = abs(av - ev)
                fuzzy_total_checks += 1
                fuzzy_max_err = max(fuzzy_max_err, err)
                if err > args.tol:
                    fuzzy_mismatches.append({
                        "config": cfg["name"],
                        "case": ci,
                        "metric": "lcs_similarity",
                        "actual": av,
                        "expected": ev,
                        "abs_error": err,
                        "a_file": a.filename,
                        "b_file": b.filename,
                    })

    report = {
        "source_results_file": str(latest_file),
        "cases": args.cases,
        "seed": args.seed,
        "tolerance": args.tol,
        "total_checks": total_checks,
        "mismatches_count": len(mismatches),
        "max_abs_error": max_err,
        "mismatches": mismatches[:200],
        "fuzzy_checks_total": fuzzy_total_checks,
        "fuzzy_mismatches_count": len(fuzzy_mismatches),
        "fuzzy_max_abs_error": fuzzy_max_err,
        "fuzzy_mismatches": fuzzy_mismatches[:200],
    }

    out_path = ROOT / "debug" / "debug_synthetic_indices_report.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 80)
    print("DEBUG SYNTHETIC INDICES")
    print("=" * 80)
    print(f"Dataset reale sorgente : {latest_file}")
    print(f"Casi sintetici         : {args.cases}")
    print(f"Check totali           : {total_checks}")
    print(f"Mismatch               : {len(mismatches)}")
    print(f"Errore assoluto max    : {max_err:.3e}")
    if not args.skip_fuzzy:
        print(f"Fuzzy checks totali    : {fuzzy_total_checks}")
        print(f"Fuzzy mismatch         : {len(fuzzy_mismatches)}")
        print(f"Fuzzy errore max       : {fuzzy_max_err:.3e}")
    print(f"Report JSON            : {out_path}")
    if mismatches:
        print("\nPrimi mismatch:")
        for m in mismatches[:10]:
            print(f"  - case {m['case']:02d} | {m['metric']}: actual={m['actual']:.6f}, expected={m['expected']:.6f}, err={m['abs_error']:.3e}")
        return 1
    if fuzzy_mismatches:
        print("\nPrimi mismatch fuzzy:")
        for m in fuzzy_mismatches[:10]:
            print(f"  - {m['config']} case {m['case']:02d}: actual={m['actual']:.6f}, expected={m['expected']:.6f}, err={m['abs_error']:.3e}")
        return 1
    print("\nOK: tutti gli indici verificati entro la tolleranza.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
