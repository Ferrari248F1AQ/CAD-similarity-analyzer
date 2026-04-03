# -*- coding: utf-8 -*-
"""
Script per l'analisi delle sessioni d'esame CAD e generazione di una tabella LaTeX.

Utilizza il CAD Similarity Analyzer esistente (solid_edge_similarity_v2) per calcolare
le similarità pairwise tra i file CAD di ogni sessione d'esame.

Usage:
    python exam_session_analysis.py --root <dataset_root> --out_tex <output.tex>
    python exam_session_analysis.py --root <dataset_root> --out_tex <output.tex> --track_regex "A(\d+)"
    python exam_session_analysis.py --root <dataset_root> --out_tex <output.tex> --plag_threshold 75.0
"""

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field
import statistics

# Configura logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Importa il modulo di analisi similarità esistente
try:
    from solid_edge_similarity_v2 import (
        extract_signature,
        compute_similarity,
        FeatureSignature,
        HAS_COM
    )
    ANALYZER_AVAILABLE = True
except ImportError as e:
    logger.warning(f"Impossibile importare solid_edge_similarity_v2: {e}")
    ANALYZER_AVAILABLE = False
    HAS_COM = False

# Fallback: prova a importare dal modulo v1
if not ANALYZER_AVAILABLE:
    try:
        from solid_edge_similarity import (
            extract_signature,
            compute_similarity,
            FeatureSignature,
            HAS_COM
        )
        ANALYZER_AVAILABLE = True
        logger.info("Usando solid_edge_similarity (v1) come fallback")
    except ImportError:
        pass

# Verifica che win32com sia disponibile (necessario per l'analisi COM)
try:
    import win32com.client
    HAS_WIN32COM = True
except ImportError:
    HAS_WIN32COM = False


# ============================================================================
# TRACK EXTRACTION
# ============================================================================

# Pattern di default per l'estrazione del track ID dai nomi file
# Cerca pattern comuni come "A001", "A01", "TRACCIA_1", "Track1", ecc.
DEFAULT_TRACK_PATTERNS = [
    r'A(\d{2,4})',           # A001, A01, A0001
    r'TRACCIA[_\s]?(\d+)',   # TRACCIA_1, TRACCIA 1, TRACCIA1
    r'TRACK[_\s]?(\d+)',     # TRACK_1, TRACK 1, TRACK1
    r'T(\d{2,4})',           # T001, T01
    r'ES[_\s]?(\d+)',        # ES_1, ES1 (esercizio)
    r'EX[_\s]?(\d+)',        # EX_1, EX1 (exercise)
]

OUTLIER_LABEL = "OUTLIER"


def extract_track_id(filename: str, track_regex: Optional[str] = None) -> str:
    """
    Estrae l'identificativo del track (traccia d'esame) dal nome del file.

    Args:
        filename: Nome del file (senza estensione o con estensione)
        track_regex: Pattern regex personalizzato. Se fornito, usa solo questo.
                     Il primo gruppo di cattura sarà usato come track ID.

    Returns:
        Track ID come stringa, oppure OUTLIER_LABEL se non trovato.
    """
    # Rimuovi estensione se presente
    name_without_ext = Path(filename).stem

    # Prepara i pattern da usare
    if track_regex:
        patterns = [track_regex]
    else:
        patterns = DEFAULT_TRACK_PATTERNS

    # Cerca match con i pattern
    for pattern in patterns:
        try:
            match = re.search(pattern, name_without_ext, re.IGNORECASE)
            if match:
                # Usa il primo gruppo di cattura, o l'intero match se non ci sono gruppi
                track_id = match.group(1) if match.lastindex else match.group(0)
                # Normalizza: rimuovi zeri iniziali ma mantieni almeno una cifra
                if track_id.isdigit():
                    track_id = str(int(track_id))
                return f"T{track_id}" if track_id.isdigit() else track_id
        except re.error as e:
            logger.warning(f"Pattern regex non valido '{pattern}': {e}")

    # Nessun match trovato → outlier
    return OUTLIER_LABEL


# ============================================================================
# SESSION DATA STRUCTURES
# ============================================================================

@dataclass
class SessionStatistics:
    """Statistiche aggregate per una sessione d'esame."""
    session_name: str
    total_students: int = 0
    number_of_assigned_tracks: int = 0
    average_similarity: float = 0.0
    maximum_similarity: float = 0.0
    minimum_similarity: float = 0.0
    similarity_std_dev: float = 0.0
    confirmed_plagiarism_cases: int = 0

    # Dati grezzi per debug
    files_processed: int = 0
    pairs_analyzed: int = 0
    errors: List[str] = field(default_factory=list)


# ============================================================================
# FILE COLLECTION
# ============================================================================

def collect_par_files(session_folder: Path) -> List[Path]:
    """
    Raccoglie tutti i file .par da una cartella di sessione.

    Logica:
    - Se esistono sottocartelle "MECCANICI" e/o "NON MECCANICI", raccoglie da queste.
    - Altrimenti, raccoglie direttamente dalla cartella sessione.

    Args:
        session_folder: Path della cartella sessione

    Returns:
        Lista di Path ai file .par trovati
    """
    par_files = []

    # Cerca sottocartelle MECCANICI e NON MECCANICI (case-insensitive)
    subfolders_found = []
    for subfolder_name in ["MECCANICI", "NON MECCANICI"]:
        # Cerca in modo case-insensitive
        for item in session_folder.iterdir():
            if item.is_dir() and item.name.upper() == subfolder_name.upper():
                subfolders_found.append(item)
                break

    if subfolders_found:
        # Raccogli da sottocartelle specifiche
        for subfolder in subfolders_found:
            for f in subfolder.rglob('*'):
                if f.is_file() and f.suffix.lower() == '.par':
                    par_files.append(f)
        logger.info(f"  Trovate {len(par_files)} file .par in sottocartelle MECCANICI/NON MECCANICI")
    else:
        # Raccogli direttamente dalla cartella sessione (incluso sottocartelle)
        for f in session_folder.rglob('*'):
            if f.is_file() and f.suffix.lower() == '.par':
                par_files.append(f)
        logger.info(f"  Trovate {len(par_files)} file .par nella cartella sessione")

    return par_files


# ============================================================================
# SIMILARITY ANALYSIS
# ============================================================================

def analyze_session(
    session_folder: Path,
    track_regex: Optional[str] = None,
    plag_threshold: float = 80.0,
    solid_edge_app=None
) -> SessionStatistics:
    """
    Analizza una singola sessione d'esame.

    Args:
        session_folder: Path della cartella sessione
        track_regex: Pattern regex per estrazione track ID
        plag_threshold: Soglia percentuale per considerare un caso come plagio (0-100)
        solid_edge_app: Istanza COM di Solid Edge (opzionale, verrà creata se necessario)

    Returns:
        SessionStatistics con i risultati dell'analisi
    """
    session_name = normalize_session_name(session_folder.name)
    stats = SessionStatistics(session_name=session_name)

    # Raccogli file .par
    par_files = collect_par_files(session_folder)

    if not par_files:
        logger.warning(f"  Nessun file .par trovato nella sessione '{session_name}'")
        stats.errors.append("Nessun file .par trovato")
        return stats

    stats.total_students = len(par_files)

    # Estrai track IDs e conta i track unici
    track_ids = {}
    for f in par_files:
        track_id = extract_track_id(f.name, track_regex)
        track_ids[f] = track_id

    unique_tracks = set(track_ids.values())
    stats.number_of_assigned_tracks = len(unique_tracks)
    logger.info(f"  Track unici: {stats.number_of_assigned_tracks} ({unique_tracks})")

    # Se c'è solo un file, non possiamo calcolare similarità pairwise
    if len(par_files) < 2:
        logger.warning(f"  Solo {len(par_files)} file trovati, impossibile calcolare similarità pairwise")
        stats.errors.append("Meno di 2 file, impossibile calcolare similarità")
        return stats

    # Estrai firme dai file CAD
    signatures: Dict[Path, FeatureSignature] = {}

    for f in par_files:
        try:
            sig = extract_signature(f, app=solid_edge_app)
            signatures[f] = sig
            stats.files_processed += 1
        except Exception as e:
            logger.error(f"    Errore estrazione firma per {f.name}: {e}")
            stats.errors.append(f"Errore {f.name}: {str(e)[:50]}")

    if len(signatures) < 2:
        logger.warning(f"  Solo {len(signatures)} firme estratte con successo")
        stats.errors.append("Firme insufficienti per analisi")
        return stats

    # Calcola similarità pairwise
    similarity_values: List[float] = []
    files_list = list(signatures.keys())

    for i, file1 in enumerate(files_list):
        for file2 in files_list[i + 1:]:
            try:
                sim_result = compute_similarity(signatures[file1], signatures[file2])
                # L'output è in [0,1], converti in percentuale [0,100]
                similarity_pct = sim_result['overall'] * 100.0
                similarity_values.append(similarity_pct)
                stats.pairs_analyzed += 1
            except Exception as e:
                logger.error(f"    Errore calcolo similarità {file1.name} vs {file2.name}: {e}")

    if not similarity_values:
        logger.warning(f"  Nessun valore di similarità calcolato")
        stats.errors.append("Nessuna similarità calcolata")
        return stats

    # Calcola statistiche
    stats.average_similarity = statistics.mean(similarity_values)
    stats.maximum_similarity = max(similarity_values)
    stats.minimum_similarity = min(similarity_values)

    if len(similarity_values) > 1:
        stats.similarity_std_dev = statistics.stdev(similarity_values)
    else:
        stats.similarity_std_dev = 0.0

    # Conta casi di plagio proxy (similarità >= soglia)
    stats.confirmed_plagiarism_cases = sum(1 for s in similarity_values if s >= plag_threshold)

    logger.info(f"  Statistiche: avg={stats.average_similarity:.1f}%, "
                f"max={stats.maximum_similarity:.1f}%, "
                f"min={stats.minimum_similarity:.1f}%, "
                f"std={stats.similarity_std_dev:.1f}%, "
                f"plagio={stats.confirmed_plagiarism_cases}")

    return stats


# ============================================================================
# SESSION NAME NORMALIZATION
# ============================================================================

def normalize_session_name(folder_name: str) -> str:
    """
    Normalizza il nome della sessione per presentazione nella tabella.

    Args:
        folder_name: Nome originale della cartella

    Returns:
        Nome normalizzato (trimmed, sanitizzato)
    """
    # Rimuovi spazi extra
    name = folder_name.strip()

    # Sostituisci underscore e trattini multipli con spazio singolo
    name = re.sub(r'[_\-]+', ' ', name)

    # Rimuovi spazi multipli
    name = re.sub(r'\s+', ' ', name)

    # Capitalizza ogni parola
    name = name.title()

    # Escape caratteri speciali LaTeX
    name = escape_latex(name)

    return name


def escape_latex(text: str) -> str:
    """Escape caratteri speciali per LaTeX."""
    replacements = {
        '&': r'\&',
        '%': r'\%',
        '$': r'\$',
        '#': r'\#',
        '_': r'\_',
        '{': r'\{',
        '}': r'\}',
        '~': r'\textasciitilde{}',
        '^': r'\textasciicircum{}',
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    return text


# ============================================================================
# LATEX TABLE GENERATION
# ============================================================================

def generate_latex_table(sessions_stats: List[SessionStatistics]) -> str:
    """
    Genera una tabella LaTeX in stile booktabs con le statistiche delle sessioni.

    Args:
        sessions_stats: Lista di SessionStatistics, una per sessione

    Returns:
        Stringa contenente la tabella LaTeX completa
    """
    # Header della tabella
    latex_lines = [
        r"% Tabella generata automaticamente da exam_session_analysis.py",
        r"% Richiede i pacchetti: booktabs, siunitx (opzionale per allineamento numeri)",
        r"\begin{table}[htbp]",
        r"    \centering",
        r"    \caption{Statistiche di similarità per sessione d'esame CAD}",
        r"    \label{tab:exam_similarity_stats}",
        r"    \small",
        r"    \begin{tabular}{l r r r r r r r}",
        r"        \toprule",
        r"        Session & \shortstack{Total\\students} & \shortstack{Assigned\\tracks} & \shortstack{Avg sim.\\(\%)} & \shortstack{Max sim.\\(\%)} & \shortstack{Min sim.\\(\%)} & \shortstack{Std dev\\(\%)} & \shortstack{Plagiarism\\cases} \\",
        r"        \midrule",
    ]

    # Righe dati
    for stats in sessions_stats:
        row = (
            f"        {stats.session_name} & "
            f"{stats.total_students} & "
            f"{stats.number_of_assigned_tracks} & "
            f"{stats.average_similarity:.1f} & "
            f"{stats.maximum_similarity:.1f} & "
            f"{stats.minimum_similarity:.1f} & "
            f"{stats.similarity_std_dev:.1f} & "
            f"{stats.confirmed_plagiarism_cases} \\\\"
        )
        latex_lines.append(row)

    # Footer della tabella
    latex_lines.extend([
        r"        \bottomrule",
        r"    \end{tabular}",
        r"\end{table}",
    ])

    return '\n'.join(latex_lines)


# ============================================================================
# MAIN PROCESSING
# ============================================================================

def process_dataset(
    root_path: Path,
    track_regex: Optional[str] = None,
    plag_threshold: float = 80.0
) -> List[SessionStatistics]:
    """
    Processa l'intero dataset di sessioni d'esame.

    Args:
        root_path: Path della cartella root contenente le cartelle sessione
        track_regex: Pattern regex per estrazione track ID
        plag_threshold: Soglia percentuale per considerare plagio

    Returns:
        Lista di SessionStatistics, una per ogni sessione processata
    """
    if not root_path.exists():
        raise FileNotFoundError(f"La cartella root non esiste: {root_path}")

    if not root_path.is_dir():
        raise NotADirectoryError(f"Il path non è una directory: {root_path}")

    # Trova le cartelle sessione (sottocartelle dirette del root)
    session_folders = [
        f for f in root_path.iterdir()
        if f.is_dir() and not f.name.startswith('.')
    ]

    if not session_folders:
        raise ValueError(f"Nessuna cartella sessione trovata in: {root_path}")

    logger.info(f"Trovate {len(session_folders)} cartelle sessione")

    # Inizializza connessione COM a Solid Edge (se disponibile)
    solid_edge_app = None
    if ANALYZER_AVAILABLE and HAS_COM and HAS_WIN32COM:
        try:
            # Prova a connettersi a un'istanza esistente
            solid_edge_app = win32com.client.GetActiveObject("SolidEdge.Application")
            logger.info("Connesso a istanza Solid Edge esistente")
        except:
            try:
                # Avvia nuova istanza
                solid_edge_app = win32com.client.Dispatch("SolidEdge.Application")
                solid_edge_app.Visible = False  # Nascondi l'applicazione
                logger.info("Avviata nuova istanza Solid Edge")
            except Exception as e:
                logger.warning(f"Impossibile avviare Solid Edge: {e}")
                solid_edge_app = None

        if solid_edge_app:
            solid_edge_app.DisplayAlerts = False

    # Processa ogni sessione
    all_stats: List[SessionStatistics] = []

    for i, session_folder in enumerate(sorted(session_folders), 1):
        logger.info(f"\n[{i}/{len(session_folders)}] Processando sessione: {session_folder.name}")

        try:
            stats = analyze_session(
                session_folder,
                track_regex=track_regex,
                plag_threshold=plag_threshold,
                solid_edge_app=solid_edge_app
            )
            all_stats.append(stats)
        except Exception as e:
            logger.error(f"  Errore fatale nella sessione: {e}")
            # Crea statistiche vuote con errore
            stats = SessionStatistics(
                session_name=normalize_session_name(session_folder.name),
                errors=[str(e)]
            )
            all_stats.append(stats)

    return all_stats


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Analizza sessioni d'esame CAD e genera tabella LaTeX con statistiche di similarità.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Esempi:
  python exam_session_analysis.py --root ./esami --out_tex results.tex
  python exam_session_analysis.py --root ./esami --out_tex results.tex --track_regex "A(\\d+)"
  python exam_session_analysis.py --root ./esami --out_tex results.tex --plag_threshold 75.0

Struttura dataset attesa:
  <root>/
    <sessione_1>/
      [MECCANICI/]      # opzionale
      [NON MECCANICI/]  # opzionale
      file1.par
      file2.par
      ...
    <sessione_2>/
      ...
        """
    )

    parser.add_argument(
        '--root',
        type=Path,
        required=True,
        help='Cartella root del dataset contenente le cartelle delle sessioni d\'esame'
    )

    parser.add_argument(
        '--out_tex',
        type=Path,
        required=True,
        help='Path del file LaTeX di output'
    )

    parser.add_argument(
        '--track_regex',
        type=str,
        default=None,
        help='Pattern regex per estrarre il track ID dai nomi file. '
             'Il primo gruppo di cattura sarà usato. '
             'Default: cerca pattern comuni come A001, TRACCIA_1, ecc.'
    )

    parser.add_argument(
        '--plag_threshold',
        type=float,
        default=80.0,
        help='Soglia di similarità (in percentuale, 0-100) per considerare '
             'una coppia come potenziale plagio. Default: 80.0'
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Output più dettagliato'
    )

    parser.add_argument(
        '--print',
        action='store_true',
        dest='print_table',
        help='Stampa la tabella LaTeX anche su stdout'
    )

    args = parser.parse_args()

    # Configura livello di logging
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Verifica disponibilità analyzer
    if not ANALYZER_AVAILABLE:
        logger.error("Il modulo di analisi CAD non è disponibile!")
        logger.error("Assicurati che solid_edge_similarity_v2.py sia nella stessa directory.")
        sys.exit(1)

    if not HAS_COM or not HAS_WIN32COM:
        logger.warning("pywin32 non disponibile - l'analisi COM potrebbe non funzionare")

    # Processa il dataset
    logger.info(f"Inizio analisi dataset: {args.root}")
    logger.info(f"Soglia plagio: {args.plag_threshold}%")
    if args.track_regex:
        logger.info(f"Track regex: {args.track_regex}")

    try:
        all_stats = process_dataset(
            root_path=args.root,
            track_regex=args.track_regex,
            plag_threshold=args.plag_threshold
        )
    except Exception as e:
        logger.error(f"Errore durante l'elaborazione: {e}")
        sys.exit(1)

    if not all_stats:
        logger.error("Nessuna sessione elaborata con successo")
        sys.exit(1)

    # Genera tabella LaTeX
    latex_table = generate_latex_table(all_stats)

    # Salva su file
    try:
        args.out_tex.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out_tex, 'w', encoding='utf-8') as f:
            f.write(latex_table)
        logger.info(f"\nTabella LaTeX salvata in: {args.out_tex}")
    except Exception as e:
        logger.error(f"Errore salvataggio file LaTeX: {e}")
        sys.exit(1)

    # Stampa su stdout se richiesto
    if args.print_table:
        print("\n" + "="*80)
        print("TABELLA LATEX GENERATA:")
        print("="*80)
        print(latex_table)

    # Riepilogo finale
    print("\n" + "="*80)
    print("RIEPILOGO ANALISI")
    print("="*80)

    total_students = sum(s.total_students for s in all_stats)
    total_plagiarism = sum(s.confirmed_plagiarism_cases for s in all_stats)
    sessions_with_data = [s for s in all_stats if s.pairs_analyzed > 0]

    print(f"Sessioni processate:       {len(all_stats)}")
    print(f"Sessioni con dati validi:  {len(sessions_with_data)}")
    print(f"Totale studenti:           {total_students}")
    print(f"Totale casi plagio (proxy): {total_plagiarism}")

    if sessions_with_data:
        avg_of_avgs = statistics.mean(s.average_similarity for s in sessions_with_data)
        print(f"Media similarità globale:  {avg_of_avgs:.1f}%")

    print(f"\nOutput: {args.out_tex}")
    print("="*80)


if __name__ == '__main__':
    main()
