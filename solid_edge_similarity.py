# -*- coding: utf-8 -*-
"""
Analisi similarità file CAD Solid Edge basata su feature patterns.
Identifica potenziali "firme" di modellazione per attribuire file allo stesso autore.
"""

import json
import hashlib
from pathlib import Path
from typing import Dict, List, Tuple, Any
from dataclasses import dataclass, field, asdict
from collections import Counter

# Per connessione COM diretta a Solid Edge
try:
    import win32com.client
    HAS_COM = True
except ImportError:
    HAS_COM = False
    print("⚠️  Installa pywin32: pip install pywin32")


# Mapping reale dei tipi feature di Solid Edge (basato su output diagnostico)
FEATURE_TYPE_MAP = {
    462094706: "ExtrudedProtrusion",
    462094714: "ExtrudedCutout",
    462094722: "Hole",
    462094742: "Chamfer",
    462094738: "Round",
    462094718: "RevolvedProtrusion",
    462094726: "RevolvedCutout",
    462094730: "Loft",
    462094734: "Sweep",
    462094746: "Draft",
    462094750: "Shell",
    462094754: "Rib",
    462094758: "Web",
    462094762: "Lip",
    462094766: "Thread",
    462094770: "CircularPattern",
    462094774: "RectangularPattern",
    462094778: "MirrorPattern",
    462094782: "ThinWall",
    462094786: "Thicken",
    462094790: "Boolean",
}


@dataclass
class FeatureSignature:
    """Firma estratta da un file CAD."""
    filename: str
    filepath: str
    file_hash: str

    # Metadati documento (da Properties)
    author: str = ""
    last_author: str = ""
    company: str = ""
    creation_date: str = ""
    last_save_date: str = ""
    template: str = ""
    revision: str = ""

    # Feature analysis
    feature_count: int = 0
    feature_types: Dict[str, int] = field(default_factory=dict)
    feature_sequence: List[str] = field(default_factory=list)
    feature_names: List[str] = field(default_factory=list)

    # Collezioni specifiche
    extrusions_count: int = 0
    cutouts_count: int = 0
    holes_count: int = 0
    rounds_count: int = 0
    chamfers_count: int = 0
    sketches_count: int = 0

    # Pattern di modellazione (fingerprint stilistico)
    extrusion_ratio: float = 0.0
    cutout_ratio: float = 0.0
    hole_ratio: float = 0.0
    round_chamfer_ratio: float = 0.0

    # Sequenze caratteristiche (bigram di feature consecutive)
    common_sequences: List[Tuple[str, int]] = field(default_factory=list)

    # Stile naming
    naming_style: str = ""


def compute_file_hash(filepath: Path) -> str:
    """Calcola hash SHA256 del file."""
    sha = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha.update(chunk)
    return sha.hexdigest()[:16]


def get_feature_type_name(type_enum: int) -> str:
    """Converte l'enum Type di Solid Edge in nome leggibile."""
    return FEATURE_TYPE_MAP.get(type_enum, f"Unknown_{type_enum}")


def extract_properties_via_com(doc) -> Dict[str, Any]:
    """Estrae le proprietà del documento (Author, Company, ecc.) via COM."""
    props_data = {}

    try:
        properties = doc.Properties
        for i in range(1, properties.Count + 1):
            try:
                prop_set = properties.Item(i)
                set_name = getattr(prop_set, 'Name', f'Set_{i}')

                if set_name == 'SummaryInformation':
                    for j in range(1, prop_set.Count + 1):
                        try:
                            p = prop_set.Item(j)
                            pname = getattr(p, 'Name', '')
                            pval = getattr(p, 'Value', '')

                            if pname == 'Author':
                                props_data['author'] = str(pval) if pval else ''
                            elif pname == 'Last Author':
                                props_data['last_author'] = str(pval) if pval else ''
                            elif pname == 'Template':
                                props_data['template'] = str(pval) if pval else ''
                            elif pname == 'Revision Number':
                                props_data['revision'] = str(pval) if pval else ''
                            elif pname == 'Origination Date':
                                props_data['creation_date'] = str(pval) if pval else ''
                            elif pname == 'Last Save Date':
                                props_data['last_save_date'] = str(pval) if pval else ''
                        except:
                            pass

                elif set_name == 'DocumentSummaryInformation':
                    for j in range(1, prop_set.Count + 1):
                        try:
                            p = prop_set.Item(j)
                            pname = getattr(p, 'Name', '')
                            pval = getattr(p, 'Value', '')

                            if pname == 'Company':
                                props_data['company'] = str(pval) if pval else ''
                        except:
                            pass

                elif set_name == 'ExtendedSummaryInformation':
                    for j in range(1, prop_set.Count + 1):
                        try:
                            p = prop_set.Item(j)
                            pname = getattr(p, 'Name', '')
                            pval = getattr(p, 'Value', '')

                            if pname == 'Username':
                                props_data['username'] = str(pval) if pval else ''
                        except:
                            pass
            except:
                pass
    except Exception as e:
        props_data['error'] = str(e)

    return props_data


def extract_features_via_com(filepath: Path, app=None) -> Dict[str, Any]:
    """Estrae feature tramite COM automation."""
    if not HAS_COM:
        return {'error': 'pywin32 non installato'}

    data = {
        'properties': {},
        'feature_list': [],
        'feature_types': Counter(),
        'collections': {},
    }
    doc = None

    try:
        # Connessione a Solid Edge
        if app is None:
            try:
                app = win32com.client.GetActiveObject("SolidEdge.Application")
            except:
                app = win32com.client.Dispatch("SolidEdge.Application")
                app.Visible = True

        app.DisplayAlerts = False

        ext = filepath.suffix.lower()

        if ext in ['.par', '.psm']:
            doc = app.Documents.Open(str(filepath))

            # Estrai proprietà documento
            data['properties'] = extract_properties_via_com(doc)

            # Estrai feature dal modello
            if doc.Models.Count > 0:
                model = doc.Models.Item(1)

                # Usa model.Features (funziona!)
                try:
                    features_coll = model.Features
                    for i in range(1, features_coll.Count + 1):
                        try:
                            feat = features_coll.Item(i)
                            feat_name = getattr(feat, 'Name', f'Feature_{i}')
                            feat_type_enum = getattr(feat, 'Type', 0)
                            feat_type = get_feature_type_name(feat_type_enum)

                            data['feature_list'].append({
                                'index': i,
                                'name': str(feat_name),
                                'type': feat_type,
                                'type_enum': feat_type_enum,
                            })
                            data['feature_types'][feat_type] += 1
                        except Exception as e:
                            data['feature_list'].append({
                                'index': i,
                                'name': f'Feature_{i}',
                                'type': 'Error',
                                'error': str(e)
                            })
                except Exception as e:
                    data['features_error'] = str(e)

                # Conta collezioni specifiche
                collections_to_check = [
                    'ExtrudedProtrusions',
                    'ExtrudedCutouts',
                    'Holes',
                    'Rounds',
                    'Chamfers',
                    'RevolvedProtrusions',
                    'RevolvedCutouts',
                    'Sweeps',
                    'Lofts',
                    'CircularPatterns',
                    'RectangularPatterns',
                    'MirrorPatterns',
                    'Shells',
                    'Drafts',
                    'Threads',
                    'Ribs',
                    'Webs',
                    'BooleanFeatures',
                ]

                for coll_name in collections_to_check:
                    try:
                        coll = getattr(model, coll_name)
                        if hasattr(coll, 'Count'):
                            data['collections'][coll_name] = coll.Count
                    except:
                        pass

            # Conta sketches
            try:
                data['sketches_count'] = doc.Sketches.Count
            except:
                data['sketches_count'] = 0

            doc.Close(False)

        elif ext == '.asm':
            doc = app.Documents.Open(str(filepath))
            data['properties'] = extract_properties_via_com(doc)

            try:
                data['occurrences_count'] = doc.Occurrences.Count
            except:
                pass

            try:
                data['relations_count'] = doc.Relations3d.Count
            except:
                pass

            doc.Close(False)

    except Exception as e:
        data['error'] = str(e)
        import traceback
        data['traceback'] = traceback.format_exc()
        if doc:
            try:
                doc.Close(False)
            except:
                pass

    return data


def analyze_naming_style(feature_names: List[str]) -> str:
    """Analizza lo stile di naming delle feature."""
    if not feature_names:
        return "unknown"

    # Conta pattern
    patterns = Counter()
    for name in feature_names:
        if '_' in name and name.split('_')[-1].isdigit():
            patterns['default_numbered'] += 1  # Es: ExtrudedProtrusion_1
        elif name[0].isupper() and '_' not in name:
            patterns['camelcase'] += 1
        elif name.lower().startswith(('feat', 'feature')):
            patterns['generic'] += 1
        else:
            patterns['custom'] += 1

    if patterns:
        return patterns.most_common(1)[0][0]
    return "unknown"


def compute_feature_sequences(feature_types: List[str]) -> List[Tuple[str, int]]:
    """Calcola i bigram (coppie consecutive) di feature più comuni."""
    if len(feature_types) < 2:
        return []

    bigrams = []
    for i in range(len(feature_types) - 1):
        bigram = f"{feature_types[i]} → {feature_types[i+1]}"
        bigrams.append(bigram)

    return Counter(bigrams).most_common(5)


def extract_signature(filepath: Path, app=None) -> FeatureSignature:
    """Estrae la firma completa da un file Solid Edge."""

    sig = FeatureSignature(
        filename=filepath.name,
        filepath=str(filepath),
        file_hash=compute_file_hash(filepath)
    )

    # Estrai tutto via COM
    data = extract_features_via_com(filepath, app=app)

    if 'error' in data:
        print(f"    ⚠️ Errore: {data['error']}")
        return sig

    # Proprietà documento
    props = data.get('properties', {})
    sig.author = props.get('author', '') or props.get('username', '')
    sig.last_author = props.get('last_author', '')
    sig.company = props.get('company', '')
    sig.creation_date = props.get('creation_date', '')
    sig.last_save_date = props.get('last_save_date', '')
    sig.template = props.get('template', '')
    sig.revision = props.get('revision', '')

    # Feature
    feature_list = data.get('feature_list', [])
    sig.feature_count = len(feature_list)
    sig.feature_types = dict(data.get('feature_types', {}))
    sig.feature_sequence = [f['type'] for f in feature_list]
    sig.feature_names = [f['name'] for f in feature_list]

    # Collezioni specifiche
    colls = data.get('collections', {})
    sig.extrusions_count = colls.get('ExtrudedProtrusions', 0)
    sig.cutouts_count = colls.get('ExtrudedCutouts', 0)
    sig.holes_count = colls.get('Holes', 0)
    sig.rounds_count = colls.get('Rounds', 0)
    sig.chamfers_count = colls.get('Chamfers', 0)
    sig.sketches_count = data.get('sketches_count', 0)

    # Calcola rapporti
    total = sig.feature_count or 1
    sig.extrusion_ratio = sig.extrusions_count / total
    sig.cutout_ratio = sig.cutouts_count / total
    sig.hole_ratio = sig.holes_count / total
    sig.round_chamfer_ratio = (sig.rounds_count + sig.chamfers_count) / total

    # Sequenze
    sig.common_sequences = compute_feature_sequences(sig.feature_sequence)

    # Stile naming
    sig.naming_style = analyze_naming_style(sig.feature_names)

    return sig


def compute_similarity(sig1: FeatureSignature, sig2: FeatureSignature) -> Dict[str, float]:
    """Calcola similarità tra due firme CAD."""
    scores = {}

    # 1. Stesso autore dichiarato
    if sig1.author and sig2.author:
        scores['author_match'] = 1.0 if sig1.author.lower().strip() == sig2.author.lower().strip() else 0.0
    else:
        scores['author_match'] = 0.5

    # 2. Similarità numero di feature (con tolleranza ±1)
    diff_count = abs(sig1.feature_count - sig2.feature_count)
    if diff_count <= 1:
        scores['feature_count_similarity'] = 1.0
    elif diff_count <= 3:
        scores['feature_count_similarity'] = 0.7
    elif diff_count <= 5:
        scores['feature_count_similarity'] = 0.4
    else:
        scores['feature_count_similarity'] = max(0.0, 1.0 - (diff_count / max(sig1.feature_count, sig2.feature_count, 1)))

    # 3. Similarità distribuzione feature types (cosine similarity)
    all_types = set(sig1.feature_types.keys()) | set(sig2.feature_types.keys())
    if all_types:
        vec1 = [sig1.feature_types.get(t, 0) for t in all_types]
        vec2 = [sig2.feature_types.get(t, 0) for t in all_types]
        dot = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a ** 2 for a in vec1) ** 0.5
        norm2 = sum(b ** 2 for b in vec2) ** 0.5
        scores['feature_type_similarity'] = dot / (norm1 * norm2 + 1e-9)
    else:
        scores['feature_type_similarity'] = 0.5

    # 4. Similarità rapporti stilistici
    style_diff = (
        abs(sig1.extrusion_ratio - sig2.extrusion_ratio) +
        abs(sig1.cutout_ratio - sig2.cutout_ratio) +
        abs(sig1.hole_ratio - sig2.hole_ratio) +
        abs(sig1.round_chamfer_ratio - sig2.round_chamfer_ratio)
    ) / 4.0
    scores['style_similarity'] = 1.0 - min(style_diff, 1.0)

    # 5. Stile naming uguale
    if sig1.naming_style and sig2.naming_style:
        scores['naming_similarity'] = 1.0 if sig1.naming_style == sig2.naming_style else 0.3
    else:
        scores['naming_similarity'] = 0.5

    # 6. BIGRAM - Calcola TUTTI i bigram dalla sequenza originale
    def get_bigrams(seq):
        if len(seq) < 2:
            return []
        return [tuple(seq[i:i+2]) for i in range(len(seq) - 1)]

    bi1 = get_bigrams(sig1.feature_sequence)
    bi2 = get_bigrams(sig2.feature_sequence)

    if bi1 or bi2:
        # Usa set per Jaccard similarity
        set1 = set(bi1)
        set2 = set(bi2)
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        scores['bigram_similarity'] = intersection / union if union > 0 else 0.0
    else:
        scores['bigram_similarity'] = 0.5

    # 7. Sequenze comuni - TRIGRAM (3 feature consecutive nello stesso ordine)
    def get_trigrams(seq):
        if len(seq) < 3:
            return set()
        return set(tuple(seq[i:i+3]) for i in range(len(seq) - 2))

    tri1 = get_trigrams(sig1.feature_sequence)
    tri2 = get_trigrams(sig2.feature_sequence)

    if tri1 or tri2:
        intersection = len(tri1 & tri2)
        union = len(tri1 | tri2)
        scores['trigram_similarity'] = intersection / union if union > 0 else 0.0
    else:
        scores['trigram_similarity'] = 0.5

    # 8. Longest Common Subsequence (LCS) normalizzata
    def lcs_length(seq1, seq2):
        if not seq1 or not seq2:
            return 0
        m, n = len(seq1), len(seq2)
        prev = [0] * (n + 1)
        curr = [0] * (n + 1)
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if seq1[i-1] == seq2[j-1]:
                    curr[j] = prev[j-1] + 1
                else:
                    curr[j] = max(prev[j], curr[j-1])
            prev, curr = curr, prev
        return prev[n]

    lcs = lcs_length(sig1.feature_sequence, sig2.feature_sequence)
    max_len = max(len(sig1.feature_sequence), len(sig2.feature_sequence), 1)
    scores['lcs_similarity'] = lcs / max_len

    # Score complessivo pesato
    # - company_match: RIMOSSO (non significativo)
    # - template_match: RIMOSSO (è il file template usato, es. "iso metric part.par")
    # - author_match: RIDOTTO (in caso di copia, l'autore sarà diverso!)
    weights = {
        'author_match': 0.05,            # 5% - solo indizio (copie hanno autori diversi!)
        'feature_count_similarity': 0.15, # 15% - numero feature simile (±1)
        'feature_type_similarity': 0.15,  # 15% - distribuzione tipi
        'style_similarity': 0.10,         # 10% - rapporti stilistici
        'naming_similarity': 0.05,        # 5% - stile naming
        'bigram_similarity': 0.15,        # 15% - coppie consecutive
        'trigram_similarity': 0.15,       # 15% - triple consecutive (IMPORTANTE)
        'lcs_similarity': 0.20,           # 20% - ordine complessivo (IL PIÙ IMPORTANTE)
    }

    scores['overall'] = sum(scores[k] * weights[k] for k in weights)

    return scores


def analyze_directory(directory: Path, use_com: bool = True) -> List[FeatureSignature]:
    """Analizza tutti i file Solid Edge in una directory."""
    extensions = {'.par', '.psm', '.asm'}
    signatures = []
    app = None

    if use_com and HAS_COM:
        try:
            app = win32com.client.GetActiveObject("SolidEdge.Application")
            print("  ✓ Connesso a Solid Edge esistente")
        except:
            app = win32com.client.Dispatch("SolidEdge.Application")
            app.Visible = True
            print("  ✓ Avviato Solid Edge")
        app.DisplayAlerts = False

    for filepath in directory.rglob('*'):
        if filepath.suffix.lower() in extensions:
            print(f"  Analizzando: {filepath.name}")
            try:
                sig = extract_signature(filepath, app=app)
                signatures.append(sig)
            except Exception as e:
                print(f"    ⚠️ Errore: {e}")

    return signatures


def find_similar_authors(signatures: List[FeatureSignature], threshold: float = 0.7) -> List[Tuple[FeatureSignature, FeatureSignature, float, Dict]]:
    """Trova coppie di file potenzialmente dello stesso autore.

    Esclude confronti tra file nella stessa cartella foglia (senza sottocartelle),
    poiché si assume che appartengano alla stessa persona.
    """
    similar_pairs = []

    for i, sig1 in enumerate(signatures):
        for sig2 in signatures[i + 1:]:
            # Ottieni le cartelle parent
            folder1 = Path(sig1.filepath).parent if sig1.filepath else None
            folder2 = Path(sig2.filepath).parent if sig2.filepath else None

            # Salta se sono nella stessa cartella
            if folder1 and folder2 and folder1 == folder2:
                continue

            sim = compute_similarity(sig1, sig2)
            if sim['overall'] >= threshold:
                similar_pairs.append((sig1, sig2, sim['overall'], sim))

    return sorted(similar_pairs, key=lambda x: -x[2])


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Analisi similarità file CAD Solid Edge")
    parser.add_argument('--input', type=Path, required=True, help='Directory o file da analizzare')
    parser.add_argument('--compare', type=Path, help='Secondo file/directory da confrontare')
    parser.add_argument('--use-com', action='store_true', default=True, help='Usa COM automation')
    parser.add_argument('--threshold', type=float, default=0.7, help='Soglia similarità (0-1)')
    parser.add_argument('--output', type=Path, help='Salva report JSON')
    parser.add_argument('--verbose', '-v', action='store_true', help='Output dettagliato')

    args = parser.parse_args()

    print("\n🔍 ANALISI SIMILARITÀ FILE CAD SOLID EDGE\n")

    signatures = []

    if args.input.is_dir():
        print(f"📁 Scansione directory: {args.input}")
        signatures.extend(analyze_directory(args.input, use_com=args.use_com))
    else:
        sig = extract_signature(args.input)
        signatures.append(sig)

    if args.compare:
        if args.compare.is_dir():
            print(f"📁 Scansione directory comparazione: {args.compare}")
            signatures.extend(analyze_directory(args.compare, use_com=args.use_com))
        else:
            sig = extract_signature(args.compare)
            signatures.append(sig)

    print(f"\n✅ File analizzati: {len(signatures)}\n")

    # Mostra info firme
    if args.verbose:
        for sig in signatures:
            parent_folder = Path(sig.filepath).parent.name if sig.filepath else "(sconosciuto)"

            print(f"📄 {sig.filename}")
            print(f"   📁 Cartella: {parent_folder}")
            print(f"   Autore: {sig.author or '(non disponibile)'}")
            print(f"   Ultimo salv.: {sig.last_author or '(non disponibile)'}")
            print(f"   Azienda: {sig.company or '(non disponibile)'}")
            print(f"   Template: {sig.template or '(non disponibile)'}")
            print(f"   Feature count: {sig.feature_count}")
            print(f"   Feature types: {sig.feature_types}")
            print(f"   Extrusions: {sig.extrusions_count}, Cutouts: {sig.cutouts_count}, Holes: {sig.holes_count}")
            print(f"   Rounds: {sig.rounds_count}, Chamfers: {sig.chamfers_count}")
            print(f"   Sketches: {sig.sketches_count}")
            print(f"   Naming style: {sig.naming_style}")
            if sig.feature_sequence:
                seq_display = sig.feature_sequence[:10]
                if len(sig.feature_sequence) > 10:
                    seq_display.append(f"... (+{len(sig.feature_sequence) - 10} altre)")
                print(f"   Sequenza: {' → '.join(str(s) for s in seq_display)}")
            if sig.common_sequences:
                print(f"   Bigram comuni: {sig.common_sequences[:3]}")
            print()

    # Trova coppie simili
    if len(signatures) >= 2:
        print("🔗 ANALISI SIMILARITÀ\n")
        similar = find_similar_authors(signatures, threshold=args.threshold)

        if similar:
            print(f"Coppie con similarità ≥ {args.threshold:.0%}:\n")
            for sig1, sig2, score, details in similar:
                folder1 = Path(sig1.filepath).parent.name if sig1.filepath else "?"
                folder2 = Path(sig2.filepath).parent.name if sig2.filepath else "?"

                print(f"  • [{folder1}] {sig1.filename} ({sig1.feature_count} features)")
                print(f"    ↔ [{folder2}] {sig2.filename} ({sig2.feature_count} features)")
                print(f"    Similarità totale: {score:.1%}")
                if args.verbose:
                    print(f"    - Autore match: {details['author_match']:.0%}")
                    print(f"    - Feature count: {details['feature_count_similarity']:.0%}")
                    print(f"    - Feature types: {details['feature_type_similarity']:.0%}")
                    print(f"    - Trigram (3 feat.): {details['trigram_similarity']:.0%}")
                    print(f"    - LCS (ordine): {details['lcs_similarity']:.0%}")
                    print(f"    - Bigram: {details['bigram_similarity']:.0%}")
                    print(f"    - Stile: {details['style_similarity']:.0%}")
                print()
        else:
            print(f"Nessuna coppia con similarità ≥ {args.threshold:.0%}")

        # Matrice completa
        if args.verbose and len(signatures) <= 10:
            print("\n📊 MATRICE SIMILARITÀ COMPLETA\n")
            header = "".ljust(20) + "".join(s.filename[:12].ljust(14) for s in signatures)
            print(header)
            for sig1 in signatures:
                row = sig1.filename[:18].ljust(20)
                folder1 = Path(sig1.filepath).parent if sig1.filepath else None
                for sig2 in signatures:
                    folder2 = Path(sig2.filepath).parent if sig2.filepath else None
                    if sig1.filename == sig2.filename and folder1 == folder2:
                        row += "---".ljust(14)
                    elif folder1 and folder2 and folder1 == folder2:
                        row += "(stesso)".ljust(14)  # Stessa cartella, skip
                    else:
                        sim = compute_similarity(sig1, sig2)['overall']
                        row += f"{sim:.1%}".ljust(14)
                print(row)

    # Salva report
    if args.output:
        similar_for_report = [
            {
                'file1': sig1.filename,
                'folder1': Path(sig1.filepath).parent.name,
                'file2': sig2.filename,
                'folder2': Path(sig2.filepath).parent.name,
                'similarity': sc,
            }
            for sig1, sig2, sc, _ in find_similar_authors(signatures, 0.5)
        ]
        report = {
            'signatures': [asdict(s) for s in signatures],
            'similar_pairs': similar_for_report,
        }
        with open(args.output, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\n💾 Report salvato: {args.output}")


if __name__ == '__main__':
    main()

