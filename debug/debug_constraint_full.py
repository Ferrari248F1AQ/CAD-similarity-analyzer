#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Debug per scoprire il VERO nome del tipo di vincolo
Prova tutte le possibili proprietà
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

def debug_constraint_full(filepath_str):
    """Debug completo di ogni vincolo."""

    filepath = Path(filepath_str)
    print(f"\n{'='*80}")
    print(f"🔍 DEBUG COMPLETO VINCOLI")
    print(f"{'='*80}\n")

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
        sketches = doc.Sketches

        type_mapping = {}

        for sk_idx in range(1, sketches.Count + 1):
            sketch = sketches.Item(sk_idx)

            try:
                profiles = sketch.Profiles
                if profiles and profiles.Count > 0:
                    profile = profiles.Item(1)

                    if hasattr(profile, 'Relations2d'):
                        relations = profile.Relations2d
                        if relations and relations.Count > 0:

                            for i in range(1, relations.Count + 1):
                                rel = relations.Item(i)
                                rel_type = rel.Type
                                rel_name = rel.Name

                                # Prova a determinare il tipo dal Name o da altre proprietà
                                # Il Type è un COM enum, convertiamolo in hex per capire meglio
                                type_hex = hex(rel_type & 0xFFFFFFFF)

                                # Prova TypeName se esiste
                                type_name = None
                                for prop in ['TypeName', 'RelationType', 'ConstraintType', 'Kind', 'Category']:
                                    try:
                                        type_name = getattr(rel, prop)
                                        break
                                    except:
                                        pass

                                if rel_type not in type_mapping:
                                    type_mapping[rel_type] = {
                                        'hex': type_hex,
                                        'examples': [],
                                        'type_name': type_name
                                    }

                                if len(type_mapping[rel_type]['examples']) < 3:
                                    type_mapping[rel_type]['examples'].append(rel_name)

            except:
                pass

        # Mostra risultati
        print("📊 TIPI DI VINCOLI TROVATI:")
        print("-"*80)

        # Provo a mappare in base ai pattern noti di Solid Edge
        # Questi sono i valori COM enum tipici
        SOLID_EDGE_RELATION_TYPES = {
            # Basato su documentazione Solid Edge API
            0x2DD09600: "seRelationKeypoint",          # 768508992
            0x104C3870: "seRelationConnect",           # 273497200
            0xFAFD5880: "seRelationRigidSet",          # -83892864
            0xEF4D6A10: "seRelationHorizontal/Vertical", # -280074960
            0x458DA0A0: "seRelationTangent",           # 1166881824
            0x6412C2F0: "seRelationEqual",             # 1679388272
        }

        for type_val, data in sorted(type_mapping.items()):
            # Converti in unsigned
            unsigned_type = type_val & 0xFFFFFFFF
            known_name = SOLID_EDGE_RELATION_TYPES.get(unsigned_type, "Unknown")

            print(f"\n   Type = {type_val}")
            print(f"   Hex  = {data['hex']}")
            print(f"   Guess = {known_name}")
            print(f"   Esempi: {data['examples']}")

        print(f"\n{'='*80}")
        print("✅ DEBUG COMPLETATO")
        print("="*80 + "\n")

    finally:
        doc.Close(False)
        print("✅ File chiuso")

    return True


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Uso: python debug_constraint_full.py <filepath>")
        sys.exit(1)

    filepath = sys.argv[1]
    debug_constraint_full(filepath)

