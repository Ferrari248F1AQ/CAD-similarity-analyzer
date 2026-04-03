"""
Multi-CAD Feature Extractors Package

Supporta:
- Solid Edge (COM)
- SolidWorks (COM)
- Autodesk Inventor (COM)
- CATIA V5 (COM)
- FreeCAD (Python API)
- Fusion 360 (Autodesk API)
"""

from .cad_signature import CADModelSignature, SketchData, FeatureData
from .base_extractor import BaseCADExtractor, ExtractionResult, ExtractionError
from .factory import (
    get_extractor,
    get_supported_extensions,
    detect_cad_type,
    extract_from_file,
    get_available_cads,
    get_extractor_info,
    filter_cad_files,
)

__all__ = [
    'CADModelSignature',
    'SketchData',
    'FeatureData',
    'BaseCADExtractor',
    'ExtractionResult',
    'ExtractionError',
    'get_extractor',
    'get_supported_extensions',
    'detect_cad_type',
    'extract_from_file',
    'get_available_cads',
    'get_extractor_info',
    'filter_cad_files',
]

