#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script per estrarre i DETTAGLI delle Dimensions (quote dimensionali)
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

def extract_dimensions_details(filepath_str):
    """Estrae dettagli delle dimensioni."""

    filepath = Path(filepath_str)
    print(f"\n{'='*80}")
    print(f"🔍 DETTAGLI DIMENSIONI (QUOTE)")
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
        all_dimensions = defaultdict(lambda: {
            'count': 0,
            'has_value': False,
            'values': [],
            'examples': []
        })

        sketches = doc.Sketches
        print(f"📋 Analisi {sketches.Count} sketch...\n")

        for sk_idx in range(1, sketches.Count + 1):
            sketch = sketches.Item(sk_idx)
            sketch_name = getattr(sketch, 'Name', f'Sketch_{sk_idx}')

            try:
                profiles = sketch.Profiles
                for p_idx in range(1, profiles.Count + 1):
                    profile = profiles.Item(p_idx)

                    # DIMENSIONI
                    if hasattr(profile, 'Dimensions'):
                        dims = profile.Dimensions
                        if dims and dims.Count > 0:
                            print(f"📋 {sketch_name} - Profile {p_idx}: {dims.Count} dimensioni")

                            for i in range(1, dims.Count + 1):
                                try:
                                    dim = dims.Item(i)

                                    # Estrai TUTTE le proprietà
                                    dim_type = getattr(dim, 'Type', -1)
                                    dim_name = getattr(dim, 'Name', f'Dim_{i}')

                                    # Prova vari attributi per il valore
                                    value = None
                                    value_attr = None
                                    for attr in ['Value', 'ModelValue', 'DrawingValue', 'Size', 'Length', 'Angle', 'Radius', 'Diameter']:
                                        try:
                                            v = getattr(dim, attr)
                                            if v is not None:
                                                value = v
                                                value_attr = attr
                                                break
                                        except:
                                            pass

                                    # Stampa dettagli
                                    print(f"   [{i}] Type={dim_type}, Name={dim_name}")
                                    if value is not None:
                                        print(f"       {value_attr}={value}")

                                    # Lista TUTTE le proprietà disponibili
                                    props = [p for p in dir(dim) if not p.startswith('_') and not callable(getattr(dim, p, None))]
                                    print(f"       Props: {props[:10]}...")

                                    # Registra
                                    all_dimensions[dim_type]['count'] += 1
                                    if value is not None:
                                        all_dimensions[dim_type]['has_value'] = True
                                        if len(all_dimensions[dim_type]['values']) < 5:
                                            all_dimensions[dim_type]['values'].append(value)
                                    if len(all_dimensions[dim_type]['examples']) < 3:
                                        all_dimensions[dim_type]['examples'].append({
                                            'name': dim_name,
                                            'sketch': sketch_name,
                                            'value': value,
                                            'value_attr': value_attr
                                        })

                                    print()
                                except Exception as e:
                                    print(f"   [{i}] Errore: {e}")
            except:
                pass

        # RIEPILOGO
        print(f"\n{'='*80}")
        print("📊 RIEPILOGO TIPI DI DIMENSIONI")
        print("="*80)

        for type_val, data in sorted(all_dimensions.items()):
            print(f"\n   Type = {type_val}")
            print(f"   Occorrenze: {data['count']}")
            print(f"   Ha valore: {data['has_value']}")
            if data['values']:
                print(f"   Valori: {data['values']}")
            for ex in data['examples']:
                print(f"   Esempio: {ex['name']} = {ex['value']} ({ex['value_attr']}) in {ex['sketch']}")

        # Genera codice
        print(f"\n\n{'='*80}")
        print("📝 CODICE PYTHON DA AGGIUNGERE")
        print("="*80)

        print("\n# Aggiungi a CONSTRAINT_2D_TYPE_MAP:")
        for type_val, data in sorted(all_dimensions.items()):
            nome = "Dimension" if data['has_value'] else f"DimType_{type_val}"
            print(f"    {type_val}: \"{nome}\",  # Dimensione, {data['count']} occorrenze")

        print("\n# Aggiungi a CONSTRAINT_DESCRIPTIONS:")
        for type_val, data in sorted(all_dimensions.items()):
            print(f'    {type_val}: {{"categoria": "Dimensionale", "descrizione": "Quota dimensionale", "tipo": "dimensionale"}},')

    finally:
        doc.Close(False)
        print("\n✅ File chiuso")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Uso: python debug_dimensions_detail.py <filepath>")
        sys.exit(1)

    filepath = sys.argv[1]
    extract_dimensions_details(filepath)

