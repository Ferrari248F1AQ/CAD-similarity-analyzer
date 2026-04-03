import json
from urllib.request import urlopen, Request
import sys

BASE = 'http://127.0.0.1:5000'
try:
    req = Request(BASE + '/api/pairs?threshold=0&limit=10000')
    with urlopen(req, timeout=30) as resp:
        data = json.load(resp)
except Exception as e:
    print('ERROR fetching /api/pairs:', e)
    sys.exit(1)

pairs = data.get('pairs', [])
print(f"total_pairs_in_memory: {len(pairs)}")
# target pair from DB
target_a = 'esercizio desame 2.par'
target_b = 'esercizio_desame_2_v1.par'

count = 0
matches = []
for p in pairs:
    f1 = p.get('file1','')
    f2 = p.get('file2','')
    if set([f1,f2]) == set([target_a, target_b]):
        count += 1
        matches.append((f1,f2,p.get('similarity')))

print('matches count for target pair:', count)
for m in matches[:20]:
    print(m)

# Also list unique normalized pair keys in labels DB
try:
    req2 = Request(BASE + '/api/plagiarism_labels')
    with urlopen(req2, timeout=30) as resp2:
        labels = json.load(resp2)
except Exception as e:
    print('ERROR fetching /api/plagiarism_labels:', e)
    sys.exit(1)

labels_db = labels.get('labels', {})
print('labels_db_count:', len(labels_db))
for k, v in labels_db.items():
    print('DB KEY:', k)
    print('  session:', v.get('session'))
    print('  file_a:', v.get('file_a'))
    print('  file_b:', v.get('file_b'))
    print('  label:', v.get('label'))


