#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Debug per cercare le costanti enum di Solid Edge
"""

import sys
from pathlib import Path

try:
    import win32com.client
    from win32com.client import constants
    HAS_COM = True
except:
    HAS_COM = False
    print("❌ COM non disponibile")
    sys.exit(1)

def find_relation_constants():
    """Cerca le costanti relative ai vincoli in Solid Edge."""

    print(f"\n{'='*80}")
    print(f"🔍 RICERCA COSTANTI SOLID EDGE")
    print(f"{'='*80}\n")

    # Connetti Solid Edge per caricare la type library
    try:
        app = win32com.client.gencache.EnsureDispatch("SolidEdge.Application")
        print("✅ Connesso a Solid Edge con gencache")
    except Exception as e:
        print(f"⚠️ gencache fallito: {e}")
        try:
            app = win32com.client.Dispatch("SolidEdge.Application")
            print("✅ Connesso a Solid Edge con Dispatch")
        except Exception as e2:
            print(f"❌ Errore connessione: {e2}")
            return

    # Prova a ottenere le costanti
    try:
        print("\n📋 Costanti disponibili in win32com.client.constants:")

        # Lista tutte le costanti che contengono "Relation" o "Constraint"
        all_constants = dir(constants)
        relation_constants = [c for c in all_constants if 'relation' in c.lower() or 'constraint' in c.lower()]

        if relation_constants:
            print(f"   Trovate {len(relation_constants)} costanti relative:")
            for c in relation_constants[:50]:
                try:
                    val = getattr(constants, c)
                    print(f"      {c} = {val}")
                except:
                    print(f"      {c} = (errore)")
        else:
            print("   Nessuna costante 'relation' o 'constraint' trovata")

        # Prova a cercare tutte le costanti con valori simili
        print(f"\n📋 Ricerca costanti con valori noti:")
        target_values = [768508992, 273497200, -83892864, -280074960, 1166881824, 1679388272]

        for c in all_constants:
            try:
                val = getattr(constants, c)
                if isinstance(val, int) and val in target_values:
                    print(f"   {c} = {val}")
            except:
                pass

        # Mostra alcune costanti per capire il pattern
        print(f"\n📋 Primi 30 costanti (per capire il pattern):")
        for c in all_constants[:30]:
            if not c.startswith('_'):
                try:
                    val = getattr(constants, c)
                    print(f"   {c} = {val}")
                except:
                    pass

    except Exception as e:
        print(f"❌ Errore: {e}")

    print(f"\n{'='*80}")
    print("✅ DEBUG COMPLETATO")
    print("="*80 + "\n")


if __name__ == '__main__':
    find_relation_constants()

