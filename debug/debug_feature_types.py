#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script per trovare TUTTI i tipi di feature 3D in un file Solid Edge.
Usa questo per mappare i valori Type corretti.
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

def analyze_features(filepath_str):
    """Analizza tutte le feature 3D in un file."""

    filepath = Path(filepath_str)
    print(f"\n{'='*80}")
    print(f"🔍 ANALISI FEATURE 3D: {filepath.name}")
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
        all_features = defaultdict(lambda: {
            'count': 0,
            'examples': []
        })

        # Cerca nel Model
        models = doc.Models
        print(f"📋 Models.Count = {models.Count}")

        for m_idx in range(1, models.Count + 1):
            model = models.Item(m_idx)

            # Features generiche
            if hasattr(model, 'Features'):
                features = model.Features
                print(f"\n   model.Features.Count = {features.Count}")

                for i in range(1, features.Count + 1):
                    try:
                        feat = features.Item(i)
                        feat_type = getattr(feat, 'Type', -1)
                        feat_name = getattr(feat, 'Name', f'Feature_{i}')
                        display_name = getattr(feat, 'DisplayName', feat_name)
                        edgebar_name = getattr(feat, 'EdgebarName', feat_name)

                        print(f"      [{i}] Type={feat_type}, Name={feat_name}")
                        print(f"           DisplayName={display_name}, EdgebarName={edgebar_name}")

                        all_features[feat_type]['count'] += 1
                        if len(all_features[feat_type]['examples']) < 5:
                            all_features[feat_type]['examples'].append({
                                'name': feat_name,
                                'display': display_name,
                                'edgebar': edgebar_name
                            })
                    except Exception as e:
                        print(f"      [{i}] Errore: {e}")

            # DesignEdgebarFeatures (albero completo)
            if hasattr(doc, 'DesignEdgebarFeatures'):
                edgebar = doc.DesignEdgebarFeatures
                print(f"\n   doc.DesignEdgebarFeatures.Count = {edgebar.Count}")

                for i in range(1, min(edgebar.Count + 1, 30)):  # Max 30
                    try:
                        feat = edgebar.Item(i)
                        feat_type = getattr(feat, 'Type', -1)
                        feat_name = getattr(feat, 'Name', f'Feature_{i}')

                        # Se non già registrato
                        if feat_type not in [f for f in all_features.keys()]:
                            all_features[feat_type]['count'] += 1
                            if len(all_features[feat_type]['examples']) < 5:
                                all_features[feat_type]['examples'].append({
                                    'name': feat_name
                                })
                    except:
                        pass

        # RIEPILOGO
        print(f"\n\n{'='*80}")
        print("📊 RIEPILOGO TIPI DI FEATURE")
        print("="*80)

        for type_val, data in sorted(all_features.items()):
            print(f"\n   Type = {type_val} ({data['count']} occorrenze)")
            for ex in data['examples']:
                name = ex.get('name', '?')
                display = ex.get('display', '')
                print(f"      Esempio: {name} (display: {display})")

        # Genera codice Python
        print(f"\n\n{'='*80}")
        print("📝 CODICE PYTHON PER IL MAPPING")
        print("="*80)

        print("\nFEATURE_TYPE_MAP = {")
        for type_val, data in sorted(all_features.items()):
            esempio = data['examples'][0]['name'] if data['examples'] else f'Type_{type_val}'
            # Prova a indovinare il tipo dal nome
            nome_lower = esempio.lower()
            if 'protrusion' in nome_lower or 'extruded' in nome_lower:
                tipo = 'ExtrudedProtrusion'
            elif 'cutout' in nome_lower:
                tipo = 'ExtrudedCutout'
            elif 'hole' in nome_lower:
                tipo = 'Hole'
            elif 'round' in nome_lower or 'fillet' in nome_lower:
                tipo = 'Round'
            elif 'chamfer' in nome_lower:
                tipo = 'Chamfer'
            elif 'revolution' in nome_lower or 'revolve' in nome_lower:
                tipo = 'Revolution'
            elif 'loft' in nome_lower:
                tipo = 'Loft'
            elif 'sweep' in nome_lower:
                tipo = 'Sweep'
            elif 'mirror' in nome_lower:
                tipo = 'Mirror'
            elif 'pattern' in nome_lower:
                if 'circular' in nome_lower:
                    tipo = 'CircularPattern'
                elif 'rectangular' in nome_lower:
                    tipo = 'RectangularPattern'
                else:
                    tipo = 'Pattern'
            elif 'sketch' in nome_lower:
                tipo = 'Sketch'
            elif 'plane' in nome_lower:
                tipo = 'RefPlane'
            else:
                tipo = esempio.split('_')[0] if '_' in esempio else esempio

            print(f"    {type_val}: \"{tipo}\",  # {data['count']} occorrenze, es: {esempio}")
        print("}")

    finally:
        doc.Close(False)
        print("\n✅ File chiuso")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Uso: python debug_feature_types.py <filepath>")
        print("\nQuesto script analizza TUTTE le feature 3D in un file")
        sys.exit(1)

    filepath = sys.argv[1]
    analyze_features(filepath)

