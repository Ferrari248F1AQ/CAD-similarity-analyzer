#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Debug script per verificare che geometry_detailed e constraint_detailed
vengono estratti e salvati correttamente
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from solid_edge_similarity_v2 import (
    extract_signature,
    load_sketch_data,
    HAS_COM
)

def debug_sketch_extraction(filepath_str):
    """Estrae e stampa i dettagli sketch."""

    filepath = Path(filepath_str)
    print(f"\n{'='*70}")
    print(f"🔍 DEBUG: Estrazione Sketch da {filepath.name}")
    print(f"{'='*70}\n")

    if not filepath.exists():
        print(f"❌ File non trovato: {filepath}")
        return False

    # Estrai signature
    print("1️⃣ Estrazione signature da file...")
    try:
        sig = extract_signature(filepath, app=None)
        print(f"✅ Signature estratta")
    except Exception as e:
        print(f"❌ Errore estrazione: {e}")
        return False

    # Verifica sketches_data
    print(f"\n2️⃣ Verifica sketches_data...")
    print(f"   - sketches_count: {sig.sketches_count}")
    print(f"   - total_2d_geometry_count: {sig.total_2d_geometry_count}")
    print(f"   - total_2d_constraint_count: {sig.total_2d_constraint_count}")
    print(f"   - sketches_data length: {len(sig.sketches_data)}")

    if not sig.sketches_data:
        print("   ⚠️ sketches_data è vuota!")
        return False

    # Per ogni sketch, verifica i dettagli
    print(f"\n3️⃣ Verifica dettagli per ogni sketch...")
    for i, sketch in enumerate(sig.sketches_data):
        print(f"\n   📋 Sketch {i+1}: {sketch.get('name', 'Unknown')}")
        print(f"      - geometry_count: {sketch.get('geometry_count', 0)}")
        print(f"      - geometry_detailed: {len(sketch.get('geometry_detailed', []))} elementi")
        print(f"      - constraint_count: {sketch.get('constraint_count', 0)}")
        print(f"      - constraint_detailed: {len(sketch.get('constraint_detailed', []))} elementi")

        # Stampa primi geometry_detailed
        geom_list = sketch.get('geometry_detailed', [])
        if geom_list:
            print(f"\n      ✅ Geometrie (primi 5):")
            for geom in geom_list[:5]:
                print(f"         - {geom.get('id', 'N/A')}: {geom.get('type', 'Unknown')}")
            if len(geom_list) > 5:
                print(f"         ... e {len(geom_list)-5} altri")
        else:
            print(f"      ⚠️ Nessun geometry_detailed trovato!")

        # Stampa primi constraint_detailed
        const_list = sketch.get('constraint_detailed', [])
        if const_list:
            print(f"\n      ✅ Vincoli (primi 5):")
            for constraint in const_list[:5]:
                val = f" = {constraint.get('value')}" if constraint.get('value') is not None else ""
                print(f"         - {constraint.get('id', 'N/A')}: {constraint.get('type', 'Unknown')}{val}")
            if len(const_list) > 5:
                print(f"         ... e {len(const_list)-5} altri")
        else:
            print(f"      ⚠️ Nessun constraint_detailed trovato!")

    # Verifica cache
    print(f"\n4️⃣ Verifica cache...")
    cached_data = load_sketch_data(filepath_str)
    if cached_data:
        print(f"✅ Cache trovata con {len(cached_data)} sketch")
        # Verifica primo sketch in cache
        if cached_data:
            first_sketch = cached_data[0]
            print(f"   - geometry_detailed: {len(first_sketch.get('geometry_detailed', []))} elementi")
            print(f"   - constraint_detailed: {len(first_sketch.get('constraint_detailed', []))} elementi")
    else:
        print(f"⚠️ Cache non trovata o vuota")

    # Stampa JSON per verifica finale
    print(f"\n5️⃣ Verifica JSON struttura...")
    print(f"   sketches_data è lista: {isinstance(sig.sketches_data, list)}")
    if sig.sketches_data:
        first = sig.sketches_data[0]
        print(f"   Primo sketch ha 'geometry_detailed': {'geometry_detailed' in first}")
        print(f"   Primo sketch ha 'constraint_detailed': {'constraint_detailed' in first}")

        # Mostra struttura JSON
        print(f"\n   Primo sketch JSON preview:")
        preview = {
            'name': first.get('name'),
            'geometry_count': first.get('geometry_count'),
            'geometry_detailed_count': len(first.get('geometry_detailed', [])),
            'constraint_count': first.get('constraint_count'),
            'constraint_detailed_count': len(first.get('constraint_detailed', [])),
            'geometry_detailed_sample': first.get('geometry_detailed', [])[:2] if first.get('geometry_detailed') else [],
            'constraint_detailed_sample': first.get('constraint_detailed', [])[:2] if first.get('constraint_detailed') else [],
        }
        print(json.dumps(preview, indent=2, default=str))

    print(f"\n{'='*70}")
    print(f"✅ DEBUG COMPLETATO")
    print(f"{'='*70}\n")

    return True


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Uso: python debug_sketch_extraction.py <filepath>")
        print("Esempio: python debug_sketch_extraction.py C:\\path\\file.par")
        sys.exit(1)

    filepath = sys.argv[1]

    if HAS_COM:
        print(f"✅ COM disponibile - estrazione completa")
    else:
        print(f"⚠️ COM non disponibile - estrazione limitata")

    success = debug_sketch_extraction(filepath)
    sys.exit(0 if success else 1)

