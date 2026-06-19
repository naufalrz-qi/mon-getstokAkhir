import re

file_path = 'app/Http/Controllers/StokController.py'
methods_to_remove = {
    'trigger_refresh', 'trigger_refresh_target', 'trigger_delta_refresh', 
    'trigger_delta_refresh_target', 'trigger_weekly_refresh', 'trigger_weekly_refresh_target', 
    'trigger_yearly_refresh', 'trigger_yearly_refresh_target', 'snapshot_status', 
    'snapshot_status_target', 'cancel_refresh', 'global_snapshot_status', 'mass_refresh_page', 
    'sync_duckdb_page', 'mass_sync_duckdb_page', 'trigger_duckdb_sync', 'check_duckdb_status', 
    'trigger_mass_duckdb_sync', 'check_mass_duckdb_status'
}

with open(file_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
skip = False
for line in lines:
    if re.match(r'^\s*@staticmethod', line):
        continue  # We will add it back when we hit the def if we keep it
    
    m = re.match(r'^\s*def\s+([a-zA-Z0-9_]+)\s*\(', line)
    if m:
        method_name = m.group(1)
        if method_name in methods_to_remove:
            skip = True
        else:
            skip = False
            new_lines.append('    @staticmethod\n')
            new_lines.append(line)
    elif not skip:
        new_lines.append(line)

with open(file_path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
