# -*- coding: utf-8 -*-
"""
Test rapido dell'analisi - da eseguire direttamente
"""
import sys
from pathlib import Path

# Aggiungi path
sys.path.insert(0, str(Path(__file__).parent))

print("=" * 60)
print("TEST ANALISI SOLID EDGE")
print("=" * 60)

# Test 1: Import moduli
print("\n1️⃣ Test import moduli...")
try:
    from solid_edge_similarity_v2 import (
        analyze_directory,
        extract_signature,
        HAS_COM
    )
    print(f"   ✅ Import OK")
    print(f"   📦 HAS_COM = {HAS_COM}")
except Exception as e:
    print(f"   ❌ Errore import: {e}")
    sys.exit(1)

# Test 2: Import webapp
print("\n2️⃣ Test import webapp...")
try:
    from webapp.app import (
        count_cad_files,
        get_extensions_for_cad,
        CAD_EXTENSIONS,
        HAS_MULTI_CAD
    )
    print(f"   ✅ Import webapp OK")
    print(f"   📦 HAS_MULTI_CAD = {HAS_MULTI_CAD}")
    print(f"   📦 CAD_EXTENSIONS = {CAD_EXTENSIONS}")
except Exception as e:
    print(f"   ❌ Errore import webapp: {e}")

# Test 3: Conta file in una directory
print("\n3️⃣ Test conteggio file...")
test_dir = Path(r"C:\Users\emanu\Downloads\test")
if test_dir.exists():
    try:
        count = count_cad_files(test_dir, 'auto')
        print(f"   📁 Directory: {test_dir}")
        print(f"   📊 File CAD trovati (auto): {count}")

        count_se = count_cad_files(test_dir, 'SolidEdge')
        print(f"   📊 File Solid Edge: {count_se}")
    except Exception as e:
        print(f"   ❌ Errore conteggio: {e}")
else:
    print(f"   ⚠️ Directory test non esiste: {test_dir}")
    print("   💡 Modifica il path nel test!")

# Test 4: Analisi con Solid Edge
print("\n4️⃣ Test analisi Solid Edge...")
if test_dir.exists():
    try:
        print(f"   🔍 Avvio analyze_directory...")
        import pythoncom
        pythoncom.CoInitialize()

        signatures = analyze_directory(test_dir, use_com=True)
        print(f"   ✅ Analisi completata!")
        print(f"   📊 Firme estratte: {len(signatures)}")

        for sig in signatures[:3]:
            print(f"      - {sig.filename}: {sig.feature_count} features")

        pythoncom.CoUninitialize()
    except Exception as e:
        import traceback
        print(f"   ❌ Errore analisi: {e}")
        print(f"   📋 Traceback:\n{traceback.format_exc()}")

print("\n" + "=" * 60)
print("TEST COMPLETATO")
print("=" * 60)

