#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script di debug MASSIVO per vincoli - Stampa TUTTO quello che COM sa sui vincoli
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

def debug_constraints(filepath_str):
    """Debug massivo dell'estrazione vincoli."""

    filepath = Path(filepath_str)
    print(f"\n{'='*80}")
    print(f"🔍 DEBUG MASSIVO VINCOLI: {filepath.name}")
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
        # 1. Esplora sketches
        print("1️⃣ SKETCHES")
        print("-" * 80)
        try:
            sketches = doc.Sketches
            print(f"✅ doc.Sketches.Count = {sketches.Count}")

            for i in range(1, sketches.Count + 1):
                sketch = sketches.Item(i)
                sketch_name = getattr(sketch, 'Name', f'Sketch_{i}')

                print(f"\n   📋 Sketch {i}: {sketch_name}")

                # Verifica attributi di vincoli
                print(f"      Attributi disponibili:")
                attrs = [attr for attr in dir(sketch) if 'elation' in attr.lower() or 'constraint' in attr.lower()]
                for attr in attrs:
                    print(f"        - {attr}")

                # Prova Relations2d
                print(f"\n      Prova Relations2d:")
                try:
                    relations = sketch.Relations2d
                    print(f"        ✅ sketch.Relations2d esiste")
                    print(f"        Count: {relations.Count}")

                    if relations.Count > 0:
                        rel = relations.Item(1)
                        print(f"        Primo vincolo attributi:")
                        rel_attrs = [attr for attr in dir(rel) if not attr.startswith('_')]
                        for attr in rel_attrs[:20]:  # Primi 20
                            print(f"          - {attr}")

                        # Prova Type e Value
                        try:
                            rel_type = rel.Type
                            print(f"        Type valore: {rel_type}")
                        except Exception as e:
                            print(f"        ❌ Errore lettura Type: {e}")

                        try:
                            rel_value = rel.Value
                            print(f"        Value valore: {rel_value}")
                        except Exception as e:
                            print(f"        ❌ Errore lettura Value: {e}")
                    else:
                        print(f"        ⚠️ Relations2d.Count = 0")
                except AttributeError:
                    print(f"        ❌ Relations2d NON disponibile")
                except Exception as e:
                    print(f"        ❌ Errore Relations2d: {e}")

                # Prova Constraints
                print(f"\n      Prova Constraints:")
                try:
                    constraints = sketch.Constraints
                    print(f"        ✅ sketch.Constraints esiste")
                    print(f"        Count: {constraints.Count}")

                    if constraints.Count > 0:
                        c = constraints.Item(1)
                        print(f"        Primo constraint attributi:")
                        c_attrs = [attr for attr in dir(c) if not attr.startswith('_')]
                        for attr in c_attrs[:20]:
                            print(f"          - {attr}")
                except AttributeError:
                    print(f"        ❌ Constraints NON disponibile")
                except Exception as e:
                    print(f"        ❌ Errore Constraints: {e}")

                # Prova Profile
                print(f"\n      Prova Profile:")
                try:
                    profiles = sketch.Profiles
                    print(f"        ✅ sketch.Profiles esiste")
                    print(f"        Count: {profiles.Count}")

                    if profiles.Count > 0:
                        profile = profiles.Item(1)
                        print(f"        Attributi Profile:")
                        p_attrs = [attr for attr in dir(profile) if 'constraint' in attr.lower() or 'relation' in attr.lower()]
                        if p_attrs:
                            for attr in p_attrs:
                                print(f"          - {attr}")
                        else:
                            print(f"          (no constraint/relation attributes)")
                except AttributeError:
                    print(f"        ❌ Profiles NON disponibile")
                except Exception as e:
                    print(f"        ❌ Errore Profiles: {e}")

        except Exception as e:
            print(f"❌ Errore sketch exploration: {e}")

        # 2. Verifica Model
        print(f"\n\n2️⃣ MODEL")
        print("-" * 80)
        try:
            if doc.Models.Count > 0:
                model = doc.Models.Item(1)
                print(f"✅ Model trovato")

                # Verifica attributi model
                model_attrs = [attr for attr in dir(model) if 'constraint' in attr.lower() or 'relation' in attr.lower()]
                if model_attrs:
                    print(f"   Attributi constraint/relation in model:")
                    for attr in model_attrs:
                        print(f"     - {attr}")
                else:
                    print(f"   (nessun attributo constraint/relation in model)")
            else:
                print(f"❌ Nessun model trovato")
        except Exception as e:
            print(f"❌ Errore model: {e}")

        print(f"\n{'='*80}")
        print(f"✅ DEBUG COMPLETATO")
        print(f"{'='*80}\n")

    finally:
        doc.Close(False)
        print("✅ File chiuso")

    return True


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Uso: python debug_constraints_massive.py <filepath>")
        print("Esempio: python debug_constraints_massive.py C:\\path\\file.par")
        sys.exit(1)

    filepath = sys.argv[1]
    success = debug_constraints(filepath)
    sys.exit(0 if success else 1)

