#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script APPROFONDITO per trovare TUTTI i vincoli, inclusi quelli dimensionali.
Cerca in OGNI possibile posizione nel documento.
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

def explore_object(obj, name, depth=0, max_depth=3):
    """Esplora ricorsivamente un oggetto COM cercando vincoli/dimensioni."""
    if depth > max_depth:
        return []

    results = []
    indent = "  " * depth

    # Attributi da cercare
    search_attrs = [
        'Constraints', 'Relations', 'Relations2d', 'Relations3d',
        'Dimensions', 'Dimensions2d', 'Dimensions3d',
        'Variables', 'SmartDimensions', 'DrivenDimensions',
        'LinearDimensions', 'AngularDimensions', 'RadialDimensions',
        'DiameterDimensions', 'PMI', 'Annotations'
    ]

    for attr in search_attrs:
        try:
            coll = getattr(obj, attr)
            if coll and hasattr(coll, 'Count') and coll.Count > 0:
                results.append({
                    'path': f"{name}.{attr}",
                    'count': coll.Count,
                    'collection': coll
                })
        except:
            pass

    return results


def find_all_dimensions(filepath_str):
    """Cerca TUTTE le dimensioni in un file."""

    filepath = Path(filepath_str)
    print(f"\n{'='*80}")
    print(f"🔍 RICERCA APPROFONDITA VINCOLI/DIMENSIONI")
    print(f"   File: {filepath.name}")
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
        print(f"✅ File aperto\n")
    except Exception as e:
        print(f"❌ Errore apertura file: {e}")
        return

    try:
        all_found = []

        # 1. CERCA NEL DOCUMENTO
        print("📋 1. RICERCA NEL DOCUMENTO")
        print("-"*80)

        doc_attrs = [
            'Constraints', 'Variables', 'Dimensions',
            'Relations2d', 'Relations3d'
        ]

        for attr in doc_attrs:
            try:
                coll = getattr(doc, attr)
                if coll and hasattr(coll, 'Count'):
                    print(f"   doc.{attr}.Count = {coll.Count}")
                    if coll.Count > 0:
                        all_found.append(('doc', attr, coll))
            except Exception as e:
                print(f"   doc.{attr} → Non disponibile")

        # 2. CERCA NEI MODELLI
        print("\n📋 2. RICERCA NEI MODELLI")
        print("-"*80)

        try:
            models = doc.Models
            print(f"   Models.Count = {models.Count}")

            for m_idx in range(1, models.Count + 1):
                model = models.Item(m_idx)
                model_name = getattr(model, 'Name', f'Model_{m_idx}')

                model_attrs = [
                    'Features', 'Dimensions', 'Variables',
                    'ExtrudedProtrusions', 'ExtrudedCutouts', 'Holes'
                ]

                for attr in model_attrs:
                    try:
                        coll = getattr(model, attr)
                        if coll and hasattr(coll, 'Count') and coll.Count > 0:
                            print(f"   model.{attr}.Count = {coll.Count}")
                            all_found.append(('model', attr, coll))
                    except:
                        pass
        except Exception as e:
            print(f"   Errore: {e}")

        # 3. CERCA NEGLI SKETCH/PROFILE con più dettaglio
        print("\n📋 3. RICERCA NEGLI SKETCH (DETTAGLIATA)")
        print("-"*80)

        sketches = doc.Sketches
        print(f"   Sketches.Count = {sketches.Count}")

        for sk_idx in range(1, sketches.Count + 1):
            sketch = sketches.Item(sk_idx)
            sketch_name = getattr(sketch, 'Name', f'Sketch_{sk_idx}')
            print(f"\n   📋 {sketch_name}")

            # Cerca direttamente nello sketch
            sketch_attrs = [
                'Constraints', 'Dimensions', 'Relations2d',
                'Variables', 'SmartDimensions'
            ]

            for attr in sketch_attrs:
                try:
                    coll = getattr(sketch, attr)
                    if coll and hasattr(coll, 'Count') and coll.Count > 0:
                        print(f"      sketch.{attr}.Count = {coll.Count}")
                        all_found.append((sketch_name, attr, coll))
                except:
                    pass

            # Cerca nei Profile
            try:
                profiles = sketch.Profiles
                for p_idx in range(1, profiles.Count + 1):
                    profile = profiles.Item(p_idx)

                    profile_attrs = [
                        'Relations2d', 'Dimensions', 'Constraints',
                        'SmartDimensions', 'Variables'
                    ]

                    for attr in profile_attrs:
                        try:
                            coll = getattr(profile, attr)
                            if coll and hasattr(coll, 'Count') and coll.Count > 0:
                                print(f"      profile[{p_idx}].{attr}.Count = {coll.Count}")
                                all_found.append((f"{sketch_name}_Profile_{p_idx}", attr, coll))
                        except:
                            pass
            except:
                pass

        # 4. CERCA NELLE VARIABILI GLOBALI
        print("\n📋 4. VARIABILI GLOBALI (potrebbero essere le quote)")
        print("-"*80)

        try:
            variables = doc.Variables
            print(f"   doc.Variables.Count = {variables.Count}")

            if variables.Count > 0:
                for i in range(1, min(variables.Count + 1, 20)):
                    try:
                        var = variables.Item(i)
                        var_name = getattr(var, 'Name', f'Var_{i}')
                        var_value = getattr(var, 'Value', None)
                        var_formula = getattr(var, 'Formula', None)
                        print(f"      [{i}] {var_name} = {var_value} (formula: {var_formula})")
                    except Exception as e:
                        print(f"      [{i}] Errore: {e}")
        except Exception as e:
            print(f"   Errore: {e}")

        # 5. CERCA NEI PROFILESETS
        print("\n📋 5. RICERCA NEI PROFILE SETS")
        print("-"*80)

        try:
            profile_sets = doc.ProfileSets
            print(f"   ProfileSets.Count = {profile_sets.Count}")

            for ps_idx in range(1, profile_sets.Count + 1):
                ps = profile_sets.Item(ps_idx)
                ps_name = getattr(ps, 'Name', f'ProfileSet_{ps_idx}')

                profiles = ps.Profiles
                for p_idx in range(1, profiles.Count + 1):
                    profile = profiles.Item(p_idx)

                    # Relations2d con dettaglio Value
                    if hasattr(profile, 'Relations2d'):
                        relations = profile.Relations2d
                        if relations and relations.Count > 0:
                            for i in range(1, relations.Count + 1):
                                try:
                                    rel = relations.Item(i)
                                    rel_type = rel.Type

                                    # Prova TUTTI i modi per ottenere un valore
                                    value = None
                                    for val_attr in ['Value', 'Dimension', 'Length', 'Angle', 'Size']:
                                        try:
                                            value = getattr(rel, val_attr)
                                            if value is not None:
                                                print(f"      {ps_name}/P{p_idx}: Type={rel_type}, {val_attr}={value}")
                                                break
                                        except:
                                            pass
                                except:
                                    pass
        except Exception as e:
            print(f"   Errore: {e}")

        # 6. ESPLORA ATTRIBUTI DEL DOCUMENTO
        print("\n📋 6. TUTTI GLI ATTRIBUTI DEL DOCUMENTO")
        print("-"*80)

        interesting_attrs = []
        for attr in dir(doc):
            if not attr.startswith('_'):
                lower_attr = attr.lower()
                if any(kw in lower_attr for kw in ['dim', 'constraint', 'relation', 'variable', 'quota', 'measure']):
                    try:
                        val = getattr(doc, attr)
                        if hasattr(val, 'Count'):
                            interesting_attrs.append((attr, val.Count))
                    except:
                        pass

        if interesting_attrs:
            for attr, count in interesting_attrs:
                print(f"   doc.{attr}.Count = {count}")
        else:
            print("   Nessun attributo interessante trovato")

        print(f"\n{'='*80}")
        print("✅ RICERCA COMPLETATA")
        print("="*80)

    finally:
        doc.Close(False)
        print("✅ File chiuso")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Uso: python debug_find_dimensions.py <filepath>")
        sys.exit(1)

    filepath = sys.argv[1]
    find_all_dimensions(filepath)

