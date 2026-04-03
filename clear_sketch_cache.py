"""
Pulisce la cache degli sketch per forzare la ri-estrazione con parametric_frame.
"""
import shutil
from pathlib import Path

CACHE_DIR = Path.home() / '.cache' / 'solid_edge_similarity'

if CACHE_DIR.exists():
    print(f"Eliminazione cache: {CACHE_DIR}")

    # Conta i file
    cache_files = list(CACHE_DIR.glob('sketch_data_*.json'))
    print(f"  File da eliminare: {len(cache_files)}")

    # Elimina
    shutil.rmtree(CACHE_DIR)
    print(f"  [OK] Cache eliminata!")

    # Ricrea directory vuota
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  [OK] Directory cache ricreata vuota")
else:
    print(f"La cache non esiste: {CACHE_DIR}")

print("\nAdesso rianalizza i file CAD per rigenerare la cache con parametric_frame.")
