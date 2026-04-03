#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Debug per capire la CLASSE del vincolo tramite COM
"""

import sys
from pathlib import Path

try:
    import win32com.client
    import pythoncom
    HAS_COM = True
except:
    HAS_COM = False
    print("❌ COM non disponibile")
    sys.exit(1)

def debug_constraint_class(filepath_str):
    """Debug per capire la classe COM del vincolo."""

    filepath = Path(filepath_str)
    print(f"\n{'='*80}")
    print(f"🔍 DEBUG CLASSE COM VINCOLI")
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

        for sk_idx in range(1, min(sketches.Count + 1, 3)):
            sketch = sketches.Item(sk_idx)

            try:
                profiles = sketch.Profiles
                if profiles and profiles.Count > 0:
                    profile = profiles.Item(1)

                    if hasattr(profile, 'Relations2d'):
                        relations = profile.Relations2d
                        if relations and relations.Count > 0:
                            print(f"📋 Sketch: {sketch.Name}")

                            for i in range(1, min(relations.Count + 1, 5)):
                                rel = relations.Item(i)

                                print(f"\n   Vincolo [{i}]")

                                # Prova a ottenere info sulla classe
                                try:
                                    # Tipo COM
                                    rel_type = rel.Type
                                    print(f"      Type = {rel_type}")

                                    # Prova __class__
                                    print(f"      __class__ = {rel.__class__}")
                                    print(f"      __class__.__name__ = {rel.__class__.__name__}")

                                    # Prova str
                                    try:
                                        print(f"      str(rel) = {str(rel)[:100]}")
                                    except:
                                        pass

                                    # Prova repr
                                    try:
                                        print(f"      repr(rel) = {repr(rel)[:100]}")
                                    except:
                                        pass

                                    # Lista attributi che potrebbero indicare il tipo
                                    for attr in ['ProgID', 'ObjectType', 'ObjectClass', 'ClassName',
                                                 'TypeName', 'RelationType', 'ConstraintType',
                                                 'DefinitionType', 'GeometryType']:
                                        try:
                                            val = getattr(rel, attr)
                                            print(f"      {attr} = {val}")
                                        except AttributeError:
                                            pass

                                    # Prova a vedere se ha metodi che indicano il tipo
                                    methods = [m for m in dir(rel) if 'type' in m.lower() or 'kind' in m.lower()]
                                    if methods:
                                        print(f"      Metodi 'type/kind': {methods}")

                                except Exception as e:
                                    print(f"      Errore: {e}")

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
        print("Uso: python debug_constraint_class.py <filepath>")
        sys.exit(1)

    filepath = sys.argv[1]
    debug_constraint_class(filepath)

