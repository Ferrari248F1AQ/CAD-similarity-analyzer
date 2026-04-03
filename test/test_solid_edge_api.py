# -*- coding: utf-8 -*-
"""
Script diagnostico per esplorare l'API COM reale di Solid Edge.
Apri un file .par in Solid Edge prima di eseguire questo script.
"""

import win32com.client

def explore_object(obj, name="Object", depth=0, max_depth=2):
    """Esplora ricorsivamente un oggetto COM."""
    indent = "  " * depth
    print(f"{indent}=== {name} ===")

    # Lista attributi/metodi
    try:
        attrs = dir(obj)
        # Filtra attributi privati e comuni
        attrs = [a for a in attrs if not a.startswith('_') and a not in
                 ['QueryInterface', 'QueryInterface_', 'QueryInterface__']]

        for attr in attrs[:50]:  # Limita per non esplodere
            try:
                val = getattr(obj, attr)
                val_type = type(val).__name__

                # Se è un valore semplice, mostralo
                if val_type in ('str', 'int', 'float', 'bool', 'NoneType'):
                    print(f"{indent}  .{attr} = {repr(val)}")
                elif val_type == 'CDispatch' or 'win32' in val_type.lower():
                    print(f"{indent}  .{attr} -> [COM Object]")
                    # Esplora ricorsivamente se non troppo profondo
                    if depth < max_depth and attr in ['Models', 'Features', 'Sketches', 'Properties']:
                        try:
                            if hasattr(val, 'Count'):
                                print(f"{indent}    .Count = {val.Count}")
                        except:
                            pass
                else:
                    print(f"{indent}  .{attr} -> ({val_type})")
            except Exception as e:
                print(f"{indent}  .{attr} -> [Error: {e}]")
    except Exception as e:
        print(f"{indent}  [Cannot enumerate: {e}]")


def main():
    print("=" * 60)
    print("DIAGNOSTICA API SOLID EDGE")
    print("=" * 60)

    try:
        # Connetti a Solid Edge
        app = win32com.client.GetActiveObject("SolidEdge.Application")
        print("\n✓ Connesso a Solid Edge\n")

        print(f"Version: {app.Version}")
        print(f"Documents.Count: {app.Documents.Count}")

        if app.Documents.Count == 0:
            print("\n⚠️ Nessun documento aperto! Apri un file .par prima.")
            return

        # Documento attivo
        doc = app.ActiveDocument
        print(f"\n=== DOCUMENTO ATTIVO ===")
        print(f"Name: {doc.Name}")
        print(f"Type: {doc.Type}")
        print(f"FullName: {doc.FullName}")

        # Esplora proprietà documento
        print("\n--- Proprietà disponibili del documento ---")
        doc_attrs = [a for a in dir(doc) if not a.startswith('_')]
        for attr in doc_attrs:
            try:
                val = getattr(doc, attr)
                if hasattr(val, 'Count'):
                    print(f"  doc.{attr}.Count = {val.Count}")
                elif isinstance(val, (str, int, float, bool)):
                    print(f"  doc.{attr} = {repr(val)[:60]}")
            except:
                pass

        # Prova a trovare le feature
        print("\n=== RICERCA FEATURE ===")

        # Metodo 1: doc.Models
        try:
            models = doc.Models
            print(f"\ndoc.Models.Count = {models.Count}")
            if models.Count > 0:
                model = models.Item(1)
                print(f"\nmodel = Models.Item(1)")
                model_attrs = [a for a in dir(model) if not a.startswith('_')]
                print(f"Attributi model: {model_attrs[:30]}")

                # Cerca collezioni feature-like
                for attr in ['Features', 'DesignEdgebarFeatures', 'ExtrudedProtrusions',
                             'ExtrudedCutouts', 'Holes', 'Rounds', 'Chamfers', 'Revolutions',
                             'Sketches', 'RefPlanes']:
                    try:
                        coll = getattr(model, attr)
                        if hasattr(coll, 'Count'):
                            print(f"\n  model.{attr}.Count = {coll.Count}")
                            if coll.Count > 0:
                                item = coll.Item(1)
                                print(f"    Item(1) attributes: {[a for a in dir(item) if not a.startswith('_')][:15]}")
                                # Prova a leggere Name e Type
                                try:
                                    print(f"    .Name = {item.Name}")
                                except:
                                    pass
                                try:
                                    print(f"    .Type = {item.Type}")
                                except:
                                    pass
                    except Exception as e:
                        print(f"  model.{attr} -> Error: {e}")
        except Exception as e:
            print(f"doc.Models error: {e}")

        # Metodo 2: doc.Sketches direttamente
        try:
            sketches = doc.Sketches
            print(f"\ndoc.Sketches.Count = {sketches.Count}")
        except Exception as e:
            print(f"doc.Sketches error: {e}")

        # Metodo 3: Properties (metadati)
        print("\n=== PROPERTIES/METADATI ===")
        try:
            props = doc.Properties
            print(f"doc.Properties -> {type(props)}")
            if hasattr(props, 'Count'):
                print(f"  Count = {props.Count}")
                for i in range(1, min(props.Count + 1, 10)):
                    try:
                        prop_set = props.Item(i)
                        print(f"\n  PropertySet {i}: {getattr(prop_set, 'Name', '?')}")
                        if hasattr(prop_set, 'Count'):
                            for j in range(1, min(prop_set.Count + 1, 20)):
                                try:
                                    p = prop_set.Item(j)
                                    pname = getattr(p, 'Name', f'Prop_{j}')
                                    pval = getattr(p, 'Value', '?')
                                    print(f"    {pname} = {repr(pval)[:50]}")
                                except:
                                    pass
                    except:
                        pass
        except Exception as e:
            print(f"doc.Properties error: {e}")

        # SummaryInfo
        try:
            si = doc.SummaryInfo
            print(f"\n=== SUMMARY INFO ===")
            for attr in ['Author', 'Comments', 'Keywords', 'LastAuthor', 'Subject', 'Title']:
                try:
                    val = getattr(si, attr)
                    print(f"  {attr}: {repr(val)}")
                except:
                    pass
        except Exception as e:
            print(f"SummaryInfo error: {e}")

    except Exception as e:
        print(f"\n❌ Errore: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()

