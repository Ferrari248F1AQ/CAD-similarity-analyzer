#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Debug per scoprire i VERI valori di Type dei vincoli in Solid Edge
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    import win32com.client
    HAS_COM = True
except:
    HAS_COM = False
    print("❌ COM non disponibile")
    sys.exit(1)

def debug_constraint_types(filepath_str):
    """Debug per scoprire i veri Type dei vincoli."""

    filepath = Path(filepath_str)
    print(f"\n{'='*80}")
    print(f"🔍 DEBUG TIPI VINCOLI: {filepath.name}")
    print(f"{'='*80}\n")

    if not filepath.exists():
        print(f"❌ File non trovato: {filepath}")
        return False

    # Connetti Solid Edge
    try:
        app = win32com.client.GetActiveObject("SolidEdge.Application")
        print("✅ Connesso a Solid Edge")
    except:
        app = win32com.client.Dispatch("SolidEdge.Application")
        app.Visible = True
        print("✅ Avviato Solid Edge")

    app.DisplayAlerts = False

    # Apri file
    try:
        doc = app.Documents.Open(str(filepath))
        print(f"✅ File aperto: {filepath.name}\n")
    except Exception as e:
        print(f"❌ Errore apertura file: {e}")
        return False

    try:
        # Esplora sketches
        print("📋 VINCOLI NEI PROFILE")
        print("-" * 80)

        sketches = doc.Sketches
        print(f"Sketches.Count = {sketches.Count}")

        all_constraint_types = {}

        for sk_idx in range(1, sketches.Count + 1):
            sketch = sketches.Item(sk_idx)
            sketch_name = getattr(sketch, 'Name', f'Sketch_{sk_idx}')
            print(f"\n📋 {sketch_name}")

            # Verifica Profile
            try:
                profiles = sketch.Profiles
                if profiles and hasattr(profiles, 'Count') and profiles.Count > 0:
                    for p_idx in range(1, profiles.Count + 1):
                        profile = profiles.Item(p_idx)

                        if hasattr(profile, 'Relations2d'):
                            relations = profile.Relations2d
                            if relations and hasattr(relations, 'Count') and relations.Count > 0:
                                print(f"   Profile {p_idx} - Relations2d.Count = {relations.Count}")

                                for i in range(1, min(relations.Count + 1, 20)):  # Max 20 per profile
                                    try:
                                        rel = relations.Item(i)
                                        rel_type = getattr(rel, 'Type', -999)

                                        # Prova a ottenere anche altre proprietà
                                        rel_name = getattr(rel, 'Name', 'N/A')
                                        has_value = hasattr(rel, 'Value')
                                        value = None
                                        if has_value:
                                            try:
                                                value = float(rel.Value)
                                            except:
                                                pass

                                        print(f"      [{i}] Type = {rel_type}, Name = {rel_name}, HasValue = {has_value}, Value = {value}")

                                        # Colleziona i tipi
                                        if rel_type not in all_constraint_types:
                                            all_constraint_types[rel_type] = {
                                                'count': 0,
                                                'examples': []
                                            }
                                        all_constraint_types[rel_type]['count'] += 1
                                        if len(all_constraint_types[rel_type]['examples']) < 3:
                                            all_constraint_types[rel_type]['examples'].append({
                                                'name': rel_name,
                                                'value': value
                                            })
                                    except Exception as e:
                                        print(f"      [{i}] Errore: {e}")
                            else:
                                print(f"   Profile {p_idx} - Relations2d vuoto")
            except Exception as e:
                print(f"   Errore Profile: {e}")

        # Riepilogo
        print(f"\n\n{'='*80}")
        print("📊 RIEPILOGO TIPI DI VINCOLI TROVATI")
        print("="*80)

        for type_val, data in sorted(all_constraint_types.items()):
            print(f"\n   Type = {type_val} → {data['count']} occorrenze")
            for ex in data['examples']:
                print(f"      Esempio: Name={ex['name']}, Value={ex['value']}")

        print(f"\n{'='*80}")
        print("✅ DEBUG COMPLETATO")
        print("="*80 + "\n")

        print("💡 USA QUESTI VALORI PER AGGIORNARE IL MAPPING:")
        print("   Copia i valori di 'Type' e crea il mapping corretto!")

    finally:
        doc.Close(False)
        print("✅ File chiuso")

    return True


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Uso: python debug_constraint_types.py <filepath>")
        print("Esempio: python debug_constraint_types.py C:\\path\\file.par")
        sys.exit(1)

    filepath = sys.argv[1]
    success = debug_constraint_types(filepath)
    sys.exit(0 if success else 1)

