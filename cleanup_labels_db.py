#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script per pulire il database delle label:
Converte session name da ROOT (Esami Fondamenti di CAD - Copia) ai nomi folder veri
estratti dai path file (es. Exam 03-07-2024)
"""
import json
import os
from pathlib import Path
from collections import defaultdict

LABELS_DB_PATH = Path.home() / '.cache' / 'cad_similarity_analyzer' / 'plagiarism_labels.json'
DATASET_ROOT = Path(r'C:\Users\emanu\Downloads\Esami Fondamenti di CAD - Copia')

if not LABELS_DB_PATH.exists():
    print(f"❌ Database not found: {LABELS_DB_PATH}")
    exit(1)

if not DATASET_ROOT.exists():
    print(f"❌ Dataset root not found: {DATASET_ROOT}")
    exit(1)

print("=" * 80)
print("LABEL DATABASE CLEANUP - SESSION NAME FIX")
print("=" * 80)

with open(LABELS_DB_PATH, 'r', encoding='utf-8') as f:
    labels_db = json.load(f)

print(f"\n📊 Database has {len(labels_db)} entries")

# Mappa i file alle loro sessioni reali
# Scansiona il dataset e crea una mappa: filename -> session_name
file_to_session = {}
for session_folder in DATASET_ROOT.iterdir():
    if not session_folder.is_dir() or session_folder.name.startswith('.'):
        continue
    session_name = session_folder.name
    for file_path in session_folder.rglob('*.par'):
        file_to_session[file_path.name] = session_name

print(f"🔍 Found {len(file_to_session)} .par files in dataset")
print(f"   Sessions: {set(file_to_session.values())}")

# Aggiorna le sessioni nel DB
fixed_count = 0
unfixed_count = 0

for key, entry in labels_db.items():
    old_session = entry.get('session', '')
    file_a = entry.get('file_a', '')
    file_b = entry.get('file_b', '')

    # Cerca il session name vero da uno dei file
    new_session = None
    if file_a in file_to_session:
        new_session = file_to_session[file_a]
    elif file_b in file_to_session:
        new_session = file_to_session[file_b]

    if new_session and new_session != old_session:
        print(f"🔧 Entry {key[:50]}...")
        print(f"   Old session: '{old_session}'")
        print(f"   New session: '{new_session}'")
        entry['session'] = new_session
        fixed_count += 1
    elif new_session:
        print(f"✅ Entry already correct: '{new_session}'")
    else:
        print(f"⚠️  Could not find session for: {file_a} <-> {file_b}")
        unfixed_count += 1

print(f"\n✅ Fixed {fixed_count} entries")
print(f"⚠️  Could not fix {unfixed_count} entries")

if fixed_count > 0:
    # Backup del file originale
    import time
    backup_path = LABELS_DB_PATH.with_suffix(f'.json.backup.{int(time.time())}')
    import shutil
    shutil.copy(LABELS_DB_PATH, backup_path)
    print(f"📦 Backup created: {backup_path.name}")

    # Salva il file aggiornato
    with open(LABELS_DB_PATH, 'w', encoding='utf-8') as f:
        json.dump(labels_db, f, indent=2, ensure_ascii=False)
    print(f"💾 Database updated and saved!")
else:
    print(f"✅ No changes needed!")

print("\n" + "=" * 80)
print("END CLEANUP")
print("=" * 80)




