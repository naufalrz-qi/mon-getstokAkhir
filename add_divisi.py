import os
file_path = 'app/Services/Snapshot/SnapshotQuery.py'
with open(file_path, 'r', encoding='utf-8') as f:
    text = f.read()

text = text.replace(
    "'divisi_count': len(divisi_set),",
    "'divisi_count': len(divisi_set), 'divisi_list': sorted(list(divisi_set)),"
)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(text)
