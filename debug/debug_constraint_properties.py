#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Debug avanzato per capire il VERO tipo di ogni vincolo
Esplora tutte le proprietà disponibili
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

def debug_constraint_properties(filepath_str):
    """Debug avanzato proprietà vincoli."""

    filepath = Path(filepath_str)
    print(f"\n{'='*80}")
    print(f"🔍 DEBUG PROPRIETÀ VINCOLI: {filepath.name}")
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

        # Prendi primo sketch con vincoli
        for sk_idx in range(1, sketches.Count + 1):
            sketch = sketches.Item(sk_idx)

            try:
                profiles = sketch.Profiles
                if profiles and hasattr(profiles, 'Count') and profiles.Count > 0:
                    profile = profiles.Item(1)

                    if hasattr(profile, 'Relations2d'):
                        relations = profile.Relations2d
                        if relations and hasattr(relations, 'Count') and relations.Count > 0:
                            print(f"📋 Analizzando vincoli di {sketch.Name}\n")

                            for i in range(1, min(relations.Count + 1, 5)):  # Max 5
                                rel = relations.Item(i)

                                print(f"   Vincolo [{i}]")
                                print(f"   " + "-"*60)

                                # Lista TUTTE le proprietà
                                attrs = [a for a in dir(rel) if not a.startswith('_')]

                                for attr in attrs[:30]:  # Primi 30
                                    try:
                                        val = getattr(rel, attr)
                                        if not callable(val):
                                            print(f"      {attr} = {val}")
                                    except Exception as e:
                                        pass

                                print()

                            # Esci dopo primo sketch con vincoli
                            break
            except:
                pass

        print(f"\n{'='*80}")
        print("✅ DEBUG COMPLETATO")
        print("="*80 + "\n")

    finally:
        doc.Close(False)
        print("✅ File chiuso")

    return True


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Uso: python debug_constraint_properties.py <filepath>")
        sys.exit(1)

    filepath = sys.argv[1]
    success = debug_constraint_properties(filepath)
    sys.exit(0 if success else 1)

