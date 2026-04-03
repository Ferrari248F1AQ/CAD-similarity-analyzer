# -*- coding: utf-8 -*-
"""
Web application for multi-platform CAD file similarity analysis.
Supports: Solid Edge, SolidWorks, Inventor, CATIA, FreeCAD, Fusion 360
"""

import sys
import os
from pathlib import Path

# FIX encoding per Windows: imposta la variabile d'ambiente prima degli import
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
os.environ.setdefault('PYTHONUTF8', '1')

from flask import Flask, render_template, request, jsonify, make_response
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
import threading
import time
import json
import shutil
import re
from datetime import datetime
from collections import Counter
from typing import Any
import numpy as np

# Per inizializzazione COM in thread Flask
try:
    import pythoncom
    HAS_PYTHONCOM = True
except ImportError:
    HAS_PYTHONCOM = False

# Aggiungi parent directory al path per importare il modulo principale
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import modulo similarity originale (per retrocompatibilitÃ )
from solid_edge_similarity_v2 import (
    extract_signature,
    compute_similarity,
    compute_raw_scores,
    combine_scores,
    analyze_directory,
    FeatureSignature,
    HAS_COM,
    CACHE_DIR,
    load_weights,
    save_weights,
    DEFAULT_WEIGHTS
)

try:
    from scipy.optimize import minimize, differential_evolution
    HAS_SCIPY = True
except Exception:
    minimize = None
    differential_evolution = None
    HAS_SCIPY = False

OPT_WEIGHT_NAMES = [
    'author_match',
    'feature_count_similarity',
    'feature_type_similarity',
    'style_similarity',
    'bigram_similarity',
    'trigram_similarity',
    'lcs_similarity',
    'feature_names_similarity',
    'geometry_2d_similarity',
    'constraint_2d_similarity',
    'constraint_ratio_similarity',
    'sketch_parametric_similarity',
]
OPT_NON_AUTHOR_WEIGHT_NAMES = [n for n in OPT_WEIGHT_NAMES if n != 'author_match']
OPT_NON_AUTHOR_INDICES = [i for i, n in enumerate(OPT_WEIGHT_NAMES) if n != 'author_match']
OPT_AUTHOR_INDEX = OPT_WEIGHT_NAMES.index('author_match') if 'author_match' in OPT_WEIGHT_NAMES else -1

# Cache in-memory dei componenti raw usati dall'optimizer:
# evita di ricalcolare compute_raw_scores per le stesse coppie etichettate a ogni run.
# Key: (raw_config_key, session, normalized_file_a, normalized_file_b)
optimizer_raw_components_cache: dict[tuple[str, str, str, str], dict] = {}

#  Sistema etichettatura plagio - implementato direttamente
from enum import Enum

class PlagiarismLabel(Enum):
    """Labels for plagiarism cases - assigned ONLY manually."""
    CONFIRMED_PLAGIARISM = "CONFIRMED_PLAGIARISM"
    NOT_PLAGIARISM = "NOT_PLAGIARISM"
    UNDECIDED = "UNDECIDED"

# Default path for labels database
DEFAULT_LABELS_DB_PATH = Path.home() / '.cache' / 'cad_similarity_analyzer' / 'plagiarism_labels.json'
RAW_SCORER_VERSION = 5

def normalize_pair_key(session: str, file_a: str, file_b: str) -> str:
    """Generate a normalized key for a file pair (order-independent)."""
    files_sorted = tuple(sorted([file_a, file_b]))
    return f"{session}|{files_sorted[0]}|{files_sorted[1]}"


def _normalize_pair_storage_path(path_value: str) -> str:
    raw = str(path_value or '').strip()
    if not raw:
        return ''
    normalized = raw.replace('/', '\\')
    normalized = re.sub(r'\\+', r'\\', normalized)
    return normalized.lower()


def normalize_pair_storage_key(session: str,
                               file_a: str,
                               file_b: str,
                               path_a: str = '',
                               path_b: str = '') -> str:
    """Storage key for labels: path-aware when available, legacy-compatible otherwise."""
    base_key = normalize_pair_key(session, file_a, file_b)
    pa = _normalize_pair_storage_path(path_a)
    pb = _normalize_pair_storage_path(path_b)
    if not pa or not pb:
        return base_key
    paths_sorted = tuple(sorted([pa, pb]))
    return f"{base_key}|PATH|{paths_sorted[0]}|{paths_sorted[1]}"


def load_labels_db(labels_path: Path) -> dict:
    """Load the manual labels database."""
    if not labels_path.exists():
        return {}
    try:
        #  Controlla se il file Ã¨ vuoto
        if labels_path.stat().st_size == 0:
            print(f" Labels DB file is empty: {labels_path}")
            return {}

        with open(labels_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip():
                print(f" Labels DB file contains only whitespace: {labels_path}")
                return {}

            try:
                return json.loads(content)
            except json.JSONDecodeError as je:
                print(f" Invalid JSON in labels DB: {labels_path}")
                print(f"   Error: {je}")
                # Backup del file corrotto
                import time
                backup_path = labels_path.with_suffix(f'.json.corrupted.{int(time.time())}')
                labels_path.rename(backup_path)
                print(f"   Corrupted file backed up to: {backup_path}")
                return {}
    except Exception as e:
        print(f" Error loading labels database: {e}")
        return {}

def save_labels_db(labels_path: Path, labels: dict) -> bool:
    """Save the labels database."""
    try:
        labels_path.parent.mkdir(parents=True, exist_ok=True)
        with open(labels_path, 'w', encoding='utf-8') as f:
            json.dump(labels, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f" Error saving labels database: {e}")
        return False

def set_label(labels_db: dict,
              session: str,
              file_a: str,
              file_b: str,
              label: PlagiarismLabel,
              notes: str = "",
              path_a: str = "",
              path_b: str = "") -> str:
    """Set a label for a file pair."""
    base_key = normalize_pair_key(session, file_a, file_b)
    key = normalize_pair_storage_key(
        session=session,
        file_a=file_a,
        file_b=file_b,
        path_a=path_a,
        path_b=path_b,
    )

    if key != base_key and base_key in labels_db:
        legacy_entry = labels_db.get(base_key, {}) or {}
        legacy_path_a = _normalize_pair_storage_path(legacy_entry.get('path_a', ''))
        legacy_path_b = _normalize_pair_storage_path(legacy_entry.get('path_b', ''))
        if not (legacy_path_a and legacy_path_b):
            labels_db.pop(base_key, None)

    previous = labels_db.get(key, {}) or {}
    stored_path_a = str(path_a or previous.get('path_a', '') or '')
    stored_path_b = str(path_b or previous.get('path_b', '') or '')
    labels_db[key] = {
        'session': session,
        'file_a': file_a,
        'file_b': file_b,
        'path_a': stored_path_a,
        'path_b': stored_path_b,
        'label': label.value,
        'timestamp': datetime.now().isoformat(),
        'notes': notes
    }
    return key


def _parse_similarity_hint_from_notes(notes: str) -> float | None:
    raw = str(notes or '')
    if not raw:
        return None
    m = re.search(r'([0-9]+(?:[.,][0-9]+)?)\s*%', raw)
    if not m:
        return None
    try:
        val = float(m.group(1).replace(',', '.')) / 100.0
    except Exception:
        return None
    if not np.isfinite(val):
        return None
    return max(0.0, min(1.0, float(val)))


def migrate_legacy_labels_with_paths(labels_db: dict,
                                     pairs: list[dict] | None,
                                     analysis_root: Path | None = None) -> dict:
    """
    Upgrade legacy labels without path_a/path_b by resolving exact analyzed pairs.

    Strategy:
    1) strict match on (session, normalized file_a/file_b)
    2) fallback on global normalized file_a/file_b
    3) if multiple candidates, disambiguate by similarity hint in notes (if robust)
    """
    stats = {
        'labels_total': len(labels_db or {}),
        'considered': 0,
        'already_with_paths': 0,
        'migrated': 0,
        'ambiguous': 0,
        'unresolved': 0,
        'similarity_disambiguated': 0,
        'saved': False,
    }
    if not isinstance(labels_db, dict) or not labels_db:
        return stats

    legacy_targets: list[tuple[str, dict]] = []
    for key, entry in list(labels_db.items()):
        label_value = str((entry or {}).get('label', '') or '').strip()
        if label_value not in ('CONFIRMED_PLAGIARISM', 'NOT_PLAGIARISM'):
            continue
        stats['considered'] += 1
        pa = _normalize_pair_storage_path((entry or {}).get('path_a', '') or (entry or {}).get('path1', ''))
        pb = _normalize_pair_storage_path((entry or {}).get('path_b', '') or (entry or {}).get('path2', ''))
        if pa and pb:
            stats['already_with_paths'] += 1
            continue
        legacy_targets.append((key, entry))

    if not legacy_targets:
        return stats

    target_file_keys: set[tuple[str, str]] = set()
    for _, entry in legacy_targets:
        fa = _normalize_filename(str((entry or {}).get('file_a', '') or ''))
        fb = _normalize_filename(str((entry or {}).get('file_b', '') or ''))
        if fa and fb:
            target_file_keys.add(tuple(sorted([fa, fb])))

    if not target_file_keys:
        stats['unresolved'] += len(legacy_targets)
        return stats

    all_pairs = pairs or []
    if not all_pairs:
        stats['unresolved'] += len(legacy_targets)
        return stats

    by_session_file: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    by_file: dict[tuple[str, str], list[dict[str, Any]]] = {}
    session_cache: dict[str, tuple[str, str]] = {}

    def _session_cached(path_raw: str) -> tuple[str, str]:
        key = _normalize_pair_storage_path(path_raw)
        if not key:
            return '', ''
        if key in session_cache:
            return session_cache[key]
        sess_raw = _infer_session_from_filepath(path_raw, analysis_root)
        sess_ns = _normalize_session_name(sess_raw)
        session_cache[key] = (sess_raw, sess_ns)
        return sess_raw, sess_ns

    for p in all_pairs:
        path1_raw = str((p or {}).get('path1', '') or '')
        path2_raw = str((p or {}).get('path2', '') or '')
        path1_norm = _normalize_pair_storage_path(path1_raw)
        path2_norm = _normalize_pair_storage_path(path2_raw)
        if not path1_norm or not path2_norm:
            continue
        file1_norm = _normalize_filename((p or {}).get('file1', '') or Path(path1_raw).name)
        file2_norm = _normalize_filename((p or {}).get('file2', '') or Path(path2_raw).name)
        if not file1_norm or not file2_norm:
            continue

        key_file = tuple(sorted([file1_norm, file2_norm]))
        if key_file not in target_file_keys:
            continue
        s1_raw, ns1 = _session_cached(path1_raw)
        s2_raw, ns2 = _session_cached(path2_raw)

        candidate = {
            'file1_norm': file1_norm,
            'file2_norm': file2_norm,
            'path1_raw': path1_raw,
            'path2_raw': path2_raw,
            'path1_norm': path1_norm,
            'path2_norm': path2_norm,
            'similarity': _safe_float((p or {}).get('similarity', 0.0), 0.0),
            'ns1': ns1,
            'ns2': ns2,
            'session_raw': s1_raw if ns1 and ns1 == ns2 else '',
            'session_ns': ns1 if ns1 and ns1 == ns2 else '',
        }
        by_file.setdefault(key_file, []).append(candidate)
        if candidate['session_ns']:
            by_session_file.setdefault((candidate['session_ns'], key_file[0], key_file[1]), []).append(candidate)

    def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for c in candidates:
            ck = tuple(sorted([str(c.get('path1_norm', '')), str(c.get('path2_norm', ''))]))
            if not ck[0] or not ck[1]:
                continue
            if ck in seen:
                continue
            seen.add(ck)
            out.append(c)
        return out

    for old_key, entry in legacy_targets:
        label_value = str((entry or {}).get('label', '') or '').strip()
        try:
            label_enum = PlagiarismLabel(label_value)
        except Exception:
            stats['unresolved'] += 1
            continue

        fa_raw = str((entry or {}).get('file_a', '') or '')
        fb_raw = str((entry or {}).get('file_b', '') or '')
        fa = _normalize_filename(fa_raw)
        fb = _normalize_filename(fb_raw)
        if not fa or not fb:
            stats['unresolved'] += 1
            continue
        files_sorted = tuple(sorted([fa, fb]))

        session_raw = str((entry or {}).get('session', '') or '').strip()
        session_ns = _normalize_session_name(session_raw)
        notes = str((entry or {}).get('notes', '') or '')
        sim_hint = _parse_similarity_hint_from_notes(notes)

        candidates = []
        if session_ns:
            candidates.extend(by_session_file.get((session_ns, files_sorted[0], files_sorted[1]), []))
        if not candidates:
            candidates.extend(by_file.get(files_sorted, []))
        candidates = _dedupe_candidates(candidates)

        if not candidates:
            stats['unresolved'] += 1
            continue

        chosen = None
        chosen_by_similarity = False
        if len(candidates) == 1:
            chosen = candidates[0]
        elif sim_hint is not None:
            ranked = sorted(
                ((abs(float(c.get('similarity', 0.0)) - sim_hint), c) for c in candidates),
                key=lambda item: (item[0], str(item[1].get('path1_norm', '')), str(item[1].get('path2_norm', '')))
            )
            if ranked:
                best_diff = float(ranked[0][0])
                second_diff = float(ranked[1][0]) if len(ranked) > 1 else 1.0
                # Robust disambiguation: best close enough and clearly better than second.
                if best_diff <= 0.02 and (len(ranked) == 1 or (second_diff - best_diff) >= 0.003):
                    chosen = ranked[0][1]
                    chosen_by_similarity = True

        if chosen is None:
            stats['ambiguous'] += 1
            continue

        path_a = str(chosen.get('path1_raw', '') or '')
        path_b = str(chosen.get('path2_raw', '') or '')
        cf1 = str(chosen.get('file1_norm', '') or '')
        cf2 = str(chosen.get('file2_norm', '') or '')
        if fa == cf2 and fb == cf1:
            path_a, path_b = path_b, path_a

        resolved_session = session_raw
        chosen_session_raw = str(chosen.get('session_raw', '') or '')
        chosen_session_ns = str(chosen.get('session_ns', '') or '')
        if chosen_session_raw:
            if not _looks_like_exam_session_name(resolved_session):
                resolved_session = chosen_session_raw
            elif chosen_session_ns and _normalize_session_name(resolved_session) != chosen_session_ns:
                resolved_session = chosen_session_raw
        if not resolved_session:
            resolved_session = chosen_session_raw or session_raw or 'unknown'

        # Non-distruttivo: aggiorna solo i campi path/session nell'entry legacy,
        # senza rinominare la chiave del DB (evita collisioni/sovrascritture).
        updated_entry = dict(entry or {})
        updated_entry['session'] = resolved_session
        updated_entry['file_a'] = Path(fa_raw).name if fa_raw else fa
        updated_entry['file_b'] = Path(fb_raw).name if fb_raw else fb
        updated_entry['label'] = label_enum.value
        updated_entry['notes'] = notes
        updated_entry['path_a'] = path_a
        updated_entry['path_b'] = path_b
        updated_entry['timestamp'] = str(updated_entry.get('timestamp') or datetime.now().isoformat())
        labels_db[old_key] = updated_entry
        stats['migrated'] += 1
        if chosen_by_similarity:
            stats['similarity_disambiguated'] += 1

    return stats


def _normalize_session_name(s: str) -> str:
    s = (s or '').strip().replace('_', ' ')
    s = re.sub(r'\s+', ' ', s)
    return s.upper()


def _normalize_filename(name: str) -> str:
    if not name:
        return ''
    base = Path(str(name)).name.strip().replace('_', ' ')
    base = re.sub(r'\s+', ' ', base)
    return base.lower()


def build_pair_label_lookup_keys(session: str,
                                 file_a: str,
                                 file_b: str,
                                 path_a: str = '',
                                 path_b: str = '') -> list[str]:
    """Build deterministic frontend lookup keys for pair labels."""
    fa = _normalize_filename(file_a)
    fb = _normalize_filename(file_b)
    if not fa or not fb:
        return []

    keys: list[str] = []
    files_sorted = tuple(sorted([fa, fb]))
    pa = _normalize_pair_storage_path(path_a)
    pb = _normalize_pair_storage_path(path_b)
    if pa and pb:
        path_sorted = tuple(sorted([pa, pb]))
        keys.append(f"PATH|{path_sorted[0]}|{path_sorted[1]}")

    ns = _normalize_session_name(session)
    if ns:
        keys.append(f"SESSION|{ns}|{files_sorted[0]}|{files_sorted[1]}")

    keys.append(f"FILE|{files_sorted[0]}|{files_sorted[1]}")

    # Legacy key compatibility (pre path/session-aware frontend mapping).
    legacy_a = Path(file_a or '').name
    legacy_b = Path(file_b or '').name
    if legacy_a and legacy_b:
        keys.append('|'.join(sorted([legacy_a, legacy_b])))

    return list(dict.fromkeys(keys))


def _filename_alias_groups(name: str) -> tuple[list[str], list[str]]:
    """
    Build robust aliases for matching legacy labels with renamed files.

    Returns:
      (strong_aliases, loose_aliases)
      - strong: exact normalized filename + stem
      - loose: alnum-only stem + numeric IDs
    """
    full = _normalize_filename(name)
    if not full:
        return [], []

    stem = Path(full).stem
    alnum = re.sub(r'[^a-z0-9]+', '', stem)
    ids = re.findall(r'\d{5,9}', stem)

    strong: list[str] = []
    loose: list[str] = []
    seen = set()

    for alias in [full, stem]:
        if not alias:
            continue
        if alias in seen:
            continue
        seen.add(alias)
        strong.append(alias)

    for alias in [alnum] + [f'id:{x}' for x in ids]:
        if not alias:
            continue
        if alias in seen:
            continue
        seen.add(alias)
        loose.append(alias)

    return strong, loose


def _filename_aliases(name: str) -> list[str]:
    strong, loose = _filename_alias_groups(name)
    return strong + loose


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        if np.isfinite(out):
            return out
        return default
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if raw in ('1', 'true', 'yes', 'y', 'on'):
        return True
    if raw in ('0', 'false', 'no', 'n', 'off'):
        return False
    return default


def _infer_session_from_filepath(filepath: str, analysis_root: Path | None = None) -> str:
    if not filepath:
        return ''

    try:
        p = Path(filepath).resolve()
    except Exception:
        p = Path(filepath)

    if analysis_root:
        try:
            root = analysis_root.resolve()
        except Exception:
            root = analysis_root
        try:
            rel = p.relative_to(root)
            parts = list(rel.parts)
            if not parts:
                return root.name
            first = parts[0]
            if first.upper() in ('MECCANICI', 'NON MECCANICI'):
                return root.name
            return first
        except Exception:
            pass

    current = p.parent
    for _ in range(8):
        try:
            children = {c.name.upper() for c in current.iterdir() if c.is_dir()}
        except Exception:
            children = set()
        if 'MECCANICI' in children or 'NON MECCANICI' in children:
            return current.name
        if current == current.parent:
            break
        current = current.parent

    return p.parent.name if p.parent else ''


def _infer_shared_session_from_paths(path1: str = '', path2: str = '', analysis_root: Path | None = None) -> str:
    """Infer a specific session name from one or two file paths, only if unambiguous."""
    sessions: dict[str, str] = {}
    for path in (path1, path2):
        raw_session = _infer_session_from_filepath(path, analysis_root)
        ns = _normalize_session_name(raw_session)
        if ns and raw_session:
            sessions[ns] = raw_session
    if len(sessions) == 1:
        return next(iter(sessions.values()))
    return ''


def _looks_like_exam_session_name(session_name: str) -> bool:
    return bool(re.match(r'^(EXAM|EXEMPTION|SIMULATION)\b', _normalize_session_name(session_name)))


def _infer_current_optimizer_session_from_results() -> str:
    """Infer a single concrete exam session from the loaded analysis, if possible."""
    directory = analysis_results.get('directory', '') or ''
    if not directory:
        return ''

    try:
        analysis_dir = Path(directory).resolve()
    except Exception:
        analysis_dir = Path(directory)

    parts = [part for part in analysis_dir.parts if part not in ('\\', '/')]
    if parts:
        last = parts[-1]
        if last.upper() in ('MECCANICI', 'NON MECCANICI') and len(parts) > 1:
            candidate = parts[-2]
            if _looks_like_exam_session_name(candidate):
                return candidate
        elif _looks_like_exam_session_name(last):
            return last

    sessions: dict[str, str] = {}
    for sig_dict in analysis_results.get('signatures', []) or []:
        raw_session = _infer_session_from_filepath(sig_dict.get('filepath', ''), analysis_dir)
        ns = _normalize_session_name(raw_session)
        if ns and raw_session:
            sessions[ns] = raw_session
            if len(sessions) > 1:
                return ''

    if len(sessions) == 1:
        return next(iter(sessions.values()))
    return ''


def _resolve_optimizer_scope_session(scope: str, requested_current_session: str) -> tuple[str, bool]:
    requested = (requested_current_session or '').strip()
    if scope != 'current':
        return requested, False
    if requested:
        return requested, False
    inferred = _infer_current_optimizer_session_from_results()
    return inferred, not bool(inferred)


def _normalize_optimizer_session_filters(raw_sessions: Any) -> list[str]:
    """
    Normalize a frontend-provided session filter payload into unique session names.
    Accepts:
      - comma/semicolon/newline separated string
      - list/tuple/set of strings
      - repeated query args list
    """
    tokens: list[str] = []
    if raw_sessions is None:
        return []

    if isinstance(raw_sessions, str):
        tokens.extend(re.split(r'[,\n;]+', raw_sessions))
    elif isinstance(raw_sessions, (list, tuple, set)):
        for item in raw_sessions:
            if item is None:
                continue
            if isinstance(item, str):
                tokens.extend(re.split(r'[,\n;]+', item))
            else:
                tokens.append(str(item))
    else:
        tokens.append(str(raw_sessions))

    normalized: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        ns = _normalize_session_name(str(token or '').strip())
        if not ns or ns in seen:
            continue
        seen.add(ns)
        normalized.append(ns)
    return normalized


def _extract_optimizer_session_tokens(session_name: str) -> set[str]:
    """
    Expand one training-sample session key into normalized concrete sessions.
    Handles synthetic cross-session keys like:
      CROSS::SESSION_A|SESSION_B
    """
    ns = _normalize_session_name(session_name)
    if not ns:
        return set()
    if ns.startswith('CROSS::'):
        body = ns[len('CROSS::'):]
        out: set[str] = set()
        for part in body.split('|'):
            part_ns = _normalize_session_name(part)
            if part_ns:
                out.add(part_ns)
        return out
    return {ns}


def _session_matches_optimizer_filter(session_name: str, allowed_sessions: set[str]) -> bool:
    if not allowed_sessions:
        return True
    tokens = _extract_optimizer_session_tokens(session_name)
    if not tokens:
        return False
    return bool(tokens & allowed_sessions)


def _annotate_optimizer_summary_scope(summary: dict,
                                      scope: str,
                                      requested_current_session: str,
                                      resolved_current_session: str,
                                      scope_session_unresolved: bool,
                                      selected_sessions_requested: list[str] | None = None,
                                      selected_sessions_effective: list[str] | None = None,
                                      selected_sessions_mode: str = 'all') -> dict:
    summary = dict(summary or {})
    summary['scope'] = scope
    summary['current_session_requested'] = requested_current_session or ''
    summary['current_session_resolved'] = resolved_current_session or ''
    summary['scope_session_unresolved'] = bool(scope_session_unresolved)
    summary['selected_sessions_mode'] = (selected_sessions_mode or 'all').strip().lower()
    summary['selected_sessions_requested'] = list(selected_sessions_requested or [])
    summary['selected_sessions_effective'] = list(selected_sessions_effective or [])
    return summary


def _current_session_is_available(summary: dict, current_session: str) -> bool:
    target = _normalize_session_name(current_session)
    if not target:
        return False
    files_by_session = (summary or {}).get('files_by_session', {}) or {}
    return any(_normalize_session_name(name) == target for name in files_by_session.keys())


def _resolve_optimizer_label_session(provided_session: str,
                                     file_a: str,
                                     file_b: str,
                                     available_sessions: set[str],
                                     file_session_index: dict[str, set[str]],
                                     pair_session_index: dict[tuple[str, str], set[str]]) -> tuple[str, str]:
    """
    Resolve the session for a labeled pair.

    Legacy labels may store the analysis root (e.g. "Esami Fondamenti di CAD - Copia")
    instead of the real exam/exemption/simulation session. In that case recover the
    unique matching session from the currently loaded analysis.

    Returns:
      (normalized_session, reason) where reason in:
      - provided
      - recovered
      - ambiguous
      - cross_session
      - unresolved
      - missing_file
    """
    ns = _normalize_session_name(provided_session)
    if ns and ns in available_sessions:
        return ns, 'provided'

    strong_a, loose_a = _filename_alias_groups(file_a)
    strong_b, loose_b = _filename_alias_groups(file_b)
    all_a = strong_a + loose_a
    all_b = strong_b + loose_b

    def _sessions_for(aliases: list[str]) -> set[str]:
        out: set[str] = set()
        for alias in aliases:
            out.update(file_session_index.get(alias, set()))
        return out

    strong_sessions_a = _sessions_for(strong_a)
    strong_sessions_b = _sessions_for(strong_b)
    all_sessions_a = _sessions_for(all_a)
    all_sessions_b = _sessions_for(all_b)

    candidates: set[str] = set()
    for aa in strong_a:
        for bb in strong_b:
            pair_key = tuple(sorted([aa, bb]))
            candidates.update(pair_session_index.get(pair_key, set()))
    if not candidates:
        candidates = strong_sessions_a & strong_sessions_b

    if len(candidates) == 1:
        return next(iter(candidates)), 'recovered'
    if len(candidates) > 1:
        return '', 'ambiguous'

    # Fallback loose: only accept if uniquely determined.
    loose_candidates = all_sessions_a & all_sessions_b
    if len(loose_candidates) == 1:
        return next(iter(loose_candidates)), 'recovered'
    if len(loose_candidates) > 1:
        return '', 'cross_session'
    if all_sessions_a and all_sessions_b and not loose_candidates:
        return '', 'cross_session'

    if not all_sessions_a or not all_sessions_b:
        return '', 'missing_file'
    return '', 'unresolved'


def _extract_numeric_weight_vector(weights: dict | None) -> np.ndarray:
    arr = np.array([_safe_float((weights or {}).get(k, 0.0), 0.0) for k in OPT_WEIGHT_NAMES], dtype=float)
    arr = np.maximum(arr, 0.0)
    s = float(arr.sum())
    if s <= 1e-12:
        arr = np.ones(len(OPT_WEIGHT_NAMES), dtype=float) / max(len(OPT_WEIGHT_NAMES), 1)
    else:
        arr = arr / s
    return arr


def _merge_numeric_weights(base_weights: dict, vector: np.ndarray) -> dict:
    merged = dict(base_weights or {})
    for idx, name in enumerate(OPT_WEIGHT_NAMES):
        merged[name] = float(vector[idx])
    return merged


def _normalize_criteria_exclusion_policy(exclusion_policy: dict | None = None) -> dict:
    """
    Normalize exclusion policy for fast optimizer-side checks.

    Returns keys:
    - enabled
    - exclude_if_unavailable
    - exclude_if_missing_or_non_finite
    - force_excluded (set[str])
    - force_included (set[str])
    """
    policy = exclusion_policy if isinstance(exclusion_policy, dict) else {}
    if not policy:
        try:
            policy = get_criteria_exclusion_policy()
        except Exception:
            policy = {}

    valid_names = set(OPT_WEIGHT_NAMES)
    force_excluded = {
        str(x).strip() for x in (policy.get('force_excluded', []) or [])
        if str(x).strip() in valid_names
    }
    force_included = {
        str(x).strip() for x in (policy.get('force_included', []) or [])
        if str(x).strip() in valid_names
    } - force_excluded

    normalized = {
        'enabled': bool(policy.get('enabled', True)),
        'exclude_if_unavailable': bool(policy.get('exclude_if_unavailable', True)),
        'exclude_if_missing_or_non_finite': bool(policy.get('exclude_if_missing_or_non_finite', True)),
        'force_excluded': force_excluded,
        'force_included': force_included,
    }

    # Legacy mode: preserve historical behavior.
    if not normalized['enabled']:
        normalized['exclude_if_unavailable'] = True
        normalized['exclude_if_missing_or_non_finite'] = True
        normalized['force_excluded'] = set()
        normalized['force_included'] = set()

    return normalized


def _clamp_raw_components(raw_scores: dict | None, exclusion_policy: dict | None = None) -> dict:
    out = {}
    raw = raw_scores or {}
    policy = _normalize_criteria_exclusion_policy(exclusion_policy)
    unavailable = []
    raw_unavailable = raw.get('_unavailable_criteria', [])
    if isinstance(raw_unavailable, list):
        unavailable = [str(x) for x in raw_unavailable]
        if unavailable:
            out['_unavailable_criteria'] = list(dict.fromkeys(unavailable))
    for name in OPT_WEIGHT_NAMES:
        if name in raw:
            v = _safe_float(raw.get(name, 0.0), 0.0)
            out[name] = max(0.0, min(1.0, v))
        elif name in policy['force_included']:
            out[name] = 0.0
    return out


def _serialize_raw_scores(raw_scores: dict | None) -> dict:
    """
    Normalize raw scores for transport/storage.

    Keeps:
    - numeric criterion values (OPT_WEIGHT_NAMES)
    - selected metadata needed for exclusion logic
    """
    out: dict[str, Any] = {}
    raw = raw_scores if isinstance(raw_scores, dict) else {}

    raw_unavailable = raw.get('_unavailable_criteria', [])
    if isinstance(raw_unavailable, list):
        unavailable = []
        for item in raw_unavailable:
            name = str(item or '').strip()
            if not name or name in unavailable:
                continue
            unavailable.append(name)
        if unavailable:
            out['_unavailable_criteria'] = unavailable

    cov = raw.get('_constraint_coverage', {})
    if isinstance(cov, dict):
        cov_out = {}
        for key, value in cov.items():
            cv = max(0.0, min(1.0, _safe_float(value, 0.0)))
            cov_out[str(key)] = cv
        if cov_out:
            out['_constraint_coverage'] = cov_out

    for name in OPT_WEIGHT_NAMES:
        if name not in raw:
            continue
        value = _safe_float(raw.get(name, 0.0), 0.0)
        out[name] = max(0.0, min(1.0, value))

    return out


def _weighted_similarity_from_vector(
    components: dict,
    weight_vector: np.ndarray,
    exclusion_policy: dict | None = None,
) -> float:
    w = np.maximum(np.array(weight_vector, dtype=float), 0.0)
    if w.size != len(OPT_WEIGHT_NAMES):
        return 0.0

    policy = _normalize_criteria_exclusion_policy(exclusion_policy)
    unavailable = set()
    raw_unavailable = (components or {}).get('_unavailable_criteria', [])
    if isinstance(raw_unavailable, list):
        unavailable = {str(x) for x in raw_unavailable}

    # Apply exclusion policy before renormalization.
    for idx, name in enumerate(OPT_WEIGHT_NAMES):
        if name in policy['force_excluded']:
            w[idx] = 0.0
            continue
        forced_included = name in policy['force_included']
        if policy['exclude_if_unavailable'] and name in unavailable and not forced_included:
            w[idx] = 0.0
            continue
        raw_value = (components or {}).get(name, np.nan)
        value = _safe_float(raw_value, np.nan)
        if (not np.isfinite(value)) and policy['exclude_if_missing_or_non_finite'] and not forced_included:
            w[idx] = 0.0

    # author_match is gated by its own score, same as backend combine_scores linear branch.
    am_idx = OPT_AUTHOR_INDEX
    am_score = max(0.0, min(1.0, _safe_float(components.get('author_match', 0.0), 0.0)))
    if am_idx >= 0:
        w[am_idx] = w[am_idx] * am_score

    total = float(w.sum())
    if total <= 1e-12:
        return 0.0

    score = 0.0
    for idx, name in enumerate(OPT_WEIGHT_NAMES):
        if w[idx] <= 0.0:
            continue
        c = max(0.0, min(1.0, _safe_float(components.get(name, 0.0), 0.0)))
        score += c * (w[idx] / total)
    return max(0.0, min(1.0, float(score)))


def _prepare_optimizer_sample_arrays(
    samples: list[dict],
    exclusion_policy: dict | None = None,
) -> dict:
    """Precompute dense arrays for fast optimizer/evaluation loops."""
    n_samples = len(samples or [])
    n_criteria = len(OPT_WEIGHT_NAMES)
    components = np.zeros((n_samples, n_criteria), dtype=float)
    active_mask = np.zeros((n_samples, n_criteria), dtype=float)
    targets = np.zeros(n_samples, dtype=np.int8)
    session_index = np.zeros(n_samples, dtype=np.int32)
    session_to_idx: dict[str, int] = {}
    policy = _normalize_criteria_exclusion_policy(exclusion_policy)

    for i, sample in enumerate(samples or []):
        comp = (sample or {}).get('components', {}) or {}
        raw_unavailable = comp.get('_unavailable_criteria', [])
        unavailable = set()
        if isinstance(raw_unavailable, list):
            unavailable = {str(x) for x in raw_unavailable}

        for idx, name in enumerate(OPT_WEIGHT_NAMES):
            if name in policy['force_excluded']:
                continue
            forced_included = name in policy['force_included']
            if policy['exclude_if_unavailable'] and name in unavailable and not forced_included:
                continue
            raw_value = comp.get(name, np.nan)
            value = _safe_float(raw_value, np.nan)
            if not np.isfinite(value):
                if policy['exclude_if_missing_or_non_finite'] and not forced_included:
                    continue
                value = 0.0
            value = max(0.0, min(1.0, value))
            components[i, idx] = value
            active_mask[i, idx] = 1.0

        targets[i] = 1 if int((sample or {}).get('target', 0) or 0) == 1 else 0
        session = str((sample or {}).get('session', '') or '')
        sid = session_to_idx.get(session)
        if sid is None:
            sid = len(session_to_idx)
            session_to_idx[session] = sid
        session_index[i] = int(sid)

    session_names = [''] * len(session_to_idx)
    for session_name, sid in session_to_idx.items():
        session_names[int(sid)] = session_name

    author_scores = np.zeros(n_samples, dtype=float)
    if OPT_AUTHOR_INDEX >= 0 and n_samples > 0:
        am_idx = int(OPT_AUTHOR_INDEX)
        author_scores = np.where(active_mask[:, am_idx] > 0.0, components[:, am_idx], 0.0)
        author_scores = np.clip(author_scores, 0.0, 1.0)

    return {
        'components': components,
        'active_mask': active_mask,
        'targets': targets,
        'session_index': session_index,
        'session_names': session_names,
        'author_scores': author_scores,
        'n_samples': int(n_samples),
    }


def _weighted_similarity_batch_from_vector(prepared: dict, weight_vector: np.ndarray) -> np.ndarray:
    """Compute weighted similarity for all prepared samples with one vectorized pass."""
    n_samples = int((prepared or {}).get('n_samples', 0) or 0)
    if n_samples <= 0:
        return np.zeros((0,), dtype=float)

    w = np.maximum(np.asarray(weight_vector, dtype=float), 0.0)
    if w.size != len(OPT_WEIGHT_NAMES):
        return np.zeros((n_samples,), dtype=float)

    components = prepared.get('components')
    active_mask = prepared.get('active_mask')
    if not isinstance(components, np.ndarray) or not isinstance(active_mask, np.ndarray):
        return np.zeros((n_samples,), dtype=float)
    if components.shape[0] != n_samples or active_mask.shape[0] != n_samples:
        return np.zeros((n_samples,), dtype=float)

    effective_weights = active_mask * w.reshape((1, -1))
    if OPT_AUTHOR_INDEX >= 0:
        am_idx = int(OPT_AUTHOR_INDEX)
        author_scores = np.asarray(prepared.get('author_scores', np.zeros((n_samples,), dtype=float)), dtype=float)
        if author_scores.size != n_samples:
            author_scores = np.zeros((n_samples,), dtype=float)
        effective_weights[:, am_idx] = effective_weights[:, am_idx] * np.clip(author_scores, 0.0, 1.0)

    denom = np.sum(effective_weights, axis=1)
    numer = np.sum(components * effective_weights, axis=1)
    sims = np.zeros((n_samples,), dtype=float)
    np.divide(numer, denom, out=sims, where=denom > 1e-12)
    return np.clip(sims, 0.0, 1.0)


def _build_non_author_floor_targets(
    floor: float,
    non_author_floor_targets: dict[int, float] | None = None,
) -> dict[int, float]:
    floor = max(0.0, float(floor))
    targets: dict[int, float] = {}
    if not OPT_NON_AUTHOR_INDICES:
        return targets

    for idx in OPT_NON_AUTHOR_INDICES:
        t = floor
        if non_author_floor_targets and idx in non_author_floor_targets:
            t = max(t, max(0.0, _safe_float(non_author_floor_targets.get(idx, 0.0), 0.0)))
        targets[idx] = t

    total_target = float(sum(targets.values()))
    if total_target > 0.999:
        scale = 0.999 / total_target
        for idx in list(targets.keys()):
            targets[idx] *= scale
    return targets


def _apply_non_author_floor(
    w: np.ndarray,
    floor: float,
    non_author_floor_targets: dict[int, float] | None = None,
) -> np.ndarray:
    targets = _build_non_author_floor_targets(
        floor=floor,
        non_author_floor_targets=non_author_floor_targets,
    )
    if not targets:
        return w

    for idx in OPT_NON_AUTHOR_INDICES:
        target_floor = float(targets.get(idx, 0.0))
        deficit = float(target_floor - w[idx])
        if deficit <= 0.0:
            continue
        donors = np.argsort(-w)
        for d in donors:
            if d == idx:
                continue
            donor_floor = float(targets.get(d, 0.0)) if d in OPT_NON_AUTHOR_INDICES else 0.0
            spare = max(0.0, float(w[d] - donor_floor))
            if spare <= 0.0:
                continue
            take = min(spare, deficit)
            w[d] -= take
            w[idx] += take
            deficit -= take
            if deficit <= 1e-12:
                break

    w = np.maximum(w, 0.0)
    return w / max(float(np.sum(w)), 1e-12)


def _build_non_author_correlation_redundancy(samples: list[dict], corr_threshold: float) -> tuple[np.ndarray, int]:
    n = len(OPT_NON_AUTHOR_WEIGHT_NAMES)
    redundancy = np.zeros((n, n), dtype=float)
    if n <= 1 or len(samples) < 3:
        return redundancy, 0

    rows: list[list[float]] = []
    for sample in samples:
        comps = sample.get('components', {}) or {}
        rows.append([
            max(0.0, min(1.0, _safe_float(comps.get(name, 0.0), 0.0)))
            for name in OPT_NON_AUTHOR_WEIGHT_NAMES
        ])

    if len(rows) < 3:
        return redundancy, 0

    X = np.asarray(rows, dtype=float)
    X = np.nan_to_num(X, nan=0.0, posinf=1.0, neginf=0.0)
    X = np.clip(X, 0.0, 1.0)
    if X.shape[1] <= 1:
        return redundancy, 0

    corr = np.corrcoef(X, rowvar=False)
    corr = np.nan_to_num(corr, nan=0.0, posinf=0.0, neginf=0.0)
    abs_corr = np.abs(corr)
    np.fill_diagonal(abs_corr, 0.0)
    redundancy = np.maximum(0.0, abs_corr - max(0.0, float(corr_threshold)))
    pairs = int(np.sum(redundancy > 0.0) // 2)
    return redundancy, pairs


def _project_weight_vector(vector: np.ndarray,
                           prior_vector: np.ndarray,
                           ignore_author: bool,
                           max_cap: float = 0.35,
                           min_active_weight: float = 0.02,
                           min_active_count: int = 6,
                           author_cap: float = 0.06,
                           non_author_min_weight: float = 0.0,
                           non_author_floor_targets: dict[int, float] | None = None) -> np.ndarray:
    w = np.maximum(np.array(vector, dtype=float), 0.0)
    if w.size != len(OPT_WEIGHT_NAMES):
        return prior_vector.copy()

    prior = np.maximum(np.array(prior_vector, dtype=float), 0.0)
    if prior.size != len(OPT_WEIGHT_NAMES) or float(np.sum(prior)) <= 1e-12:
        prior = np.ones(len(OPT_WEIGHT_NAMES), dtype=float) / max(len(OPT_WEIGHT_NAMES), 1)
    else:
        prior = prior / float(np.sum(prior))

    if float(np.sum(w)) <= 1e-12:
        w = prior.copy()
    else:
        w = w / float(np.sum(w))

    # Global cap
    for _ in range(10):
        over = w > max_cap
        if not np.any(over):
            break
        excess = float(np.sum(w[over] - max_cap))
        w[over] = max_cap
        under = ~over
        if np.any(under):
            base = np.maximum(prior[under], 1e-12)
            w[under] += excess * (base / float(base.sum()))
        w = np.maximum(w, 0.0)
        w = w / max(float(w.sum()), 1e-12)

    # Author handling: hard-disable oppure cap.
    if OPT_AUTHOR_INDEX >= 0:
        if ignore_author:
            excess = float(w[OPT_AUTHOR_INDEX])
            w[OPT_AUTHOR_INDEX] = 0.0
            donors = np.ones_like(w, dtype=bool)
            donors[OPT_AUTHOR_INDEX] = False
            base = np.maximum(prior[donors], 1e-12)
            w[donors] += excess * (base / float(base.sum()))
            w = np.maximum(w, 0.0)
            w = w / max(float(w.sum()), 1e-12)
        else:
            cap = max(0.0, float(author_cap))
            if float(w[OPT_AUTHOR_INDEX]) > cap:
                excess = float(w[OPT_AUTHOR_INDEX] - cap)
                w[OPT_AUTHOR_INDEX] = cap
                donors = np.ones_like(w, dtype=bool)
                donors[OPT_AUTHOR_INDEX] = False
                base = np.maximum(prior[donors], 1e-12)
                w[donors] += excess * (base / float(base.sum()))
                w = np.maximum(w, 0.0)
                w = w / max(float(w.sum()), 1e-12)

    # Tutti i criteri non booleani devono restare attivi.
    w = _apply_non_author_floor(
        w,
        non_author_min_weight,
        non_author_floor_targets=non_author_floor_targets,
    )

    # Keep at least a minimum number of active criteria.
    active_count = int(np.sum(w >= min_active_weight))
    if active_count < min_active_count:
        need = min_active_count - active_count
        candidates = [int(i) for i in np.argsort(-prior)]
        if ignore_author and OPT_AUTHOR_INDEX >= 0:
            candidates = [i for i in candidates if i != OPT_AUTHOR_INDEX]
        promote = [i for i in candidates if w[i] < min_active_weight][:need]
        for idx in promote:
            deficit = float(min_active_weight - w[idx])
            if deficit <= 0.0:
                continue
            donors = np.argsort(-w)
            for d in donors:
                if d == idx:
                    continue
                donor_floor = min_active_weight
                if d in OPT_NON_AUTHOR_INDICES:
                    donor_floor = max(
                        donor_floor,
                        float((non_author_floor_targets or {}).get(d, non_author_min_weight)),
                    )
                elif ignore_author and d == OPT_AUTHOR_INDEX:
                    donor_floor = 0.0
                spare = max(0.0, float(w[d] - donor_floor))
                take = min(spare, deficit)
                if take > 0.0:
                    w[d] -= take
                    w[idx] += take
                    deficit -= take
                if deficit <= 1e-12:
                    break

    w = _apply_non_author_floor(
        w,
        non_author_min_weight,
        non_author_floor_targets=non_author_floor_targets,
    )
    w = np.maximum(w, 0.0)
    w = w / max(float(w.sum()), 1e-12)
    return w


def _build_optimizer_training_samples(labels_db: dict,
                                      scope: str = 'all',
                                      current_session: str = '',
                                      selected_sessions: list[str] | None = None,
                                      selected_sessions_mode: str = 'all',
                                      ignore_author: bool = True,
                                      progress_callback: Any = None,
                                      exclusion_policy: dict | None = None,
                                      strict_path_labeled_pairs_only: bool = True) -> tuple[list[dict], dict]:
    """
    Build training samples from manual labels and currently analyzed signatures.
    Positive class: CONFIRMED_PLAGIARISM
    Negative class: NOT_PLAGIARISM
    """
    signatures = analysis_results.get('signatures', []) or []
    pairs = analysis_results.get('similar_pairs', []) or []
    current_weights = load_weights()
    optimizer_exclusion_policy = _normalize_criteria_exclusion_policy(exclusion_policy)
    current_raw_cfg = extract_raw_score_config(current_weights)
    raw_config_key = json.dumps(current_raw_cfg, sort_keys=True)
    analysis_raw_cfg = analysis_results.get('raw_score_config', {}) or {}
    pair_index_raw_is_current = bool(analysis_raw_cfg == current_raw_cfg)
    analysis_root = None
    if analysis_results.get('directory'):
        try:
            analysis_root = Path(analysis_results.get('directory', ''))
        except Exception:
            analysis_root = None

    sig_index: dict[tuple[str, str], list[dict]] = {}
    path_sig_index: dict[str, dict] = {}
    file_session_index: dict[str, set[str]] = {}
    file_count_by_session: Counter = Counter()
    session_ns_cache: dict[str, str] = {}
    alias_groups_cache: dict[str, tuple[list[str], list[str]]] = {}

    def _session_ns_from_path(filepath: str) -> str:
        key = str(filepath or '')
        if key in session_ns_cache:
            return session_ns_cache[key]
        ns = _normalize_session_name(_infer_session_from_filepath(key, analysis_root))
        session_ns_cache[key] = ns
        return ns

    def _alias_groups_cached(name: str) -> tuple[list[str], list[str]]:
        norm = _normalize_filename(name)
        if norm in alias_groups_cache:
            return alias_groups_cache[norm]
        groups = _filename_alias_groups(norm)
        alias_groups_cache[norm] = groups
        return groups

    for sig_dict in signatures:
        filepath = sig_dict.get('filepath', '') or ''
        path_norm = _normalize_pair_storage_path(filepath)
        if path_norm:
            path_sig_index[path_norm] = sig_dict
        filename = sig_dict.get('filename', '') or ''
        ns = _session_ns_from_path(filepath)
        strong_aliases, loose_aliases = _alias_groups_cached(filename)
        aliases = strong_aliases + loose_aliases
        if not ns or not aliases:
            continue
        for alias in aliases:
            sig_index.setdefault((ns, alias), []).append(sig_dict)
            file_session_index.setdefault(alias, set()).add(ns)
        file_count_by_session[ns] += 1

    pair_raw_index: dict[tuple[str, str, str], dict] = {}
    pair_cross_raw_index: dict[tuple[str, str], list[dict[str, Any]]] = {}
    pair_raw_path_index: dict[tuple[str, str], dict[str, Any]] = {}
    pair_session_index: dict[tuple[str, str], set[str]] = {}

    def _register_cross_pair_raw(file_left: str,
                                 file_right: str,
                                 sess_left: str,
                                 sess_right: str,
                                 raw_components: dict):
        file_key = tuple(sorted([file_left, file_right]))
        sessions = [s for s in (_normalize_session_name(sess_left), _normalize_session_name(sess_right)) if s]
        if len(sessions) >= 2:
            s1, s2 = sorted(sessions)[:2]
        elif len(sessions) == 1:
            s1, s2 = sessions[0], ''
        else:
            s1, s2 = '', ''

        entries = pair_cross_raw_index.setdefault(file_key, [])
        for existing in entries:
            if existing.get('s1', '') == s1 and existing.get('s2', '') == s2:
                existing['raw'] = dict(raw_components)
                return
        entries.append({
            's1': s1,
            's2': s2,
            'raw': dict(raw_components),
        })
    skipped_cross_session_pairs = 0
    pairs_total_count = len(pairs)
    pairs_processed = 0
    for p in pairs:
        pairs_processed += 1
        nfa = _normalize_filename(p.get('file1', ''))
        nfb = _normalize_filename(p.get('file2', ''))
        path1_norm = _normalize_pair_storage_path(p.get('path1', ''))
        path2_norm = _normalize_pair_storage_path(p.get('path2', ''))
        if not nfa or not nfb:
            if callable(progress_callback) and (pairs_processed % 5000 == 0 or pairs_processed == pairs_total_count):
                try:
                    progress_callback({
                        'stage': 'pair_index',
                        'pairs_processed': pairs_processed,
                        'pairs_total': pairs_total_count,
                    })
                except Exception:
                    pass
            continue
        s1 = _session_ns_from_path(p.get('path1', ''))
        s2 = _session_ns_from_path(p.get('path2', ''))
        is_cross_session_pair = bool(s1 and s2 and s1 != s2)
        if is_cross_session_pair:
            skipped_cross_session_pairs += 1
        ns = s1 or s2
        if not ns:
            if callable(progress_callback) and (pairs_processed % 5000 == 0 or pairs_processed == pairs_total_count):
                try:
                    progress_callback({
                        'stage': 'pair_index',
                        'pairs_processed': pairs_processed,
                        'pairs_total': pairs_total_count,
                        'skipped_cross_session_pairs': skipped_cross_session_pairs,
                    })
                except Exception:
                    pass
            continue
        exact_files = tuple(sorted([nfa, nfb]))
        strong_a, loose_a = _alias_groups_cached(nfa)
        strong_b, loose_b = _alias_groups_cached(nfb)
        pair_aliases_a = strong_a or (strong_a + loose_a)
        pair_aliases_b = strong_b or (strong_b + loose_b)
        if not pair_aliases_a or not pair_aliases_b:
            continue
        if not is_cross_session_pair:
            for aa in pair_aliases_a:
                for bb in pair_aliases_b:
                    files = tuple(sorted([aa, bb]))
                    pair_session_index.setdefault(files, set()).add(ns)
        raw = p.get('raw_scores', None)
        if pair_index_raw_is_current and isinstance(raw, dict):
            clamped = _clamp_raw_components(raw, exclusion_policy=optimizer_exclusion_policy)
            if path1_norm and path2_norm:
                path_key = tuple(sorted([path1_norm, path2_norm]))
                pair_raw_path_index[path_key] = {
                    's1': _normalize_session_name(s1),
                    's2': _normalize_session_name(s2),
                    'raw': dict(clamped),
                }
            if is_cross_session_pair:
                _register_cross_pair_raw(exact_files[0], exact_files[1], s1, s2, clamped)
            else:
                pair_raw_index[(ns, exact_files[0], exact_files[1])] = clamped
                optimizer_raw_components_cache[(raw_config_key, ns, exact_files[0], exact_files[1])] = dict(clamped)
            raw_aliases_a = [nfa, Path(nfa).stem]
            raw_aliases_b = [nfb, Path(nfb).stem]
            for aa in raw_aliases_a:
                for bb in raw_aliases_b:
                    if not aa or not bb:
                        continue
                    files = tuple(sorted([aa, bb]))
                    if is_cross_session_pair:
                        _register_cross_pair_raw(files[0], files[1], s1, s2, clamped)
                    else:
                        pair_raw_index[(ns, files[0], files[1])] = clamped
                        optimizer_raw_components_cache[(raw_config_key, ns, files[0], files[1])] = dict(clamped)

        if callable(progress_callback) and (pairs_processed % 5000 == 0 or pairs_processed == pairs_total_count):
            try:
                progress_callback({
                    'stage': 'pair_index',
                    'pairs_processed': pairs_processed,
                    'pairs_total': pairs_total_count,
                    'skipped_cross_session_pairs': skipped_cross_session_pairs,
                })
            except Exception:
                pass

    requested_selected_sessions = _normalize_optimizer_session_filters(selected_sessions)
    selected_sessions_mode = (selected_sessions_mode or 'all').strip().lower()
    if selected_sessions_mode not in ('all', 'custom'):
        selected_sessions_mode = 'all'
    selected_session_set = set(requested_selected_sessions)

    target_session = _normalize_session_name(current_session) if scope == 'current' else ''
    if target_session:
        allowed_sessions = {target_session}
        session_filter_mode_effective = 'current'
    elif selected_sessions_mode == 'custom':
        allowed_sessions = set(selected_session_set)
        session_filter_mode_effective = 'custom'
    else:
        allowed_sessions = set()
        session_filter_mode_effective = 'all'
    session_filter_active = bool(target_session) or selected_sessions_mode == 'custom'

    def _resolve_ns_for_labeled_pair(hit_sessions: list[str]) -> tuple[str, str]:
        sessions_norm = sorted({
            _normalize_session_name(s)
            for s in (hit_sessions or [])
            if _normalize_session_name(s)
        })
        if session_filter_active:
            hit_set = set(sessions_norm)
            if not allowed_sessions or not (hit_set & allowed_sessions):
                return '', 'outside_scope'
        if target_session and target_session not in sessions_norm:
            return '', 'outside_scope'
        if len(sessions_norm) == 1:
            return sessions_norm[0], ''
        if len(sessions_norm) >= 2:
            if target_session and target_session in sessions_norm:
                return target_session, ''
            if allowed_sessions:
                shared = sorted(set(sessions_norm) & allowed_sessions)
                if len(shared) == 1:
                    return shared[0], ''
                if len(shared) >= 2:
                    return f"CROSS::{shared[0]}|{shared[1]}", ''
                return '', 'outside_scope'
            return f"CROSS::{sessions_norm[0]}|{sessions_norm[1]}", ''
        return '', 'missing_session'

    available_sessions = set(file_count_by_session.keys())
    rebuilt_signature_cache: dict[str, FeatureSignature] = {}

    def _get_rebuilt_signature(sig_data: dict) -> FeatureSignature | None:
        cache_key = str(sig_data.get('filepath') or sig_data.get('filename') or id(sig_data))
        if cache_key in rebuilt_signature_cache:
            return rebuilt_signature_cache[cache_key]
        try:
            sig = rebuild_signature_from_dict(sig_data)
        except Exception:
            return None
        rebuilt_signature_cache[cache_key] = sig
        return sig

    def _get_signature_candidates(session_ns: str, file_label: str) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        strong_aliases, loose_aliases = _alias_groups_cached(file_label)

        def _collect(aliases: list[str]):
            for alias in aliases:
                for sig in sig_index.get((session_ns, alias), []):
                    key = str(sig.get('filepath', '') or sig.get('filename', '') or id(sig))
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(sig)

        _collect(strong_aliases)
        if out:
            return out
        _collect(loose_aliases)
        return out

    def _get_signature_candidates_any(file_label: str) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        seen: set[str] = set()
        strong_aliases, loose_aliases = _alias_groups_cached(file_label)
        aliases = strong_aliases + loose_aliases
        for alias in aliases:
            for session_ns in sorted(file_session_index.get(alias, set())):
                for sig in sig_index.get((session_ns, alias), []):
                    key = str(sig.get('filepath', '') or sig.get('filename', '') or id(sig))
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append((session_ns, sig))
        return out

    def _resolve_cross_session_components(file_a: str,
                                          file_b: str,
                                          session_hint: str = '',
                                          scope_session: str = '',
                                          allowed_scope_sessions: set[str] | None = None) -> tuple[dict | None, str, str]:
        hint_ns = _normalize_session_name(session_hint)
        scope_ns = _normalize_session_name(scope_session)
        allowed_ns = {
            _normalize_session_name(s)
            for s in (allowed_scope_sessions or set())
            if _normalize_session_name(s)
        }
        if scope_ns:
            allowed_ns = {scope_ns}
        aliases_a = _filename_aliases(file_a)
        aliases_b = _filename_aliases(file_b)

        def _resolve_ns_from_sessions(sessions_set: set[str]) -> str:
            sessions_sorted = sorted(sessions_set)
            if scope_ns and scope_ns in sessions_sorted:
                return scope_ns
            if allowed_ns:
                shared = sorted(set(sessions_sorted) & allowed_ns)
                if len(shared) == 1:
                    return shared[0]
                if len(shared) >= 2:
                    return f"CROSS::{shared[0]}|{shared[1]}"
                return ''
            if len(sessions_sorted) == 1:
                return sessions_sorted[0]
            if len(sessions_sorted) >= 2:
                return f"CROSS::{sessions_sorted[0]}|{sessions_sorted[1]}"
            return 'CROSS::UNKNOWN'

        cross_candidates: list[dict] = []
        seen_candidates: set[tuple[str, str, str, str]] = set()
        for aa in aliases_a:
            for bb in aliases_b:
                key = tuple(sorted([aa, bb]))
                for entry in pair_cross_raw_index.get(key, []):
                    s1 = _normalize_session_name(entry.get('s1', ''))
                    s2 = _normalize_session_name(entry.get('s2', ''))
                    ckey = (key[0], key[1], s1, s2)
                    if ckey in seen_candidates:
                        continue
                    seen_candidates.add(ckey)
                    raw = entry.get('raw')
                    if not isinstance(raw, dict):
                        continue
                    sessions = {s for s in (s1, s2) if s}
                    cross_candidates.append({
                        's1': s1,
                        's2': s2,
                        'sessions': sessions,
                        'raw': dict(raw),
                    })

        if cross_candidates:
            if allowed_ns:
                scoped = [c for c in cross_candidates if c['sessions'] & allowed_ns]
                if not scoped:
                    return None, '', 'outside_scope'
                cross_candidates = scoped

            if hint_ns:
                hinted = [c for c in cross_candidates if hint_ns in c['sessions']]
                if hinted:
                    cross_candidates = hinted

            cross_candidates.sort(key=lambda c: (c.get('s1', ''), c.get('s2', '')))
            chosen = cross_candidates[0]
            resolved_ns = _resolve_ns_from_sessions(set(chosen.get('sessions', set())))
            return dict(chosen.get('raw', {})), resolved_ns, 'pair_index'

        # Fallback: rebuild cross-session raw components from signatures across sessions.
        candidates_a = _get_signature_candidates_any(file_a)[:8]
        candidates_b = _get_signature_candidates_any(file_b)[:8]
        if not candidates_a or not candidates_b:
            return None, '', 'missing_signature'

        ranked: list[tuple[int, str, str, str, str, dict, dict]] = []
        for sa, sig_a_data in candidates_a:
            for sb, sig_b_data in candidates_b:
                sessions = {s for s in (sa, sb) if s}
                if allowed_ns and not (sessions & allowed_ns):
                    continue
                score = 0
                if sa and sb and sa != sb:
                    score += 4
                if allowed_ns:
                    score += 5 * min(2, len(sessions & allowed_ns))
                if hint_ns and hint_ns in sessions:
                    score += 2
                pa = str(sig_a_data.get('filepath', '') or sig_a_data.get('filename', ''))
                pb = str(sig_b_data.get('filepath', '') or sig_b_data.get('filename', ''))
                ranked.append((-score, sa, sb, pa, pb, sig_a_data, sig_b_data))

        if not ranked:
            return None, '', 'outside_scope' if allowed_ns else 'missing_signature'

        ranked.sort(key=lambda item: (item[0], item[1], item[2], item[3], item[4]))
        _, sa, sb, _, _, sig_a_data, sig_b_data = ranked[0]
        sig_a = _get_rebuilt_signature(sig_a_data)
        sig_b = _get_rebuilt_signature(sig_b_data)
        if sig_a is None or sig_b is None:
            return None, '', 'missing_signature'

        raw = compute_raw_scores(sig_a, sig_b, lcs_fuzzy_config=current_weights)
        components = _clamp_raw_components(raw, exclusion_policy=optimizer_exclusion_policy)
        resolved_ns = _resolve_ns_from_sessions({s for s in (sa, sb) if s})
        return components, resolved_ns, 'computed'

    samples: list[dict] = []
    labels_total_count = len(labels_db)
    labels_processed = 0
    raw_from_pair_index = 0
    raw_from_optimizer_cache = 0
    raw_computed_live = 0
    cross_session_included = 0
    _last_progress_emitted = -1

    def _emit_build_progress(force: bool = False):
        nonlocal _last_progress_emitted
        if not callable(progress_callback):
            return
        if not force and labels_processed == _last_progress_emitted:
            return
        if not force and labels_processed % 25 != 0 and labels_processed != labels_total_count:
            return
        _last_progress_emitted = labels_processed
        try:
            progress_callback({
                'labels_processed': labels_processed,
                'labels_total': labels_total_count,
                'raw_from_pair_index': raw_from_pair_index,
                'raw_from_optimizer_cache': raw_from_optimizer_cache,
                'raw_computed': raw_computed_live,
            })
        except Exception:
            pass

    ignored = {
        'undecided': 0,
        'outside_scope': 0,
        'missing_signature': 0,
        'missing_file_in_analysis': 0,
        'missing_path': 0,
        'label_pair_not_found': 0,
        'missing_session': 0,
        'unresolved_session': 0,
        'ambiguous_session': 0,
        'cross_session_pair': 0,
        'recovered_session': 0,
        'labels_total': len(labels_db),
        'skipped_cross_session_pairs': skipped_cross_session_pairs,
    }

    per_session_counts: dict[str, dict] = {}

    for entry in labels_db.values():
        labels_processed += 1
        try:
            label = (entry.get('label') or '').strip()
            if label not in ('CONFIRMED_PLAGIARISM', 'NOT_PLAGIARISM'):
                ignored['undecided'] += 1
                continue

            session_name = (entry.get('session') or '').strip()
            entry_path_a = _normalize_pair_storage_path(entry.get('path_a', '') or entry.get('path1', ''))
            entry_path_b = _normalize_pair_storage_path(entry.get('path_b', '') or entry.get('path2', ''))

            fa = _normalize_filename(entry.get('file_a', ''))
            fb = _normalize_filename(entry.get('file_b', ''))
            if not fa or not fb:
                ignored['missing_signature'] += 1
                continue
            f1, f2 = tuple(sorted([fa, fb]))

            components = None
            ns = ''
            resolution = ''
            if entry_path_a and entry_path_b:
                path_key = tuple(sorted([entry_path_a, entry_path_b]))
                path_hit = pair_raw_path_index.get(path_key)
                if isinstance(path_hit, dict):
                    hit_raw = path_hit.get('raw')
                    if isinstance(hit_raw, dict):
                        components = dict(hit_raw)
                        hit_sessions = [
                            _normalize_session_name(path_hit.get('s1', '')),
                            _normalize_session_name(path_hit.get('s2', '')),
                        ]
                        ns, ns_reason = _resolve_ns_for_labeled_pair(hit_sessions)
                        if not ns:
                            if ns_reason == 'outside_scope':
                                ignored['outside_scope'] += 1
                                continue
                            ignored['missing_session'] += 1
                            continue
                        if str(ns).upper().startswith('CROSS::'):
                            cross_session_included += 1
                        resolution = 'path_match'
                        raw_from_pair_index += 1

            if strict_path_labeled_pairs_only and (components is None or not ns):
                if not (entry_path_a and entry_path_b):
                    ignored['missing_path'] += 1
                    continue
                # Strict mode fallback: rebuild raw from exact labeled paths only (no alias/session recovery).
                sig_a_data = path_sig_index.get(entry_path_a)
                sig_b_data = path_sig_index.get(entry_path_b)
                if sig_a_data is None or sig_b_data is None:
                    ignored['label_pair_not_found'] += 1
                    ignored['missing_file_in_analysis'] += 1
                    continue
                sig_a = _get_rebuilt_signature(sig_a_data)
                sig_b = _get_rebuilt_signature(sig_b_data)
                if sig_a is None or sig_b is None:
                    ignored['missing_signature'] += 1
                    continue
                raw = compute_raw_scores(sig_a, sig_b, lcs_fuzzy_config=current_weights)
                components = _clamp_raw_components(raw, exclusion_policy=optimizer_exclusion_policy)
                raw_computed_live += 1
                hit_sessions = [
                    _session_ns_from_path(sig_a_data.get('filepath', '') or ''),
                    _session_ns_from_path(sig_b_data.get('filepath', '') or ''),
                ]
                ns, ns_reason = _resolve_ns_for_labeled_pair(hit_sessions)
                if not ns:
                    if ns_reason == 'outside_scope':
                        ignored['outside_scope'] += 1
                    else:
                        ignored['missing_session'] += 1
                    continue
                if str(ns).upper().startswith('CROSS::'):
                    cross_session_included += 1
                resolution = 'path_match_computed'

            if (not strict_path_labeled_pairs_only) and (components is None or not ns):
                ns, resolution = _resolve_optimizer_label_session(
                    provided_session=session_name,
                    file_a=fa,
                    file_b=fb,
                    available_sessions=available_sessions,
                    file_session_index=file_session_index,
                    pair_session_index=pair_session_index,
                )
                if resolution == 'cross_session':
                    components, ns, cross_source = _resolve_cross_session_components(
                        file_a=fa,
                        file_b=fb,
                        session_hint=session_name,
                        scope_session=target_session,
                        allowed_scope_sessions=allowed_sessions,
                    )
                    if components is None:
                        if cross_source == 'outside_scope':
                            ignored['outside_scope'] += 1
                        elif cross_source == 'missing_signature':
                            ignored['missing_signature'] += 1
                        else:
                            ignored['cross_session_pair'] += 1
                        continue
                    cross_session_included += 1
                    if cross_source == 'pair_index':
                        raw_from_pair_index += 1
                    elif cross_source == 'computed':
                        raw_computed_live += 1

            if not ns:
                if resolution == 'missing_file':
                    ignored['missing_signature'] += 1
                    ignored['missing_file_in_analysis'] += 1
                elif not session_name:
                    ignored['missing_session'] += 1
                elif resolution == 'ambiguous':
                    ignored['ambiguous_session'] += 1
                else:
                    ignored['unresolved_session'] += 1
                continue
            if resolution == 'recovered':
                ignored['recovered_session'] += 1
            if session_filter_active and not _session_matches_optimizer_filter(ns, allowed_sessions):
                ignored['outside_scope'] += 1
                continue

            if components is None:
                components = pair_raw_index.get((ns, f1, f2))
                if components is not None:
                    raw_from_pair_index += 1
                    components = dict(components)
            if components is None:
                cache_key = (raw_config_key, ns, f1, f2)
                cached_components = optimizer_raw_components_cache.get(cache_key)
                if cached_components is not None:
                    raw_from_optimizer_cache += 1
                    components = dict(cached_components)

            if components is None:
                sig_a_list = _get_signature_candidates(ns, fa)
                sig_b_list = _get_signature_candidates(ns, fb)
                if not sig_a_list or not sig_b_list:
                    ignored['missing_signature'] += 1
                    continue
                sig_a = _get_rebuilt_signature(sig_a_list[0])
                sig_b = _get_rebuilt_signature(sig_b_list[0])
                if sig_a is None or sig_b is None:
                    ignored['missing_signature'] += 1
                    continue
                raw = compute_raw_scores(sig_a, sig_b, lcs_fuzzy_config=current_weights)
                components = _clamp_raw_components(raw, exclusion_policy=optimizer_exclusion_policy)
                raw_computed_live += 1
                optimizer_raw_components_cache[(raw_config_key, ns, f1, f2)] = dict(components)

            if components is not None:
                # Re-apply current policy to cached/indexed components built in older runs.
                components = _clamp_raw_components(components, exclusion_policy=optimizer_exclusion_policy)

            if ignore_author:
                components['author_match'] = 0.0

            target = 1 if label == 'CONFIRMED_PLAGIARISM' else 0
            sample = {
                'session': ns,
                'file_a': Path(entry.get('file_a', '')).name,
                'file_b': Path(entry.get('file_b', '')).name,
                'label': label,
                'target': target,
                'components': components,
            }
            samples.append(sample)

            per_session_counts.setdefault(ns, {'positive': 0, 'negative': 0})
            if target == 1:
                per_session_counts[ns]['positive'] += 1
            else:
                per_session_counts[ns]['negative'] += 1
        finally:
            _emit_build_progress(force=False)

    _emit_build_progress(force=True)

    summary = {
        'sessions': per_session_counts,
        'sessions_count': len(per_session_counts),
        'samples_total': len(samples),
        'positive': sum(1 for s in samples if s['target'] == 1),
        'negative': sum(1 for s in samples if s['target'] == 0),
        'strict_path_labeled_pairs_only': bool(strict_path_labeled_pairs_only),
        'ignored': ignored,
        'scope': scope,
        'current_session': current_session or '',
        'selected_sessions_mode': session_filter_mode_effective,
        'selected_sessions_requested': sorted(selected_session_set),
        'selected_sessions_effective': sorted(allowed_sessions),
        'session_filter_active': bool(session_filter_active),
        'cross_session_included': cross_session_included,
        'files_in_analysis': len(signatures),
        'pairs_in_analysis': len(pairs),
        'files_by_session': dict(file_count_by_session),
        'build_stats': {
            'labels_processed': labels_processed,
            'labels_total': labels_total_count,
            'raw_from_pair_index': raw_from_pair_index,
            'raw_from_optimizer_cache': raw_from_optimizer_cache,
            'raw_computed': raw_computed_live,
            'cross_session_included': cross_session_included,
            'optimizer_cache_size': len(optimizer_raw_components_cache),
            'pair_index_raw_is_current': pair_index_raw_is_current,
            'raw_config_match': pair_index_raw_is_current,
        },
    }
    return samples, summary


def _evaluate_training_samples(samples: list[dict],
                               weight_vector: np.ndarray,
                               threshold: float,
                               pos_push_margin: float = 0.0,
                               neg_push_margin: float = 0.0,
                               prepared_arrays: dict | None = None,
                               exclusion_policy: dict | None = None) -> dict:
    prepared = (
        prepared_arrays
        if isinstance(prepared_arrays, dict)
        else _prepare_optimizer_sample_arrays(samples, exclusion_policy=exclusion_policy)
    )
    sims = _weighted_similarity_batch_from_vector(prepared, weight_vector)
    targets = np.asarray(prepared.get('targets', np.zeros((0,), dtype=np.int8)), dtype=np.int8)
    session_idx = np.asarray(prepared.get('session_index', np.zeros((0,), dtype=np.int32)), dtype=np.int32)
    session_names = list(prepared.get('session_names', []))

    if sims.size != targets.size:
        n = min(int(sims.size), int(targets.size))
        sims = sims[:n]
        targets = targets[:n]
        session_idx = session_idx[:n]

    pos_mask = targets == 1
    neg_mask = targets == 0
    pos_arr = sims[pos_mask]
    neg_arr = sims[neg_mask]

    def _stats(values: np.ndarray) -> dict:
        if values.size == 0:
            return {'count': 0, 'mean': 0.0, 'min': 0.0, 'max': 0.0, 'std': 0.0}
        return {
            'count': int(values.size),
            'mean': float(np.mean(values)),
            'min': float(np.min(values)),
            'max': float(np.max(values)),
            'std': float(np.std(values)),
        }

    pos_stats = _stats(pos_arr)
    neg_stats = _stats(neg_arr)
    separation = float(pos_stats['mean'] - neg_stats['mean'])
    margin = float(pos_stats['min'] - neg_stats['max'])

    pos_violations = int(np.sum(pos_arr < threshold))
    neg_violations = int(np.sum(neg_arr >= threshold))
    pos_target = min(0.995, threshold + max(0.0, float(pos_push_margin)))
    neg_target = max(0.0, threshold - max(0.0, float(neg_push_margin)))
    pos_push_violations = int(np.sum(pos_arr < pos_target))
    neg_push_violations = int(np.sum(neg_arr > neg_target))

    session_rows = []
    for sid, session in sorted(enumerate(session_names), key=lambda x: x[1]):
        sid_mask = session_idx == int(sid)
        sid_pos = sims[sid_mask & pos_mask]
        sid_neg = sims[sid_mask & neg_mask]
        ps = _stats(sid_pos)
        ns = _stats(sid_neg)
        session_rows.append({
            'session': session,
            'positive_count': ps['count'],
            'negative_count': ns['count'],
            'positive_mean': ps['mean'],
            'negative_mean': ns['mean'],
            'separation': ps['mean'] - ns['mean'],
        })

    return {
        'positive': pos_stats,
        'negative': neg_stats,
        'separation': separation,
        'margin': margin,
        'violations': {
            'positive_below_threshold': pos_violations,
            'negative_above_threshold': neg_violations,
        },
        'targets': {
            'positive_target': pos_target,
            'negative_target': neg_target,
        },
        'violations_push': {
            'positive_below_target': pos_push_violations,
            'negative_above_target': neg_push_violations,
        },
        'sessions': session_rows,
    }

HAS_PLAGIARISM_LABELS = True  # Now always available

#  Import nuovi estrattori multi-CAD
try:
    from extractors import (
        get_extractor,
        get_supported_extensions,
        detect_cad_type,
        extract_from_file,
        CADModelSignature,
    )
    from extractors.factory import get_available_cads, get_extractor_info, filter_cad_files
    from extractors.adapter import build_feature_signatures
    HAS_MULTI_CAD = True
except ImportError as e:
    print(f" Estrattori multi-CAD non disponibili: {e}")
    HAS_MULTI_CAD = False

from dataclasses import asdict, is_dataclass

#  PERSISTENZA: Directory per salvare i risultati delle analisi
RESULTS_DIR = Path.home() / '.cache' / 'cad_similarity_analyzer' / 'results'
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

def normalize_directory_for_cache(directory: str) -> str:
    """Normalizza il path directory per una cache key stabile tra sessioni."""
    try:
        p = Path(directory).expanduser()
        if p.exists():
            p = p.resolve()
        normalized = str(p)
    except Exception:
        normalized = str(directory or '')
    normalized = normalized.replace('/', '\\').rstrip('\\').strip()
    if os.name == 'nt':
        normalized = normalized.lower()
    return normalized

def get_results_filepath(directory: str) -> Path:
    """Genera il path per salvare i risultati di una directory analizzata."""
    import hashlib
    cache_key = normalize_directory_for_cache(directory)
    dir_hash = hashlib.md5(cache_key.encode('utf-8')).hexdigest()
    return RESULTS_DIR / f'analysis_{dir_hash}.json'

def save_analysis_results(directory: str, signatures: list, similar_pairs: list,
                          raw_score_config: dict | None = None,
                          weights_snapshot: dict | None = None) -> bool:
    """Salva i risultati dell'analisi in JSON."""
    try:
        filepath = get_results_filepath(directory)

        data = {
            'directory': directory,
            'directory_normalized': normalize_directory_for_cache(directory),
            'timestamp': datetime.now().isoformat(),
            'file_count': len(signatures),
            'similar_pairs_count': len(similar_pairs),
            'signatures': signatures,
            'similar_pairs': similar_pairs,
            'raw_score_config': raw_score_config or {},
            'weights_snapshot': weights_snapshot or {}
        }

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

        print(f" Risultati salvati: {filepath}")
        return True
    except Exception as e:
        print(f" Errore salvataggio risultati: {e}")
        return False

def load_analysis_results(directory: str) -> dict | None:
    """Carica i risultati precedenti di una directory.

    Se il file non viene trovato con l'hash esatto, prova a caricare il file
    di cache piÃ¹ recente come fallback (utile se il path Ã¨ leggermente diverso).
    """
    try:
        filepath = get_results_filepath(directory)

        if not filepath.exists():
            print(f"âŒ Exact cache not found for: {directory}")
            print(f"   Expected file: {filepath.name}")
            return None

        #  Controlla se il file Ã¨ vuoto
        if filepath.stat().st_size == 0:
            print(f" Results file is empty, deleting: {filepath}")
            filepath.unlink()
            return None

        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip():
                print(f" Results file contains only whitespace, deleting: {filepath}")
                filepath.unlink()
                return None

            try:
                data = json.loads(content)
            except json.JSONDecodeError as je:
                print(f" Invalid JSON in results file: {filepath}")
                print(f"   Error: {je}")
                print(f"   Position: line {je.lineno}, column {je.colno}")
                # Backup del file corrotto
                import time
                backup_path = filepath.with_suffix(f'.json.corrupted.{int(time.time())}')
                filepath.rename(backup_path)
                print(f"   Corrupted file backed up to: {backup_path}")
                return None

        # Normalizza e ordina le coppie per similarity decrescente
        pairs = data.get('similar_pairs', [])
        if isinstance(pairs, list):
            for p in pairs:
                try:
                    p['similarity'] = float(p.get('similarity', 0.0))
                except Exception:
                    p['similarity'] = 0.0
            pairs.sort(key=lambda x: -float(x.get('similarity', 0.0)))
            data['similar_pairs'] = pairs

        print(f" Risultati caricati da cache: {filepath}")
        return data
    except Exception as e:
        print(f" Errore caricamento risultati: {e}")
        return None

def _populate_results_in_memory(results: dict, directory_fallback: str = '') -> tuple[int, int]:
    """Popola le cache in-memory a partire da risultati giÃ  caricati."""
    signatures_cache.clear()
    analysis_results['signatures'] = results.get('signatures', [])
    analysis_results['directory'] = results.get('directory', directory_fallback)
    analysis_results['similar_pairs'] = results.get('similar_pairs', [])
    analysis_results['raw_score_config'] = results.get('raw_score_config', {})
    analysis_results['weights_snapshot'] = results.get('weights_snapshot', {})

    for sig_dict in results.get('signatures', []):
        try:
            kwargs = dict(sig_dict)
            if not kwargs.get('file_hash'):
                import hashlib as _hashlib
                kwargs['file_hash'] = _hashlib.md5(kwargs.get('filepath', '').encode()).hexdigest()
            sig = FeatureSignature(**kwargs)
            signatures_cache[sig.filepath] = sig
            canonical = canonical_filepath_for_lookup(sig.filepath)
            if canonical:
                signatures_cache[canonical] = sig
        except Exception as e:
            print(f"   Warning ricostruzione signature: {e}")

    return len(get_cached_signatures_unique()), len(analysis_results['similar_pairs'])


def purge_all_cache() -> bool:
    """Elimina TUTTA la cache (sketch + risultati)."""
    try:
        # Elimina sketch cache
        if CACHE_DIR.exists():
            shutil.rmtree(CACHE_DIR)
            print(f" Cache sketch eliminata: {CACHE_DIR}")

        # Elimina results cache
        if RESULTS_DIR.exists():
            shutil.rmtree(RESULTS_DIR)
            print(f" Cache risultati eliminata: {RESULTS_DIR}")

        # Ricrea le directory
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)

        print(f" Purge completato con successo")
        return True
    except Exception as e:
        print(f" Errore purge cache: {e}")
        return False

def purge_directory_cache(directory: str) -> bool:
    """Elimina la cache per UNA SOLA directory."""
    try:
        import hashlib
        cache_key = normalize_directory_for_cache(directory)
        dir_hash = hashlib.md5(cache_key.encode('utf-8')).hexdigest()

        # Elimina sketch per questa directory
        sketch_files = list(CACHE_DIR.glob(f'sketch_data_*.json'))
        for f in sketch_files:
            try:
                f.unlink()
            except:
                pass

        # Elimina risultati per questa directory
        results_file = RESULTS_DIR / f'analysis_{dir_hash}.json'
        if results_file.exists():
            results_file.unlink()
            print(f" Cache directory eliminata: {directory}")

        return True
    except Exception as e:
        print(f" Errore purge directory: {e}")
        return False

# Custom JSON Encoder per dataclass
class DataclassEncoder(json.JSONEncoder):
    """Encoder personalizzato per serializzare dataclass"""
    def default(self, obj):
        if is_dataclass(obj):
            return asdict(obj)
        return super().default(obj)

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)

# Disabilita cache file statici in sviluppo (forza ricaricamento JS/CSS)
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['TEMPLATES_AUTO_RELOAD'] = True

@app.after_request
def add_no_cache_headers(response):
    """Forza no-cache per tutti i file statici JS e CSS."""
    if request.path.startswith('/static/') and (request.path.endswith('.js') or request.path.endswith('.css')):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

# Configura il custom encoder
app.json_encoder = DataclassEncoder


def _json_api_error(message: str, status_code: int):
    """Restituisce un errore JSON consistente per endpoint /api/*."""
    resp = jsonify({
        'success': False,
        'error': str(message or 'Internal server error')
    })
    resp.status_code = int(status_code)
    return resp


@app.errorhandler(HTTPException)
def handle_http_exception(error):
    """Converte errori HTTP in JSON per le route API."""
    if request.path.startswith('/api/'):
        return _json_api_error(error.description or error.name, error.code or 500)
    return error


@app.errorhandler(Exception)
def handle_unexpected_exception(error):
    """Evita pagine HTML 500 per API: ritorna sempre JSON."""
    if request.path.startswith('/api/'):
        app.logger.exception("Unhandled API exception on %s", request.path)
        return _json_api_error(str(error) or error.__class__.__name__, 500)

    # Per pagine non-API manteniamo una risposta HTML minimale.
    app.logger.exception("Unhandled non-API exception on %s", request.path)
    return make_response(
        "<!doctype html><html lang='en'><title>500 Internal Server Error</title>"
        "<h1>Internal Server Error</h1></html>",
        500
    )

# ================================================================================
# API: WEIGHTS - Gestione pesi similaritÃ 
# ================================================================================

@app.route('/api/weights', methods=['GET'])
def api_get_weights():
    """Restituisce i pesi globali attivi."""
    try:
        weights = load_weights()
        return jsonify({'success': True, 'weights': weights})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/config', methods=['GET'])
def api_get_config():
    """Restituisce la configurazione dell'applicazione dal config.json."""
    try:
        config_path = Path(__file__).parent.parent / 'config.json'
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        else:
            cfg = {}
        return jsonify({
            'success': True,
            'show_sketch_parametric': cfg.get('show_sketch_parametric', True),
            'paper_writing_mode': cfg.get('paper_writing_mode', {}),
            'criteria_exclusion_policy': get_criteria_exclusion_policy(cfg),
            'optimizer_training_policy': get_optimizer_training_policy(cfg),
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/weights', methods=['POST'])
def api_set_weights():
    """Aggiorna i pesi globali (persistenti)."""
    try:
        data = request.json or {}

        #  Normalizza: mantieni tipo originale per parametri fuzzy
        new_weights = {}
        for k in DEFAULT_WEIGHTS:
            value = data.get(k, DEFAULT_WEIGHTS.get(k, 0))
            # Se Ã¨ un parametro fuzzy (lcs_fuzzy_* o fuzzy_combination_*), mantieni tipo originale
            if k.startswith('lcs_fuzzy_') or k.startswith('fuzzy_combination_'):
                new_weights[k] = value
            else:
                # Altrimenti converti in float (Ã¨ un peso numerico)
                new_weights[k] = float(value)

        ok = save_weights(new_weights)
        if ok:
            return jsonify({'success': True, 'weights': new_weights})
        else:
            return jsonify({'success': False, 'error': 'Save failed'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ================================================================================

# Cache globale delle firme analizzate
signatures_cache = {}
analysis_results = {
    'signatures': [],
    'directory': None,
    'similar_pairs': [],
    'raw_score_config': {},
    'weights_snapshot': {}
}

# Variabile globale per le statistiche della Paper Writing Mode
paper_stats = {
    'generated_at': None,
    'threshold': None,
    'sessions': []
}


def normalize_filepath(raw_path: str) -> str:
    if not raw_path:
        return ''
    return raw_path.replace('|', ':')


def canonical_filepath_for_lookup(raw_path: str) -> str:
    path = normalize_filepath(raw_path).strip()
    if not path:
        return ''
    normalized = path.replace('/', '\\')
    try:
        normalized = os.path.normpath(normalized)
    except Exception:
        pass
    return os.path.normcase(normalized)


def rebuild_signature_from_dict(data: dict) -> FeatureSignature:
    sig = FeatureSignature(
        filename=data.get('filename', ''),
        filepath=data.get('filepath', ''),
        file_hash=data.get('file_hash', '')
    )
    for key, value in data.items():
        if hasattr(sig, key):
            try:
                setattr(sig, key, value)
            except Exception:
                pass
    return sig


def fetch_signature(filepath: str) -> FeatureSignature | None:
    if not filepath:
        return None
    filepath = normalize_filepath(filepath)
    canonical = canonical_filepath_for_lookup(filepath)

    if filepath in signatures_cache:
        return signatures_cache[filepath]
    if canonical and canonical in signatures_cache:
        return signatures_cache[canonical]

    for entry in analysis_results.get('signatures', []):
        entry_path = entry.get('filepath', '')
        if entry_path == filepath or canonical_filepath_for_lookup(entry_path) == canonical:
            sig = rebuild_signature_from_dict(entry)
            signatures_cache[filepath] = sig
            if canonical:
                signatures_cache[canonical] = sig
            return sig
    return None


def get_cached_signatures_unique() -> list[FeatureSignature]:
    unique: dict[str, FeatureSignature] = {}
    for sig in signatures_cache.values():
        if sig is None:
            continue
        path = getattr(sig, 'filepath', '') or ''
        if not path:
            continue
        if path not in unique:
            unique[path] = sig
    return list(unique.values())


def extract_raw_score_config(weights: dict | None) -> dict:
    """Config dei parametri che impattano compute_raw_scores (non solo la combinazione)."""
    w = weights or {}
    return {
        'scorer_version': RAW_SCORER_VERSION,
        'lcs_fuzzy_enabled': bool(w.get('lcs_fuzzy_enabled', True)),
        'lcs_fuzzy_function': str(w.get('lcs_fuzzy_function', 'exponential')),
        'lcs_fuzzy_alpha': float(w.get('lcs_fuzzy_alpha', 2.0)),
        'lcs_fuzzy_mix': float(w.get('lcs_fuzzy_mix', 0.7)),
    }


def extract_weights_snapshot(weights: dict | None) -> dict:
    """Snapshot normalizzato dei parametri che influenzano il punteggio finale."""
    merged = dict(DEFAULT_WEIGHTS or {})
    merged.update(weights or {})

    snapshot = {}
    for key in sorted(merged.keys()):
        if str(key).startswith('_'):
            continue
        value = merged.get(key)
        if isinstance(value, bool):
            snapshot[key] = bool(value)
        elif isinstance(value, (int, float)):
            snapshot[key] = round(float(value), 12)
        elif value is not None:
            snapshot[key] = str(value)
    return snapshot


def ensure_pairs_synced_with_current_weights(allow_raw_recompute: bool = False, persist: bool = False) -> dict:
    """Allinea le coppie in memoria ai pesi backend correnti prima di servirle alla UI."""
    pairs = analysis_results.get('similar_pairs', []) or []
    directory = analysis_results.get('directory')

    sync_info = {
        'synced': False,
        'updated': 0,
        'recomputed_raw': 0,
        'raw_config_changed': False,
        'weights_changed': False,
        'persisted': False,
    }

    if not pairs or not directory:
        return sync_info

    current_weights = load_weights()
    exclusion_policy = get_criteria_exclusion_policy()
    current_raw_cfg = extract_raw_score_config(current_weights)
    current_weights_snapshot = extract_weights_snapshot(current_weights)
    saved_raw_cfg = analysis_results.get('raw_score_config', {}) or {}
    saved_weights_snapshot = extract_weights_snapshot(analysis_results.get('weights_snapshot', {}) or {})

    raw_config_changed = current_raw_cfg != saved_raw_cfg
    weights_changed = current_weights_snapshot != saved_weights_snapshot

    sync_info['raw_config_changed'] = raw_config_changed
    sync_info['weights_changed'] = weights_changed

    if not raw_config_changed and not weights_changed:
        return sync_info

    if raw_config_changed and not allow_raw_recompute:
        return sync_info

    updated_count = 0
    recomputed_raw_count = 0

    for pair in pairs:
        try:
            if raw_config_changed:
                path1 = pair.get('path1', '')
                path2 = pair.get('path2', '')
                sig1 = fetch_signature(path1)
                sig2 = fetch_signature(path2)
                if sig1 is None or sig2 is None:
                    continue

                raw = compute_raw_scores(sig1, sig2, lcs_fuzzy_config=current_weights)
                pair['raw_scores'] = _serialize_raw_scores(raw)
                recomputed_raw_count += 1
            else:
                raw = pair.get('raw_scores')
                if raw is None and isinstance(pair.get('details'), dict):
                    raw = pair.get('details')
                if raw is None:
                    continue

            new_sim = combine_scores(raw, current_weights, exclusion_policy=exclusion_policy)
            pair['similarity'] = float(new_sim.get('overall', 0.0))
            pair['details'] = new_sim
            updated_count += 1
        except Exception as e:
            print(f" Warning: backend sync failed for pair {pair.get('file1')} vs {pair.get('file2')}: {e}")

    pairs.sort(key=lambda x: -float(x.get('similarity', 0.0)))
    analysis_results['raw_score_config'] = current_raw_cfg
    analysis_results['weights_snapshot'] = current_weights_snapshot

    if persist:
        try:
            save_analysis_results(
                str(directory),
                analysis_results.get('signatures', []),
                analysis_results.get('similar_pairs', []),
                analysis_results.get('raw_score_config', {}),
                analysis_results.get('weights_snapshot', {})
            )
            sync_info['persisted'] = True
        except Exception as e:
            print(f" Warning: failed to persist backend-synced pairs: {e}")

    sync_info.update({
        'synced': True,
        'updated': updated_count,
        'recomputed_raw': recomputed_raw_count,
    })
    return sync_info

# Stato di avanzamento dell'analisi
progress_state = {
    'active': False,
    'current_file': '',
    'total_files': 0,
    'processed_files': 0,
    'current_step': 'Inizializzazione...',
    'percentage': 0,
    'status': 'idle',  # idle, analyzing, comparing, complete, error
    'error_message': None
}

# Lock per accesso thread-safe
progress_lock = threading.Lock()


# Stato progresso ottimizzazione pesi iterativa (paper mode)
optimization_progress_state = {
    'active': False,
    'phase': 'idle',  # idle, preparing, loading_labels, building_dataset, solver_running, evaluating, complete, error
    'message': '',
    'started_at': None,
    'elapsed_sec': 0,
    'config': {},
    'dataset': {},
    'solver': {},
    'error': None
}
optimization_progress_lock = threading.Lock()


def _set_optimization_progress(**kwargs):
    with optimization_progress_lock:
        optimization_progress_state.update(kwargs)
        started_at = optimization_progress_state.get('started_at')
        if started_at:
            optimization_progress_state['elapsed_sec'] = int(max(0.0, time.time() - float(started_at)))
        else:
            optimization_progress_state['elapsed_sec'] = 0


def _get_optimization_progress_snapshot() -> dict:
    with optimization_progress_lock:
        snap = dict(optimization_progress_state)
        started_at = snap.get('started_at')
        if started_at:
            snap['elapsed_sec'] = int(max(0.0, time.time() - float(started_at)))
        else:
            snap['elapsed_sec'] = 0
        return snap


@app.route('/')
def index():
    """Pagina principale."""
    config = load_app_config()
    pw = config.get('paper_writing_mode', {})
    threshold_float = get_default_similarity_threshold(config)
    threshold_pct = int(round(threshold_float * 100))

    # Calcola hash del file app.js per cache-busting affidabile
    import hashlib as _hl
    try:
        js_path = Path(__file__).parent / 'static' / 'js' / 'app.js'
        js_version = _hl.md5(js_path.read_bytes()).hexdigest()[:10]
    except Exception:
        js_version = '1'

    response = make_response(render_template('index.html',
                           paper_mode_enabled=pw.get('enabled', False),
                           paper_mode_show_toggle=pw.get('show_toggle', False),
                           paper_mode_threshold_pct=threshold_pct,
                           paper_mode_threshold_float=threshold_float,
                           js_version=js_version))
    # Forza il browser a non cachare mai la pagina HTML principale
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


def update_progress(step, current=None, total=None, percentage=None):
    """Aggiorna lo stato di avanzamento in modo thread-safe."""
    with progress_lock:
        progress_state['current_step'] = step
        if current is not None:
            progress_state['processed_files'] = current
        if total is not None:
            progress_state['total_files'] = total
        if percentage is not None:
            progress_state['percentage'] = percentage
        elif total and total > 0:
            progress_state['percentage'] = int((current or 0) / total * 100)


def set_progress_status(status, message=None):
    """Imposta lo stato generale del progresso."""
    with progress_lock:
        progress_state['status'] = status
        if message:
            if status == 'error':
                progress_state['error_message'] = message
            progress_state['current_step'] = message


#  Mapping CAD type  estensioni
CAD_EXTENSIONS = {
    'SolidEdge': {'.par', '.psm', '.asm'},
    'SolidWorks': {'.sldprt', '.sldasm', '.slddrw'},
    'Inventor': {'.ipt', '.iam', '.idw'},
    'CATIA': {'.catpart', '.catproduct', '.catdrawing'},
    'FreeCAD': {'.fcstd'},
    'Fusion360': {'.f3d', '.f3z'},
    'auto': None  # Tutte le estensioni
}

def get_extensions_for_cad(cad_type: str) -> set:
    """Ottiene le estensioni per un tipo CAD."""
    if cad_type == 'auto' or cad_type not in CAD_EXTENSIONS:
        # Tutte le estensioni supportate
        all_ext = set()
        for exts in CAD_EXTENSIONS.values():
            if exts:
                all_ext.update(exts)
        return all_ext
    return CAD_EXTENSIONS.get(cad_type, set())


def count_cad_files(directory: Path, cad_type: str = 'auto') -> int:
    """Conta i file CAD nella directory."""
    extensions = get_extensions_for_cad(cad_type)
    print(f" DEBUG count_cad_files: cad_type={cad_type}, extensions={extensions}")
    count = 0
    for filepath in directory.rglob('*'):
        if filepath.suffix.lower() in extensions:
            count += 1
    print(f" DEBUG count_cad_files: trovati {count} file")
    return count


@app.route('/api/status')
def api_status():
    """Stato del sistema con info sui CAD disponibili."""
    with progress_lock:
        progress_copy = progress_state.copy()

    #  Info sui CAD disponibili
    cad_info = {}
    if HAS_MULTI_CAD:
        try:
            cad_info = get_extractor_info()
        except Exception as e:
            print(f" Errore getting CAD info: {e}")

    return jsonify({
        'com_available': HAS_COM,
        'multi_cad_available': HAS_MULTI_CAD,
        'cad_info': cad_info,
        'cached_files': len(get_cached_signatures_unique()),
        'current_directory': analysis_results.get('directory'),
        'progress': progress_copy
    })


@app.route('/api/progress')
def api_progress():
    """Stato di avanzamento dell'analisi (per polling)."""
    with progress_lock:
        progress_copy = progress_state.copy()
    return jsonify(progress_copy)


@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    """Analizza una directory di file CAD."""
    data = request.json
    directory = data.get('directory', '')
    # Opzione per decidere se saltare confronti tra file nella stessa "leaf" folder
    skip_same_leaf = bool(data.get('skip_same_leaf', True))
    #  Tipo CAD selezionato (auto = tutti)
    cad_type = data.get('cad_type', 'auto')

    if not directory or not Path(directory).exists():
        return jsonify({'error': f'Directory not found: {directory}'}), 400

    # Controlla se un'analisi Ã¨ giÃ  in corso
    with progress_lock:
        if progress_state['active']:
            return jsonify({'error': 'An analysis is already running'}), 400

    # Avvia analisi in background thread
    def analyze_in_background():
        cad_label = cad_type if cad_type != 'auto' else 'all CAD types'
        set_progress_status('analyzing', f'Scanning directory ({cad_label})...')
        with progress_lock:
            progress_state['active'] = True

        try:
            # FASE 1: Conteggio file (multi-cad aware)
            update_progress(f' Scanning directory ({cad_label})...', 0, 1, 5)
            total_files = count_cad_files(Path(directory), cad_type)
            if total_files == 0:
                cad_msg = f" of type {cad_type}" if cad_type != 'auto' else ""
                set_progress_status('complete', f'No CAD files{cad_msg} found in the directory')
                with progress_lock:
                    progress_state['percentage'] = 100
                return
            update_progress(f' Found {total_files} {cad_label} files', 0, total_files, 10)
            time.sleep(0.5)

            # Inizializza COM per questo thread
            if HAS_PYTHONCOM:
                pythoncom.CoInitialize()

            # FASE 2: Estrazione firme (multi-cad)
            update_progress(f' Extracting 3D features (0/{total_files})...', 0, total_files, 15)
            print(f" DEBUG: HAS_MULTI_CAD={HAS_MULTI_CAD}, cad_type={cad_type}")

            if cad_type == 'SolidEdge':
                #  Solid Edge: usa la pipeline comprovata legacy
                signatures = analyze_directory(Path(directory), use_com=True)
            else:
                if HAS_MULTI_CAD:
                    signatures = extract_signatures_multi(Path(directory), cad_type)
                else:
                    # Se multi-cad non disponibile: consenti solo SolidEdge/auto, altrimenti errore esplicito
                    if cad_type not in ('auto', 'SolidEdge'):
                        set_progress_status('error', f"CAD {cad_type} is not supported (multi-CAD extractors unavailable)")
                        return
                    signatures = analyze_directory(Path(directory), use_com=True)

            # Aggiorna cache con messaggi
            signatures_cache.clear()
            if len(signatures) == 0:
                update_progress(' No files analyzed successfully', 0, 1, 95)
            else:
                for i, sig in enumerate(signatures):
                    signatures_cache[sig.filepath] = sig
                    canonical = canonical_filepath_for_lookup(sig.filepath)
                    if canonical:
                        signatures_cache[canonical] = sig
                    progress = 15 + int(((i + 1) / len(signatures)) * 35)
                    update_progress(
                        f' Extracted 3D features ({i + 1}/{len(signatures)}): {sig.filename}',
                        i + 1,
                        len(signatures),
                        progress
                    )

            # FASE 3: Confronto coppie
            update_progress(' Comparing file pairs...', 0, 1, 52)
            similar_pairs = []
            total_comparisons = 0
            current_comparison = 0
            weights = load_weights()
            exclusion_policy = get_criteria_exclusion_policy()

            for i in range(len(signatures)):
                for j in range(i + 1, len(signatures)):
                    folder1 = Path(signatures[i].filepath).parent
                    folder2 = Path(signatures[j].filepath).parent
                    if skip_same_leaf and folder1 == folder2 and is_leaf_folder(folder1):
                        continue
                    total_comparisons += 1

            if total_comparisons > 0:
                for i, sig1 in enumerate(signatures):
                    folder1 = Path(sig1.filepath).parent
                    for sig2 in signatures[i + 1:]:
                        folder2 = Path(sig2.filepath).parent
                        if skip_same_leaf and folder1 == folder2 and is_leaf_folder(folder1):
                            continue

                        # Fase 1: estrai raw scores (lento, dipende dai dati)
                        raw = compute_raw_scores(sig1, sig2, lcs_fuzzy_config=weights)
                        # Fase 2: combina con pesi (veloce)
                        sim = combine_scores(raw, weights, exclusion_policy=exclusion_policy)

                        current_comparison += 1
                        progress = 52 + int((current_comparison / max(total_comparisons, 1)) * 45)
                        update_progress(
                            f' Comparison {current_comparison}/{total_comparisons}: {sig1.filename}  {sig2.filename}',
                            current_comparison,
                            total_comparisons,
                            progress
                        )

                        # Prepara raw_scores serializzabili (escludi _sketch_matches con tuple)
                        raw_clean = _serialize_raw_scores(raw)

                        similar_pairs.append({
                            'file1': sig1.filename,
                            'path1': sig1.filepath,
                            'folder1': folder1.name,
                            'features1': sig1.feature_count,
                            'file2': sig2.filename,
                            'path2': sig2.filepath,
                            'folder2': folder2.name,
                            'features2': sig2.feature_count,
                            'similarity': sim['overall'],
                            'details': sim,
                            'raw_scores': raw_clean
                        })
            else:
                update_progress(' No file pairs to compare', 0, 1, 95)

            similar_pairs.sort(key=lambda x: -x['similarity'])
            analysis_results['signatures'] = [asdict(s) for s in signatures]
            analysis_results['directory'] = str(directory)
            analysis_results['similar_pairs'] = similar_pairs
            analysis_results['raw_score_config'] = extract_raw_score_config(weights)
            analysis_results['weights_snapshot'] = extract_weights_snapshot(weights)
            save_analysis_results(
                str(directory),
                analysis_results['signatures'],
                analysis_results['similar_pairs'],
                analysis_results['raw_score_config'],
                analysis_results['weights_snapshot']
            )
            update_progress(' Analysis completed!', len(signatures), len(signatures), 100)
            set_progress_status('complete', f'Analysis completed: {len(signatures)} files, {len(similar_pairs)} similar pairs')

            if HAS_PYTHONCOM:
                pythoncom.CoUninitialize()

        except Exception as e:
            import traceback
            error_msg = f'Error: {str(e)}'
            set_progress_status('error', error_msg)
            print(f'Errore analisi: {traceback.format_exc()}')
        finally:
            with progress_lock:
                progress_state['active'] = False
    # Avvia il thread
    thread = threading.Thread(target=analyze_in_background, daemon=False)
    thread.start()

    return jsonify({
        'success': True,
        'message': 'Analysis started. Track progress at /api/progress'
    })


@app.route('/api/signatures')
def api_signatures():
    """Restituisce tutte le firme analizzate (solo campi leggeri)."""
    # Ritorna solo i campi necessari alla UI (non i dati sketch completi)
    light = []
    for sig in analysis_results['signatures']:
        light.append({
            'filename': sig.get('filename', ''),
            'filepath': sig.get('filepath', ''),
            'feature_count': sig.get('feature_count', 0),
            'file_hash': sig.get('file_hash', ''),
        })
    return jsonify(light)


@app.route('/api/pairs')
def api_pairs():
    """Restituisce le coppie simili filtrate per soglia.
    Query params:
      - threshold: float 0..1 (default 0.0 = tutte)
      - limit: int (default 5000)
      - sync_global: bool (default false). Se true, riallinea ai pesi globali backend.
      - allow_raw_recompute: bool (default false). Se true, permette raw recompute durante sync.
    I raw_scores NON vengono inviati al client (troppo pesanti).
    """
    try:
        threshold = float(request.args.get('threshold', 0.0))
        limit = int(request.args.get('limit', 5000))
        sync_global = str(request.args.get('sync_global', '0')).strip().lower() in ('1', 'true', 'yes', 'on')
        allow_raw_recompute = str(request.args.get('allow_raw_recompute', '0')).strip().lower() in ('1', 'true', 'yes', 'on')
        if sync_global:
            sync_info = ensure_pairs_synced_with_current_weights(
                allow_raw_recompute=allow_raw_recompute,
                persist=False
            )
        else:
            sync_info = {
                'synced': False,
                'updated': 0,
                'recomputed_raw': 0,
                'raw_config_changed': False,
                'weights_changed': False,
                'persisted': False,
            }

        all_pairs = analysis_results.get('similar_pairs', [])
        filtered = []
        for p in all_pairs:
            try:
                sim_val = float(p.get('similarity', 0.0))
            except Exception:
                sim_val = 0.0
            if sim_val >= threshold:
                p['similarity'] = sim_val
                filtered.append(p)
        filtered.sort(key=lambda x: -float(x.get('similarity', 0.0)))
        filtered = filtered[:limit]

        # Strip raw_scores dalla risposta (sono solo per ricalcolo server-side)
        light_pairs = []
        for p in filtered:
            lp = {k: v for k, v in p.items() if k not in ('raw_scores',)}
            light_pairs.append(lp)

        return jsonify({
            'success': True,
            'total': len(all_pairs),
            'filtered': len(filtered),
            'pairs': light_pairs,
            'raw_score_config': analysis_results.get('raw_score_config', {}),
            'sync_info': sync_info
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# âœ¨ ALIAS: `/api/similar_pairs` per backward compatibility con versioni precedenti
@app.route('/api/similar_pairs')
def api_similar_pairs():
    """Alias per /api/pairs - forward compatibilitÃ  con code che cerca similar_pairs."""
    return api_pairs()


@app.route('/api/cad_info')
def api_cad_info():
    """Restituisce informazioni sui CAD supportati e disponibili."""
    cad_info = {
        'multi_cad_available': HAS_MULTI_CAD,
        'cad_types': {}
    }

    # Info di base per ogni CAD
    cad_data = {
        'SolidEdge': {
            'name': 'Solid Edge',
            'extensions': ['.par', '.psm', '.asm'],
            'type': 'COM',
            'icon': 'bi-box'
        },
        'SolidWorks': {
            'name': 'SolidWorks',
            'extensions': ['.sldprt', '.sldasm', '.slddrw'],
            'type': 'COM',
            'icon': 'bi-gear'
        },
        'Inventor': {
            'name': 'Autodesk Inventor',
            'extensions': ['.ipt', '.iam', '.idw'],
            'type': 'COM',
            'icon': 'bi-grid-3x3'
        },
        'CATIA': {
            'name': 'CATIA V5',
            'extensions': ['.catpart', '.catproduct', '.catdrawing'],
            'type': 'COM',
            'icon': 'bi-layers'
        },
        'FreeCAD': {
            'name': 'FreeCAD',
            'extensions': ['.fcstd'],
            'type': 'Native',
            'icon': 'bi-wrench'
        },
        'Fusion360': {
            'name': 'Fusion 360',
            'extensions': ['.f3d', '.f3z'],
            'type': 'Native',
            'icon': 'bi-cloud'
        }
    }

    # Verifica disponibilitÃ  se HAS_MULTI_CAD
    #  Tutti i CAD sono sempre selezionabili nell'interfaccia
    # 'verified' indica se abbiamo verificato che il CAD Ã¨ installato
    # L'utente puÃ² selezionare qualsiasi CAD, riceverÃ  un errore se non disponibile
    if HAS_MULTI_CAD:
        try:
            available_cads = get_available_cads()
            for cad_name, info in cad_data.items():
                info['available'] = True  # Sempre selezionabile
                info['verified'] = available_cads.get(cad_name, False)  # Indica se verificato
                cad_info['cad_types'][cad_name] = info
        except Exception as e:
            print(f" Errore verifica CAD: {e}")
            for cad_name, info in cad_data.items():
                info['available'] = True  # Sempre selezionabile
                info['verified'] = cad_name == 'SolidEdge'  # Solo SE verificato
                cad_info['cad_types'][cad_name] = info
    else:
        # Tutti selezionabili, solo Solid Edge verificato
        for cad_name, info in cad_data.items():
            info['available'] = True  # Sempre selezionabile
            info['verified'] = cad_name == 'SolidEdge'
            cad_info['cad_types'][cad_name] = info

    return jsonify(cad_info)


@app.route('/api/signature', methods=['POST'])
def api_signature_detail():
    """Dettaglio di una singola firma."""
    data = request.json
    filepath = normalize_filepath(data.get('filepath', ''))

    sig = fetch_signature(filepath)
    if sig is not None:
        return jsonify(asdict(sig))

    return jsonify({'error': 'File not found in cache'}), 404


@app.route('/api/compare', methods=['POST'])
def api_compare():
    """Confronto dettagliato di due file.

    Restituisce:
        - similarity: score combinati con pesi (include 'overall')
        - raw_scores: score grezzi per criterio (per ricalcolo istantaneo via /api/recombine)
    """
    data = request.json or {}
    path1 = normalize_filepath(data.get('path1', ''))
    path2 = normalize_filepath(data.get('path2', ''))

    custom_weights = data.get('weights', None)

    if not path1 or not path2:
        return jsonify({'error': 'Missing file paths'}), 400

    sig1 = fetch_signature(path1)
    sig2 = fetch_signature(path2)

    if not sig1 or not sig2:
        return jsonify({'error': 'Unable to find requested signatures'}), 404

    # Fase 1: estrazione score grezzi (LENTA)
    weights = custom_weights if custom_weights is not None else load_weights()
    raw_scores = compute_raw_scores(sig1, sig2, lcs_fuzzy_config=weights)

    # Fase 2: combinazione con pesi (VELOCE)
    exclusion_policy = get_criteria_exclusion_policy()
    similarity = combine_scores(raw_scores, weights, exclusion_policy=exclusion_policy)

    # Prepara raw_scores serializzabili (mantiene anche metadati utili alla policy di esclusione)
    raw_scores_clean = _serialize_raw_scores(raw_scores)

    return jsonify({
        'file1': asdict(sig1),
        'file2': asdict(sig2),
        'similarity': similarity,
        'raw_scores': raw_scores_clean
    })


@app.route('/api/compare_batch', methods=['POST'])
def api_compare_batch():
    """Confronto batch leggero per riallineare rapidamente le coppie visibili."""
    data = request.json or {}
    items = data.get('items', []) or []
    custom_weights = data.get('weights', None)
    weights = custom_weights if custom_weights is not None else load_weights()
    exclusion_policy = get_criteria_exclusion_policy()
    raw_cfg = extract_raw_score_config(weights)

    results = []
    for item in items:
        try:
            path1 = normalize_filepath((item or {}).get('path1', ''))
            path2 = normalize_filepath((item or {}).get('path2', ''))
            if not path1 or not path2:
                continue

            sig1 = fetch_signature(path1)
            sig2 = fetch_signature(path2)
            if not sig1 or not sig2:
                continue

            raw_scores = compute_raw_scores(sig1, sig2, lcs_fuzzy_config=weights)
            similarity = combine_scores(raw_scores, weights, exclusion_policy=exclusion_policy)
            raw_scores_clean = _serialize_raw_scores(raw_scores)

            results.append({
                'path1': path1,
                'path2': path2,
                'similarity': float(similarity.get('overall', 0.0)),
                'details': similarity,
                'raw_scores': raw_scores_clean,
                'raw_score_config': raw_cfg
            })
        except Exception as e:
            results.append({
                'path1': (item or {}).get('path1', ''),
                'path2': (item or {}).get('path2', ''),
                'error': str(e)
            })

    return jsonify({
        'success': True,
        'count': len(results),
        'raw_score_config': raw_cfg,
        'results': results
    })


@app.route('/api/open_in_cad', methods=['POST'])
def api_open_in_cad():
    """
    Apri un file CAD direttamente nell'applicazione associata.

    Strategia:
    1) Per file Solid Edge (.par/.psm/.asm), prova prima via COM su istanza esistente.
    2) Fallback universale: os.startfile (associazione sistema operativo).
    """
    data = request.json or {}
    filepath = normalize_filepath(data.get('filepath', ''))

    if not filepath:
        return jsonify({'success': False, 'error': 'Missing filepath'}), 400

    p = Path(filepath)
    if not p.exists() or not p.is_file():
        return jsonify({'success': False, 'error': f'File not found: {filepath}'}), 404

    ext = p.suffix.lower()
    solid_edge_exts = {'.par', '.psm', '.asm'}
    open_method = None
    fallback_reason = None

    if ext in solid_edge_exts:
        try:
            import win32com.client
            com_inited_here = False
            if HAS_PYTHONCOM:
                try:
                    pythoncom.CoInitialize()
                    com_inited_here = True
                except Exception:
                    com_inited_here = False

            try:
                se_app = win32com.client.GetActiveObject("SolidEdge.Application")
            except Exception:
                se_app = win32com.client.Dispatch("SolidEdge.Application")

            se_app.Visible = True
            try:
                se_app.DisplayAlerts = False
            except Exception:
                pass
            se_app.Documents.Open(str(p))
            open_method = 'solid_edge_com'
            return jsonify({
                'success': True,
                'method': open_method,
                'filepath': str(p)
            })
        except Exception as e:
            fallback_reason = str(e)
        finally:
            if ext in solid_edge_exts and HAS_PYTHONCOM:
                try:
                    if 'com_inited_here' in locals() and com_inited_here:
                        pythoncom.CoUninitialize()
                except Exception:
                    pass

    try:
        os.startfile(str(p))  # type: ignore[attr-defined]
        open_method = 'os_startfile'
        payload = {
            'success': True,
            'method': open_method,
            'filepath': str(p)
        }
        if fallback_reason:
            payload['fallback_reason'] = fallback_reason
        return jsonify(payload)
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Unable to open file in associated CAD application: {e}',
            'filepath': str(p),
            'fallback_reason': fallback_reason
        }), 500


@app.route('/api/recombine', methods=['POST'])
def api_recombine():
    """Ricombina score grezzi con pesi diversi (ISTANTANEO, no ricalcolo dati).

    Accetta:
        - raw_scores: dict di score grezzi (da /api/compare)
        - weights: dict di pesi + parametri fuzzy

    Restituisce:
        - similarity: score combinati con i nuovi pesi (include 'overall')
    """
    data = request.json or {}
    raw_scores = data.get('raw_scores', None)
    weights = data.get('weights', None)

    # Usa check espliciti per None: {} o dict vuoto sono valori validi
    if raw_scores is None or weights is None:
        return jsonify({'error': 'raw_scores and weights are required'}), 400

    if not isinstance(raw_scores, dict):
        return jsonify({'error': 'Malformed raw_scores payload'}), 400
    raw_scores_normalized = _serialize_raw_scores(raw_scores)

    try:
        exclusion_policy = get_criteria_exclusion_policy()
        similarity = combine_scores(raw_scores_normalized, weights, exclusion_policy=exclusion_policy)
        return jsonify({'success': True, 'similarity': similarity})
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        app.logger.error(f"Exception in /api/recombine: {e}\n{tb}")
        return jsonify({'error': str(e), 'traceback': tb}), 500


@app.route('/api/recombine_all', methods=['POST'])
def api_recombine_all():
    """Ricombina TUTTE le coppie con nuovi pesi usando i raw_scores giÃ  salvati.

    Istantaneo: non ricalcola i dati, solo aritmetica.
    Aggiorna analysis_results in-memory.

    Accetta:
        - weights: dict completo dei pesi + parametri fuzzy
        - limit: numero massimo di coppie restituite (default 10000)

    Restituisce:
        - pairs: lista aggiornata con nuovi similarity/details
    """
    data = request.json or {}
    weights = data.get('weights', None)

    if not weights:
        return jsonify({'error': 'weights are required'}), 400

    all_pairs = analysis_results.get('similar_pairs', [])
    if not all_pairs:
        return jsonify({'error': 'No pairs in memory. Run Analyze first.'}), 400

    incoming_raw_cfg = extract_raw_score_config(weights)
    current_raw_cfg = analysis_results.get('raw_score_config', {}) or {}
    raw_config_changed = incoming_raw_cfg != current_raw_cfg
    force_recompute_raw = bool(data.get('recompute_raw', False))
    auto_recompute_raw = bool(data.get('auto_recompute_raw', False))
    persist = bool(data.get('persist', False))
    limit = int(data.get('limit', 10000) or 10000)
    recompute_raw = force_recompute_raw or (auto_recompute_raw and raw_config_changed)
    raw_config_stale = raw_config_changed and not recompute_raw
    exclusion_policy = get_criteria_exclusion_policy()

    updated_count = 0
    recomputed_raw_count = 0
    for pair in all_pairs:
        try:
            if recompute_raw:
                path1 = pair.get('path1', '')
                path2 = pair.get('path2', '')
                sig1 = fetch_signature(path1)
                sig2 = fetch_signature(path2)
                if sig1 is None or sig2 is None:
                    app.logger.warning(
                        f"Warning: missing signatures for raw recompute ({pair.get('file1')} vs {pair.get('file2')})"
                    )
                    continue
                raw = compute_raw_scores(sig1, sig2, lcs_fuzzy_config=weights)
                pair['raw_scores'] = _serialize_raw_scores(raw)
                recomputed_raw_count += 1
            else:
                raw = pair.get('raw_scores')
                if raw is None:
                    continue

            new_sim = combine_scores(raw, weights, exclusion_policy=exclusion_policy)
            pair['similarity'] = new_sim.get('overall', 0.0)
            pair['details'] = new_sim
            updated_count += 1
        except Exception as e:
            app.logger.warning(f"Warning: recombine failed for pair {pair.get('file1')} vs {pair.get('file2')}: {e}")

    # Riordina per similaritÃ 
    all_pairs.sort(key=lambda x: -float(x.get('similarity', 0.0)))
    if recompute_raw:
        analysis_results['raw_score_config'] = incoming_raw_cfg
    analysis_results['weights_snapshot'] = extract_weights_snapshot(weights)

    # Persistenza opzionale (evita I/O pesante durante ricalcoli UI interattivi)
    persisted = False
    try:
        if persist and analysis_results.get('directory'):
            save_analysis_results(
                analysis_results.get('directory'),
                analysis_results.get('signatures', []),
                analysis_results.get('similar_pairs', []),
                analysis_results.get('raw_score_config', {}),
                analysis_results.get('weights_snapshot', {})
            )
            persisted = True
    except Exception as e:
        app.logger.warning(f"Warning: failed to persist recombined analysis: {e}")

    threshold = float(data.get('threshold', 0.0))
    filtered = [p for p in all_pairs if float(p.get('similarity', 0.0)) >= threshold]
    returned_pairs = filtered[:max(0, limit)] if limit >= 0 else filtered

    return jsonify({
        'success': True,
        'updated': updated_count,
        'recomputed_raw': recomputed_raw_count,
        'recompute_raw_used': recompute_raw,
        'raw_config_changed': raw_config_changed,
        'raw_config_stale': raw_config_stale,
        'persisted': persisted,
        'total': len(all_pairs),
        'filtered': len(filtered),
        'returned': len(returned_pairs),
        'pairs': returned_pairs,
        'raw_score_config': analysis_results.get('raw_score_config', {})
    })


@app.route('/api/matrix', methods=['GET', 'POST'])
def api_matrix():
    """Genera matrice di similaritÃ ."""
    payload = request.json if request.method == 'POST' else {}
    custom_weights = payload.get('weights') if isinstance(payload, dict) else None
    if not isinstance(custom_weights, dict):
        custom_weights = None
    use_custom_weights = isinstance(custom_weights, dict)
    exclusion_policy = get_criteria_exclusion_policy() if use_custom_weights else None
    signatures_objs = get_cached_signatures_unique()
    pair_index: dict[tuple[str, str], dict[str, Any]] = {}
    for pair in analysis_results.get('similar_pairs', []) or []:
        path1_key = canonical_filepath_for_lookup(pair.get('path1', ''))
        path2_key = canonical_filepath_for_lookup(pair.get('path2', ''))
        if not path1_key or not path2_key or path1_key == path2_key:
            continue
        pair_key = tuple(sorted([path1_key, path2_key]))
        pair_index[pair_key] = pair
    matrix = []

    for sig1 in signatures_objs:
        row = {
            'file': sig1.filename,
            'path': sig1.filepath,
            'folder': Path(sig1.filepath).parent.name,
            'similarities': {}
        }
        folder1 = Path(sig1.filepath).parent
        for sig2 in signatures_objs:
            folder2 = Path(sig2.filepath).parent
            if sig1.filepath == sig2.filepath:
                row['similarities'][sig2.filepath] = None
            else:
                try:
                    path1_key = canonical_filepath_for_lookup(sig1.filepath)
                    path2_key = canonical_filepath_for_lookup(sig2.filepath)
                    pair_key = tuple(sorted([path1_key, path2_key])) if path1_key and path2_key else ('', '')
                    pair_entry = pair_index.get(pair_key) if pair_key != ('', '') else None

                    sim_value = None
                    if isinstance(pair_entry, dict):
                        if use_custom_weights:
                            raw_scores = pair_entry.get('raw_scores')
                            if not isinstance(raw_scores, dict):
                                # Fallback best-effort: details contiene anche i criteri base.
                                raw_scores = pair_entry.get('details') if isinstance(pair_entry.get('details'), dict) else None
                            if isinstance(raw_scores, dict):
                                sim_fast = combine_scores(raw_scores, custom_weights, exclusion_policy=exclusion_policy)
                                sim_value = float(sim_fast.get('overall', 0.0))
                        else:
                            sim_value = float(pair_entry.get('similarity', 0.0))

                    if sim_value is None:
                        sim = compute_similarity(sig1, sig2, custom_weights=custom_weights)
                        sim_value = float(sim.get('overall', 0.0))

                    row['similarities'][sig2.filepath] = sim_value
                except Exception as e:
                    print(f" Errore compute_similarity in api_matrix per {sig1.filename} vs {sig2.filename}: {e}")
                    row['similarities'][sig2.filepath] = 0.0
        matrix.append(row)

    return jsonify({
        'files': [{'path': s.filepath, 'name': s.filename, 'folder': Path(s.filepath).parent.name} for s in signatures_objs],
        'matrix': matrix
    })


@app.route('/api/export')
def api_export():
    """Esporta i risultati in JSON."""
    return jsonify(analysis_results)


@app.route('/api/sketch_cache/<path:filepath>')
def api_sketch_cache(filepath):
    """Ritorna informazioni sulla cache sketch di un file."""
    try:
        from solid_edge_similarity_v2 import get_sketch_cache_info
        cache_info = get_sketch_cache_info(filepath)

        if cache_info:
            return jsonify(cache_info)
        else:
            return jsonify({'cached': False, 'message': 'No cache available'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/file_sketches/<path:filepath>')
def api_file_sketches(filepath):
    """Ritorna i dati degli sketch di un file specifico."""
    try:
        # Cerca nella cache con lookup canonico (case/slash agnostic su Windows)
        sig = fetch_signature(normalize_filepath(filepath))

        if not sig:
            return jsonify({'error': 'File not found in cache'}), 404

        # Ritorna i dati sketch
        return jsonify({
            'filepath': sig.filepath,
            'filename': sig.filename,
            'sketches_count': sig.sketches_count,
            'total_2d_geometry_count': sig.total_2d_geometry_count,
            'total_2d_constraint_count': sig.total_2d_constraint_count,
            'geometry_2d_types': sig.geometry_2d_types,
            'constraint_2d_types': sig.constraint_2d_types,
            'sketches_data': sig.sketches_data,
            'avg_geometry_per_sketch': sig.avg_geometry_per_sketch,
            'avg_constraints_per_sketch': sig.avg_constraints_per_sketch,
            'constraint_to_geometry_ratio': sig.constraint_to_geometry_ratio
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/purge', methods=['POST'])
def api_purge():
    """Elimina TUTTA la cache."""
    try:
        success = purge_all_cache()
        if success:
            return jsonify({
                'success': True,
                'message': 'Cache fully cleared',
                'cache_dir': str(CACHE_DIR),
                'results_dir': str(RESULTS_DIR)
            })
        else:
            return jsonify({'success': False, 'error': 'Error while purging cache'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/purge_directory', methods=['POST'])
def api_purge_directory():
    """Elimina la cache per una directory specifica."""
    try:
        data = request.json
        directory = data.get('directory', '')

        if not directory:
            return jsonify({'error': 'Directory not specified'}), 400

        success = purge_directory_cache(directory)
        if success:
            return jsonify({
                'success': True,
                'message': f'Directory cache cleared: {directory}'
            })
        else:
            return jsonify({'success': False, 'error': 'Error while purging directory cache'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/load_results', methods=['POST'])
def api_load_results():
    """Carica i risultati salvati di una directory in memoria server-side.
    NON restituisce i dati grezzi al client (possono essere decine di MB).
    Il frontend deve poi chiamare /api/signatures e /api/pairs per i dati.
    """
    try:
        data = request.get_json(silent=True) or {}
        directory = (data.get('directory') or '').strip()

        if not directory:
            return jsonify({
                'success': False,
                'cached': False,
                'error': 'Directory not specified'
            }), 400

        app.logger.info("/api/load_results called with directory: %s", directory)

        results = load_analysis_results(directory)
        if results:
            app.logger.info("Results found in cache. Populating memory...")
            n_sigs, n_pairs = _populate_results_in_memory(results, directory_fallback=directory)
            app.logger.info(
                "Loaded in memory: signatures=%s pairs=%s (backend summary: %s/%s)",
                len(analysis_results['signatures']),
                len(analysis_results['similar_pairs']),
                n_sigs,
                n_pairs
            )

            # Restituisce SOLO il summary (no dati grezzi)
            return jsonify({
                'success': True,
                'cached': True,
                'summary': {
                    'directory': analysis_results['directory'],
                    'file_count': n_sigs,
                    'pairs_count': n_pairs,
                    'timestamp': results.get('timestamp', '')
                }
            })
        else:
            app.logger.info("No saved results found for directory: %s", directory)
            return jsonify({
                'success': True,
                'cached': False,
                'message': 'No saved results for this directory'
            })
    except Exception as e:
        app.logger.exception("Unhandled exception in /api/load_results")
        return jsonify({
            'success': False,
            'cached': False,
            'error': str(e) or e.__class__.__name__
        }), 500
@app.route('/api/load_latest_results', methods=['GET'])
def api_load_latest_results():
    """Carica in memoria l'ultimo file risultati disponibile in cache."""
    try:
        candidates = sorted(
            RESULTS_DIR.glob('analysis_*.json'),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        if not candidates:
            return jsonify({
                'success': True,
                'cached': False,
                'message': 'No saved results available'
            })

        for filepath in candidates:
            try:
                if filepath.stat().st_size == 0:
                    continue
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                if not content.strip():
                    continue
                results = json.loads(content)
            except Exception:
                continue

            n_sigs, n_pairs = _populate_results_in_memory(results, directory_fallback='')
            return jsonify({
                'success': True,
                'cached': True,
                'summary': {
                    'directory': analysis_results.get('directory', ''),
                    'file_count': n_sigs,
                    'pairs_count': n_pairs,
                    'timestamp': results.get('timestamp', '')
                }
            })

        return jsonify({
            'success': True,
            'cached': False,
            'message': 'Cache found but not readable'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/cache_info')
def api_cache_info():
    """Restituisce informazioni sulla cache."""
    try:
        # Conta file sketch
        sketch_files = list(CACHE_DIR.glob('sketch_data_*.json'))

        # Conta file risultati
        results_files = list(RESULTS_DIR.glob('analysis_*.json'))

        # Calcola size totale
        total_size = 0
        for f in sketch_files + results_files:
            try:
                total_size += f.stat().st_size
            except:
                pass

        return jsonify({
            'cache_dir': str(CACHE_DIR),
            'results_dir': str(RESULTS_DIR),
            'sketch_files': len(sketch_files),
            'results_files': len(results_files),
            'total_size_bytes': total_size,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'results_files_list': [f.name for f in results_files]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/debug_cache', methods=['POST'])
def api_debug_cache():
    """Debug endpoint: mostra quale file di cache corrisponde a un path."""
    try:
        data = request.json or {}
        path_to_check = data.get('path', '')

        if not path_to_check:
            return jsonify({'error': 'path parameter required'}), 400

        import hashlib
        expected_hash = hashlib.md5(path_to_check.encode()).hexdigest()
        expected_filename = f'analysis_{expected_hash}.json'
        expected_filepath = RESULTS_DIR / expected_filename

        # Lista tutti i file di cache
        all_files = list(RESULTS_DIR.glob('analysis_*.json'))

        result = {
            'path_checked': path_to_check,
            'expected_hash': expected_hash,
            'expected_filename': expected_filename,
            'file_exists': expected_filepath.exists(),
            'file_size_mb': None,
            'all_cache_files': [],
            'original_paths': []
        }

        if expected_filepath.exists():
            result['file_size_mb'] = round(expected_filepath.stat().st_size / (1024*1024), 2)

        # Mostra tutti i file E il path originale salvato dentro
        for f in all_files:
            file_info = {
                'name': f.name,
                'size_mb': round(f.stat().st_size / (1024*1024), 2),
                'original_path': None
            }

            # Leggi il JSON per ottenere il path originale
            try:
                with open(f, 'r', encoding='utf-8') as jf:
                    cache_data = json.load(jf)
                    file_info['original_path'] = cache_data.get('directory', 'NOT FOUND')
            except Exception as e:
                file_info['original_path'] = f'ERROR: {str(e)}'

            result['all_cache_files'].append(file_info)
            result['original_paths'].append(file_info['original_path'])

        return jsonify({'success': True, 'debug': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================================
# PLAGIARISM LABELS API - Etichettatura manuale dei casi di plagio
# ============================================================================

@app.route('/api/plagiarism_labels', methods=['GET'])
def api_get_plagiarism_labels():
    """
    Restituisce tutte le etichette di plagio salvate.
    Query params opzionali:
    - session: filtra per nome sessione
    """
    if not HAS_PLAGIARISM_LABELS:
        return jsonify({'error': 'Labeling system is not available'}), 500

    try:
        labels_db = load_labels_db(DEFAULT_LABELS_DB_PATH)

        # Filtra per sessione se richiesto
        session_filter = request.args.get('session')
        if session_filter:
            labels_db = {
                k: v for k, v in labels_db.items()
                if v.get('session') == session_filter
            }

        return jsonify({
            'success': True,
            'labels': labels_db,
            'count': len(labels_db),
            'labels_db_path': str(DEFAULT_LABELS_DB_PATH)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/plagiarism_labels', methods=['POST'])
def api_set_plagiarism_label():
    """
    Imposta o aggiorna l'etichetta di plagio per una coppia di file.
    Body JSON:
    {
        "session": "nome_sessione",
        "file_a": "file1.par",
        "file_b": "file2.par",
        "label": "CONFIRMED_PLAGIARISM" | "NOT_PLAGIARISM" | "UNDECIDED",
        "notes": "note opzionali"
    }
    """
    if not HAS_PLAGIARISM_LABELS:
        return jsonify({'error': 'Labeling system is not available'}), 500

    try:
        data = request.json or {}

        session = data.get('session', '')
        file_a = data.get('file_a', '')
        file_b = data.get('file_b', '')
        label_str = data.get('label', '')
        notes = data.get('notes', '')

        if not session or not file_a or not file_b:
            return jsonify({'error': 'Missing required fields: session, file_a, file_b'}), 400

        # Valida l'etichetta
        try:
            label = PlagiarismLabel(label_str)
        except ValueError:
            valid_labels = [l.value for l in PlagiarismLabel]
            return jsonify({
                'error': f'Invalid label: {label_str}. Allowed values: {valid_labels}'
            }), 400

        # Carica, aggiorna e salva
        labels_db = load_labels_db(DEFAULT_LABELS_DB_PATH)
        key = set_label(labels_db, session, file_a, file_b, label, notes)

        if save_labels_db(DEFAULT_LABELS_DB_PATH, labels_db):
            return jsonify({
                'success': True,
                'key': key,
                'label': label.value,
                'message': f'Label saved: {label.value}'
            })
        else:
            return jsonify({'error': 'Error saving database'}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/plagiarism_labels/<path:key>', methods=['DELETE'])
def api_delete_plagiarism_label(key):
    """Elimina un'etichetta di plagio."""
    if not HAS_PLAGIARISM_LABELS:
        return jsonify({'error': 'Labeling system is not available'}), 500

    try:
        labels_db = load_labels_db(DEFAULT_LABELS_DB_PATH)

        if key in labels_db:
            del labels_db[key]
            if save_labels_db(DEFAULT_LABELS_DB_PATH, labels_db):
                return jsonify({'success': True, 'message': f'Label deleted: {key}'})
            else:
                return jsonify({'error': 'Error saving database'}), 500
        else:
            return jsonify({'error': f'Label not found: {key}'}), 404

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/plagiarism_labels/export', methods=['GET'])
def api_export_plagiarism_labels():
    """Esporta le etichette in formato CSV."""
    if not HAS_PLAGIARISM_LABELS:
        return jsonify({'error': 'Labeling system is not available'}), 500

    try:
        labels_db = load_labels_db(DEFAULT_LABELS_DB_PATH)

        if not labels_db:
            return jsonify({'error': 'No labels available for export'}), 400

        # Genera CSV in memoria
        import io
        output = io.StringIO()
        import csv
        writer = csv.DictWriter(output, fieldnames=[
            'session', 'file_a', 'file_b', 'label', 'timestamp', 'notes'
        ])
        writer.writeheader()
        for entry in labels_db.values():
            writer.writerow(entry)

        csv_content = output.getvalue()

        from flask import Response
        return Response(
            csv_content,
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=plagiarism_labels.csv'}
        )

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/plagiarism_labels/import', methods=['POST'])
def api_import_plagiarism_labels():
    """Importa etichette da file CSV caricato."""
    if not HAS_PLAGIARISM_LABELS:
        return jsonify({'error': 'Labeling system is not available'}), 500

    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        # Leggi CSV
        import io
        import csv
        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        reader = csv.DictReader(stream)

        labels_db = load_labels_db(DEFAULT_LABELS_DB_PATH)
        imported = 0

        for row in reader:
            key = normalize_pair_key(row['session'], row['file_a'], row['file_b'])
            labels_db[key] = {
                'session': row['session'],
                'file_a': row['file_a'],
                'file_b': row['file_b'],
                'label': row['label'],
                'timestamp': row.get('timestamp', ''),
                'notes': row.get('notes', '')
            }
            imported += 1

        if save_labels_db(DEFAULT_LABELS_DB_PATH, labels_db):
            return jsonify({
                'success': True,
                'imported': imported,
                'message': f'Imported {imported} labels'
            })
        else:
            return jsonify({'error': 'Error saving database'}), 500

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/plagiarism_labels/stats')
def api_plagiarism_stats():
    """Returns statistics on plagiarism labels."""
    try:
        labels_db = load_labels_db(DEFAULT_LABELS_DB_PATH)

        stats = {
            'total': len(labels_db),
            'confirmed': 0,
            'not_plagiarism': 0,
            'undecided': 0,
            'by_session': {}
        }

        for entry in labels_db.values():
            label = entry.get('label', '')
            session = entry.get('session', 'unknown')

            if label == 'CONFIRMED_PLAGIARISM':
                stats['confirmed'] += 1
            elif label == 'NOT_PLAGIARISM':
                stats['not_plagiarism'] += 1
            elif label == 'UNDECIDED':
                stats['undecided'] += 1

            if session not in stats['by_session']:
                stats['by_session'][session] = {
                    'total': 0,
                    'confirmed': 0,
                    'not_plagiarism': 0,
                    'undecided': 0
                }
            stats['by_session'][session]['total'] += 1
            if label == 'CONFIRMED_PLAGIARISM':
                stats['by_session'][session]['confirmed'] += 1
            elif label == 'NOT_PLAGIARISM':
                stats['by_session'][session]['not_plagiarism'] += 1
            elif label == 'UNDECIDED':
                stats['by_session'][session]['undecided'] += 1

        return jsonify({'success': True, 'stats': stats})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ============================================================================
# PAPER WRITING MODE API
# ============================================================================

def load_app_config() -> dict:
    """Carica la configurazione dell'app da config.json.
    Restituisce i default_weights se presenti."""
    config_path = Path(__file__).parent.parent / 'config.json'
    try:
        if not config_path.exists():
            print(f"âš ï¸  Config file not found: {config_path}")
            return {}

        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)

        return config
    except Exception as e:
        print(f"âš ï¸  Error loading config.json: {e}")
        return {}


def get_default_similarity_threshold(config: dict | None = None) -> float:
    """Default threshold (0..1) used by paper-writing workflows and UI highlighting."""
    cfg = config if isinstance(config, dict) else load_app_config()
    pw_cfg = cfg.get('paper_writing_mode', {}) if isinstance(cfg, dict) else {}
    threshold = _safe_float((pw_cfg or {}).get('default_similarity_threshold', 0.75), 0.75)
    return max(0.5, min(0.99, threshold))


def get_optimizer_training_policy(config: dict | None = None) -> dict:
    """Returns optimizer dataset policy from config."""
    cfg = config if isinstance(config, dict) else load_app_config()
    pw_cfg = cfg.get('paper_writing_mode', {}) if isinstance(cfg, dict) else {}
    opt_cfg = (pw_cfg or {}).get('optimization', {}) if isinstance(pw_cfg, dict) else {}
    if not isinstance(opt_cfg, dict):
        opt_cfg = {}
    return {
        'strict_path_labeled_pairs_only': bool(opt_cfg.get('strict_path_labeled_pairs_only', True)),
    }


def get_criteria_exclusion_policy(config: dict | None = None) -> dict:
    """Returns normalized criteria-exclusion policy from config."""
    cfg = config if isinstance(config, dict) else load_app_config()
    raw = cfg.get('criteria_exclusion_policy', {}) if isinstance(cfg, dict) else {}
    if not isinstance(raw, dict):
        raw = {}

    valid_names = set(OPT_WEIGHT_NAMES)

    def _normalize_names(values: Any) -> list[str]:
        out: list[str] = []
        if not isinstance(values, list):
            return out
        for item in values:
            name = str(item or '').strip()
            if not name or name not in valid_names:
                continue
            if name not in out:
                out.append(name)
        return out

    force_excluded = _normalize_names(raw.get('force_excluded', []))
    force_included = [n for n in _normalize_names(raw.get('force_included', [])) if n not in force_excluded]

    return {
        'enabled': bool(raw.get('enabled', True)),
        'exclude_if_unavailable': bool(raw.get('exclude_if_unavailable', True)),
        'exclude_if_missing_or_non_finite': bool(raw.get('exclude_if_missing_or_non_finite', True)),
        'force_excluded': force_excluded,
        'force_included': force_included,
    }


def get_effective_weights() -> dict:
    """Ottiene i pesi effettivi da usare.
    PrioritÃ :
    1. default_weights dal config.json
    2. Fallback a load_weights() dal modulo
    """
    config = load_app_config()
    if config and 'default_weights' in config:
        weights = config['default_weights']
        print(f"âœ… Loaded weights from config.json ({len(weights)} entries)")
        return weights
    else:
        weights = load_weights()
        print(f"âš ï¸  Using default module weights (no config.json found)")
        return weights


@app.route('/api/paper_writing_mode', methods=['GET'])
def api_get_paper_writing_mode():
    """Returns the Paper Writing Mode status."""
    config = load_app_config()
    pw_config = config.get('paper_writing_mode', {})
    return jsonify({
        'success': True,
        'enabled': pw_config.get('enabled', False),
        'show_toggle': pw_config.get('show_toggle', False),
        'config': pw_config
    })


@app.route('/api/paper_writing_mode', methods=['POST'])
def api_set_paper_writing_mode():
    """Attiva/disattiva il Paper Writing Mode."""
    try:
        data = request.json or {}
        enabled = data.get('enabled', False)

        config = load_app_config()
        if 'paper_writing_mode' not in config:
            config['paper_writing_mode'] = {
                'enabled': False,
                'description': 'When enabled, activates Paper Writing features',
                'default_similarity_threshold': 0.75
            }

        config['paper_writing_mode']['enabled'] = enabled

        if save_app_config(config):
            return jsonify({'success': True, 'enabled': enabled})
        else:
            return jsonify({'error': 'Error saving configuration'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def extract_track_id_from_filename(filename: str, patterns: list = None) -> str:
    """Estrae l'identificativo del track dal nome del file."""
    import re

    if patterns is None:
        patterns = [
            r'A(\d{2,4})',
            r'TRACCIA[_\s]?(\d+)',
            r'TRACK[_\s]?(\d+)',
            r'T(\d{2,4})',
            r'ES[_\s]?(\d+)',
            r'EX[_\s]?(\d+)'
        ]

    name_without_ext = Path(filename).stem

    for pattern in patterns:
        try:
            match = re.search(pattern, name_without_ext, re.IGNORECASE)
            if match:
                track_id = match.group(1) if match.lastindex else match.group(0)
                if track_id.isdigit():
                    track_id = str(int(track_id))
                return f"T{track_id}" if track_id.isdigit() else track_id
        except:
            pass

    return "OUTLIER"


def collect_session_files(session_folder: Path) -> list:
    """Raccoglie tutti i file .par da una sessione d'esame."""
    par_files = []

    # Cerca sottocartelle MECCANICI e NON MECCANICI
    subfolders_found = []
    for subfolder_name in ["MECCANICI", "NON MECCANICI"]:
        for item in session_folder.iterdir():
            if item.is_dir() and item.name.upper() == subfolder_name.upper():
                subfolders_found.append(item)
                break

    if subfolders_found:
        for subfolder in subfolders_found:
            for f in subfolder.rglob('*'):
                if f.is_file() and f.suffix.lower() == '.par':
                    par_files.append(f)
    else:
        for f in session_folder.rglob('*'):
            if f.is_file() and f.suffix.lower() == '.par':
                par_files.append(f)

    return par_files


def count_students_in_session(session_folder: Path) -> int:
    """
    Conta il numero di studenti in una sessione.

    LOGICA CORRETTA:
    1 studente = 1 cartella che contiene almeno un file .par

    Alcuni studenti consegnano piÃ¹ file .par nella stessa cartella,
    quindi NON possiamo contare i file ma dobbiamo contare le cartelle.

    Returns:
        int: numero di cartelle uniche che contengono almeno un file .par
    """
    student_folders = set()

    # Cerca sottocartelle MECCANICI e NON MECCANICI
    subfolders_to_scan = []
    for subfolder_name in ["MECCANICI", "NON MECCANICI"]:
        for item in session_folder.iterdir():
            if item.is_dir() and item.name.upper() == subfolder_name.upper():
                subfolders_to_scan.append(item)
                break

    # Se non troviamo MECCANICI/NON MECCANICI, scansiona direttamente la root
    if not subfolders_to_scan:
        subfolders_to_scan = [session_folder]

    # Trova tutte le cartelle che contengono almeno un .par
    for base_folder in subfolders_to_scan:
        for par_file in base_folder.rglob('*.par'):
            # La cartella dello studente Ã¨ il parent del file .par
            student_folder = par_file.parent
            # Usa il path assoluto come chiave univoca
            student_folders.add(str(student_folder.resolve()))

    return len(student_folders)


@app.route('/api/paper_writing/analyze_sessions', methods=['POST'])
def api_analyze_sessions():
    """
    Analizza tutte le sessioni d'esame nel dataset.
    Il dataset root deve contenere sottocartelle, una per ogni sessione/appello.
    """
    try:
        data = request.json or {}
        root_path = Path(data.get('root', ''))
        default_threshold = get_default_similarity_threshold()
        threshold = max(0.5, min(0.99, _safe_float(data.get('threshold', default_threshold), default_threshold)))

        if not root_path.exists() or not root_path.is_dir():
            return jsonify({'error': f'Invalid directory: {root_path}'}), 400

        # Trova tutte le sessioni (sottocartelle dirette)
        sessions = [f for f in root_path.iterdir() if f.is_dir() and not f.name.startswith('.')]

        if not sessions:
            return jsonify({'error': 'No sessions found in dataset'}), 400

        results = []

        for session_folder in sorted(sessions):
            session_name = session_folder.name
            par_files = collect_session_files(session_folder)

            #  CORRETTO: Conta le cartelle, non i file
            total_students = count_students_in_session(session_folder)

            # Estrai track IDs
            tracks = {}
            for f in par_files:
                track_id = extract_track_id_from_filename(f.name)
                tracks[f.name] = track_id

            unique_tracks = set(tracks.values())

            results.append({
                'session': session_name,
                'total_students': total_students,
                'tracks': len(unique_tracks),
                'files': [{'name': f.name, 'track': tracks[f.name]} for f in par_files]
            })

        return jsonify({
            'success': True,
            'root': str(root_path),
            'sessions_count': len(results),
            'sessions': results
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/paper_writing/set_pair_label', methods=['POST'])
def api_paper_writing_set_pair_label():
    """
    Salva una label di plagio per una coppia di file.
    Chiamato dal frontend JS con payload:
    { file1, file2, label, similarity, path1, path2, session }
    """
    try:
        data = request.json or {}
        file1 = data.get('file1', '') or ''
        file2 = data.get('file2', '') or ''
        label_str = data.get('label', '') or ''
        similarity = float(data.get('similarity', 0.0) or 0.0)
        path1 = data.get('path1', '') or ''
        path2 = data.get('path2', '') or ''
        session = data.get('session', '') or ''

        if not file1 or not file2 or not label_str:
            return jsonify({'success': False, 'error': 'file1, file2 and label are required'}), 400

        analysis_root = None
        if analysis_results.get('directory'):
            try:
                analysis_root = Path(analysis_results.get('directory', ''))
            except Exception:
                analysis_root = None

        inferred_session = _infer_shared_session_from_paths(path1, path2, analysis_root)
        if inferred_session:
            session = inferred_session

        # Fallback: se ancora non abbiamo il session, usa il nome folder
        if not session:
            session = analysis_results.get('directory', '')
            if session:
                session = Path(session).name
            else:
                session = 'unknown'

        # Estrai solo il nome file (non il path completo)
        fname1 = Path(file1).name if file1 else file1
        fname2 = Path(file2).name if file2 else file2

        try:
            label = PlagiarismLabel(label_str)
        except ValueError:
            valid = [l.value for l in PlagiarismLabel]
            return jsonify({'success': False, 'error': f'Invalid label: {label_str}. Allowed values: {valid}'}), 400

        notes = f'similarity: {similarity * 100:.1f}%'
        labels_db = load_labels_db(DEFAULT_LABELS_DB_PATH)
        key = set_label(
            labels_db,
            session,
            fname1,
            fname2,
            label,
            notes,
            path_a=path1,
            path_b=path2,
        )

        print(f"  âœ… Label saved: session='{session}', file_a='{fname1}', file_b='{fname2}', label={label.value}")

        if save_labels_db(DEFAULT_LABELS_DB_PATH, labels_db):
            return jsonify({'success': True, 'key': key, 'label': label.value, 'session': session})
        else:
            return jsonify({'success': False, 'error': 'Error saving database'}), 500

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/paper_writing/get_pair_labels', methods=['GET'])
def api_paper_writing_get_pair_labels():
    """
    Restituisce le labels per le coppie, come mappa lookup_key -> label_value.
    Usato dal frontend per inizializzare paperWritingState.pairLabels.
    """
    try:
        labels_db = load_labels_db(DEFAULT_LABELS_DB_PATH)

        labels_map = {}
        for entry in labels_db.values():
            label_value = entry.get('label', '')
            keys = build_pair_label_lookup_keys(
                session=entry.get('session', ''),
                file_a=entry.get('file_a', ''),
                file_b=entry.get('file_b', ''),
                path_a=entry.get('path_a', '') or entry.get('path1', ''),
                path_b=entry.get('path_b', '') or entry.get('path2', ''),
            )
            if not keys:
                continue
            for key in keys:
                labels_map[key] = label_value

        return jsonify({'success': True, 'labels': labels_map, 'count': len(labels_map)})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/paper_writing/export_latex', methods=['POST'])
def api_export_latex():
    """
    Generate and export LaTeX table for Springer two-column.

    Supports two structures:
    - SINGLE SESSION: root = the session folder itself (e.g. Exam 03-07-2024)
       produces one row in the table
    - MULTI SESSION: root contains multiple session subfolders
       one row per session subfolder

    The session is detected automatically by checking if root contains
    MECCANICI/NON MECCANICI subfolders (single session) or other subfolders.
    """
    import statistics as stats_module

    try:
        data = request.json or {}
        root_path = Path(data.get('root', ''))
        default_threshold = get_default_similarity_threshold()
        threshold = max(0.5, min(0.99, _safe_float(data.get('threshold', default_threshold), default_threshold)))

        # Se root Ã¨ vuoto, usa il path del nuovo dataset
        if not root_path or not root_path.exists():
            default_root = Path(r'C:\Users\emanu\Desktop\Didattica CAD\Esami Fondamenti di CAD')
            if default_root.exists():
                root_path = default_root
                print(f"  â„¹ï¸  Using default dataset root: {root_path}")

        if not root_path.exists():
            return jsonify({'error': f'Directory not found: {root_path}'}), 400

        # Load plagiarism labels
        labels_db = load_labels_db(DEFAULT_LABELS_DB_PATH) if HAS_PLAGIARISM_LABELS else {}

        # Helper functions
        def _norm_session(s: str) -> str:
            if not s: return ''
            return ' '.join(s.replace('_', ' ').split()).upper()

        confirmed_by_session: dict[str, int] = {}
        for entry in (labels_db.values() if isinstance(labels_db, dict) else []):
            if (entry or {}).get('label') != 'CONFIRMED_PLAGIARISM':
                continue
            entry_sess = str((entry or {}).get('session', '') or '').strip()
            if not entry_sess:
                continue
            confirmed_by_session[entry_sess] = int(confirmed_by_session.get(entry_sess, 0)) + 1

        def _get_confirmed_count(session_name_str: str) -> int:
            # Match diretto col nome sessione salvato nel DB etichette.
            return int(confirmed_by_session.get(session_name_str, 0))


        #
        # AUTO-DETECT SESSIONS: Scansiona root_path per trovare cartelle figlie che sono sessioni d'esame
        #
        all_subfolders = [f for f in root_path.iterdir() if f.is_dir() and not f.name.startswith('.')]

        # Identifica quale cartelle sono sessioni (contengono MECCANICI/NON MECCANICI o .par)
        session_folders = []
        for folder in all_subfolders:
            subfolder_names_upper = {f.name.upper() for f in folder.iterdir() if f.is_dir()}
            has_student_categories = 'MECCANICI' in subfolder_names_upper or 'NON MECCANICI' in subfolder_names_upper
            has_par_files = any(f.suffix.lower() == '.par' for f in folder.rglob('*') if f.is_file())

            if has_student_categories or has_par_files:
                session_folders.append(folder)

        # Se nessuna session_folders trovata, prova root_path stesso come singola sessione
        if not session_folders:
            subfolder_names_root = {f.name.upper() for f in root_path.iterdir() if f.is_dir() and not f.name.startswith('.')}
            is_root_session = (
                'MECCANICI' in subfolder_names_root or
                'NON MECCANICI' in subfolder_names_root or
                any(f.suffix.lower() == '.par' for f in root_path.rglob('*') if f.is_file())
            )
            if is_root_session:
                session_folders = [root_path]

        print(f" Export LaTeX: root={root_path}, detected {len(session_folders)} session(s)")

        # Carica le coppie una sola volta (non per ogni sessione).
        print(f" Loading pairs data (this may take a moment)...")
        all_pairs_root = []
        all_signatures_root = []

        current_analyzed = analysis_results.get('directory', '')
        if analysis_results.get('similar_pairs') and current_analyzed:
            if Path(current_analyzed).resolve() == root_path.resolve():
                all_pairs_root = analysis_results['similar_pairs']
                all_signatures_root = analysis_results.get('signatures', []) or []
                print(f"   Using in-memory data: {len(all_pairs_root)} pairs")

        if not all_pairs_root:
            data_cached = load_analysis_results(str(root_path))
            if data_cached and 'similar_pairs' in data_cached:
                all_pairs_root = data_cached.get('similar_pairs', []) or []
                all_signatures_root = data_cached.get('signatures', []) or []
                print(f"   Loaded from cache: {len(all_pairs_root)} pairs")

        if not all_pairs_root:
            all_pairs_root = []
            print(f"   No pairs data found")

        # Fast path: precompute per-session aggregates from loaded analysis data.
        # This avoids repeated filesystem scans and per-session full scans of all pairs.
        session_fast_data: dict[str, dict] = {}
        if all_pairs_root:
            session_student_folders: dict[str, set[str]] = {}
            session_tracks: dict[str, set[str]] = {}
            session_similarity_stats: dict[str, dict] = {}
            root_norm = str(root_path).replace('/', '\\').rstrip('\\')
            root_norm_lower = root_norm.lower()

            def _fast_session_from_path(filepath: str) -> str:
                raw = str(filepath or '')
                if not raw:
                    return ''
                norm = raw.replace('/', '\\')
                norm_lower = norm.lower()
                if norm_lower.startswith(root_norm_lower + '\\'):
                    rel = norm[len(root_norm) + 1:]
                    first = rel.split('\\', 1)[0].strip()
                    if not first:
                        return ''
                    if first.upper() in ('MECCANICI', 'NON MECCANICI'):
                        return root_path.name
                    return first
                # Fallback for unexpected paths.
                return _infer_session_from_filepath(raw, root_path)

            def _register_student_and_track(session_ns: str, filepath: str, filename: str):
                if not session_ns:
                    return
                session_student_folders.setdefault(session_ns, set())
                session_tracks.setdefault(session_ns, set())

                p = Path(filepath) if filepath else Path(filename or '')
                student_folder = ''
                try:
                    if filepath:
                        student_folder = str(p.parent)
                    elif p.parent and str(p.parent) not in ('.', ''):
                        student_folder = str(p.parent)
                except Exception:
                    student_folder = str(p.parent) if p.parent else ''
                if student_folder:
                    session_student_folders[session_ns].add(student_folder)

                track = extract_track_id_from_filename(filename or p.name)
                if track:
                    session_tracks[session_ns].add(track)

            def _update_similarity_stats(session_ns: str, sim_raw: float):
                stats = session_similarity_stats.get(session_ns)
                if stats is None:
                    stats = {
                        'count': 0,
                        'sum': 0.0,
                        'sum_sq': 0.0,
                        'min': float('inf'),
                        'max': 0.0,
                        'suspected': 0,
                    }
                    session_similarity_stats[session_ns] = stats
                sim_pct = max(0.0, float(sim_raw) * 100.0)
                stats['count'] += 1
                stats['sum'] += sim_pct
                stats['sum_sq'] += sim_pct * sim_pct
                stats['min'] = min(float(stats['min']), sim_pct)
                stats['max'] = max(float(stats['max']), sim_pct)
                if float(sim_raw) >= threshold:
                    stats['suspected'] += 1

            # Single pass over all pairs.
            for p in all_pairs_root:
                path1 = str(p.get('path1', '') or '')
                path2 = str(p.get('path2', '') or '')
                file1 = str(p.get('file1', '') or Path(path1).name)
                file2 = str(p.get('file2', '') or Path(path2).name)

                sess1 = _fast_session_from_path(path1)
                sess2 = _fast_session_from_path(path2)
                ns1 = _norm_session(sess1)
                ns2 = _norm_session(sess2)

                if ns1:
                    _register_student_and_track(ns1, path1, file1)
                if ns2:
                    _register_student_and_track(ns2, path2, file2)

                # For session statistics include pairs where both files are in the same session.
                if ns1 and ns2 and ns1 == ns2:
                    _update_similarity_stats(ns1, float(p.get('similarity', 0.0) or 0.0))

            # Signatures provide a more robust student/track count when available.
            if isinstance(all_signatures_root, list) and all_signatures_root:
                for sig in all_signatures_root:
                    filepath = str((sig or {}).get('filepath', '') or '')
                    filename = str((sig or {}).get('filename', '') or Path(filepath).name)
                    sess = _fast_session_from_path(filepath)
                    ns = _norm_session(sess)
                    _register_student_and_track(ns, filepath, filename)

            for folder in session_folders:
                ns = _norm_session(folder.name)
                session_fast_data[ns] = {
                    'sim_stats': session_similarity_stats.get(ns, {}),
                    'students': len(session_student_folders.get(ns, set())),
                    'tracks': len(session_tracks.get(ns, set())),
                }

        def process_session(session_folder):
            """Processa UNA sessione e restituisce i dati aggregati."""
            session_name = session_folder.name
            session_ns = _norm_session(session_name)

            fast_data = session_fast_data.get(session_ns, {})
            sim_stats_fast = fast_data.get('sim_stats', {}) or {}
            total_students = int(fast_data.get('students', 0) or 0)
            num_tracks = int(fast_data.get('tracks', 0) or 0)

            par_files = []
            if total_students <= 0:
                par_files = collect_session_files(session_folder)
                total_students = count_students_in_session(session_folder)
                tracks = set(extract_track_id_from_filename(f.name) for f in par_files)
                num_tracks = len(tracks)

            if total_students == 0:
                print(f"  âŠ˜ Skipping '{session_name}': no students found")
                return None

            pair_count = int(sim_stats_fast.get('count', 0) or 0)
            if pair_count > 0:
                sim_sum = float(sim_stats_fast.get('sum', 0.0) or 0.0)
                sim_sum_sq = float(sim_stats_fast.get('sum_sq', 0.0) or 0.0)
                avg_sim = sim_sum / pair_count
                max_sim = float(sim_stats_fast.get('max', 0.0) or 0.0)
                min_sim = float(sim_stats_fast.get('min', 0.0) or 0.0)
                if pair_count > 1:
                    variance = max(0.0, (sim_sum_sq - (sim_sum * sim_sum) / pair_count) / (pair_count - 1))
                    std_sim = variance ** 0.5
                else:
                    std_sim = 0.0
                suspected_count = int(sim_stats_fast.get('suspected', 0) or 0)
            else:
                # Legacy fallback filtering only when fast precompute did not provide stats.
                if not par_files:
                    par_files = collect_session_files(session_folder)
                session_file_names = set(f.name for f in par_files)
                session_pairs = [
                    p for p in all_pairs_root
                    if (p.get('file1', '') in session_file_names or session_name in (p.get('path1', '') or ''))
                    and (p.get('file2', '') in session_file_names or session_name in (p.get('path2', '') or ''))
                ]
                if session_pairs:
                    sims = [float(p.get('similarity', 0.0) or 0.0) * 100.0 for p in session_pairs]
                    avg_sim = stats_module.mean(sims)
                    max_sim = max(sims)
                    min_sim = min(sims)
                    std_sim = stats_module.stdev(sims) if len(sims) > 1 else 0.0
                    suspected_count = sum(1 for p in session_pairs if float(p.get('similarity', 0.0) or 0.0) >= threshold)
                    pair_count = len(session_pairs)
                else:
                    avg_sim = max_sim = min_sim = std_sim = 0.0
                    suspected_count = 0
                    pair_count = 0

            # Conta confirmed plagiarism labels per questa sessione
            confirmed_count = _get_confirmed_count(session_name)

            # Formatta session name per LaTeX
            session_name_normalized = session_name.replace('_', ' ').replace('-', ' ').strip()
            session_display = session_name_normalized
            for char, repl in [('&', r'\&'), ('%', r'\%'), ('_', r'\_'), ('#', r'\#')]:
                session_display = session_display.replace(char, repl)

            print(f"  âœ… Session '{session_name}': {pair_count} pairs, {total_students} students, confirmed={confirmed_count}")

            return {
                'session': session_display,
                'students': total_students,
                'tracks': num_tracks,
                'avg': avg_sim,
                'max': max_sim,
                'min': min_sim,
                'std': std_sim,
                'suspected': suspected_count,
                'confirmed': confirmed_count
            }

        print(f" Processing {len(session_folders)} sessions...")
        table_rows = []
        for folder in sorted(session_folders):
            result = process_session(folder)
            if result:
                table_rows.append(result)

        # Ordina per nome sessione
        table_rows.sort(key=lambda x: x['session'])

        if not table_rows:
            return jsonify({'error': 'No data found for this directory. Run Analyze first.'}), 400

        latex = generate_springer_latex_table(table_rows, threshold)

        return jsonify({
            'success': True,
            'latex': latex,
            'sessions_count': len(table_rows),
            'total_pairs_analyzed': sum(r['students'] for r in table_rows),
            'debug': {
                'sessions_detected': len(session_folders),
                'root': str(root_path),
                'rows': table_rows
            }
        })

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


def generate_springer_latex_table(rows: list, threshold: float) -> str:
    """Generate a LaTeX table compatible with Springer two-column layout."""

    threshold_pct = int(threshold * 100)

    lines = [
        r"% Auto-generated LaTeX table for Springer two-column layout",
        r"% Requires packages: booktabs, array, rotating (for vertical headers)",
        r"",
        r"\begin{table}[htbp]",
        r"    \centering",
        r"    \setlength{\tabcolsep}{4pt}",
        r"    \caption{CAD exam similarity analysis results per session}",
        r"    \label{tab:cad_similarity_results}",
        r"    \footnotesize",
        r"    \begin{tabular}{l c c c c c c c c}",
        r"        \toprule",
        r"        \textbf{Session} &",
        r"        \rotatebox{90}{\textbf{Students}} &",
        r"        \rotatebox{90}{\textbf{Tracks}} &",
        r"        \rotatebox{90}{\textbf{Avg. Sim. (\%)}} &",
        r"        \rotatebox{90}{\textbf{Max Sim. (\%)}} &",
        r"        \rotatebox{90}{\textbf{Min Sim. (\%)}} &",
        r"        \rotatebox{90}{\textbf{Std. Dev. (\%)}} &",
        r"        \rotatebox{90}{\textbf{Suspected}} &",
        r"        \rotatebox{90}{\textbf{Confirmed}} \\",
        r"        \midrule",
    ]

    for row in rows:
        line = (
            f"        {row['session']} & "
            f"{row['students']} & "
            f"{row['tracks']} & "
            f"{row['avg']:.1f} & "
            f"{row['max']:.1f} & "
            f"{row['min']:.1f} & "
            f"{row['std']:.1f} & "
            f"{row['suspected']} & "
            f"{row['confirmed']} \\\\"
        )
        lines.append(line)

    lines.extend([
        r"        \bottomrule",
        r"    \end{tabular}",
        r"    \\[4pt]",
        f"    \\parbox{{\\columnwidth}}{{\\scriptsize\\textit{{Note: Suspected = pairs with similarity $\\geq$ {threshold_pct}\\%. Confirmed = manually labeled plagiarism cases.}}}}",
        r"\end{table}",
    ])

    return '\n'.join(lines)


# NOTE: set_pair_label and get_pair_labels routes are defined earlier (api_paper_writing_set_pair_label / api_paper_writing_get_pair_labels)


@app.route('/api/paper_writing/debug_session', methods=['GET'])
def api_debug_paper_writing_session():
    """Debug endpoint: return session pairs with suspected flag and matched label (if any).
    Query params:
      - root: root dataset folder (required)
      - session: session folder name (required)
      - threshold: optional float 0..1
    """
    try:
        root = request.args.get('root', '')
        session_name = request.args.get('session', '')
        default_threshold = get_default_similarity_threshold()
        threshold = max(0.5, min(0.99, _safe_float(request.args.get('threshold', default_threshold), default_threshold)))

        if not root or not Path(root).exists():
            return jsonify({'error': 'root path missing or not found'}), 400
        if not session_name:
            return jsonify({'error': 'session parameter is required'}), 400

        # Load analysis results from root or in-memory
        root_analysis_data = load_analysis_results(str(root))
        all_pairs = []
        if root_analysis_data and 'similar_pairs' in root_analysis_data:
            all_pairs = root_analysis_data['similar_pairs']
        elif analysis_results.get('similar_pairs'):
            all_pairs = analysis_results['similar_pairs']
        else:
            return jsonify({'error': 'No analysis pairs available. Run Analyze or load results first.'}), 400

        # Load labels DB
        labels_db = load_labels_db(DEFAULT_LABELS_DB_PATH) if HAS_PLAGIARISM_LABELS else {}

        # Helpers (same normalization used in export)
        def _normalize_session_name(s: str) -> str:
            if not s:
                return ''
            ns = s.replace('_', ' ').strip()
            ns = ' '.join(ns.split())
            return ns.upper()

        def _normalize_filename(fn: str) -> str:
            if not fn:
                return ''
            s = fn.strip()
            try:
                s = Path(s).name
            except Exception:
                pass
            s = s.replace('_', ' ')
            s = ' '.join(s.split())
            s = s.lower()
            import re
            s = re.sub(r"[^a-z0-9. \-]", '', s)
            return s

        norm_session = _normalize_session_name(session_name)

        # Collect session pairs first (to use file names for label matching)
        session_pairs = []
        session_folder = Path(root) / session_name
        par_files = collect_session_files(session_folder) if session_folder.exists() else []
        session_file_names = set(f.name for f in par_files)
        session_file_names_norm = set(_normalize_filename(f.name) for f in par_files)

        for pair in all_pairs:
            file1 = pair.get('file1', '')
            file2 = pair.get('file2', '')
            path1 = pair.get('path1', '')
            path2 = pair.get('path2', '')
            file1_in_session = file1 in session_file_names or session_name in (path1 or '')
            file2_in_session = file2 in session_file_names or session_name in (path2 or '')
            if not (file1_in_session and file2_in_session):
                continue
            sim = float(pair.get('similarity', 0.0))
            n1 = _normalize_filename(file1)
            n2 = _normalize_filename(file2)
            key = f"{sorted([n1, n2])[0]}|{sorted([n1, n2])[1]}"
            suspected = sim >= threshold
            session_pairs.append({
                'file1': file1,
                'file2': file2,
                'path1': path1,
                'path2': path2,
                'similarity': sim,
                'suspected': suspected,
                'label': None,  # filled below
                'normalized_key': key
            })

        # Build labels_map using BOTH session-name match AND file-based match
        session_pair_files_norm = set()
        for p in session_pairs:
            session_pair_files_norm.add(_normalize_filename(p['file1']))
            session_pair_files_norm.add(_normalize_filename(p['file2']))

        labels_map = {}
        for entry in labels_db.values():
            fa = entry.get('file_a', '')
            fb = entry.get('file_b', '')
            if not fa or not fb:
                continue
            nfa = _normalize_filename(fa)
            nfb = _normalize_filename(fb)

            entry_session = (entry.get('session') or '').strip()
            entry_session_norm = _normalize_session_name(entry_session)
            session_match = (
                entry_session_norm == norm_session or
                norm_session in entry_session_norm or
                entry_session_norm in norm_session
            )
            file_match = (
                nfa in session_pair_files_norm and
                nfb in session_pair_files_norm
            )
            if not (session_match or file_match):
                continue
            key = f"{sorted([nfa, nfb])[0]}|{sorted([nfa, nfb])[1]}"
            labels_map[key] = entry.get('label')

        # Assign labels to session pairs
        for p in session_pairs:
            p['label'] = labels_map.get(p['normalized_key'])

        # Summary
        total_pairs = len(session_pairs)
        suspected_count = sum(1 for p in session_pairs if p['suspected'])
        confirmed_count = sum(1 for p in session_pairs if p['label'] == 'CONFIRMED_PLAGIARISM')

        return jsonify({
            'success': True,
            'session': session_name,
            'total_pairs': total_pairs,
            'suspected_count': suspected_count,
            'confirmed_count': confirmed_count,
            'labels_map_size': len(labels_map),
            'labels_sample_keys': list(labels_map.keys())[:20],
            'pairs': session_pairs
        })

    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/paper_writing/debug_matches', methods=['POST'])
def api_paper_writing_debug_matches():
    """Debug: per una root (session) ritorna le coppie che sono matchate come CONFIRMED.
    Body JSON:
      - root: percorso della cartella session o root
    """
    try:
        data = request.json or {}
        root_path = Path(data.get('root', ''))
        if not root_path or not root_path.exists():
            return jsonify({'success': False, 'error': 'Root path not found or not provided'}), 400

        # carica labels
        labels_db = load_labels_db(DEFAULT_LABELS_DB_PATH) if HAS_PLAGIARISM_LABELS else {}

        # determina session name
        session_name = root_path.name

        # carica pairs: prefer in-memory se corrispondente, altrimenti dalla cache
        pairs = []
        current_analyzed = analysis_results.get('directory', '')
        if current_analyzed and Path(current_analyzed).resolve() == root_path.resolve() and analysis_results.get('similar_pairs'):
            pairs = analysis_results['similar_pairs']
        else:
            cached = load_analysis_results(str(root_path))
            if cached and 'similar_pairs' in cached:
                pairs = cached['similar_pairs']

        if not pairs:
            return jsonify({'success': True, 'matches': [], 'message': 'No pairs available for this root', 'labels_count': len(labels_db)})

        # Build a lookup for labels limited to the session_name (exact match)
        labels_lookup = {}
        for key, entry in labels_db.items():
            entry_sess = entry.get('session', '') or ''
            if entry_sess == session_name:
                fa = Path(entry.get('file_a','') or '').name
                fb = Path(entry.get('file_b','') or '').name
                labels_lookup[normalize_pair_key(entry_sess, fa, fb)] = {
                    'key': key,
                    'label': entry.get('label', ''),
                    'file_a': fa,
                    'file_b': fb,
                    'session': entry_sess,
                    'notes': entry.get('notes', '')
                }

        matches = []
        confirmed_count = 0
        for p in pairs:
            f1 = Path(p.get('file1','') or '').name
            f2 = Path(p.get('file2','') or '').name
            if not f1 or not f2:
                continue
            key = normalize_pair_key(session_name, f1, f2)
            entry = labels_lookup.get(key)
            if entry:
                matches.append({
                    'file1': f1,
                    'file2': f2,
                    'label': entry['label'],
                    'db_key': entry['key'],
                    'db_session': entry['session'],
                    'notes': entry['notes']
                })
                if entry['label'] == 'CONFIRMED_PLAGIARISM':
                    confirmed_count += 1

        return jsonify({'success': True, 'root': str(root_path), 'pairs_count': len(pairs), 'labels_count': len(labels_db), 'confirmed_count': confirmed_count, 'matches': matches})
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/paper_writing/labels_detail')
def api_paper_writing_labels_detail():
    """Restituisce statistiche dettagliate sulle etichette di plagio per session.

    Usato dal frontend per mostrare Label Statistics modal.
    """
    try:
        labels_db = load_labels_db(DEFAULT_LABELS_DB_PATH) if HAS_PLAGIARISM_LABELS else {}

        # Raggruppa per session
        sessions_data = {}
        total_count = 0

        for key, entry in labels_db.items():
            session_name = entry.get('session', 'unknown')
            label = entry.get('label', '')

            if session_name not in sessions_data:
                sessions_data[session_name] = {
                    'session': session_name,
                    'total': 0,
                    'confirmed_count': 0,
                    'not_plagiarism_count': 0,
                    'undecided_count': 0,
                    'confirmed': [],
                    'not_plagiarism': [],
                    'undecided': []
                }

            sessions_data[session_name]['total'] += 1
            total_count += 1

            entry_short = {
                'file_a': entry.get('file_a', ''),
                'file_b': entry.get('file_b', ''),
                'notes': entry.get('notes', '')
            }

            if label == 'CONFIRMED_PLAGIARISM':
                sessions_data[session_name]['confirmed_count'] += 1
                sessions_data[session_name]['confirmed'].append(entry_short)
            elif label == 'NOT_PLAGIARISM':
                sessions_data[session_name]['not_plagiarism_count'] += 1
                sessions_data[session_name]['not_plagiarism'].append(entry_short)
            elif label == 'UNDECIDED':
                sessions_data[session_name]['undecided_count'] += 1
                sessions_data[session_name]['undecided'].append(entry_short)

        return jsonify({
            'success': True,
            'total': total_count,
            'sessions': list(sessions_data.values())
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/paper_writing/weights_optimization_dataset', methods=['GET'])
def api_paper_writing_weights_optimization_dataset():
    """Returns session-aware training coverage for iterative weight optimization."""
    try:
        scope = (request.args.get('scope', 'all') or 'all').strip().lower()
        if scope not in ('all', 'current'):
            scope = 'all'
        selected_sessions_mode = (request.args.get('selected_sessions_mode', 'all') or 'all').strip().lower()
        if selected_sessions_mode not in ('all', 'custom'):
            selected_sessions_mode = 'all'
        requested_selected_sessions = _normalize_optimizer_session_filters(
            (request.args.getlist('selected_session') or []) + [request.args.get('selected_sessions', '')]
        )
        requested_current_session = request.args.get('current_session', '') or ''
        current_session, scope_session_unresolved = _resolve_optimizer_scope_session(
            scope=scope,
            requested_current_session=requested_current_session,
        )
        # Per requisito metodologico: author_match non entra nell'ottimizzazione.
        ignore_author = True
        optimizer_policy = get_optimizer_training_policy()
        strict_default = bool(optimizer_policy.get('strict_path_labeled_pairs_only', True))
        strict_path_labeled_pairs_only = _safe_bool(
            request.args.get('strict_path_labeled_pairs_only', strict_default),
            strict_default,
        )
        exclusion_policy = get_criteria_exclusion_policy()

        labels_db = load_labels_db(DEFAULT_LABELS_DB_PATH) if HAS_PLAGIARISM_LABELS else {}
        analysis_root = None
        if analysis_results.get('directory'):
            try:
                analysis_root = Path(analysis_results.get('directory', ''))
            except Exception:
                analysis_root = None
        migration_stats = migrate_legacy_labels_with_paths(
            labels_db=labels_db,
            pairs=analysis_results.get('similar_pairs', []) or [],
            analysis_root=analysis_root,
        )
        if int(migration_stats.get('migrated', 0) or 0) > 0:
            migration_stats['saved'] = bool(save_labels_db(DEFAULT_LABELS_DB_PATH, labels_db))
        if scope == 'current' and scope_session_unresolved:
            _, summary = _build_optimizer_training_samples(
                labels_db=labels_db,
                scope='all',
                current_session='',
                selected_sessions=requested_selected_sessions,
                selected_sessions_mode=selected_sessions_mode,
                ignore_author=ignore_author,
                exclusion_policy=exclusion_policy,
                strict_path_labeled_pairs_only=strict_path_labeled_pairs_only,
            )
            summary['migration'] = migration_stats
            summary = _annotate_optimizer_summary_scope(
                summary=summary,
                scope=scope,
                requested_current_session=requested_current_session,
                resolved_current_session='',
                scope_session_unresolved=True,
                selected_sessions_requested=requested_selected_sessions,
                selected_sessions_effective=summary.get('selected_sessions_effective', []),
                selected_sessions_mode=summary.get('selected_sessions_mode', selected_sessions_mode),
            )
            return jsonify({
                'success': True,
                'ready': False,
                'has_scipy': HAS_SCIPY,
                'ignore_author': True,
                'ignore_author_forced': True,
                'summary': summary,
                'recommended_minimum': {
                    'positive': 2,
                    'negative': 2,
                },
                'message': 'Scope "Current session only" requires a specific session. The analyzed root contains multiple sessions: provide the exact session name or use "All labeled sessions".',
                'samples_preview': [],
            })

        samples, summary = _build_optimizer_training_samples(
            labels_db=labels_db,
            scope=scope,
            current_session=current_session,
            selected_sessions=requested_selected_sessions,
            selected_sessions_mode=selected_sessions_mode,
            ignore_author=ignore_author,
            exclusion_policy=exclusion_policy,
            strict_path_labeled_pairs_only=strict_path_labeled_pairs_only,
        )
        summary = _annotate_optimizer_summary_scope(
            summary=summary,
            scope=scope,
            requested_current_session=requested_current_session,
            resolved_current_session=current_session if scope == 'current' else '',
            scope_session_unresolved=False,
            selected_sessions_requested=requested_selected_sessions,
            selected_sessions_effective=summary.get('selected_sessions_effective', []),
            selected_sessions_mode=summary.get('selected_sessions_mode', selected_sessions_mode),
        )
        summary['migration'] = migration_stats

        ready = summary.get('positive', 0) >= 2 and summary.get('negative', 0) >= 2
        message = '' if ready else 'At least 2 labeled positive pairs and 2 labeled negative pairs are required.'
        if summary.get('selected_sessions_mode') == 'custom' and len(summary.get('selected_sessions_effective', [])) == 0:
            ready = False
            message = 'No sessions selected for optimization. Select at least one session.'
        if scope == 'current' and current_session and not _current_session_is_available(summary, current_session):
            ready = False
            summary['current_session_resolved'] = ''
            summary['scope_session_unresolved'] = True
            message = (
                f"Session '{current_session}' is not available in the current analysis. "
                "Provide an available session or use \"All labeled sessions\"."
            )
        return jsonify({
            'success': True,
            'ready': ready,
            'has_scipy': HAS_SCIPY,
            'ignore_author': True,
            'ignore_author_forced': True,
            'summary': summary,
            'recommended_minimum': {
                'positive': 2,
                'negative': 2,
            },
            'message': message,
            'samples_preview': samples[:5],
        })
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/paper_writing/weights_optimization_progress', methods=['GET'])
def api_paper_writing_weights_optimization_progress():
    """Returns live progress for iterative optimizer."""
    try:
        return jsonify({'success': True, 'progress': _get_optimization_progress_snapshot()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/paper_writing/weights_optimization_iterative', methods=['POST'])
def api_paper_writing_weights_optimization_iterative():
    """
    Iterative weight optimization from manual labels.
    Session-aware by design:
    - positives = CONFIRMED_PLAGIARISM labels
    - negatives = NOT_PLAGIARISM labels
    """
    try:
        if not HAS_SCIPY or minimize is None:
            _set_optimization_progress(
                active=False,
                phase='error',
                message='SciPy not available',
                error='SciPy not available: install scipy for iterative optimization.'
            )
            return jsonify({
                'success': False,
                'error': 'SciPy not available: install scipy for iterative optimization.'
            }), 400

        current_progress = _get_optimization_progress_snapshot()
        if bool(current_progress.get('active')):
            return jsonify({
                'success': False,
                'error': 'Optimization already running: wait for completion before starting a new one.',
                'progress': current_progress,
            }), 409

        data = request.json or {}
        scope = (data.get('scope', 'all') or 'all').strip().lower()
        if scope not in ('all', 'current'):
            scope = 'all'
        selected_sessions_mode = (data.get('selected_sessions_mode', 'all') or 'all').strip().lower()
        if selected_sessions_mode not in ('all', 'custom'):
            selected_sessions_mode = 'all'
        requested_selected_sessions = _normalize_optimizer_session_filters(data.get('selected_sessions', []))
        requested_current_session = (data.get('current_session', '') or '').strip()
        current_session, scope_session_unresolved = _resolve_optimizer_scope_session(
            scope=scope,
            requested_current_session=requested_current_session,
        )
        # Per requisito metodologico: author_match e' sempre escluso dal training optimizer.
        ignore_author_requested = data.get('ignore_author', True)
        ignore_author = True
        exclusion_policy = get_criteria_exclusion_policy()
        optimizer_policy = get_optimizer_training_policy()
        strict_default = bool(optimizer_policy.get('strict_path_labeled_pairs_only', True))
        strict_path_labeled_pairs_only = _safe_bool(
            data.get('strict_path_labeled_pairs_only', strict_default),
            strict_default,
        )
        default_threshold = get_default_similarity_threshold()
        threshold = max(0.5, min(0.99, _safe_float(data.get('threshold', default_threshold), default_threshold)))
        maxiter = int(max(5, min(500, int(_safe_float(data.get('maxiter', 120), 120)))))
        optimizer_seed_raw = data.get('optimizer_seed', None)
        if optimizer_seed_raw in (None, '', 'auto'):
            # Default: vary seed across runs to avoid always exploring the same valley.
            optimizer_seed = int(time.time_ns() % 2_147_483_647)
            optimizer_seed_source = 'time'
        else:
            optimizer_seed = int(max(1, min(2_147_483_647, int(_safe_float(optimizer_seed_raw, 42)))))
            optimizer_seed_source = 'request'
        hybrid_restarts = int(max(1, min(6, int(_safe_float(data.get('hybrid_restarts', 3), 3)))))
        genetic_restarts = int(max(1, min(8, int(_safe_float(data.get('genetic_restarts', 3), 3)))))
        lbfgsb_restarts = int(max(1, min(6, int(_safe_float(data.get('lbfgsb_restarts', 2), 2)))))
        save_global_weights = bool(data.get('save_global_weights', False))
        start_from_ui = bool(data.get('start_from_ui', False))
        start_weights_payload = data.get('start_weights', {}) if start_from_ui else {}
        optimizer_method = str(data.get('optimizer_method', 'hybrid') or 'hybrid').strip().lower()
        if optimizer_method not in ('hybrid', 'genetic', 'lbfgsb'):
            optimizer_method = 'hybrid'
        balance_profile = str(data.get('balance_profile', 'moderate') or 'moderate').strip().lower()
        if balance_profile not in ('strict', 'moderate', 'loose'):
            balance_profile = 'moderate'

        profile_params = {
            'strict': {
                'entropy_target': 0.93,
                'max_cap': 0.22,
                'min_active_weight': 0.03,
                'min_active_count': 8,
                'author_cap': 0.04,
                'reg_l2': 0.16,
                'reg_entropy': 0.65,
                'reg_dominant': 0.90,
                'reg_active': 0.70,
                'reg_author': 2.00,
                'pos_push_margin': 0.05,
                'neg_push_margin': 0.05,
                'non_author_min_weight': 0.025,
                'preserve_start_ratio': 0.55,
                'reg_preserve_start': 1.00,
                'reg_non_author_floor': 1.20,
                'reg_hard': 0.55,
                'reg_violation_neg': 1.80,
                'reg_violation_pos': 1.30,
                'corr_threshold': 0.82,
                'reg_corr_redundancy': 0.0,
                'target_gap': 0.03,
            },
            'moderate': {
                'entropy_target': 0.89,
                'max_cap': 0.28,
                'min_active_weight': 0.03,
                'min_active_count': 7,
                'author_cap': 0.06,
                'reg_l2': 0.10,
                'reg_entropy': 0.30,
                'reg_dominant': 0.40,
                'reg_active': 0.25,
                'reg_author': 1.20,
                'pos_push_margin': 0.04,
                'neg_push_margin': 0.04,
                'non_author_min_weight': 0.018,
                'preserve_start_ratio': 0.45,
                'reg_preserve_start': 0.70,
                'reg_non_author_floor': 0.75,
                'reg_hard': 0.35,
                'reg_violation_neg': 1.20,
                'reg_violation_pos': 0.90,
                'corr_threshold': 0.85,
                'reg_corr_redundancy': 0.0,
                'target_gap': 0.03,
            },
            'loose': {
                'entropy_target': 0.84,
                'max_cap': 0.35,
                'min_active_weight': 0.02,
                'min_active_count': 5,
                'author_cap': 0.10,
                'reg_l2': 0.05,
                'reg_entropy': 0.12,
                'reg_dominant': 0.18,
                'reg_active': 0.08,
                'reg_author': 0.35,
                'pos_push_margin': 0.03,
                'neg_push_margin': 0.03,
                'non_author_min_weight': 0.012,
                'preserve_start_ratio': 0.30,
                'reg_preserve_start': 0.35,
                'reg_non_author_floor': 0.30,
                'reg_hard': 0.20,
                'reg_violation_neg': 0.60,
                'reg_violation_pos': 0.45,
                'corr_threshold': 0.88,
                'reg_corr_redundancy': 0.0,
                'target_gap': 0.03,
            },
        }
        opt_cfg = dict(profile_params[balance_profile])
        opt_cfg['pos_push_margin'] = max(0.0, min(0.20, _safe_float(data.get('pos_push_margin', opt_cfg['pos_push_margin']), opt_cfg['pos_push_margin'])))
        opt_cfg['neg_push_margin'] = max(0.0, min(0.20, _safe_float(data.get('neg_push_margin', opt_cfg['neg_push_margin']), opt_cfg['neg_push_margin'])))
        opt_cfg['target_gap'] = max(0.0, min(0.20, _safe_float(data.get('target_gap', opt_cfg['target_gap']), opt_cfg['target_gap'])))
        if ignore_author:
            opt_cfg['author_cap'] = 0.0

        _set_optimization_progress(
            active=True,
            phase='preparing',
            message='Preparing iterative optimization',
            started_at=time.time(),
            config={
                'scope': scope,
                'current_session': current_session,
                'selected_sessions_mode': selected_sessions_mode,
                'selected_sessions_requested': requested_selected_sessions,
                'ignore_author': ignore_author,
                'ignore_author_requested': ignore_author_requested,
                'ignore_author_forced': True,
                'criteria_exclusion_policy': exclusion_policy,
                'strict_path_labeled_pairs_only': strict_path_labeled_pairs_only,
                'threshold': threshold,
                'optimizer_method': optimizer_method,
                'balance_profile': balance_profile,
                'pos_push_margin': opt_cfg['pos_push_margin'],
                'neg_push_margin': opt_cfg['neg_push_margin'],
                'target_gap': opt_cfg['target_gap'],
                'maxiter': maxiter,
                'optimizer_seed': optimizer_seed,
                'optimizer_seed_source': optimizer_seed_source,
                'hybrid_restarts': hybrid_restarts,
                'genetic_restarts': genetic_restarts,
                'lbfgsb_restarts': lbfgsb_restarts,
                'save_global_weights': save_global_weights
            },
            dataset={},
            solver={'iteration': 0, 'maxiter': maxiter},
            error=None
        )

        _set_optimization_progress(phase='loading_labels', message='Loading manual labels')
        labels_db = load_labels_db(DEFAULT_LABELS_DB_PATH) if HAS_PLAGIARISM_LABELS else {}
        analysis_root = None
        if analysis_results.get('directory'):
            try:
                analysis_root = Path(analysis_results.get('directory', ''))
            except Exception:
                analysis_root = None
        migration_stats = migrate_legacy_labels_with_paths(
            labels_db=labels_db,
            pairs=analysis_results.get('similar_pairs', []) or [],
            analysis_root=analysis_root,
        )
        if int(migration_stats.get('migrated', 0) or 0) > 0:
            migration_stats['saved'] = bool(save_labels_db(DEFAULT_LABELS_DB_PATH, labels_db))
        if scope == 'current' and scope_session_unresolved:
            _, summary = _build_optimizer_training_samples(
                labels_db=labels_db,
                scope='all',
                current_session='',
                selected_sessions=requested_selected_sessions,
                selected_sessions_mode=selected_sessions_mode,
                ignore_author=ignore_author,
                exclusion_policy=exclusion_policy,
                strict_path_labeled_pairs_only=strict_path_labeled_pairs_only,
            )
            summary['migration'] = migration_stats
            summary = _annotate_optimizer_summary_scope(
                summary=summary,
                scope=scope,
                requested_current_session=requested_current_session,
                resolved_current_session='',
                scope_session_unresolved=True,
                selected_sessions_requested=requested_selected_sessions,
                selected_sessions_effective=summary.get('selected_sessions_effective', []),
                selected_sessions_mode=summary.get('selected_sessions_mode', selected_sessions_mode),
            )
            error_message = (
                'Scope "Current session only" requires a specific session. '
                'The analyzed root contains multiple sessions: provide the exact session name '
                'or use "All labeled sessions".'
            )
            _set_optimization_progress(
                active=False,
                phase='error',
                message='Current session unresolved',
                dataset=summary,
                error=error_message
            )
            return jsonify({
                'success': False,
                'error': error_message,
                'summary': summary
            }), 400
        def _on_dataset_progress(stats: dict):
            stage = str(stats.get('stage', '') or 'labels')
            if stage == 'pair_index':
                pairs_processed = int(stats.get('pairs_processed', 0))
                pairs_total = int(stats.get('pairs_total', 0))
                skipped_cross = int(stats.get('skipped_cross_session_pairs', 0))
                progress_msg = (
                    'Building label/session-aware dataset '
                    f'| indexing pairs {pairs_processed}/{pairs_total} '
                    f'(cross-session detected {skipped_cross})'
                )
                _set_optimization_progress(
                    phase='building_dataset',
                    message=progress_msg,
                    dataset={
                        'pairs_processed': pairs_processed,
                        'pairs_total': pairs_total,
                        'skipped_cross_session_pairs': skipped_cross,
                    }
                )
                return

            processed = int(stats.get('labels_processed', 0))
            total = int(stats.get('labels_total', 0))
            from_pairs = int(stats.get('raw_from_pair_index', 0))
            from_cache = int(stats.get('raw_from_optimizer_cache', 0))
            computed = int(stats.get('raw_computed', 0))
            progress_msg = (
                'Building label/session-aware dataset '
                f'({processed}/{total}) | raw: pair-index {from_pairs}, cache {from_cache}, computed {computed}'
            )
            _set_optimization_progress(
                phase='building_dataset',
                message=progress_msg,
                dataset={
                    'labels_processed': processed,
                    'labels_total': total,
                    'raw_from_pair_index': from_pairs,
                    'raw_from_optimizer_cache': from_cache,
                    'raw_computed': computed,
                }
            )

        _set_optimization_progress(phase='building_dataset', message='Building label/session-aware dataset')
        samples, summary = _build_optimizer_training_samples(
            labels_db=labels_db,
            scope=scope,
            current_session=current_session,
            selected_sessions=requested_selected_sessions,
            selected_sessions_mode=selected_sessions_mode,
            ignore_author=ignore_author,
            progress_callback=_on_dataset_progress,
            exclusion_policy=exclusion_policy,
            strict_path_labeled_pairs_only=strict_path_labeled_pairs_only,
        )
        summary = _annotate_optimizer_summary_scope(
            summary=summary,
            scope=scope,
            requested_current_session=requested_current_session,
            resolved_current_session=current_session if scope == 'current' else '',
            scope_session_unresolved=False,
            selected_sessions_requested=requested_selected_sessions,
            selected_sessions_effective=summary.get('selected_sessions_effective', []),
            selected_sessions_mode=summary.get('selected_sessions_mode', selected_sessions_mode),
        )
        summary['migration'] = migration_stats
        if scope == 'current' and current_session and not _current_session_is_available(summary, current_session):
            summary['current_session_resolved'] = ''
            summary['scope_session_unresolved'] = True
            error_message = (
                f"Session '{current_session}' is not available in the current analysis. "
                'Provide an available session or use "All labeled sessions".'
            )
            _set_optimization_progress(
                active=False,
                phase='error',
                message='Invalid current session',
                dataset=summary,
                error=error_message
            )
            return jsonify({
                'success': False,
                'error': error_message,
                'summary': summary
            }), 400

        n_pos = int(summary.get('positive', 0))
        n_neg = int(summary.get('negative', 0))
        if n_pos < 1 or n_neg < 1:
            if summary.get('selected_sessions_mode') == 'custom' and len(summary.get('selected_sessions_effective', [])) == 0:
                err_msg = 'No sessions selected for optimization. Select at least one session.'
            else:
                err_msg = 'At least one positive pair and one negative pair are required.'
            _set_optimization_progress(
                active=False,
                phase='error',
                message='Insufficient labeled dataset',
                dataset=summary,
                error=err_msg
            )
            return jsonify({
                'success': False,
                'error': f'Insufficient labeled dataset: {err_msg}',
                'summary': summary
            }), 400

        corr_redundancy_matrix = np.zeros(
            (len(OPT_NON_AUTHOR_WEIGHT_NAMES), len(OPT_NON_AUTHOR_WEIGHT_NAMES)),
            dtype=float,
        )
        corr_pair_count = 0
        if _safe_float(opt_cfg.get('reg_corr_redundancy', 0.0), 0.0) > 0.0:
            corr_redundancy_matrix, corr_pair_count = _build_non_author_correlation_redundancy(
                samples=samples,
                corr_threshold=opt_cfg['corr_threshold'],
            )
        has_corr_regularizer = bool(
            corr_pair_count > 0 and _safe_float(opt_cfg.get('reg_corr_redundancy', 0.0), 0.0) > 0.0
        )
        pos_target = min(0.995, threshold + opt_cfg['pos_push_margin'])
        neg_target = max(0.0, threshold - opt_cfg['neg_push_margin'])

        base_weights = load_weights()
        start_source = 'global'
        if start_from_ui and isinstance(start_weights_payload, dict):
            for name in OPT_WEIGHT_NAMES:
                if name in start_weights_payload:
                    base_weights[name] = _safe_float(
                        start_weights_payload.get(name, base_weights.get(name, 0.0)),
                        _safe_float(base_weights.get(name, 0.0), 0.0),
                    )
            start_source = 'ui'
        start_vec = _extract_numeric_weight_vector(base_weights)
        if ignore_author and OPT_AUTHOR_INDEX >= 0:
            start_vec = start_vec.copy()
            start_vec[OPT_AUTHOR_INDEX] = 0.0
            start_sum = float(np.sum(start_vec))
            if start_sum > 1e-12:
                start_vec = start_vec / start_sum
            else:
                start_vec = np.ones_like(start_vec, dtype=float)
                start_vec[OPT_AUTHOR_INDEX] = 0.0
                start_vec = start_vec / max(float(np.sum(start_vec)), 1e-12)

        non_author_floor_targets: dict[int, float] = {}
        if OPT_NON_AUTHOR_INDICES:
            preserve_ratio = max(0.0, min(0.95, _safe_float(opt_cfg.get('preserve_start_ratio', 0.0), 0.0)))
            for idx in OPT_NON_AUTHOR_INDICES:
                base_floor = max(0.0, _safe_float(opt_cfg['non_author_min_weight'], 0.0))
                preserve_floor = preserve_ratio * float(start_vec[idx])
                non_author_floor_targets[idx] = max(base_floor, preserve_floor)
            non_author_floor_targets = _build_non_author_floor_targets(
                floor=opt_cfg['non_author_min_weight'],
                non_author_floor_targets=non_author_floor_targets,
            )

        prepared_samples = _prepare_optimizer_sample_arrays(
            samples,
            exclusion_policy=exclusion_policy,
        )
        targets_vec = np.asarray(prepared_samples.get('targets', np.zeros((0,), dtype=np.int8)), dtype=np.int8)
        session_idx_vec = np.asarray(prepared_samples.get('session_index', np.zeros((0,), dtype=np.int32)), dtype=np.int32)
        session_names = list(prepared_samples.get('session_names', []))
        pos_sample_mask = targets_vec == 1
        neg_sample_mask = targets_vec == 0
        sample_count = int(prepared_samples.get('n_samples', len(samples)))
        # Differential Evolution iteration is a generation; each generation evaluates many candidates.
        # Keep population adaptive to avoid very slow "iteration" perception on medium datasets.
        de_popsize = int(max(8, min(16, round(8 + sample_count / 120.0))))
        de_tol = 1e-5
        n_sessions = max(len(session_names), 1)
        session_pos_masks: list[np.ndarray] = []
        session_neg_masks: list[np.ndarray] = []
        for sid in range(len(session_names)):
            sid_mask = session_idx_vec == int(sid)
            sid_pos = sid_mask & pos_sample_mask
            sid_neg = sid_mask & neg_sample_mask
            if np.any(sid_pos):
                session_pos_masks.append(sid_pos)
            if np.any(sid_neg):
                session_neg_masks.append(sid_neg)
        solver_label = {
            'hybrid': 'GENETIC + L-BFGS-B',
            'genetic': 'GENETIC (Differential Evolution)',
            'lbfgsb': 'L-BFGS-B',
        }.get(optimizer_method, 'HYBRID')
        _set_optimization_progress(
            phase='solver_running',
            message=f'Solver in esecuzione ({solver_label})',
            dataset={
                'samples_total': len(samples),
                'positive': n_pos,
                'negative': n_neg,
                'sessions_count': n_sessions,
                'positive_target': pos_target,
                'negative_target': neg_target,
                'corr_regularizer_pairs': corr_pair_count,
                'de_popsize': de_popsize,
            },
            solver={'iteration': 0, 'maxiter': maxiter}
        )

        def objective(x: np.ndarray) -> float:
            w = _project_weight_vector(
                vector=x,
                prior_vector=start_vec,
                ignore_author=ignore_author,
                max_cap=opt_cfg['max_cap'],
                min_active_weight=opt_cfg['min_active_weight'],
                min_active_count=opt_cfg['min_active_count'],
                author_cap=opt_cfg['author_cap'],
                non_author_min_weight=opt_cfg['non_author_min_weight'],
                non_author_floor_targets=non_author_floor_targets,
            )

            sim_all = _weighted_similarity_batch_from_vector(prepared_samples, w)
            pos_arr = sim_all[pos_sample_mask]
            neg_arr = sim_all[neg_sample_mask]

            pos_hinge = np.square(np.maximum(0.0, threshold - sim_all))
            neg_hinge = np.square(np.maximum(0.0, sim_all - threshold))
            pos_push = np.square(np.maximum(0.0, pos_target - sim_all))
            neg_push = np.square(np.maximum(0.0, sim_all - neg_target))

            pos_session_hinge = [float(np.mean(pos_hinge[m])) for m in session_pos_masks]
            neg_session_hinge = [float(np.mean(neg_hinge[m])) for m in session_neg_masks]
            pos_session_push = [float(np.mean(pos_push[m])) for m in session_pos_masks]
            neg_session_push = [float(np.mean(neg_push[m])) for m in session_neg_masks]

            pos_loss = float(np.mean(pos_session_hinge)) if pos_session_hinge else 0.0
            neg_loss = float(np.mean(neg_session_hinge)) if neg_session_hinge else 0.0
            pos_push_loss = float(np.mean(pos_session_push)) if pos_session_push else 0.0
            neg_push_loss = float(np.mean(neg_session_push)) if neg_session_push else 0.0

            pos_target_success = 1.0 - min(1.0, float(np.sqrt(max(pos_push_loss, 0.0))))
            neg_target_success = 1.0 - min(1.0, float(np.sqrt(max(neg_push_loss, 0.0))))

            if pos_arr.size > 0 and neg_arr.size > 0:
                pos_push_arr = np.maximum(0.0, pos_target - pos_arr)
                neg_push_arr = np.maximum(0.0, neg_arr - neg_target)
                pos_violation_rate = float(np.mean(pos_arr < threshold))
                neg_violation_rate = float(np.mean(neg_arr >= threshold))

                def _top_tail_loss(v: np.ndarray, frac: float = 0.15) -> float:
                    if v.size == 0:
                        return 0.0
                    k = max(1, int(np.ceil(v.size * frac)))
                    tail = np.sort(v)[-k:]
                    return float(np.mean(tail ** 2))

                hard_pos_loss = _top_tail_loss(pos_push_arr)
                hard_neg_loss = _top_tail_loss(neg_push_arr)
                hard_loss = 0.42 * hard_pos_loss + 0.58 * hard_neg_loss

                overlap = max(
                    0.0,
                    opt_cfg['target_gap'] - (
                        float(np.quantile(pos_arr, 0.10)) - float(np.quantile(neg_arr, 0.90))
                    )
                ) ** 2
                separation = float(np.mean(pos_arr) - np.mean(neg_arr))
                margin = float(np.min(pos_arr) - np.max(neg_arr))
            else:
                hard_loss = 0.0
                overlap = 0.0
                separation = 0.0
                margin = 0.0
                pos_violation_rate = 0.0
                neg_violation_rate = 0.0

            pos_thr_success = 1.0 - pos_violation_rate
            neg_thr_success = 1.0 - neg_violation_rate
            separation_util = 0.5 + 0.5 * float(np.tanh(separation / 0.15))
            margin_util = 0.5 + 0.5 * float(np.tanh(margin / 0.15))

            prior_uniform = np.ones_like(start_vec, dtype=float) / max(len(start_vec), 1)
            prior_mix = 0.75 * start_vec + 0.25 * prior_uniform
            prior_l2 = float(np.mean((w - prior_mix) ** 2))
            eps = 1e-12
            entropy = float(-np.sum(w * np.log(w + eps)) / np.log(max(len(w), 2)))
            entropy_penalty = max(0.0, opt_cfg['entropy_target'] - entropy) ** 2
            dominant_penalty = max(0.0, float(np.max(w)) - opt_cfg['max_cap']) ** 2
            active_count = int(np.sum(w >= opt_cfg['min_active_weight']))
            active_penalty = (
                max(0, int(opt_cfg['min_active_count']) - active_count) / max(int(opt_cfg['min_active_count']), 1)
            ) ** 2

            non_author_floor_penalty = 0.0
            preserve_start_penalty = 0.0
            corr_penalty = 0.0
            if OPT_NON_AUTHOR_INDICES:
                non_author_w = w[OPT_NON_AUTHOR_INDICES]
                target_floor = np.array(
                    [float(non_author_floor_targets.get(i, opt_cfg['non_author_min_weight'])) for i in OPT_NON_AUTHOR_INDICES],
                    dtype=float,
                )
                non_author_deficit = np.maximum(0.0, target_floor - non_author_w)
                non_author_floor_penalty = float(np.mean(non_author_deficit ** 2))
                preserve_delta = np.maximum(0.0, start_vec[OPT_NON_AUTHOR_INDICES] - non_author_w)
                preserve_start_penalty = float(np.mean(preserve_delta ** 2))
                if has_corr_regularizer:
                    corr_penalty = float(non_author_w @ corr_redundancy_matrix @ non_author_w)

            author_penalty = 0.0
            if OPT_AUTHOR_INDEX >= 0:
                author_penalty = max(0.0, float(w[OPT_AUTHOR_INDEX]) - float(opt_cfg['author_cap'])) ** 2

            # Session-aware balancing: if many sessions, discourage collapse on a subset.
            expected_sessions = float(n_sessions)
            covered_sessions = float(len(session_pos_masks) + len(session_neg_masks)) / 2.0
            session_coverage_penalty = max(0.0, expected_sessions - covered_sessions) / expected_sessions

            context_utility = (
                0.34 * neg_thr_success
                + 0.28 * pos_thr_success
                + 0.16 * neg_target_success
                + 0.10 * pos_target_success
                + 0.07 * separation_util
                + 0.05 * margin_util
            )

            fit_penalty = (
                0.34 * pos_loss
                + 0.46 * neg_loss
                + 0.10 * pos_push_loss
                + 0.10 * neg_push_loss
                + opt_cfg['reg_hard'] * hard_loss
                + 0.70 * overlap
                + opt_cfg['reg_violation_neg'] * neg_violation_rate
                + opt_cfg['reg_violation_pos'] * pos_violation_rate
            )

            regularization_penalty = (
                opt_cfg['reg_l2'] * prior_l2
                + opt_cfg['reg_entropy'] * entropy_penalty
                + opt_cfg['reg_dominant'] * dominant_penalty
                + opt_cfg['reg_active'] * active_penalty
                + opt_cfg['reg_non_author_floor'] * non_author_floor_penalty
                + opt_cfg['reg_preserve_start'] * preserve_start_penalty
                + opt_cfg['reg_corr_redundancy'] * corr_penalty
                + opt_cfg['reg_author'] * author_penalty
                + 0.10 * session_coverage_penalty
            )

            # SciPy minimizza: usiamo il negativo della utility contestualizzata.
            utility = context_utility - fit_penalty - regularization_penalty
            return float(-utility)

        iter_counter = {'value': 0}
        solver_runs: dict[str, dict] = {}
        bounds = []
        for idx, _ in enumerate(OPT_WEIGHT_NAMES):
            if ignore_author and idx == OPT_AUTHOR_INDEX:
                bounds.append((0.0, 0.0))
            else:
                bounds.append((0.0, 1.0))

        def _emit_solver_progress(stage: str, it: int):
            if it == 1 or it % 5 == 0:
                _set_optimization_progress(
                    phase='solver_running',
                    message=f'Solver {stage}: iterazione {it}/{maxiter}',
                    solver={'iteration': it, 'maxiter': maxiter, 'stage': stage}
                )

        def _lbfgs_callback(_xk):
            iter_counter['value'] += 1
            _emit_solver_progress('L-BFGS-B', int(iter_counter['value']))
            return False

        def _de_callback(_xk, _convergence=None):
            iter_counter['value'] += 1
            _emit_solver_progress('GENETIC', int(iter_counter['value']))
            return False

        rng_solver = np.random.default_rng(int(optimizer_seed))

        def _result_objective(res: Any) -> float:
            try:
                val = float(getattr(res, 'fun', np.inf))
            except Exception:
                val = np.inf
            if not np.isfinite(val):
                return np.inf
            return float(val)

        def _result_success(res: Any) -> bool:
            return bool(getattr(res, 'success', False))

        def _result_valid(res: Any) -> bool:
            if res is None:
                return False
            if not hasattr(res, 'x'):
                return False
            return np.isfinite(_result_objective(res))

        def _result_sort_key(res: Any) -> tuple[int, int, float]:
            # Prefer finite + converged runs; among them choose lower objective.
            is_valid = _result_valid(res)
            is_success = _result_success(res)
            return (
                0 if is_valid else 2,
                0 if is_success else 1,
                _result_objective(res),
            )

        def _result_status_int(res: Any) -> int:
            try:
                return int(getattr(res, 'status', -1))
            except Exception:
                return -1

        def _result_message_str(res: Any) -> str:
            try:
                return str(getattr(res, 'message', '') or '')
            except Exception:
                return ''

        def _pick_best(results_list: list[Any]) -> Any | None:
            candidates = [r for r in (results_list or []) if r is not None]
            if not candidates:
                return None
            candidates.sort(key=_result_sort_key)
            return candidates[0]

        def _project_for_start(vector: np.ndarray) -> np.ndarray:
            return _project_weight_vector(
                vector=np.array(vector, dtype=float),
                prior_vector=start_vec,
                ignore_author=ignore_author,
                max_cap=opt_cfg['max_cap'],
                min_active_weight=opt_cfg['min_active_weight'],
                min_active_count=opt_cfg['min_active_count'],
                author_cap=opt_cfg['author_cap'],
                non_author_min_weight=opt_cfg['non_author_min_weight'],
                non_author_floor_targets=non_author_floor_targets,
            )

        def _run_lbfgsb(x0: np.ndarray, local_maxiter: int) -> Any:
            return minimize(
                objective,
                np.array(x0, dtype=float),
                method='L-BFGS-B',
                bounds=bounds,
                options={
                    'maxiter': int(max(5, local_maxiter)),
                    'disp': False,
                    # More robust line-search budget for non-smooth objective landscapes.
                    'maxls': 80,
                },
                callback=_lbfgs_callback
            )

        result = None
        if optimizer_method == 'lbfgsb':
            lbfgs_results: list[Any] = []
            lbfgs_starts: list[np.ndarray] = [start_vec.copy()]
            for _ in range(max(0, lbfgsb_restarts - 1)):
                alpha = float(rng_solver.uniform(0.25, 0.85))
                rand_vec = rng_solver.dirichlet(np.ones(len(start_vec), dtype=float))
                mixed = alpha * start_vec + (1.0 - alpha) * rand_vec
                lbfgs_starts.append(_project_for_start(mixed))

            for start in lbfgs_starts[:lbfgsb_restarts]:
                lbfgs_results.append(_run_lbfgsb(start, maxiter))

            result = _pick_best(lbfgs_results)
            solver_runs['lbfgsb'] = {
                'success': bool(_result_success(result)),
                'iterations': int(getattr(result, 'nit', 0)) if result is not None else 0,
                'objective': float(_result_objective(result)),
                'status': _result_status_int(result),
                'message': _result_message_str(result),
                'restarts': len(lbfgs_results),
            }
            solver_runs['lbfgsb_restarts'] = [
                {
                    'run': int(idx + 1),
                    'success': bool(_result_success(res)),
                    'iterations': int(getattr(res, 'nit', 0)),
                    'objective': float(_result_objective(res)),
                    'status': _result_status_int(res),
                    'message': _result_message_str(res),
                }
                for idx, res in enumerate(lbfgs_results)
            ]
        elif optimizer_method == 'genetic':
            if differential_evolution is None:
                raise RuntimeError('Differential Evolution non disponibile: aggiorna scipy.')
            de_results: list[Any] = []
            for ridx in range(max(1, genetic_restarts)):
                de_seed = int((int(optimizer_seed) + ridx * 104_729) % 2_147_483_647) or 42
                de_results.append(
                    differential_evolution(
                        objective,
                        bounds=bounds,
                        seed=de_seed,
                        maxiter=maxiter,
                        popsize=de_popsize,
                        tol=de_tol,
                        mutation=(0.5, 1.4),
                        recombination=0.85,
                        polish=False,
                        disp=False,
                        callback=_de_callback,
                    )
                )
            result = _pick_best(de_results)
            solver_runs['genetic'] = {
                'success': bool(_result_success(result)),
                'iterations': int(getattr(result, 'nit', 0)) if result is not None else 0,
                'objective': float(_result_objective(result)),
                'status': _result_status_int(result),
                'message': _result_message_str(result),
                'restarts': len(de_results),
            }
            solver_runs['genetic_restarts'] = [
                {
                    'run': int(idx + 1),
                    'seed': int((int(optimizer_seed) + idx * 104_729) % 2_147_483_647) or 42,
                    'success': bool(_result_success(res)),
                    'iterations': int(getattr(res, 'nit', 0)),
                    'objective': float(_result_objective(res)),
                    'status': _result_status_int(res),
                    'message': _result_message_str(res),
                }
                for idx, res in enumerate(de_results)
            ]
        else:  # hybrid (default): global exploration + local refinement
            if differential_evolution is None:
                raise RuntimeError('Differential Evolution non disponibile: aggiorna scipy.')

            de_restart_count = max(1, hybrid_restarts)
            de_total_budget = max(8, int(round(maxiter * 0.55)))
            de_maxiter = max(4, int(np.ceil(float(de_total_budget) / float(de_restart_count))))
            lbfgs_maxiter = max(20, maxiter - de_total_budget)

            de_results: list[Any] = []
            for ridx in range(de_restart_count):
                de_seed = int((int(optimizer_seed) + ridx * 104_729) % 2_147_483_647) or 42
                de_results.append(
                    differential_evolution(
                        objective,
                        bounds=bounds,
                        seed=de_seed,
                        maxiter=de_maxiter,
                        popsize=de_popsize,
                        tol=de_tol,
                        mutation=(0.5, 1.4),
                        recombination=0.85,
                        polish=False,
                        disp=False,
                        callback=_de_callback,
                    )
                )
            de_result = _pick_best(de_results)
            solver_runs['genetic'] = {
                'success': bool(_result_success(de_result)),
                'iterations': int(getattr(de_result, 'nit', 0)) if de_result is not None else 0,
                'objective': float(_result_objective(de_result)),
                'status': _result_status_int(de_result),
                'message': _result_message_str(de_result),
                'restarts': len(de_results),
            }
            solver_runs['genetic_restarts'] = [
                {
                    'run': int(idx + 1),
                    'seed': int((int(optimizer_seed) + idx * 104_729) % 2_147_483_647) or 42,
                    'success': bool(_result_success(res)),
                    'iterations': int(getattr(res, 'nit', 0)),
                    'objective': float(_result_objective(res)),
                    'status': _result_status_int(res),
                    'message': _result_message_str(res),
                }
                for idx, res in enumerate(de_results)
            ]

            lbfgs_starts: list[np.ndarray] = []
            de_sorted = sorted([r for r in de_results if r is not None], key=_result_sort_key)
            for de_res in de_sorted:
                lbfgs_starts.append(_project_for_start(np.array(getattr(de_res, 'x', start_vec), dtype=float)))
                if len(lbfgs_starts) >= lbfgsb_restarts:
                    break
            if not lbfgs_starts:
                lbfgs_starts.append(start_vec.copy())
            if len(lbfgs_starts) < lbfgsb_restarts:
                lbfgs_starts.append(start_vec.copy())
            while len(lbfgs_starts) < lbfgsb_restarts:
                base = lbfgs_starts[0]
                alpha = float(rng_solver.uniform(0.20, 0.80))
                rand_vec = rng_solver.dirichlet(np.ones(len(start_vec), dtype=float))
                mixed = _project_for_start(alpha * base + (1.0 - alpha) * rand_vec)
                lbfgs_starts.append(mixed)

            lbfgs_results: list[Any] = []
            for start in lbfgs_starts[:lbfgsb_restarts]:
                lbfgs_results.append(_run_lbfgsb(start, lbfgs_maxiter))
            lbfgs_result = _pick_best(lbfgs_results)
            solver_runs['lbfgsb'] = {
                'success': bool(_result_success(lbfgs_result)),
                'iterations': int(getattr(lbfgs_result, 'nit', 0)) if lbfgs_result is not None else 0,
                'objective': float(_result_objective(lbfgs_result)),
                'status': _result_status_int(lbfgs_result),
                'message': _result_message_str(lbfgs_result),
                'restarts': len(lbfgs_results),
            }
            solver_runs['lbfgsb_restarts'] = [
                {
                    'run': int(idx + 1),
                    'success': bool(_result_success(res)),
                    'iterations': int(getattr(res, 'nit', 0)),
                    'objective': float(_result_objective(res)),
                    'status': _result_status_int(res),
                    'message': _result_message_str(res),
                }
                for idx, res in enumerate(lbfgs_results)
            ]

            # Keep the best finite/converged solution found across global and local stages.
            result = _pick_best([de_result, lbfgs_result])
            if result is None:
                # Fallback: at least return one candidate even if all failed validity checks.
                result = lbfgs_result or de_result

        if result is None:
            raise RuntimeError('Solver did not produce a result.')

        _set_optimization_progress(phase='evaluating', message='Evaluating optimized weights')
        optimal_vec = _project_weight_vector(
            vector=result.x if result is not None else start_vec,
            prior_vector=start_vec,
            ignore_author=ignore_author,
            max_cap=opt_cfg['max_cap'],
            min_active_weight=opt_cfg['min_active_weight'],
            min_active_count=opt_cfg['min_active_count'],
            author_cap=opt_cfg['author_cap'],
            non_author_min_weight=opt_cfg['non_author_min_weight'],
            non_author_floor_targets=non_author_floor_targets,
        )
        if ignore_author and OPT_AUTHOR_INDEX >= 0:
            optimal_vec[OPT_AUTHOR_INDEX] = 0.0
            vec_sum = float(np.sum(optimal_vec))
            if vec_sum > 1e-12:
                optimal_vec = optimal_vec / vec_sum
            else:
                optimal_vec = np.ones_like(optimal_vec, dtype=float)
                optimal_vec[OPT_AUTHOR_INDEX] = 0.0
                optimal_vec = optimal_vec / max(float(np.sum(optimal_vec)), 1e-12)

        eval_before = _evaluate_training_samples(
            samples,
            start_vec,
            threshold,
            pos_push_margin=opt_cfg['pos_push_margin'],
            neg_push_margin=opt_cfg['neg_push_margin'],
            prepared_arrays=prepared_samples,
            exclusion_policy=exclusion_policy,
        )
        eval_candidate = _evaluate_training_samples(
            samples,
            optimal_vec,
            threshold,
            pos_push_margin=opt_cfg['pos_push_margin'],
            neg_push_margin=opt_cfg['neg_push_margin'],
            prepared_arrays=prepared_samples,
            exclusion_policy=exclusion_policy,
        )

        before_viol = eval_before.get('violations', {}) or {}
        after_viol = eval_candidate.get('violations', {}) or {}
        before_neg = int(before_viol.get('negative_above_threshold', 0))
        before_pos = int(before_viol.get('positive_below_threshold', 0))
        after_neg = int(after_viol.get('negative_above_threshold', 0))
        after_pos = int(after_viol.get('positive_below_threshold', 0))
        before_total = before_neg + before_pos
        after_total = after_neg + after_pos

        guardrail_reason = ''
        guardrail_accepted = (
            after_neg <= before_neg
            and after_pos <= before_pos
            and (
                after_total < before_total
                or float(eval_candidate.get('separation', 0.0)) > float(eval_before.get('separation', 0.0))
                or float(eval_candidate.get('margin', 0.0)) > float(eval_before.get('margin', 0.0))
            )
        )
        selected_vec = optimal_vec.copy()
        eval_after = eval_candidate
        if not guardrail_accepted:
            guardrail_reason = (
                'Candidate solution worsened threshold violations '
                f'(pos {before_pos}->{after_pos}, neg {before_neg}->{after_neg}); '
                'baseline weights were kept.'
            )
            selected_vec = start_vec.copy()
            eval_after = eval_before

        optimized_weights = _merge_numeric_weights(base_weights, selected_vec)
        baseline_weights = _merge_numeric_weights(base_weights, start_vec)
        candidate_weights = _merge_numeric_weights(base_weights, optimal_vec)

        saved = False
        if save_global_weights and guardrail_accepted:
            saved = bool(save_weights(optimized_weights))

        weight_changes = []
        candidate_weight_changes = []
        for idx, name in enumerate(OPT_WEIGHT_NAMES):
            old_v = float(start_vec[idx])
            new_v = float(selected_vec[idx])
            cand_v = float(optimal_vec[idx])
            weight_changes.append({
                'name': name,
                'old': old_v,
                'new': new_v,
                'delta': new_v - old_v,
            })
            candidate_weight_changes.append({
                'name': name,
                'old': old_v,
                'new': cand_v,
                'delta': cand_v - old_v,
            })
        weight_changes.sort(key=lambda x: abs(x['delta']), reverse=True)
        candidate_weight_changes.sort(key=lambda x: abs(x['delta']), reverse=True)

        _set_optimization_progress(
            active=False,
            phase='complete',
            message='Optimization completed' if guardrail_accepted else 'Optimization completed (baseline preserved)',
            dataset=summary,
            solver={
                'iteration': int(getattr(result, 'nit', iter_counter.get('value', 0))),
                'maxiter': maxiter,
                'objective': float(result.fun),
                'success': bool(result.success),
                'method': optimizer_method,
            },
            error=None
        )

        result_status = getattr(result, 'status', 0)
        try:
            result_status = int(result_status)
        except Exception:
            result_status = 0

        return jsonify({
            'success': True,
            'scope': scope,
            'current_session': current_session,
            'selected_sessions_mode': selected_sessions_mode,
            'selected_sessions_requested': requested_selected_sessions,
            'selected_sessions_effective': summary.get('selected_sessions_effective', []),
            'optimizer_seed': int(optimizer_seed),
            'optimizer_seed_source': optimizer_seed_source,
            'hybrid_restarts': int(hybrid_restarts),
            'genetic_restarts': int(genetic_restarts),
            'lbfgsb_restarts': int(lbfgsb_restarts),
            'ignore_author': ignore_author,
            'ignore_author_requested': ignore_author_requested,
            'ignore_author_forced': True,
            'start_source_requested': 'ui' if start_from_ui else 'global',
            'start_source': start_source,
            'optimizer_method': optimizer_method,
            'balance_profile': balance_profile,
            'threshold': threshold,
            'positive_target': pos_target,
            'negative_target': neg_target,
            'maxiter': maxiter,
            'training_summary': summary,
            'optimization': {
                'success': bool(result.success),
                'status': result_status,
                'message': str(result.message),
                'objective': float(result.fun),
                'objective_mode': 'maximize_context_utility',
                'utility': float(-result.fun),
                'iterations': int(getattr(result, 'nit', iter_counter.get('value', 0))),
                'runs': solver_runs,
                'corr_regularizer_pairs': corr_pair_count,
                'corr_regularizer_active': has_corr_regularizer,
            },
            'evaluation_before': eval_before,
            'evaluation_after': eval_after,
            'evaluation_candidate': eval_candidate,
            'improvement': {
                'separation_delta': float(eval_after['separation'] - eval_before['separation']),
                'margin_delta': float(eval_after['margin'] - eval_before['margin']),
                'positive_mean_delta': float(eval_after['positive']['mean'] - eval_before['positive']['mean']),
                'negative_mean_delta': float(eval_after['negative']['mean'] - eval_before['negative']['mean']),
            },
            'improvement_candidate': {
                'separation_delta': float(eval_candidate['separation'] - eval_before['separation']),
                'margin_delta': float(eval_candidate['margin'] - eval_before['margin']),
                'positive_mean_delta': float(eval_candidate['positive']['mean'] - eval_before['positive']['mean']),
                'negative_mean_delta': float(eval_candidate['negative']['mean'] - eval_before['negative']['mean']),
            },
            'guardrail': {
                'accepted': guardrail_accepted,
                'reason': guardrail_reason,
                'before': {
                    'positive_below_threshold': before_pos,
                    'negative_above_threshold': before_neg,
                    'total_violations': before_total,
                },
                'after_candidate': {
                    'positive_below_threshold': after_pos,
                    'negative_above_threshold': after_neg,
                    'total_violations': after_total,
                },
            },
            'selected_solution': 'candidate' if guardrail_accepted else 'baseline',
            'optimized_weights': optimized_weights,
            'baseline_weights': baseline_weights,
            'candidate_weights': candidate_weights,
            'numeric_weights': {name: float(selected_vec[idx]) for idx, name in enumerate(OPT_WEIGHT_NAMES)},
            'numeric_weights_baseline': {name: float(start_vec[idx]) for idx, name in enumerate(OPT_WEIGHT_NAMES)},
            'numeric_weights_candidate': {name: float(optimal_vec[idx]) for idx, name in enumerate(OPT_WEIGHT_NAMES)},
            'weight_changes': weight_changes,
            'weight_changes_candidate': candidate_weight_changes,
            'saved_global_weights': saved,
        })
    except Exception as e:
        _set_optimization_progress(
            active=False,
            phase='error',
            message='Error during optimization',
            error=str(e)
        )
        import traceback
        return jsonify({'success': False, 'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/paper_writing/clear_stats', methods=['POST'])
def api_paper_writing_clear_stats():
    """Cancella tutte le etichette di plagio dal database."""
    try:
        data = request.json or {}
        clear_all = data.get('all', False)

        labels_db = load_labels_db(DEFAULT_LABELS_DB_PATH) if HAS_PLAGIARISM_LABELS else {}
        deleted_count = len(labels_db)

        if clear_all or not data.get('session'):
            # Cancella TUTTO
            labels_db.clear()
        else:
            # Cancella solo una session
            session = data.get('session', '')
            to_delete = [k for k, v in labels_db.items() if v.get('session') == session]
            for k in to_delete:
                del labels_db[k]
            deleted_count = len(to_delete)

        if save_labels_db(DEFAULT_LABELS_DB_PATH, labels_db):
            return jsonify({'success': True, 'deleted': deleted_count})
        else:
            return jsonify({'success': False, 'error': 'Save failed'}), 500

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def is_leaf_folder(p: Path) -> bool:
    """Ritorna True se la cartella non contiene sottocartelle.
    Usata per decidere se saltare confronti tra file nella stessa cartella "leaf".
    """
    try:
        for child in p.iterdir():
            if child.is_dir():
                return False
        return True
    except Exception:
        # In caso di errori di accesso, assumiamo non-leaf per evitare saltare troppo
        return False

#  Pipeline di estrazione multi-CAD

def extract_signatures_multi(directory: Path, cad_type: str) -> list[FeatureSignature]:
    """Estrae firme da qualsiasi CAD supportato.
    - Se multi-CAD disponibile: usa extract_from_file + adapter
    - Altrimenti fallback a analyze_directory (Solid Edge)
    """
    # Se il sistema multi-CAD non Ã¨ disponibile, fallback Solid Edge
    if not HAS_MULTI_CAD:
        return analyze_directory(directory, use_com=True)

    # Verifica disponibilitÃ  del CAD selezionato (se specificato)
    try:
        availability = get_available_cads()
    except Exception as e:
        print(f" get_available_cads fallita: {e}")
        availability = {}

    if cad_type != 'auto':
        if not availability.get(cad_type, True):
            raise RuntimeError(f"CAD {cad_type} non disponibile sull'host")

    sigs: list[CADModelSignature] = []

    # Filtra i file CAD: se auto prende tutti, altrimenti solo quelli del CAD richiesto
    all_candidates = filter_cad_files(directory, recursive=True)
    if cad_type == 'auto':
        cad_files = all_candidates
    else:
        cad_files = []
        for p in all_candidates:
            detected = None
            try:
                detected = detect_cad_type(p)
            except Exception:
                detected = None
            if detected == cad_type:
                cad_files.append(p)

    if len(cad_files) == 0:
        print(f" Nessun file {cad_type} trovato dopo il filtro.")
        return []

    for fp in cad_files:
        try:
            res = extract_from_file(fp)
            if res.success and res.signature:
                sigs.append(res.signature)
            else:
                print(f" Estrazione fallita per {fp}: {res.error_message}")
        except Exception as e:
            print(f" Errore estrazione {fp}: {e}")
            continue

    # Adatta in FeatureSignature per compatibilitÃ  con compute_similarity
    try:
        return build_feature_signatures(sigs)
    except Exception as e:
        print(f" Adapter error, fallback a 0 firme: {e}")
        return []


def run_server(host: str = '127.0.0.1', port: int = 5000, debug: bool = False):
    """Wrapper per avviare il server Flask dall'esterno (usato da run_webapp.py).

    Manteniamo questa funzione per compatibilitÃ  e per permettere
    chiamate programmatiche al server (es. script di avvio).
    """
    # In modalitÃ  debug Flask avvia il reloader che ri-importa il modulo; evitare duplicati
    use_reloader = bool(debug)
    try:
        # Stampa una riga informativa
        print(f"Avvio web server su http://{host}:{port} (debug={debug})")
        app.run(host=host, port=port, debug=debug, use_reloader=use_reloader)
    except Exception as e:
        print(f"Errore avvio server: {e}")
        raise


@app.route('/api/paper_writing/confirmed_count', methods=['POST'])
def api_paper_writing_confirmed_count():
    """Return confirmed labels count for a given session/root.
    Body JSON:
      - root: path to session folder OR
      - session: session name (optional)
    This endpoint is stateless: it only inspects the labels DB.
    """
    try:
        data = request.json or {}
        root = data.get('root', '')
        session = data.get('session', '')

        labels_db = load_labels_db(DEFAULT_LABELS_DB_PATH) if HAS_PLAGIARISM_LABELS else {}

        # If root provided, infer session name from folder name
        if root and not session:
            try:
                session = Path(root).name
            except Exception:
                session = session or ''

        if not session:
            return jsonify({'success': False, 'error': 'Provide root or session parameter'}), 400

        def _norm_session(s: str) -> str:
            return ' '.join(s.replace('_', ' ').split()).upper() if s else ''

        norm_s = _norm_session(session)
        confirmed = 0
        matched_keys = []
        for key, entry in labels_db.items():
            entry_sess = (entry.get('session') or '').strip()
            if not entry_sess:
                continue
            if _norm_session(entry_sess) != norm_s:
                continue
            if entry.get('label') == 'CONFIRMED_PLAGIARISM':
                confirmed += 1
                matched_keys.append(key)

        return jsonify({'success': True, 'session': session, 'confirmed_count': confirmed, 'matched_keys': matched_keys})
    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'traceback': traceback.format_exc()}), 500
