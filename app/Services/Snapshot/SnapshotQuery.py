import os
from datetime import datetime
from collections import defaultdict

from app.Services.Snapshot.SnapshotState import SnapshotState
from app.Models.Database import db_manager

QUERIES_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'database', 'queries')
PARALLEL_DIR = os.path.join(QUERIES_DIR, 'parallel')

def _load_sql(filename):
    path = os.path.join(PARALLEL_DIR, filename)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

class SnapshotQuery:
    @classmethod
    def _like_match(cls, value, pattern):
        if not pattern:
            return True
        val = (value or '').lower()
        pat = pattern.lower()

        if pat.startswith('%') and pat.endswith('%'):
            return pat[1:-1] in val
        elif pat.endswith('%'):
            return val.startswith(pat[:-1])
        elif pat.startswith('%'):
            return val.endswith(pat[1:])
        else:
            return val == pat

    @classmethod
    def search_perhitungan(cls, server_key, search_kode=None, search_nama=None, divisi=None,
                           kategori=None, merk=None, limit=None, offset=None,
                           sort_by=None, sort_order='asc'):
        with SnapshotState._cache_lock:
            cache_data = SnapshotState._perhitungan_cache.get(server_key, {})
            data = cache_data.get('data', [])

        filtered = []
        search_kode_lower = (search_kode or '').lower()
        search_nama_lower = (search_nama or '').lower()

        for row in data:
            if search_kode_lower or search_nama_lower:
                match = False
                if search_kode_lower and (row.get('kd_barang', '') or '').lower().startswith(search_kode_lower):
                    match = True
                if search_nama_lower:
                    name_val = (row.get('barang', '') or '').lower()
                    if (name_val.startswith(search_nama_lower) or 
                        f" {search_nama_lower}" in name_val or 
                        f"-{search_nama_lower}" in name_val or 
                        f"/{search_nama_lower}" in name_val):
                        match = True
                if not match:
                    continue

            if divisi and (row.get('divisi', '') or '').lower() != divisi.lower():
                continue
            if kategori and (row.get('kategori', '') or '').lower() != kategori.lower():
                continue
            if merk and (row.get('merk', '') or '').lower() != merk.lower():
                continue

            filtered.append(row)

        total_items = len(filtered)
        total_nominal = 0
        total_stok = 0
        divisi_set = set()

        for r in filtered:
            stok = r.get('stok_akhir', 0)
            nominal = r.get('nominal', 0)
            total_nominal += nominal
            total_stok += stok
            div = r.get('divisi')
            if div:
                divisi_set.add(div)

        sort_key_map = {
            'divisi': 'divisi', 'barang': 'barang', 'kd_barang': 'kd_barang',
            'kategori': 'kategori', 'merk': 'merk', 'stok_akhir': 'stok_akhir',
            'harga_jual': 'harga_jual', 'harga_avg': 'harga_avg',
            'harga_beli_akhir': 'harga_beli_akhir', 'ukuran': 'ukuran', 'nominal': 'nominal'
        }
        if sort_by and sort_by in sort_key_map:
            key_field = sort_key_map[sort_by]
            reverse = sort_order.lower() == 'desc'
            filtered.sort(key=lambda r: (r.get(key_field) is None, r.get(key_field, '')), reverse=reverse)

        if offset is not None:
            filtered = filtered[offset:]
        if limit is not None:
            filtered = filtered[:limit]

        return {
            'status': 'success',
            'data': filtered,
            'summary': {
                'total_items': total_items,
                'divisi_count': len(divisi_set), 'divisi_list': sorted(list(divisi_set)),
                'total_nominal': round(total_nominal, 2),
                'avg_stok': round(total_stok / total_items, 2) if total_items > 0 else 0
            },
            'source': 'memory'
        }

    @classmethod
    def get_divisi_list(cls, server_key):
        with SnapshotState._cache_lock:
            cache_data = SnapshotState._perhitungan_cache.get(server_key, {})
            data = cache_data.get('data', [])
        return sorted(list(set(r.get('divisi', '') for r in data if r.get('divisi'))))

    @classmethod
    def get_barang_histori(cls, server_key, kd_barang, kd_divisi, start_date=None, end_date=None):
        try:
            sql = _load_sql('11_barang_histori.sql')
            conn = db_manager.create_new_connection(server_key)
            try:
                cursor = conn.cursor()
                cursor.execute(sql, [kd_barang, kd_divisi or ''])
                columns = [desc[0] for desc in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                cursor.close()
            finally:
                conn.close()

            final_data = []
            for row in rows:
                tgl = row.get('tanggal')
                if isinstance(tgl, datetime):
                    tgl = tgl.strftime('%Y-%m-%d %H:%M:%S')

                final_data.append({
                    'Kd_Divisi': row.get('Kd_Divisi', ''),
                    'Divisi': row.get('Divisi', ''),
                    'K.Nota': row.get('K.Nota', ''),
                    'tanggal': tgl,
                    'Transaksi': row.get('Transaksi', ''),
                    'no_transaksi': row.get('no_transaksi', ''),
                    'kd_barang': row.get('kd_barang', ''),
                    'barang': row.get('barang', ''),
                    'Debet': float(row.get('Debet') or 0),
                    'Kredit': float(row.get('Kredit') or 0),
                    'kd_satuan': row.get('kd_satuan', ''),
                    'satuan': row.get('satuan', ''),
                    'harga': float(row.get('harga') or 0),
                    'Konversi': float(row.get('Konversi') or 1),
                })

            if start_date or end_date:
                filtered_data = []
                for r in final_data:
                    tgl_str = r['tanggal']
                    if not tgl_str:
                        continue
                    r_date = datetime.strptime(tgl_str.split(' ')[0], '%Y-%m-%d').date()
                    if start_date:
                        s_date = datetime.strptime(start_date, '%Y-%m-%d').date()
                        if r_date < s_date:
                            continue
                    if end_date:
                        e_date = datetime.strptime(end_date, '%Y-%m-%d').date()
                        if r_date > e_date:
                            continue
                    filtered_data.append(r)
                return filtered_data
            return final_data

        except Exception as e:
            import traceback
            traceback.print_exc()
            return []

    @classmethod
    def get_barang_tanpa_transaksi(cls, server_key, stok_filter='all'):
        try:
            sql = _load_sql('21_barang_tanpa_transaksi.sql')
            conn = db_manager.create_new_connection(server_key)
            try:
                cursor = conn.cursor()
                cursor.execute(sql)
                columns = [desc[0] for desc in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                cursor.close()
            finally:
                conn.close()

            final_data = []
            for row in rows:
                stok = float(row.get('stok_akhir') or 0)
                if stok_filter == 'positive' and stok <= 0:
                    continue
                if stok_filter == 'zero' and stok != 0:
                    continue
                if stok_filter == 'negative' and stok >= 0:
                    continue

                tgl = row.get('tgl_trans_terakhir')
                if isinstance(tgl, datetime):
                    tgl = tgl.strftime('%Y-%m-%d')

                final_data.append({
                    'kd_barang': row.get('kd_barang', ''),
                    'nama_barang': row.get('nama_barang', ''),
                    'kategori': row.get('kategori', ''),
                    'merk': row.get('merk', ''),
                    'stok_akhir': stok,
                    'tgl_trans_terakhir': tgl,
                    'hari_tanpa_transaksi': int(row.get('hari_tanpa_transaksi') or 0),
                    'jenis_trans_terakhir': row.get('jenis_trans_terakhir', ''),
                })
            return final_data
        except Exception as e:
            print(f"[ERROR] get_barang_tanpa_transaksi: {e}")
            return []

    @classmethod
    def get_barang_dengan_transaksi(cls, server_key, jenis_transaksi='Semua', start_year=None, end_year=None):
        try:
            sql = _load_sql('22_barang_dengan_transaksi.sql')
            conn = db_manager.create_new_connection(server_key)
            try:
                cursor = conn.cursor()
                cursor.execute(sql)
                columns = [desc[0] for desc in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                cursor.close()
            finally:
                conn.close()

            final_data = []
            for row in rows:
                j_trans = row.get('jenis_transaksi', '')
                if jenis_transaksi != 'Semua' and j_trans.lower() != jenis_transaksi.lower():
                    continue

                tahun = int(row.get('tahun') or 0)
                if start_year and tahun < int(start_year):
                    continue
                if end_year and tahun > int(end_year):
                    continue

                final_data.append({
                    'kd_barang': row.get('kd_barang', ''),
                    'nama_barang': row.get('nama_barang', ''),
                    'kategori': row.get('kategori', ''),
                    'jenis_transaksi': j_trans,
                    'tahun': tahun,
                    'total_qty': float(row.get('total_qty') or 0),
                    'total_nominal': float(row.get('total_nominal') or 0),
                    'freq_transaksi': int(row.get('freq_transaksi') or 0)
                })
            return final_data
        except Exception as e:
            print(f"[ERROR] get_barang_dengan_transaksi: {e}")
            return []

    @classmethod
    def get_semua_barang_stok_awal(cls, server_key, stok_filter='all'):
        try:
            sql = _load_sql('24_semua_barang_stok_awal.sql')
            conn = db_manager.create_new_connection(server_key)
            try:
                cursor = conn.cursor()
                cursor.execute(sql)
                columns = [desc[0] for desc in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                cursor.close()
            finally:
                conn.close()

            final_data = []
            for row in rows:
                stok = float(row.get('stok_awal') or 0)
                if stok_filter == 'positive' and stok <= 0:
                    continue
                if stok_filter == 'zero' and stok != 0:
                    continue
                if stok_filter == 'negative' and stok >= 0:
                    continue

                final_data.append({
                    'kd_barang': row.get('kd_barang', ''),
                    'nama_barang': row.get('nama_barang', ''),
                    'kategori': row.get('kategori', ''),
                    'merk': row.get('merk', ''),
                    'kd_divisi': row.get('kd_divisi', ''),
                    'nama_divisi': row.get('nama_divisi', ''),
                    'stok_awal': stok
                })
            return final_data
        except Exception as e:
            print(f"[ERROR] get_semua_barang_stok_awal: {e}")
            return []

    @classmethod
    def get_bulk_transaksi_detail(cls, server_key, jenis_transaksi='Semua', start_year=None, end_year=None):
        try:
            sql = _load_sql('23_bulk_transaksi_detail.sql')
            conn = db_manager.create_new_connection(server_key)
            try:
                cursor = conn.cursor()
                cursor.execute(sql)
                columns = [desc[0] for desc in cursor.description]
                rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                cursor.close()
            finally:
                conn.close()

            final_data = []
            for row in rows:
                j_trans = row.get('jenis_transaksi', '')
                if jenis_transaksi != 'Semua' and j_trans.lower() != jenis_transaksi.lower():
                    continue

                tahun = int(row.get('tahun') or 0)
                if start_year and tahun < int(start_year):
                    continue
                if end_year and tahun > int(end_year):
                    continue

                tgl = row.get('tanggal')
                if isinstance(tgl, datetime):
                    tgl = tgl.strftime('%Y-%m-%d %H:%M:%S')

                final_data.append({
                    'no_transaksi': row.get('no_transaksi', ''),
                    'tanggal': tgl,
                    'jenis_transaksi': j_trans,
                    'kd_barang': row.get('kd_barang', ''),
                    'nama_barang': row.get('nama_barang', ''),
                    'qty': float(row.get('qty') or 0),
                    'harga': float(row.get('harga') or 0),
                    'total': float(row.get('total') or 0),
                    'kd_divisi': row.get('kd_divisi', ''),
                    'tahun': tahun
                })
            return final_data
        except Exception as e:
            print(f"[ERROR] get_bulk_transaksi_detail: {e}")
            return []

    @classmethod
    def get_item_stock_detail(cls, server_key, kd_barang):
        # We can extract it from the in-memory cache directly!
        with SnapshotState._cache_lock:
            cache_data = SnapshotState._perhitungan_cache.get(server_key, {})
            data = cache_data.get('data', [])
            sat_data = cache_data.get('satuan', [])

        stok_rows = [r for r in data if r.get('kd_barang') == kd_barang]
        satuans = [r for r in sat_data if r.get('kd_barang') == kd_barang]

        divisi_list = []
        for r in stok_rows:
            divisi_list.append({
                'kd_divisi': r.get('kd_divisi', ''),
                'divisi': r.get('divisi', ''),
                'stok_akhir': r.get('stok_akhir', 0),
                'harga_jual': r.get('harga_jual', 0),
                'harga_beli_akhir': r.get('harga_beli_akhir', 0),
                'harga_avg': r.get('harga_avg', 0),
            })

        info = {}
        if stok_rows:
            first = stok_rows[0]
            info = {
                'kd_barang': first.get('kd_barang', ''),
                'barang': first.get('barang', ''),
                'kategori': first.get('kategori', ''),
                'merk': first.get('merk', ''),
                'model': first.get('model', ''),
                'warna': first.get('warna', ''),
                'ukuran': first.get('ukuran', ''),
                'total_stok': sum(r.get('stok_akhir', 0) for r in stok_rows),
            }

        return {
            'status': 'success',
            'info': info,
            'divisi_list': divisi_list,
            'satuans': satuans,
        }
