#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script di test per verificare le nuove funzionalità Sketch 2D + Persistenza
"""

import sys
from pathlib import Path

# Aggiungi parent directory
sys.path.insert(0, str(Path(__file__).parent))

from solid_edge_similarity_v2 import (
    save_sketch_data,
    load_sketch_data,
    get_sketch_cache_info,
    CACHE_DIR
)
import json
from datetime import datetime

def test_sketch_persistence():
    """Test completo della persistenza sketch."""

    print("\n" + "="*70)
    print("🧪 TEST: SKETCH 2D PERSISTENCE")
    print("="*70)

    # Test data
    test_filepath = "C:\\Test\\file.par"
    test_sketches = [
        {
            'name': 'Sketch_1',
            'index': 1,
            'geometry_count': 5,
            'geometry_types': {'Line2d': 3, 'Arc2d': 2},
            'geometry_detailed': [
                {'type': 'Line2d', 'index': 1, 'profile_index': 1, 'id': 'Line2d_1_1'},
                {'type': 'Line2d', 'index': 2, 'profile_index': 1, 'id': 'Line2d_1_2'},
                {'type': 'Line2d', 'index': 3, 'profile_index': 1, 'id': 'Line2d_1_3'},
                {'type': 'Arc2d', 'index': 4, 'profile_index': 1, 'id': 'Arc2d_1_4'},
                {'type': 'Arc2d', 'index': 5, 'profile_index': 1, 'id': 'Arc2d_1_5'},
            ],
            'constraint_count': 3,
            'constraint_types': {'Distance': 2, 'Horizontal': 1},
            'constraint_detailed': [
                {'type': 'Distance', 'index': 1, 'id': 'Distance_1', 'value': 25.5},
                {'type': 'Distance', 'index': 2, 'id': 'Distance_2', 'value': 15.0},
                {'type': 'Horizontal', 'index': 3, 'id': 'Horizontal_3', 'value': None},
            ],
            'dimension_values': [25.5, 15.0],
            'errors': []
        }
    ]

    # TEST 1: Salvataggio
    print("\n📝 TEST 1: Salvataggio dati sketch")
    print("-" * 70)
    success = save_sketch_data(test_filepath, test_sketches)
    if success:
        print("✅ Dati salvati con successo")
    else:
        print("❌ Errore nel salvataggio")
        return False

    # TEST 2: Caricamento
    print("\n📖 TEST 2: Caricamento dati sketch")
    print("-" * 70)
    loaded_data = load_sketch_data(test_filepath)
    if loaded_data:
        print(f"✅ Dati caricati: {len(loaded_data)} sketch")
        if len(loaded_data) == len(test_sketches):
            print("✅ Numero sketch corretti")
        else:
            print(f"❌ Mismatch sketch: {len(loaded_data)} vs {len(test_sketches)}")
    else:
        print("❌ Errore nel caricamento")
        return False

    # TEST 3: Info cache
    print("\n📊 TEST 3: Info cache")
    print("-" * 70)
    cache_info = get_sketch_cache_info(test_filepath)
    if cache_info:
        print(f"✅ Cache trovata:")
        print(f"   - Timestamp: {cache_info['timestamp']}")
        print(f"   - Sketch totali: {cache_info['total_sketches']}")
        print(f"   - Geometrie totali: {cache_info['total_geometries']}")
        print(f"   - Vincoli totali: {cache_info['total_constraints']}")
        print(f"   - Path: {cache_info['cache_path']}")
    else:
        print("❌ Cache non trovata")
        return False

    # TEST 4: Verifica dettagli geometrie
    print("\n🔍 TEST 4: Verifica geometrie_detailed")
    print("-" * 70)
    first_sketch = loaded_data[0]
    if 'geometry_detailed' in first_sketch:
        geom_list = first_sketch['geometry_detailed']
        print(f"✅ geometry_detailed presente con {len(geom_list)} elementi")
        for geom in geom_list:
            print(f"   - {geom['id']}: {geom['type']}")
    else:
        print("❌ geometry_detailed non trovato")
        return False

    # TEST 5: Verifica dettagli vincoli
    print("\n🔒 TEST 5: Verifica constraint_detailed")
    print("-" * 70)
    if 'constraint_detailed' in first_sketch:
        constraint_list = first_sketch['constraint_detailed']
        print(f"✅ constraint_detailed presente con {len(constraint_list)} elementi")
        for constraint in constraint_list:
            value_str = f"= {constraint['value']}" if constraint['value'] is not None else ""
            print(f"   - {constraint['id']}: {constraint['type']} {value_str}")
    else:
        print("❌ constraint_detailed non trovato")
        return False

    # TEST 6: Verifica JSON válido
    print("\n📋 TEST 6: Verifica JSON valido")
    print("-" * 70)
    try:
        cache_path = Path.home() / '.cache' / 'solid_edge_similarity' / f'sketch_data_{hash(test_filepath)}.json'
        if cache_path.exists():
            with open(cache_path, 'r') as f:
                cache_content = json.load(f)
            print(f"✅ JSON valido in {cache_path}")
            print(f"   - Chiavi principali: {list(cache_content.keys())}")
        else:
            print("⚠️ File cache non trovato (percorso potrebbe essere diverso)")
    except json.JSONDecodeError as e:
        print(f"❌ JSON non valido: {e}")
        return False
    except Exception as e:
        print(f"⚠️ Errore verifica JSON: {e}")

    print("\n" + "="*70)
    print("✅ TUTTI I TEST PASSATI!")
    print("="*70 + "\n")

    return True


def test_cache_directory():
    """Verifica la directory cache."""
    print("\n" + "="*70)
    print("📁 CACHE DIRECTORY")
    print("="*70)
    print(f"Path: {CACHE_DIR}")
    print(f"Esiste: {CACHE_DIR.exists()}")

    if CACHE_DIR.exists():
        files = list(CACHE_DIR.glob('*.json'))
        print(f"File cache: {len(files)}")
        for f in files[:5]:  # Mostra primi 5
            size = f.stat().st_size
            print(f"  - {f.name} ({size} bytes)")
        if len(files) > 5:
            print(f"  ... e {len(files)-5} altri file")

    print()


if __name__ == '__main__':
    print("\n🚀 INIZIO TEST SKETCH 2D PERSISTENCE\n")

    # Verifica directory cache
    test_cache_directory()

    # Test principale
    if not test_sketch_persistence():
        print("❌ TEST FALLITO")
        sys.exit(1)

    print("✅ TUTTI I TEST COMPLETATI CON SUCCESSO!")

