"""
BaseCADExtractor - Interfaccia base per tutti gli estrattori CAD.

Ogni estrattore specifico (SolidEdge, SolidWorks, etc.) deve ereditare
da questa classe e implementare i metodi astratti.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from pathlib import Path
import hashlib
import traceback

from .cad_signature import CADModelSignature


@dataclass
class ExtractionResult:
    """Risultato di un'estrazione."""
    success: bool
    signature: Optional[CADModelSignature] = None
    error_message: str = ""
    warnings: List[str] = field(default_factory=list)
    extraction_time_ms: float = 0.0


class ExtractionError(Exception):
    """Eccezione per errori di estrazione."""
    def __init__(self, message: str, cad_type: str = "", filepath: str = ""):
        self.message = message
        self.cad_type = cad_type
        self.filepath = filepath
        super().__init__(f"[{cad_type}] {message} - File: {filepath}")


class BaseCADExtractor(ABC):
    """
    Classe base astratta per estrattori CAD.

    Ogni estrattore deve:
    1. Implementare `_connect()` per connettersi all'applicazione CAD
    2. Implementare `_disconnect()` per chiudere la connessione
    3. Implementare `_extract_from_document()` per estrarre i dati
    4. Definire `SUPPORTED_EXTENSIONS` con le estensioni supportate
    5. Definire `CAD_NAME` con il nome del CAD
    """

    # Da sovrascrivere nelle sottoclassi
    CAD_NAME: str = "Unknown"
    SUPPORTED_EXTENSIONS: List[str] = []
    VERSION: str = "1.0.0"

    def __init__(self):
        self._app = None
        self._connected = False
        self._warnings: List[str] = []

    @property
    def is_available(self) -> bool:
        """Verifica se il CAD è disponibile nel sistema."""
        try:
            self._connect()
            available = self._app is not None
            self._disconnect()
            return available
        except Exception:
            return False

    @abstractmethod
    def _connect(self) -> bool:
        """
        Connette all'applicazione CAD.

        Returns:
            True se connesso con successo, False altrimenti.
        """
        pass

    @abstractmethod
    def _disconnect(self):
        """Disconnette dall'applicazione CAD."""
        pass

    @abstractmethod
    def _extract_from_document(self, filepath: Path) -> CADModelSignature:
        """
        Estrae la signature da un documento.

        Args:
            filepath: Path del file da analizzare

        Returns:
            CADModelSignature con i dati estratti

        Raises:
            ExtractionError: Se l'estrazione fallisce
        """
        pass

    def extract(self, filepath: Path) -> ExtractionResult:
        """
        Estrae la signature da un file CAD.

        Questo metodo gestisce la connessione e gli errori.
        Le sottoclassi non dovrebbero sovrascriverlo.

        Args:
            filepath: Path del file da analizzare

        Returns:
            ExtractionResult con la signature o l'errore
        """
        import time
        start_time = time.time()
        self._warnings = []

        # Verifica estensione supportata
        if filepath.suffix.lower() not in [e.lower() for e in self.SUPPORTED_EXTENSIONS]:
            return ExtractionResult(
                success=False,
                error_message=f"Estensione {filepath.suffix} non supportata da {self.CAD_NAME}"
            )

        # Verifica esistenza file
        if not filepath.exists():
            return ExtractionResult(
                success=False,
                error_message=f"File non trovato: {filepath}"
            )

        try:
            # Connetti al CAD
            if not self._connect():
                return ExtractionResult(
                    success=False,
                    error_message=f"Impossibile connettersi a {self.CAD_NAME}"
                )

            # Estrai signature
            signature = self._extract_from_document(filepath)

            # Aggiungi metadati comuni
            signature.cad_type = self.CAD_NAME
            signature.filepath = str(filepath)
            signature.filename = filepath.name
            signature.file_extension = filepath.suffix.lower()
            signature.file_hash = self._compute_file_hash(filepath)
            signature.extractor_version = self.VERSION
            signature.extraction_warnings = self._warnings.copy()

            elapsed = (time.time() - start_time) * 1000

            return ExtractionResult(
                success=True,
                signature=signature,
                warnings=self._warnings.copy(),
                extraction_time_ms=elapsed
            )

        except ExtractionError as e:
            return ExtractionResult(
                success=False,
                error_message=str(e),
                warnings=self._warnings.copy()
            )
        except Exception as e:
            return ExtractionResult(
                success=False,
                error_message=f"Errore imprevisto: {str(e)}\n{traceback.format_exc()}",
                warnings=self._warnings.copy()
            )
        finally:
            try:
                self._disconnect()
            except Exception:
                pass

    def _add_warning(self, message: str):
        """Aggiunge un warning alla lista."""
        self._warnings.append(message)
        print(f"⚠️ [{self.CAD_NAME}] {message}")

    @staticmethod
    def _compute_file_hash(filepath: Path) -> str:
        """Calcola hash MD5 del file."""
        try:
            hasher = hashlib.md5()
            with open(filepath, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    hasher.update(chunk)
            return hasher.hexdigest()
        except Exception:
            return ""

    @staticmethod
    def _safe_get(obj, attr: str, default: Any = None) -> Any:
        """Ottiene un attributo in modo sicuro."""
        try:
            return getattr(obj, attr, default)
        except Exception:
            return default

    @staticmethod
    def _safe_call(obj, method: str, *args, default: Any = None) -> Any:
        """Chiama un metodo in modo sicuro."""
        try:
            func = getattr(obj, method, None)
            if func and callable(func):
                return func(*args)
            return default
        except Exception:
            return default


class COMExtractorMixin:
    """
    Mixin per estrattori basati su COM (Windows).

    Fornisce utility comuni per gestire connessioni COM.
    """

    COM_PROG_ID: str = ""  # Da sovrascrivere (es. "SolidEdge.Application")

    def _get_or_create_app(self):
        """Ottiene un'istanza esistente o ne crea una nuova."""
        try:
            import win32com.client
            import pythoncom
            pythoncom.CoInitialize()
        except ImportError:
            raise ExtractionError(
                "Libreria win32com non disponibile. Installa: pip install pywin32",
                cad_type=getattr(self, 'CAD_NAME', 'COM')
            )

        # Prova a connettersi a istanza esistente
        try:
            app = win32com.client.GetActiveObject(self.COM_PROG_ID)
            return app, False  # False = non creato nuovo
        except Exception:
            pass

        # Crea nuova istanza
        try:
            app = win32com.client.Dispatch(self.COM_PROG_ID)
            return app, True  # True = creato nuovo
        except Exception as e:
            raise ExtractionError(
                f"Impossibile avviare {self.COM_PROG_ID}: {str(e)}",
                cad_type=getattr(self, 'CAD_NAME', 'COM')
            )

    def _safe_com_call(self, func, *args, default=None):
        """Esegue una chiamata COM in modo sicuro."""
        try:
            return func(*args)
        except Exception:
            return default

    def _get_com_property(self, obj, prop_name: str, default=None):
        """Ottiene una proprietà COM in modo sicuro."""
        try:
            return getattr(obj, prop_name)
        except Exception:
            return default

    def _iterate_com_collection(self, collection):
        """Itera su una collezione COM in modo sicuro."""
        try:
            count = getattr(collection, 'Count', 0)
            for i in range(1, count + 1):  # COM usa indici 1-based
                try:
                    yield collection.Item(i)
                except Exception:
                    continue
        except Exception:
            return

