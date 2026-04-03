#!/usr/bin/env python3
"""
keep_by_keywords.py

Script per eliminare (o simulare l'eliminazione) ricorsiva di file in una cartella
che NON contengono specifiche parole chiave nel nome del file.

Comportamento:
- Normalizza i nomi (lowercase, rimozione accenti) e considera substring match
  e matching fuzzy (SequenceMatcher) per gestire parti mancanti o errori.
- Di default fa una prova (--dry-run). Usare --yes per eseguire senza prompt.

Esempio:
  python keep_by_keywords.py "C:\\percorso\\alla\\cartella" --dry-run

"""
from __future__ import annotations
import argparse
import os
import sys
import unicodedata
import shutil
from difflib import SequenceMatcher
from typing import List, Tuple

DEFAULT_KEYWORDS = ["esame", "esercitazione", "esercizio"]


def normalize_text(s: str) -> str:
    """Rimuove accenti, porta a lowercase e sostituisce caratteri non alfanumerici con spazi."""
    s = s.lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    # Sostituisci tutto ciò che non è alfanumerico con spazio
    cleaned = []
    for ch in s:
        if ch.isalnum():
            cleaned.append(ch)
        else:
            cleaned.append(" ")
    return "".join(cleaned)


def best_similarity(a: str, b: str) -> float:
    """Restituisce la similarità fra due stringhe usando SequenceMatcher.
    Valore tra 0 e 1."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def filename_matches_keywords(filename: str, keywords: List[str], threshold: float) -> Tuple[bool, float]:
    """
    Controlla se filename (senza percorso, senza estensione preferibilmente) corrisponde
    a una qualsiasi keyword. Ritorna (match, best_score).

    Matching logica:
    - normalizza filename e keyword
    - se keyword è substring -> match
    - altrimenti calcola similarity tra keyword e l'intero nome e fra keyword e "token" del nome
      (split su spazi) e prende il massimo. Se >= threshold -> match
    """
    name_no_ext = os.path.splitext(filename)[0]
    norm_name = normalize_text(name_no_ext)
    tokens = [t for t in norm_name.split() if t]

    best = 0.0

    for kw in keywords:
        norm_kw = normalize_text(kw)
        if not norm_kw:
            continue
        # substring check
        if norm_kw in norm_name:
            return True, 1.0
        # similarity checks
        best = max(best, best_similarity(norm_name, norm_kw))
        for t in tokens:
            best = max(best, best_similarity(t, norm_kw))
        # also check if kw startswith or partial begins
        # (SequenceMatcher dovrebbe coprire parti mancanti, ma questo aiuta con frammenti)
        if norm_name.startswith(norm_kw) or norm_name.endswith(norm_kw):
            return True, 1.0

    return (best >= threshold, best)


def collect_files_to_delete(root: str, keywords: List[str], threshold: float) -> List[Tuple[str, float]]:
    """Scorre la cartella root ricorsivamente e ritorna la lista di file (path, score)
    che NON corrispondono a nessuna keyword (ovvero da cancellare)."""
    to_delete = []
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            match, score = filename_matches_keywords(fn, keywords, threshold)
            if not match:
                to_delete.append((full, score))
    return to_delete


def confirm(prompt: str) -> bool:
    try:
        resp = input(prompt + " [y/N]: ")
    except EOFError:
        return False
    return resp.strip().lower() in ("y", "yes", "s", "si")


def delete_files(files: List[str], dry_run: bool) -> Tuple[int, int]:
    """Elimina i file passati. Se dry_run True non elimina nulla ma conta.
    Ritorna (deleted_count, failed_count)."""
    deleted = 0
    failed = 0
    for p in files:
        try:
            if dry_run:
                print("[DRY-RUN] would remove:", p)
            else:
                os.remove(p)
                print("removed:", p)
                deleted += 1
        except Exception as e:
            print(f"failed to remove {p}: {e}")
            failed += 1
    return deleted, failed


def parse_keywords(s: str) -> List[str]:
    if not s:
        return DEFAULT_KEYWORDS
    parts = [p.strip() for p in s.split(",") if p.strip()]
    return parts or DEFAULT_KEYWORDS


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Elimina file che NON contengono certe parole chiave nel nome (ricorsivo)."
    )
    parser.add_argument("root", help="Cartella root da processare")
    parser.add_argument(
        "--keywords",
        "-k",
        help=("Coma-separated keywords da tenere (default: esame, esercitazione, esercizio)"),
    )
    parser.add_argument(
        "--threshold",
        "-t",
        type=float,
        default=0.72,
        help=("Soglia di similarità fuzzy (0..1) per considerare una corrispondenza; default=0.72"),
    )
    parser.add_argument("--dry-run", action="store_true", help="Non cancellare, solo mostrare cosa sarebbe cancellato")
    parser.add_argument("--yes", "-y", action="store_true", help="Eseguire senza chiedere conferma (usa con cautela)")

    args = parser.parse_args(argv)

    root = args.root
    if not os.path.isdir(root):
        print(f"Errore: {root} non è una directory valida.")
        return 2

    keywords = parse_keywords(args.keywords)

    print("Keywords:", keywords)
    print("Soglia fuzzy:", args.threshold)
    print("Root:", root)
    print("Modalità:", "dry-run" if args.dry_run else "delete")

    to_delete = collect_files_to_delete(root, keywords, args.threshold)

    if not to_delete:
        print("Nessun file da cancellare trovato.")
        return 0

    # Ordina per score crescente (meno simili prima)
    to_delete.sort(key=lambda x: x[1])

    print(f"Trovati {len(to_delete)} file che NON corrispondono alle keywords.\n")
    # Mostra i primi 50 per riepilogo
    for p, score in to_delete[:50]:
        print(f"{p}  (best_score={score:.3f})")
    if len(to_delete) > 50:
        print("... e altri", len(to_delete) - 50)

    if args.dry_run:
        print("Esecuzione in dry-run: nessun file verrà rimosso.")
    if not args.yes and not args.dry_run:
        ok = confirm("Procedere con la cancellazione dei file elencati?")
        if not ok:
            print("Annullato dall'utente.")
            return 1

    files_only = [p for p, _ in to_delete]
    deleted, failed = delete_files(files_only, args.dry_run)

    print(f"Risultato: deleted={deleted}, failed={failed}, total_candidates={len(files_only)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

