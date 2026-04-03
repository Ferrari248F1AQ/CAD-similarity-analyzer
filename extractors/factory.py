"""
CAD Extractor Factory - Seleziona automaticamente l'estrattore appropriato.

Questo modulo fornisce funzioni per:
- Rilevare il tipo di CAD da un file
- Ottenere l'estrattore appropriato
- Verificare quali CAD sono disponibili nel sistema
"""

from pathlib import Path
from typing import Optional, Dict, List, Type

from .base_extractor import BaseCADExtractor, ExtractionResult


# ============================================================================
# MAPPING ESTENSIONI → ESTRATTORI
# ============================================================================

# Importazione lazy per evitare problemi di dipendenze circolari
_extractors_cache: Dict[str, Type[BaseCADExtractor]] = {}

def _get_extractor_classes() -> Dict[str, Type[BaseCADExtractor]]:
    """Ottiene tutte le classi estrattore disponibili."""
    global _extractors_cache

    if _extractors_cache:
        return _extractors_cache

    extractors = {}

    # Solid Edge
    try:
        from .solid_edge_extractor import SolidEdgeExtractor
        extractors['SolidEdge'] = SolidEdgeExtractor
    except ImportError:
        pass

    # SolidWorks
    try:
        from .solidworks_extractor import SolidWorksExtractor
        extractors['SolidWorks'] = SolidWorksExtractor
    except ImportError:
        pass

    # Inventor
    try:
        from .inventor_extractor import InventorExtractor
        extractors['Inventor'] = InventorExtractor
    except ImportError:
        pass

    # CATIA
    try:
        from .catia_extractor import CATIAExtractor
        extractors['CATIA'] = CATIAExtractor
    except ImportError:
        pass

    # FreeCAD
    try:
        from .freecad_extractor import FreeCADExtractor
        extractors['FreeCAD'] = FreeCADExtractor
    except ImportError:
        pass

    # Fusion 360
    try:
        from .fusion360_extractor import Fusion360Extractor
        extractors['Fusion360'] = Fusion360Extractor
    except ImportError:
        pass

    _extractors_cache = extractors
    return extractors


# Mapping estensioni → CAD
EXTENSION_TO_CAD: Dict[str, str] = {
    # Solid Edge
    '.par': 'SolidEdge',
    '.psm': 'SolidEdge',
    '.asm': 'SolidEdge',

    # SolidWorks
    '.sldprt': 'SolidWorks',
    '.sldasm': 'SolidWorks',
    '.slddrw': 'SolidWorks',

    # Inventor
    '.ipt': 'Inventor',
    '.iam': 'Inventor',
    '.idw': 'Inventor',

    # CATIA
    '.catpart': 'CATIA',
    '.catproduct': 'CATIA',
    '.catdrawing': 'CATIA',

    # FreeCAD
    '.fcstd': 'FreeCAD',

    # Fusion 360
    '.f3d': 'Fusion360',
    '.f3z': 'Fusion360',
}


def detect_cad_type(filepath: Path) -> Optional[str]:
    """
    Rileva il tipo di CAD da un file basandosi sull'estensione.

    Args:
        filepath: Path del file CAD

    Returns:
        Nome del CAD ('SolidEdge', 'SolidWorks', etc.) o None se non riconosciuto
    """
    ext = filepath.suffix.lower()
    return EXTENSION_TO_CAD.get(ext)


def get_supported_extensions() -> List[str]:
    """
    Restituisce la lista di tutte le estensioni supportate.

    Returns:
        Lista di estensioni (es. ['.par', '.sldprt', ...])
    """
    return list(EXTENSION_TO_CAD.keys())


def get_extractor(
    filepath: Optional[Path] = None,
    cad_type: Optional[str] = None
) -> Optional[BaseCADExtractor]:
    """
    Ottiene l'estrattore appropriato per un file o tipo CAD.

    Args:
        filepath: Path del file CAD (usato per rilevare il tipo)
        cad_type: Nome esplicito del CAD (ha priorità su filepath)

    Returns:
        Istanza dell'estrattore appropriato, o None se non trovato

    Examples:
        >>> extractor = get_extractor(Path("model.par"))
        >>> extractor = get_extractor(cad_type="SolidWorks")
    """
    # Determina il tipo CAD
    if cad_type is None and filepath is not None:
        cad_type = detect_cad_type(filepath)

    if cad_type is None:
        return None

    # Ottieni la classe estrattore
    extractors = _get_extractor_classes()
    extractor_class = extractors.get(cad_type)

    if extractor_class is None:
        return None

    # Crea e restituisci l'istanza
    return extractor_class()


def get_available_cads() -> Dict[str, bool]:
    """
    Verifica quali CAD sono disponibili nel sistema.

    Returns:
        Dizionario {nome_cad: is_available}
    """
    result = {}
    extractors = _get_extractor_classes()

    for cad_name, extractor_class in extractors.items():
        try:
            extractor = extractor_class()
            # is_available deve essere True esplicito; qualsiasi altro valore -> False
            avail = False
            try:
                avail = bool(extractor.is_available)
            except Exception:
                avail = False
            result[cad_name] = avail
        except Exception:
            result[cad_name] = False

    return result


def extract_from_file(filepath: Path) -> ExtractionResult:
    """
    Estrae la signature da un file CAD, selezionando automaticamente l'estrattore.

    Args:
        filepath: Path del file CAD

    Returns:
        ExtractionResult con la signature o l'errore

    Example:
        >>> result = extract_from_file(Path("model.par"))
        >>> if result.success:
        ...     print(f"Feature count: {result.signature.feature_count}")
    """
    from .base_extractor import ExtractionResult

    # Verifica estensione
    cad_type = detect_cad_type(filepath)
    if cad_type is None:
        return ExtractionResult(
            success=False,
            error_message=f"Estensione non supportata: {filepath.suffix}"
        )

    # Ottieni estrattore
    extractor = get_extractor(cad_type=cad_type)
    if extractor is None:
        return ExtractionResult(
            success=False,
            error_message=f"Estrattore per {cad_type} non disponibile"
        )

    # Estrai
    return extractor.extract(filepath)


def get_extractor_info() -> Dict[str, Dict]:
    """
    Ottiene informazioni dettagliate su tutti gli estrattori.

    Returns:
        Dizionario con informazioni per ogni CAD:
        {
            'SolidEdge': {
                'name': 'SolidEdge',
                'version': '2.0.0',
                'extensions': ['.par', '.psm', '.asm'],
                'available': True,
                'type': 'COM'
            },
            ...
        }
    """
    result = {}
    extractors = _get_extractor_classes()

    for cad_name, extractor_class in extractors.items():
        try:
            extractor = extractor_class()

            # Determina il tipo di connessione
            connection_type = 'Native'
            if hasattr(extractor, 'COM_PROG_ID'):
                connection_type = 'COM'

            result[cad_name] = {
                'name': cad_name,
                'version': extractor.VERSION,
                'extensions': extractor.SUPPORTED_EXTENSIONS,
                'available': extractor.is_available,
                'type': connection_type,
            }
        except Exception as e:
            result[cad_name] = {
                'name': cad_name,
                'version': 'unknown',
                'extensions': [],
                'available': False,
                'type': 'unknown',
                'error': str(e)
            }

    return result


def print_available_extractors():
    """Stampa informazioni su tutti gli estrattori disponibili."""
    info = get_extractor_info()

    print("\n" + "=" * 60)
    print("🔧 ESTRATTORI CAD DISPONIBILI")
    print("=" * 60)

    for cad_name, data in info.items():
        status = "✅" if data['available'] else "❌"
        extensions = ", ".join(data['extensions']) if data['extensions'] else "N/A"

        print(f"\n{status} {cad_name}")
        print(f"   Versione: {data['version']}")
        print(f"   Tipo: {data['type']}")
        print(f"   Estensioni: {extensions}")

        if 'error' in data:
            print(f"   ⚠️ Errore: {data['error']}")

    print("\n" + "=" * 60)


# ============================================================================
# UTILITY
# ============================================================================

def is_cad_file(filepath: Path) -> bool:
    """Verifica se un file è un file CAD supportato."""
    return filepath.suffix.lower() in EXTENSION_TO_CAD


def filter_cad_files(directory: Path, recursive: bool = True) -> List[Path]:
    """Ritorna tutti i file CAD supportati in una directory (filtrati per estensioni note)."""
    files = []
    pattern = '**/*' if recursive else '*'
    for fp in directory.glob(pattern):
        if fp.is_file() and fp.suffix.lower() in EXTENSION_TO_CAD:
            files.append(fp)
    return files


# ============================================================================
# CLI
# ============================================================================

if __name__ == "__main__":
    print_available_extractors()


__all__ = [
    'detect_cad_type',
    'get_extractor',
    'get_supported_extensions',
    'get_available_cads',
    'get_extractor_info',
    'extract_from_file',
    'filter_cad_files',
]
