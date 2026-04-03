"""
Test script per verificare il funzionamento degli estrattori multi-CAD.

Uso:
    python test_extractors.py
    python test_extractors.py path/to/file.par
"""

import sys
from pathlib import Path

# Aggiungi la directory parent al path per import
sys.path.insert(0, str(Path(__file__).parent))

from extractors import (
    get_extractor,
    get_supported_extensions,
    detect_cad_type,
    extract_from_file,
    CADModelSignature
)
from extractors.factory import print_available_extractors, get_extractor_info


def test_factory():
    """Testa la factory degli estrattori."""
    print("\n" + "=" * 60)
    print("🧪 TEST: Factory Estrattori")
    print("=" * 60)

    # Lista CAD disponibili
    print_available_extractors()

    # Estensioni supportate
    extensions = get_supported_extensions()
    print(f"\n📁 Estensioni supportate: {len(extensions)}")
    print(f"   {', '.join(extensions)}")


def test_detection():
    """Testa il rilevamento del tipo CAD."""
    print("\n" + "=" * 60)
    print("🧪 TEST: Rilevamento Tipo CAD")
    print("=" * 60)

    test_files = [
        "model.par",
        "assembly.asm",
        "part.sldprt",
        "design.ipt",
        "component.catpart",
        "project.fcstd",
        "design.f3d",
        "unknown.xyz"
    ]

    for filename in test_files:
        cad_type = detect_cad_type(Path(filename))
        status = "✅" if cad_type else "❌"
        print(f"   {status} {filename} → {cad_type or 'Non riconosciuto'}")


def test_solid_edge_extraction(filepath: Path):
    """Testa l'estrazione da un file Solid Edge."""
    print("\n" + "=" * 60)
    print(f"🧪 TEST: Estrazione da {filepath.name}")
    print("=" * 60)

    if not filepath.exists():
        print(f"   ❌ File non trovato: {filepath}")
        return

    # Usa la factory
    result = extract_from_file(filepath)

    if result.success:
        sig = result.signature
        print(f"\n   ✅ Estrazione completata in {result.extraction_time_ms:.0f}ms")
        print(f"\n   📄 File: {sig.filename}")
        print(f"   🏭 CAD: {sig.cad_type}")
        print(f"   👤 Autore: {sig.author or 'N/A'}")
        print(f"   📊 Feature 3D: {sig.feature_count}")
        print(f"   📐 Sketch 2D: {sig.sketches_count}")
        print(f"   🔷 Geometrie 2D: {sig.total_2d_geometry_count}")
        print(f"   📏 Vincoli 2D: {sig.total_2d_constraint_count}")

        if sig.feature_types:
            print(f"\n   📦 Tipi Feature:")
            for feat_type, count in sig.feature_types.items():
                print(f"      - {feat_type}: {count}")

        if sig.feature_sequence:
            print(f"\n   📝 Sequenza Feature (prime 10):")
            for i, feat in enumerate(sig.feature_sequence[:10], 1):
                print(f"      {i}. {feat}")
            if len(sig.feature_sequence) > 10:
                print(f"      ... e altre {len(sig.feature_sequence) - 10}")

        if result.warnings:
            print(f"\n   ⚠️ Warnings:")
            for w in result.warnings:
                print(f"      - {w}")
    else:
        print(f"\n   ❌ Estrazione fallita: {result.error_message}")


def test_signature_serialization():
    """Testa la serializzazione/deserializzazione della signature."""
    print("\n" + "=" * 60)
    print("🧪 TEST: Serializzazione Signature")
    print("=" * 60)

    from extractors.cad_signature import SketchData, FeatureData, ConstraintData, GeometryData
    from collections import Counter

    # Crea una signature di test
    sig = CADModelSignature(
        filepath="C:/test/model.par",
        filename="model.par",
        cad_type="SolidEdge",
        author="Test User",
        feature_count=5,
        feature_types=Counter({"Protrusion": 3, "Cutout": 2}),
        feature_sequence=["Protrusion", "Protrusion", "Cutout", "Protrusion", "Cutout"],
        sketches_data=[
            SketchData(
                name="Sketch_1",
                geometry_count=10,
                constraint_count=8,
                geometry_types={"Line": 5, "Circle": 3, "Arc": 2},
                constraint_types={"Coincident": 4, "Distance": 4}
            )
        ]
    )

    # Serializza
    data = sig.to_dict()
    print(f"\n   ✅ Signature serializzata")
    print(f"   📊 Feature count: {data['feature_count']}")
    print(f"   📐 Sketches: {len(data['sketches_data'])}")

    # Deserializza
    sig2 = CADModelSignature.from_dict(data)
    print(f"\n   ✅ Signature deserializzata")
    print(f"   📊 Feature count: {sig2.feature_count}")
    print(f"   📐 Sketches: {sig2.sketches_count}")

    # Verifica uguaglianza
    assert sig.feature_count == sig2.feature_count
    assert sig.author == sig2.author
    print(f"\n   ✅ Serializzazione/Deserializzazione OK!")


def main():
    """Esegue tutti i test."""
    print("\n" + "=" * 60)
    print("🚀 MULTI-CAD EXTRACTOR TEST SUITE")
    print("=" * 60)

    # Test base
    test_factory()
    test_detection()
    test_signature_serialization()

    # Se passato un file, testa l'estrazione
    if len(sys.argv) > 1:
        filepath = Path(sys.argv[1])
        test_solid_edge_extraction(filepath)
    else:
        print("\n💡 Suggerimento: passa un file CAD come argomento per testare l'estrazione")
        print("   Esempio: python test_extractors.py model.par")

    print("\n" + "=" * 60)
    print("✅ TEST COMPLETATI")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()

