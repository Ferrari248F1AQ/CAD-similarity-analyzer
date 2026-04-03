#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Script DEFINITIVO per migrare le label dal vecchio dataset al nuovo.

Mappa ogni file alle sessioni nel nuovo dataset e aggiorna il database.
"""
import json
from pathlib import Path
from collections import defaultdict

# Percorsi
LABELS_DB_PATH = Path.home() / '.cache' / 'cad_similarity_analyzer' / 'plagiarism_labels.json'
NEW_DATASET_ROOT = Path(r'C:\Users\emanu\Desktop\Didattica CAD\Esami Fondamenti di CAD')

if not LABELS_DB_PATH.exists():
    print(f"❌ Database not found: {LABELS_DB_PATH}")
    exit(1)

if not NEW_DATASET_ROOT.exists():
    print(f"❌ New dataset root not found: {NEW_DATASET_ROOT}")
    exit(1)

print("=" * 80)
print("LABEL DATABASE MIGRATION - MAPPO I FILE ALLE SESSIONI NUOVE")
print("=" * 80)

# STEP 1: Scannerizza il nuovo dataset e crea mappa file -> sessione
print(f"\n🔍 Scanning new dataset: {NEW_DATASET_ROOT}")

file_to_session = {}
session_folders = []

for session_folder in sorted(NEW_DATASET_ROOT.iterdir()):
    if not session_folder.is_dir() or session_folder.name.startswith('.'):
        continue

    session_name = session_folder.name
    session_folders.append(session_name)

    # Scansione i file .par in questa sessione
    par_files = list(session_folder.rglob('*.par'))
    print(f"  📁 {session_name}: {len(par_files)} file")

    for par_file in par_files:
        file_to_session[par_file.name] = session_name

print(f"\n✅ Found {len(file_to_session)} unique .par files")
print(f"✅ Found {len(session_folders)} sessions:")
for s in session_folders:
    count = sum(1 for fname in file_to_session if file_to_session[fname] == s)
    print(f"   - {s} ({count} files)")

# STEP 2: Leggi il database vecchio
print(f"\n📂 Reading old database...")
with open(LABELS_DB_PATH, 'r', encoding='utf-8') as f:
    labels_db = json.load(f)

print(f"✅ Loaded {len(labels_db)} entries")

# STEP 3: Migra le sessioni
print(f"\n🔄 Migrating sessions...")

fixed_count = 0
unmapped_count = 0
details = []

for key, entry in labels_db.items():
    old_session = entry.get('session', '')
    file_a = entry.get('file_a', '')
    file_b = entry.get('file_b', '')

    # Trovaa la sessione nuova per almeno uno dei file
    new_session = None
    if file_a in file_to_session:
        new_session = file_to_session[file_a]
    elif file_b in file_to_session:
        new_session = file_to_session[file_b]

    if new_session:
        if new_session != old_session:
            entry['session'] = new_session
            fixed_count += 1
            details.append(f"  ✅ {file_a[:30]:30s} -> {new_session}")
        else:
            details.append(f"  🟢 {file_a[:30]:30s} (already correct)")
    else:
        unmapped_count += 1
        details.append(f"  ❌ {file_a[:30]:30s} NOT FOUND in new dataset")

# Mostra i dettagli
print("\nDetails:")
for d in details[:10]:  # Mostra i primi 10
    print(d)
if len(details) > 10:
    print(f"  ... and {len(details) - 10} more")

print(f"\n📊 Results:")
print(f"   Fixed: {fixed_count} entries")
print(f"   Unmapped: {unmapped_count} entries")
print(f"   Already correct: {len(labels_db) - fixed_count - unmapped_count} entries")

# STEP 4: Salva il database aggiornato
if fixed_count > 0 or unmapped_count == 0:
    print(f"\n💾 Saving updated database...")

    # Backup del file originale
    import time
    import shutil
    backup_path = LABELS_DB_PATH.with_suffix(f'.json.backup.{int(time.time())}')
    shutil.copy(LABELS_DB_PATH, backup_path)
    print(f"   📦 Backup created: {backup_path.name}")

    # Salva il file aggiornato
    with open(LABELS_DB_PATH, 'w', encoding='utf-8') as f:
        json.dump(labels_db, f, indent=2, ensure_ascii=False)

    print(f"   ✅ Database updated and saved!")
else:
    print(f"\n⚠️  No changes to make")

print("\n" + "=" * 80)
print("MIGRATION COMPLETE")
print("=" * 80)

