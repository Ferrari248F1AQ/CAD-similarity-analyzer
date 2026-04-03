#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script COMPLETO per trovare TUTTI i tipi di vincoli in Solid Edge.
Analizza anche i vincoli dimensionali (con Value).
"""

import sys
from pathlib import Path
from collections import defaultdict

try:
    import win32com.client
    HAS_COM = True
except:
    HAS_COM = False
    print("❌ COM non disponibile")
    sys.exit(1)

def analyze_all_constraints(filepath_str):
    """Analizza TUTTI i vincoli in un file."""

    filepath = Path(filepath_str)
    print(f"\n{'='*80}")
    print(f"🔍 ANALISI COMPLETA VINCOLI: {filepath.name}")
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
        return

    try:
        # Raccogli TUTTI i vincoli
        all_constraints = defaultdict(lambda: {
            'count': 0,
            'has_value': False,
            'values': [],
            'examples': []
        })

        # 1. Da Sketches -> Profiles -> Relations2d
        print("📋 1. VINCOLI DA SKETCHES/PROFILES")
        print("-"*80)

        sketches = doc.Sketches
        print(f"   Sketches.Count = {sketches.Count}")

        for sk_idx in range(1, sketches.Count + 1):
            sketch = sketches.Item(sk_idx)
            sketch_name = getattr(sketch, 'Name', f'Sketch_{sk_idx}')

            try:
                profiles = sketch.Profiles
                if profiles and profiles.Count > 0:
                    for p_idx in range(1, profiles.Count + 1):
                        profile = profiles.Item(p_idx)

                        # Relations2d
                        if hasattr(profile, 'Relations2d'):
                            relations = profile.Relations2d
                            if relations and relations.Count > 0:
                                for i in range(1, relations.Count + 1):
                                    try:
                                        rel = relations.Item(i)
                                        rel_type = rel.Type
                                        rel_name = rel.Name

                                        # Prova a ottenere il valore
                                        value = None
                                        has_value = False
                                        if hasattr(rel, 'Value'):
                                            try:
                                                value = float(rel.Value)
                                                has_value = True
                                            except:
                                                pass

                                        # Registra
                                        all_constraints[rel_type]['count'] += 1
                                        if has_value:
                                            all_constraints[rel_type]['has_value'] = True
                                            if len(all_constraints[rel_type]['values']) < 5:
                                                all_constraints[rel_type]['values'].append(value)
                                        if len(all_constraints[rel_type]['examples']) < 3:
                                            all_constraints[rel_type]['examples'].append({
                                                'name': rel_name,
                                                'sketch': sketch_name,
                                                'value': value
                                            })
                                    except:
                                        pass
            except:
                pass

        # 2. Da doc.Constraints (se esiste)
        print("\n📋 2. VINCOLI DA doc.Constraints")
        print("-"*80)

        try:
            if hasattr(doc, 'Constraints'):
                constraints = doc.Constraints
                print(f"   doc.Constraints.Count = {constraints.Count}")

                for i in range(1, min(constraints.Count + 1, 50)):
                    try:
                        c = constraints.Item(i)
                        c_type = c.Type
                        c_name = getattr(c, 'Name', f'Constraint_{i}')

                        value = None
                        has_value = False
                        if hasattr(c, 'Value'):
                            try:
                                value = float(c.Value)
                                has_value = True
                            except:
                                pass

                        all_constraints[c_type]['count'] += 1
                        if has_value:
                            all_constraints[c_type]['has_value'] = True
                            if len(all_constraints[c_type]['values']) < 5:
                                all_constraints[c_type]['values'].append(value)
                        if len(all_constraints[c_type]['examples']) < 3:
                            all_constraints[c_type]['examples'].append({
                                'name': c_name,
                                'sketch': 'doc.Constraints',
                                'value': value
                            })
                    except:
                        pass
            else:
                print("   doc.Constraints non disponibile")
        except Exception as e:
            print(f"   Errore: {e}")

        # 3. Da ProfileSets
        print("\n📋 3. VINCOLI DA PROFILE SETS")
        print("-"*80)

        try:
            profile_sets = doc.ProfileSets
            print(f"   ProfileSets.Count = {profile_sets.Count}")

            for ps_idx in range(1, profile_sets.Count + 1):
                ps = profile_sets.Item(ps_idx)
                profiles = ps.Profiles

                for p_idx in range(1, profiles.Count + 1):
                    profile = profiles.Item(p_idx)

                    if hasattr(profile, 'Relations2d'):
                        relations = profile.Relations2d
                        if relations and relations.Count > 0:
                            for i in range(1, relations.Count + 1):
                                try:
                                    rel = relations.Item(i)
                                    rel_type = rel.Type

                                    value = None
                                    has_value = False
                                    if hasattr(rel, 'Value'):
                                        try:
                                            value = float(rel.Value)
                                            has_value = True
                                        except:
                                            pass

                                    # Solo aggiungi se non già contato
                                    # (potrebbe essere lo stesso di Sketches)
                                except:
                                    pass
        except Exception as e:
            print(f"   Errore: {e}")

        # RIEPILOGO FINALE
        print(f"\n\n{'='*80}")
        print("📊 RIEPILOGO COMPLETO VINCOLI")
        print("="*80)

        # Separa geometrici da dimensionali
        geometrici = []
        dimensionali = []

        for type_val, data in sorted(all_constraints.items()):
            entry = {
                'type': type_val,
                'count': data['count'],
                'has_value': data['has_value'],
                'values': data['values'],
                'examples': data['examples']
            }

            if data['has_value']:
                dimensionali.append(entry)
            else:
                geometrici.append(entry)

        print(f"\n🔵 VINCOLI GEOMETRICI (senza valore numerico): {len(geometrici)}")
        print("-"*80)
        for item in geometrici:
            print(f"\n   Type = {item['type']}")
            print(f"   Occorrenze: {item['count']}")
            for ex in item['examples']:
                print(f"   Esempio: {ex['name']} (in {ex['sketch']})")

        print(f"\n🟠 VINCOLI DIMENSIONALI (con valore numerico): {len(dimensionali)}")
        print("-"*80)
        for item in dimensionali:
            print(f"\n   Type = {item['type']}")
            print(f"   Occorrenze: {item['count']}")
            print(f"   Valori esempio: {item['values']}")
            for ex in item['examples']:
                print(f"   Esempio: {ex['name']} = {ex['value']} (in {ex['sketch']})")

        # Genera codice Python per il mapping
        print(f"\n\n{'='*80}")
        print("📝 CODICE PYTHON PER IL MAPPING")
        print("="*80)

        print("\n# Copia questo nel file solid_edge_similarity_v2.py:")
        print("\nCONSTRAINT_2D_TYPE_MAP = {")
        for item in geometrici + dimensionali:
            tipo = "Dimensionale" if item['has_value'] else "Geometrico"
            nome = item['examples'][0]['name'] if item['examples'] else f"Type_{item['type']}"
            nome_clean = nome.replace('Relation2d ', '').split('_')[0] if 'Relation2d' in nome else nome
            print(f"    {item['type']}: \"{nome_clean}\",  # {tipo}, {item['count']} occorrenze")
        print("}")

        print("\nCONSTRAINT_DESCRIPTIONS = {")
        for item in geometrici:
            print(f'    {item["type"]}: {{"categoria": "Geometrico", "descrizione": "TODO", "tipo": "geometrico"}},')
        for item in dimensionali:
            print(f'    {item["type"]}: {{"categoria": "Dimensionale", "descrizione": "TODO", "tipo": "dimensionale"}},')
        print("}")

    finally:
        doc.Close(False)
        print("\n✅ File chiuso")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Uso: python debug_all_constraints.py <filepath>")
        print("\nQuesto script analizza TUTTI i vincoli in un file e genera")
        print("il codice Python per il mapping completo.")
        sys.exit(1)

    filepath = sys.argv[1]
    analyze_all_constraints(filepath)

