#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Filter analysis pairs for a specific session folder keeping only pairs where
at least one of the two files contains any of the target keywords.

Usage:
  python filter_pairs_by_session_and_keywords.py \
    --analysis /path/to/analysis.json \
    --session "Exemption 09-12-2025" \
    --out /path/to/output_filtered.json \
    [--keywords esercitazione,esercizio,compito] \
    [--csv /path/to/output.csv]
"""

import argparse
import json
import sys
from pathlib import Path, PurePath
from typing import List, Dict, Any

DEFAULT_KEYWORDS = ["esercitazione", "esercizio", "compito"]


def normalize_token(s: str) -> str:
    if s is None:
        return ""
    return s.strip().lower()


def path_contains_session(path: str, session_token: str) -> bool:
    """
    Check whether any path component contains (as substring) the session token.
    Case-insensitive. Works for Windows and POSIX separators.
    """
    if not path:
        return False
    token = normalize_token(session_token)
    if token == "":
        return False
    try:
        parts = [p.lower() for p in PurePath(path).parts if p]
    except Exception:
        return token in path.lower()
    for part in parts:
        if token in part:
            return True
    return token in path.lower()


def filename_matches_keywords(filename: str, keywords: List[str]) -> bool:
    """
    True if filename (or its base name) contains any of the keywords (case-insensitive).
    """
    if not filename:
        return False
    base = Path(filename).name.lower()
    for kw in keywords:
        if kw and kw.lower() in base:
            return True
    return False


def load_json(filepath: Path) -> Dict[str, Any]:
    try:
        with filepath.open("r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON in '{filepath}': {e}") from e
    except Exception as e:
        raise RuntimeError(f"Error reading '{filepath}': {e}") from e


def detect_pairs_container(data: Dict[str, Any]) -> str:
    if "similar_pairs" in data and isinstance(data["similar_pairs"], list):
        return "similar_pairs"
    if "pairs" in data and isinstance(data["pairs"], list):
        return "pairs"
    if "results" in data and isinstance(data["results"], dict) and "pairs" in data["results"] and isinstance(data["results"]["pairs"], list):
        return "results.pairs"
    candidate = None
    max_len = 0
    for k, v in data.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            if len(v) > max_len:
                max_len = len(v)
                candidate = k
    if candidate:
        return candidate
    raise RuntimeError("Could not find a 'similar_pairs' (or equivalent) list in the analysis JSON.")


def get_pairs_from_data(data: Dict[str, Any], container_key: str) -> List[Dict[str, Any]]:
    if container_key == "results.pairs":
        return data.get("results", {}).get("pairs", [])
    return data.get(container_key, [])


def set_pairs_in_data(data: Dict[str, Any], container_key: str, pairs: List[Dict[str, Any]]):
    if container_key == "results.pairs":
        if "results" not in data or not isinstance(data["results"], dict):
            data["results"] = {}
        data["results"]["pairs"] = pairs
    else:
        data[container_key] = pairs


def filter_pairs_for_session_and_keywords(
    pairs: List[Dict[str, Any]],
    session_token: str,
    keywords: List[str]
) -> List[Dict[str, Any]]:
    session_pairs = []

    for p in pairs:
        file1 = p.get("file1") or p.get("filename1") or p.get("f1") or ""
        file2 = p.get("file2") or p.get("filename2") or p.get("f2") or ""
        path1 = p.get("path1") or p.get("p1") or p.get("path") or ""
        path2 = p.get("path2") or p.get("p2") or p.get("path_b") or ""

        in_session_1 = path_contains_session(path1 or file1, session_token)
        in_session_2 = path_contains_session(path2 or file2, session_token)

        if not (in_session_1 and in_session_2):
            continue

        if (
            filename_matches_keywords(file1, keywords)
            or filename_matches_keywords(file2, keywords)
            or filename_matches_keywords(Path(path1).name, keywords)
            or filename_matches_keywords(Path(path2).name, keywords)
        ):
            session_pairs.append(p)

    return session_pairs


def write_json_atomic(obj: Any, outpath: Path):
    tmp = outpath.with_suffix(outpath.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    tmp.replace(outpath)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Filter analysis pairs for a given session keeping only pairs where at least "
                    "one of the two files contains specified keywords."
    )
    parser.add_argument("--analysis", "-i", required=True, help="Path to analysis JSON file")
    parser.add_argument("--session", "-s", default="Exemption 09-12-2025",
                        help="Session folder name to filter (default: 'Exemption 09-12-2025')")
    parser.add_argument("--out", "-o", help="Output JSON path (default: <analysis>_filtered.json)")
    parser.add_argument("--keywords", "-k", help="Comma-separated keywords (default: esercitazione,esercizio,compito)")
    parser.add_argument("--csv", help="Optional CSV output path to list kept pairs")
    args = parser.parse_args(argv)

    analysis_path = Path(args.analysis)
    if not analysis_path.exists():
        print(f"Error: analysis file not found: {analysis_path}", file=sys.stderr)
        sys.exit(2)

    out_path = Path(args.out) if args.out else analysis_path.with_name(analysis_path.stem + "_filtered.json")

    if args.keywords:
        keywords = [kw.strip().lower() for kw in args.keywords.split(",") if kw.strip()]
    else:
        keywords = DEFAULT_KEYWORDS

    try:
        data = load_json(analysis_path)
    except Exception as e:
        print(f"Error loading analysis JSON: {e}", file=sys.stderr)
        sys.exit(3)

    try:
        container_key = detect_pairs_container(data)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(4)

    all_pairs = get_pairs_from_data(data, container_key)
    if not isinstance(all_pairs, list):
        print(f"Error: detected pairs container '{container_key}' but its value is not a list.", file=sys.stderr)
        sys.exit(5)

    total_pairs = len(all_pairs)
    print(f"Loaded analysis: {analysis_path} -> found {total_pairs} pairs in container '{container_key}'.")

    filtered = filter_pairs_for_session_and_keywords(all_pairs, args.session, keywords)
    kept = len(filtered)

    print(f"Session token: '{args.session}'")
    print(f"Keywords: {keywords}")
    print(f"Pairs inside session and matching keywords: {kept}")

    out_data = data.copy()
    set_pairs_in_data(out_data, container_key, filtered)

    out_data["_filtered_by"] = {
        "session": args.session,
        "keywords": keywords,
        "original_pairs_count": total_pairs,
        "filtered_pairs_count": kept
    }

    try:
        write_json_atomic(out_data, out_path)
        print(f"Wrote filtered JSON to: {out_path}")
    except Exception as e:
        print(f"Error writing output JSON: {e}", file=sys.stderr)
        sys.exit(6)

    if args.csv:
        csv_path = Path(args.csv)
        try:
            import csv
            with csv_path.open("w", newline='', encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["file1", "path1", "file2", "path2", "similarity"])
                for p in filtered:
                    file1 = p.get("file1") or p.get("filename1") or ""
                    path1 = p.get("path1") or p.get("p1") or ""
                    file2 = p.get("file2") or p.get("filename2") or ""
                    path2 = p.get("path2") or p.get("p2") or ""
                    sim = p.get("similarity")
                    if isinstance(sim, dict):
                        sim_val = sim.get("overall")
                    else:
                        sim_val = sim
                    writer.writerow([file1, path1, file2, path2, sim_val])
            print(f"Wrote CSV of kept pairs to: {csv_path}")
        except Exception as e:
            print(f"Warning: failed to write CSV: {e}", file=sys.stderr)

    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
