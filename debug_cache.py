#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Debug script per controllare lo stato della cache
"""
import sys
sys.stdout.flush()

import json
import os
from pathlib import Path

CACHE_DIR = Path.home() / '.cache' / 'cad_similarity_analyzer' / 'results'

print("=" * 80, flush=True)
print("CACHE DEBUG SCRIPT", flush=True)
print("=" * 80)

if not CACHE_DIR.exists():
    print(f"❌ Cache directory non esiste: {CACHE_DIR}")
    exit(1)

files = list(CACHE_DIR.glob('*.json'))
print(f"\n📁 Cache directory: {CACHE_DIR}")
print(f"📄 Files found: {len(files)}")

for file in files:
    print(f"\n{'='*80}")
    print(f"File: {file.name}")
    print(f"Size: {file.stat().st_size / (1024*1024):.2f} MB")

    try:
        with open(file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        sigs = len(data.get('signatures', []))
        pairs = len(data.get('similar_pairs', []))
        directory = data.get('directory', 'N/A')
        timestamp = data.get('timestamp', 'N/A')

        print(f"✅ JSON valid")
        print(f"   Directory: {directory}")
        print(f"   Timestamp: {timestamp}")
        print(f"   Signatures: {sigs}")
        print(f"   Pairs: {pairs}")

        if pairs > 0:
            # Mostra primi e ultimi valori di similarità
            sims = [p.get('similarity', 0) for p in data.get('similar_pairs', [])]
            print(f"   Similarity range: {min(sims):.3f} - {max(sims):.3f}")

    except json.JSONDecodeError as e:
        print(f"❌ JSON INVALID: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")

print(f"\n{'='*80}")
print("END DEBUG")
print("=" * 80)


