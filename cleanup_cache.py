#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script di pulizia della cache: trova e rimuove file JSON corrotti.
"""

import json
from pathlib import Path
import sys

def check_json_file(filepath: Path) -> tuple[bool, str]:
    """
    Controlla se un file JSON è valido.

    Returns:
        (is_valid, error_message)
    """
    try:
        # Controlla se vuoto
        if filepath.stat().st_size == 0:
            return False, "File vuoto"

        # Leggi contenuto
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        if not content.strip():
            return False, "Contiene solo whitespace"

        # Prova a parsare
        json.loads(content)
        return True, ""

    except json.JSONDecodeError as e:
        return False, f"JSON invalido: {e}"
    except Exception as e:
        return False, f"Errore: {e}"


def cleanup_cache_directory(cache_dir: Path, dry_run: bool = True) -> dict:
    """
    Pulisce una directory cache rimuovendo file JSON corrotti.

    Args:
        cache_dir: Directory da pulire
        dry_run: Se True, mostra solo cosa verrebbe fatto senza eliminare

    Returns:
        Statistiche della pulizia
    """
    stats = {
        'total_files': 0,
        'valid_files': 0,
        'corrupted_files': 0,
        'deleted_files': 0,
        'errors': []
    }

    if not cache_dir.exists():
        print(f"⚠️ Directory non trovata: {cache_dir}")
        return stats

    print(f"\n🔍 Scansione: {cache_dir}")
    print(f"   Modalità: {'DRY RUN (simulazione)' if dry_run else 'ELIMINAZIONE REALE'}\n")

    # Trova tutti i file JSON
    json_files = list(cache_dir.rglob('*.json'))
    stats['total_files'] = len(json_files)

    for filepath in json_files:
        is_valid, error_msg = check_json_file(filepath)

        if is_valid:
            stats['valid_files'] += 1
            print(f"✅ OK: {filepath.name}")
        else:
            stats['corrupted_files'] += 1
            print(f"❌ CORROTTO: {filepath.name}")
            print(f"   Motivo: {error_msg}")

            if not dry_run:
                try:
                    # Backup prima di eliminare
                    import time
                    backup_path = filepath.with_suffix(f'.json.corrupted.{int(time.time())}')
                    filepath.rename(backup_path)
                    stats['deleted_files'] += 1
                    print(f"   ↳ Spostato in: {backup_path.name}")
                except Exception as e:
                    error = f"Errore spostamento {filepath.name}: {e}"
                    stats['errors'].append(error)
                    print(f"   ⚠️ {error}")
            else:
                print(f"   ↳ Verrebbe spostato (dry run)")

    return stats


def main():
    """Entry point dello script."""
    import argparse

    parser = argparse.ArgumentParser(description="Pulisce cache JSON corrotti")
    parser.add_argument('--cache-dir', type=Path, help='Directory cache da pulire')
    parser.add_argument('--execute', action='store_true',
                       help='Esegui realmente (senza questo flag è dry run)')
    parser.add_argument('--all', action='store_true',
                       help='Pulisce tutte le cache note')

    args = parser.parse_args()

    # Directory cache da controllare
    cache_dirs = []

    if args.cache_dir:
        cache_dirs.append(args.cache_dir)
    elif args.all:
        # Directory cache note
        home = Path.home()
        cache_dirs = [
            home / '.cache' / 'solid_edge_similarity',
            home / '.cache' / 'cad_similarity_analyzer',
            Path(__file__).parent / 'debug',
            Path(__file__).parent / 'webapp' / 'cache'
        ]
    else:
        # Default: solo cache principale
        cache_dirs.append(Path.home() / '.cache' / 'solid_edge_similarity')

    # Modalità
    dry_run = not args.execute
    if dry_run:
        print("\n⚠️  MODALITÀ DRY RUN (simulazione)")
        print("    I file non verranno eliminati")
        print("    Usa --execute per eliminare realmente\n")
    else:
        print("\n⚠️  MODALITÀ ESECUZIONE REALE")
        print("    I file corrotti verranno spostati")
        print("    Backup creati con estensione .corrupted.<timestamp>\n")

        # Conferma
        risposta = input("Continuare? [s/N]: ")
        if risposta.lower() not in ('s', 'si', 'sì', 'y', 'yes'):
            print("Operazione annullata.")
            return

    # Pulisci ogni directory
    total_stats = {
        'total_files': 0,
        'valid_files': 0,
        'corrupted_files': 0,
        'deleted_files': 0,
        'errors': []
    }

    for cache_dir in cache_dirs:
        stats = cleanup_cache_directory(cache_dir, dry_run)

        # Aggrega statistiche
        for key in ['total_files', 'valid_files', 'corrupted_files', 'deleted_files']:
            total_stats[key] += stats[key]
        total_stats['errors'].extend(stats['errors'])

    # Riepilogo finale
    print("\n" + "="*60)
    print("📊 RIEPILOGO")
    print("="*60)
    print(f"File totali:     {total_stats['total_files']}")
    print(f"File validi:     {total_stats['valid_files']}")
    print(f"File corrotti:   {total_stats['corrupted_files']}")

    if not dry_run:
        print(f"File spostati:   {total_stats['deleted_files']}")

    if total_stats['errors']:
        print(f"\n⚠️ Errori: {len(total_stats['errors'])}")
        for error in total_stats['errors']:
            print(f"   - {error}")

    print()

    if total_stats['corrupted_files'] > 0 and dry_run:
        print("💡 Per eliminare i file corrotti, riesegui con --execute")


if __name__ == '__main__':
    main()
