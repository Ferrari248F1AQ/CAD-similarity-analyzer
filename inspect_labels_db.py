#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
from pathlib import Path

db_path = Path.home() / '.cache' / 'cad_similarity_analyzer' / 'plagiarism_labels.json'
with open(db_path) as f:
    db = json.load(f)

print(f"Total entries: {len(db)}\n")

# Mostra session name unici
sessions = set()
for entry in db.values():
    sessions.add(entry.get('session', 'UNKNOWN'))

print("Session names in database:")
for s in sorted(sessions):
    count = sum(1 for e in db.values() if e.get('session') == s)
    print(f"  '{s}' -> {count} entries")

print("\n\nFirst 3 entries detail:")
for i, (key, entry) in enumerate(list(db.items())[:3]):
    print(f"\nEntry {i+1}:")
    print(f"  Key: {key}")
    print(f"  Session: {entry.get('session')}")
    print(f"  File A: {entry.get('file_a')}")
    print(f"  File B: {entry.get('file_b')}")
    print(f"  Label: {entry.get('label')}")

