import os
import sqlite3
import threading
import time
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal

from app.Services.Snapshot.SnapshotState import SnapshotState

QUERIES_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'database', 'queries')
PARALLEL_DIR = os.path.join(QUERIES_DIR, 'parallel')
SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'database', 'snapshots')

def _load_sql(filename):
    path = os.path.join(PARALLEL_DIR, filename)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

class SnapshotQuery:
    @classmethod
    def search(cls, server_key, search_kode=None, search_nama=None, divisi=None,
                   kategori=None, merk=None, limit=None, offset=None,
                   sort_by=None, sort_order='asc'):
            # Try memory cache first
            with SnapshotState._cache_lock:
                has_cache = server_key in SnapshotState._mem_cache
            if has_cache:
                data = cls._filter_memory(server_key, search_kode, search_nama, divisi, kategori, merk)
                return cls._build_result(data, source='memory', limit=limit, offset=offset, sort_by=sort_by, sort_order=sort_order)
    
            # Try loading from SQLite
            db_path = cls._db_path(server_key)
            if os.path.exists(db_path):
                cls._load_to_memory(server_key)
                if server_key in SnapshotState._mem_cache:
                    data = cls._filter_memory(server_key, search_kode, search_nama, divisi, kategori, merk)
                    return cls._build_result(data, source='sqlite', limit=limit, offset=offset, sort_by=sort_by, sort_order=sort_order)
    
            return {
                'status': 'no_snapshot',
                'data': [],
                'summary': {'total_items': 0, 'divisi_count': 0, 'total_nominal': 0, 'avg_stok': 0},
                'message': 'Belum ada snapshot. Klik Refresh untuk memuat data.',
            }

    @classmethod
    def search_opname(cls, server_key, search_kode=None, search_nama=None, divisi=None, **kwargs):
            db_path = cls._db_path(server_key)
            if not os.path.exists(db_path):
                return {
                    'status': 'no_snapshot',
                    'data': [],
                    'message': 'Belum ada snapshot. Klik Refresh untuk memuat data.',
                }
                
            status_filter = kwargs.get('status')
            sort_by = kwargs.get('sort_by', 'tanggal')
            sort_order = kwargs.get('sort_order', 'desc')
    
            try:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                
                # Fetch opname data
                query = "SELECT * FROM opname_snapshot WHERE 1=1"
                params = []
                
                if search_kode:
                    query += " AND kd_barang LIKE ?"
                    params.append(f"{search_kode}%")
                    
                if search_nama:
                    query += " AND (barang LIKE ? OR barang LIKE ? OR barang LIKE ? OR barang LIKE ?)"
                    params.extend([f"{search_nama}%", f"% {search_nama}%", f"%-{search_nama}%", f"%/{search_nama}%"])
                    
                if divisi:
                    query += " AND divisi = ?"
                    params.append(divisi)
    
                if status_filter:
                    query += " AND status_text = ?"
                    params.append(status_filter)
    
                # Dynamic sorting
                allowed_sort = {'tanggal', 'barang', 'divisi', 'qty', 'status_text', 'petugas', 'tanggal_server'}
                col = sort_by if sort_by in allowed_sort else 'tanggal'
                direction = 'ASC' if sort_order.lower() == 'asc' else 'DESC'
                query += f" ORDER BY {col} {direction}"
                
                opname_rows = conn.execute(query, params).fetchall()
                
                # Fetch all satuan data for the items in the result
                kd_barangs = list(set(r['kd_barang'] for r in opname_rows))
                satuan_dict = defaultdict(list)
                
                if kd_barangs:
                    placeholders = ','.join('?' for _ in kd_barangs)
                    try:
                        sat_query = f"SELECT kd_barang, kd_satuan, jumlah, nama_satuan FROM satuan_snapshot WHERE kd_barang IN ({placeholders})"
                        sat_rows = conn.execute(sat_query, kd_barangs).fetchall()
                        for sr in sat_rows:
                            satuan_dict[sr['kd_barang']].append({
                                'kd_satuan': sr['kd_satuan'],
                                'jumlah': sr['jumlah'],
                                'nama_satuan': sr['nama_satuan']
                            })
                    except sqlite3.OperationalError:
                        # Fallback for old snapshots without nama_satuan column
                        sat_query = f"SELECT kd_barang, kd_satuan, jumlah FROM satuan_snapshot WHERE kd_barang IN ({placeholders})"
                        sat_rows = conn.execute(sat_query, kd_barangs).fetchall()
                        for sr in sat_rows:
                            satuan_dict[sr['kd_barang']].append({
                                'kd_satuan': sr['kd_satuan'],
                                'jumlah': sr['jumlah'],
                                'nama_satuan': sr['kd_satuan']
                            })
    
                conn.close()
    
                data = []
                for r in opname_rows:
                    row_dict = dict(r)
                    row_dict['satuans'] = satuan_dict.get(r['kd_barang'], [])
                    data.append(row_dict)
    
                return {
                    'status': 'success',
                    'data': data,
                    'row_count': len(data),
                    'source': 'sqlite'
                }
    
            except sqlite3.OperationalError as e:
                if 'no such table' in str(e).lower():
                    return {
                        'status': 'no_snapshot',
                        'data': [],
                        'message': 'Data opname belum tersedia. Silakan lakukan Full Refresh di Dashboard terlebih dahulu.'
                    }
                import traceback
                traceback.print_exc()
                return {
                    'status': 'error',
                    'message': str(e)
                }
            except Exception as e:
                import traceback
                traceback.print_exc()
                return {
                    'status': 'error',
                    'message': str(e)
                }

    @classmethod
    def _like_match(cls, value, pattern):
            """Emulate SQL LIKE pattern matching."""
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
    def _filter_memory(cls, server_key, search_kode=None, search_nama=None, divisi=None,
                           kategori=None, merk=None):
            data = SnapshotState._mem_cache.get(server_key, [])
            filtered = []
    
            search_kode_lower = (search_kode or '').lower()
            search_nama_lower = (search_nama or '').lower()
    
            for row in data:
                # Smart Search filter
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
    
                # Divisi filter
                if divisi and (row.get('divisi', '') or '').lower() != divisi.lower():
                    continue
    
                # Kategori filter
                if kategori and (row.get('kategori', '') or '').lower() != kategori.lower():
                    continue
    
                # Merk filter
                if merk and (row.get('merk', '') or '').lower() != merk.lower():
                    continue
    
                filtered.append(row)
    
            return filtered

    @classmethod
    def _build_result(cls, data, source='memory', limit=None, offset=None, sort_by=None, sort_order='asc'):
            total_items = len(data)
            total_nominal = 0
            total_stok = 0
            divisi_set = set()
    
            for r in data:
                stok = r.get('stok_akhir', 0)
                h_avg = r.get('harga_avg', 0)
                h_beli = r.get('harga_beli_akhir', 0)
                nominal = round(stok * h_avg, 2) if h_avg else round(stok * h_beli, 2)
                
                total_nominal += nominal
                total_stok += stok
                divisi = r.get('divisi')
                if divisi:
                    divisi_set.add(divisi)
    
            # Dynamic sorting
            sort_key_map = {
                'divisi': 'divisi', 'barang': 'barang', 'kd_barang': 'kd_barang',
                'kategori': 'kategori', 'merk': 'merk', 'stok_akhir': 'stok_akhir',
                'harga_jual': 'harga_jual', 'harga_avg': 'harga_avg',
                'harga_beli_akhir': 'harga_beli_akhir', 'ukuran': 'ukuran',
            }
            if sort_by and sort_by in sort_key_map:
                key_field = sort_key_map[sort_by]
                reverse = sort_order.lower() == 'desc'
                data.sort(key=lambda r: (r.get(key_field) is None, r.get(key_field, '')), reverse=reverse)
    
            if offset is not None:
                data = data[offset:]
            if limit is not None:
                data = data[:limit]
    
            mapped = []
            for r in data:
                stok = r.get('stok_akhir', 0)
                h_avg = r.get('harga_avg', 0)
                h_beli = r.get('harga_beli_akhir', 0)
                nominal = round(stok * h_avg, 2) if h_avg else round(stok * h_beli, 2)
                mapped.append({
                    'Kode Divisi': r.get('kd_divisi', ''),
                    'Divisi': r.get('divisi', ''),
                    'Kode Barang': r.get('kd_barang', ''),
                    'Barang': r.get('barang', ''),
                    'Kategori': r.get('kategori', ''),
                    'Merk': r.get('merk', ''),
                    'Model': r.get('model', ''),
                    'Warna': r.get('warna', ''),
                    'Ukuran': r.get('ukuran', ''),
                    'Stok Akhir': stok,
                    'Harga Average': round(h_avg, 2),
                    'Harga Jual': r.get('harga_jual', 0),
                    'Nominal': nominal,
                    'Harga Beli Akhir': h_beli,
                })
    
            return {
                'status': 'success',
                'data': mapped,
                'summary': {
                    'total_items': total_items,
                    'total_nominal': round(total_nominal, 2),
                    'divisi_count': len(divisi_set),
                    'avg_stok': round(total_stok / total_items, 2) if total_items else 0,
                    'divisi_list': sorted(list(divisi_set)),
                },
                'row_count': len(mapped),
                'source': source,
                'limit': limit,
                'offset': offset
            }

    @classmethod
    def get_divisi_list(cls, server_key):
            """Return distinct divisi names — lightweight, no full data load."""
            # Try memory cache first
            with SnapshotState._cache_lock:
                if server_key in SnapshotState._mem_cache:
                    divisi_set = set(r.get('divisi', '') for r in SnapshotState._mem_cache[server_key] if r.get('divisi'))
                    return sorted(divisi_set)
    
            # Fallback to SQLite
            db_path = cls._db_path(server_key)
            if os.path.exists(db_path):
                try:
                    conn = sqlite3.connect(db_path)
                    rows = conn.execute('SELECT DISTINCT divisi FROM stok_snapshot WHERE divisi IS NOT NULL ORDER BY divisi').fetchall()
                    conn.close()
                    return [r[0] for r in rows]
                except Exception:
                    pass
            return []

    @classmethod
    def get_status(cls, server_key):
            status = SnapshotState._refresh_status.get(server_key)
    
            db_path = cls._db_path(server_key)
            has_snapshot = os.path.exists(db_path)
    
            snapshot_info = {}
            if has_snapshot:
                try:
                    conn = sqlite3.connect(db_path)
                    for key, value in conn.execute('SELECT key, value FROM snapshot_meta').fetchall():
                        snapshot_info[key] = value
                    conn.close()
                except Exception:
                    pass
    
            if status and status['state'] in ('starting', 'fetching', 'writing'):
                return {
                    'state': status['state'],
                    'progress': status['progress'],
                    'message': status['message'],
                    'has_snapshot': has_snapshot,
                    'snapshot_info': snapshot_info,
                }
    
            in_memory = server_key in SnapshotState._mem_cache
            mem_count = len(SnapshotState._mem_cache.get(server_key, []))
    
            return {
                'state': status['state'] if status else ('ready' if has_snapshot else 'empty'),
                'progress': 100 if has_snapshot else 0,
                'message': status['message'] if status else (
                    f'{mem_count} item dimuat dari snapshot' if in_memory else
                    'Snapshot tersedia di disk' if has_snapshot else
                    'Belum ada snapshot'
                ),
                'has_snapshot': has_snapshot,
                'in_memory': in_memory,
                'mem_count': mem_count,
                'snapshot_info': snapshot_info,
            }

    @classmethod
    def get_barang_tanpa_transaksi(cls, server_key, stok_filter='all'):
            """
            Fetch items that have no transactions after the last book closing date.
            stok_filter options: 'gt_zero' (> 0), 'gte_zero' (>= 0), 'all' (no filter)
            """
            from app.Models.Database import db_manager
            
            stok_condition = ""
            if stok_filter == 'gt_zero':
                stok_condition = "AND bd.stok_awal > 0"
            elif stok_filter == 'gte_zero':
                stok_condition = "AND bd.stok_awal >= 0"
    
            query = f"""
            DECLARE @TglTutupBuku DATETIME = dbo.GetTanggalTerakhirTutupBuku();
            
            WITH cte_transaksi AS (
                -- 2. Mutasi Keluar
                SELECT t.kd_divisi_asal AS kd_divisi, d.kd_barang
                FROM t_mutasi_stok_detail d (NOLOCK)
                INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                WHERE t.tanggal > @TglTutupBuku
                
                UNION ALL 
                -- 3. Mutasi Masuk
                SELECT t.kd_divisi_tujuan AS kd_divisi, d.kd_barang
                FROM t_mutasi_stok_detail d (NOLOCK)
                INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                WHERE t.tanggal > @TglTutupBuku
                
                UNION ALL
                -- 4 & 5. Opname
                SELECT kd_divisi, kd_barang
                FROM t_opname_stok (NOLOCK)
                WHERE tanggal > @TglTutupBuku
                
                UNION ALL
                -- 6. Pembelian
                SELECT t.kd_divisi, d.kd_barang
                FROM t_pembelian_detail d (NOLOCK)
                INNER JOIN t_pembelian t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                WHERE t.tanggal > @TglTutupBuku AND t.status IN (0, 1)
                
                UNION ALL
                -- 7. Retur Pembelian
                SELECT t.kd_divisi, d.kd_barang
                FROM t_pembelian_retur_detail d (NOLOCK)
                INNER JOIN t_pembelian_retur t (NOLOCK) ON d.no_retur = t.no_retur
                WHERE t.tanggal > @TglTutupBuku
                
                UNION ALL
                -- 8. Penjualan
                SELECT t.kd_divisi, d.kd_barang
                FROM t_penjualan_detail d (NOLOCK)
                INNER JOIN t_penjualan t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                INNER JOIN m_barang b (NOLOCK) ON d.kd_barang = b.kd_barang
                INNER JOIN m_kategori k (NOLOCK) ON b.kd_kategori = k.kd_kategori
                WHERE t.tanggal > @TglTutupBuku AND k.status <> 2
                
                UNION ALL
                -- 9. Retur Penjualan
                SELECT t.kd_divisi, d.kd_barang
                FROM t_penjualan_retur_detail d (NOLOCK)
                INNER JOIN t_penjualan_retur t (NOLOCK) ON d.no_retur = t.no_retur
                WHERE t.tanggal > @TglTutupBuku
            )
            SELECT 
                bd.kd_divisi,
                bd.kd_barang,
                b.nama AS nama_barang,
                bd.stok_awal
            FROM m_barang_divisi bd (NOLOCK)
            INNER JOIN m_barang b (NOLOCK) ON bd.kd_barang = b.kd_barang
            WHERE NOT EXISTS (
                SELECT 1 FROM cte_transaksi ct 
                WHERE ct.kd_barang = bd.kd_barang AND ct.kd_divisi = bd.kd_divisi
            )
            {stok_condition}
            ORDER BY bd.kd_divisi, bd.kd_barang;
            """
            
            try:
                results = db_manager.execute_query(server_key, query)
                return {'status': 'success', 'data': results}
            except Exception as e:
                return {'status': 'error', 'message': str(e)}

    @classmethod
    def get_barang_dengan_transaksi(cls, server_key, jenis_transaksi='Semua', start_year=None, end_year=None):
            """
            Fetch items that have transactions of a specific type within a year range.
            """
            from app.Models.Database import db_manager
            from datetime import datetime
    
            if not start_year:
                start_year = str(datetime.now().year)
            if not end_year:
                end_year = str(datetime.now().year)
                
            start_date = f"{start_year}-01-01 00:00:00"
            end_date = f"{end_year}-12-31 23:59:59"
    
            cte_parts = []
            
            if jenis_transaksi in ('Semua', 'Mutasi Keluar'):
                cte_parts.append("""
                SELECT t.kd_divisi_asal AS kd_divisi, d.kd_barang
                FROM t_mutasi_stok_detail d (NOLOCK)
                INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                WHERE t.tanggal BETWEEN @StartDate AND @EndDate
                """)
                
            if jenis_transaksi in ('Semua', 'Mutasi Masuk'):
                cte_parts.append("""
                SELECT t.kd_divisi_tujuan AS kd_divisi, d.kd_barang
                FROM t_mutasi_stok_detail d (NOLOCK)
                INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                WHERE t.tanggal BETWEEN @StartDate AND @EndDate
                """)
                
            if jenis_transaksi in ('Semua', 'Opname', 'Opname Masuk'):
                cond = " AND status = 2" if jenis_transaksi == 'Opname Masuk' else ""
                cte_parts.append(f"""
                SELECT kd_divisi, kd_barang
                FROM t_opname_stok (NOLOCK)
                WHERE tanggal BETWEEN @StartDate AND @EndDate{cond}
                """)
                
            if jenis_transaksi == 'Opname Keluar':
                cte_parts.append("""
                SELECT kd_divisi, kd_barang
                FROM t_opname_stok (NOLOCK)
                WHERE tanggal BETWEEN @StartDate AND @EndDate AND status <> 2
                """)
                
            if jenis_transaksi in ('Semua', 'Pembelian'):
                cte_parts.append("""
                SELECT t.kd_divisi, d.kd_barang
                FROM t_pembelian_detail d (NOLOCK)
                INNER JOIN t_pembelian t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                WHERE t.tanggal BETWEEN @StartDate AND @EndDate AND t.status IN (0, 1)
                """)
                
            if jenis_transaksi in ('Semua', 'Retur Pembelian'):
                cte_parts.append("""
                SELECT t.kd_divisi, d.kd_barang
                FROM t_pembelian_retur_detail d (NOLOCK)
                INNER JOIN t_pembelian_retur t (NOLOCK) ON d.no_retur = t.no_retur
                WHERE t.tanggal BETWEEN @StartDate AND @EndDate
                """)
                
            if jenis_transaksi in ('Semua', 'Penjualan'):
                cte_parts.append("""
                SELECT t.kd_divisi, d.kd_barang
                FROM t_penjualan_detail d (NOLOCK)
                INNER JOIN t_penjualan t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                INNER JOIN m_barang b (NOLOCK) ON d.kd_barang = b.kd_barang
                INNER JOIN m_kategori k (NOLOCK) ON b.kd_kategori = k.kd_kategori
                WHERE t.tanggal BETWEEN @StartDate AND @EndDate AND k.status <> 2
                """)
                
            if jenis_transaksi in ('Semua', 'Retur Penjualan'):
                cte_parts.append("""
                SELECT t.kd_divisi, d.kd_barang
                FROM t_penjualan_retur_detail d (NOLOCK)
                INNER JOIN t_penjualan_retur t (NOLOCK) ON d.no_retur = t.no_retur
                WHERE t.tanggal BETWEEN @StartDate AND @EndDate
                """)
    
            union_query = " UNION ALL ".join(cte_parts)
            
            query = f"""
            DECLARE @StartDate DATETIME = '{start_date}';
            DECLARE @EndDate DATETIME = '{end_date}';
            
            WITH cte_transaksi AS (
                {union_query}
            )
            SELECT 
                ct.kd_divisi,
                ct.kd_barang,
                MAX(b.nama) AS nama_barang
            FROM cte_transaksi ct
            INNER JOIN m_barang b (NOLOCK) ON ct.kd_barang = b.kd_barang
            GROUP BY ct.kd_divisi, ct.kd_barang
            ORDER BY ct.kd_divisi, ct.kd_barang;
            """
            
            try:
                results = db_manager.execute_query(server_key, query)
                return {'status': 'success', 'data': results}
            except Exception as e:
                return {'status': 'error', 'message': str(e)}

    @classmethod
    def get_bulk_transaksi_detail(cls, server_key, jenis_transaksi='Semua', start_year=None, end_year=None):
            """
            Fetch ALL detail transaction rows for a specific type within a year range.
            Used for bulk export.
            """
            from app.Models.Database import db_manager
            from datetime import datetime
    
            if not start_year:
                start_year = str(datetime.now().year)
            if not end_year:
                end_year = str(datetime.now().year)
                
            start_date = f"{start_year}-01-01 00:00:00"
            end_date = f"{end_year}-12-31 23:59:59"
    
            cte_parts = []
            
            if jenis_transaksi in ('Semua', 'Mutasi Keluar'):
                cte_parts.append("""
                SELECT t.kd_divisi_asal AS kd_divisi, d.kd_barang, t.tanggal, 'Mutasi Keluar' AS jenis_transaksi, d.no_transaksi, d.qty, d.kd_satuan, 0.0 AS harga
                FROM t_mutasi_stok_detail d (NOLOCK)
                INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                WHERE t.tanggal BETWEEN @StartDate AND @EndDate
                """)
                
            if jenis_transaksi in ('Semua', 'Mutasi Masuk'):
                cte_parts.append("""
                SELECT t.kd_divisi_tujuan AS kd_divisi, d.kd_barang, t.tanggal, 'Mutasi Masuk' AS jenis_transaksi, d.no_transaksi, d.qty, d.kd_satuan, 0.0 AS harga
                FROM t_mutasi_stok_detail d (NOLOCK)
                INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                WHERE t.tanggal BETWEEN @StartDate AND @EndDate
                """)
                
            if jenis_transaksi in ('Semua', 'Opname', 'Opname Masuk'):
                cond = " AND status = 2" if jenis_transaksi == 'Opname Masuk' else (" AND status = 2" if jenis_transaksi == 'Semua' else "")
                if jenis_transaksi in ('Semua', 'Opname', 'Opname Masuk'):
                    cte_parts.append(f"""
                    SELECT kd_divisi, kd_barang, tanggal, 'Opname Masuk' AS jenis_transaksi, no_transaksi, qty, kd_satuan, 0.0 AS harga
                    FROM t_opname_stok (NOLOCK)
                    WHERE tanggal BETWEEN @StartDate AND @EndDate AND status = 2
                    """)
                
            if jenis_transaksi in ('Semua', 'Opname', 'Opname Keluar'):
                cte_parts.append(f"""
                SELECT kd_divisi, kd_barang, tanggal, 'Opname Keluar' AS jenis_transaksi, no_transaksi, qty, kd_satuan, 0.0 AS harga
                FROM t_opname_stok (NOLOCK)
                WHERE tanggal BETWEEN @StartDate AND @EndDate AND status <> 2
                """)
                
            if jenis_transaksi in ('Semua', 'Pembelian'):
                cte_parts.append("""
                SELECT t.kd_divisi, d.kd_barang, t.tanggal, 'Pembelian' AS jenis_transaksi, d.no_transaksi, d.qty, d.kd_satuan, d.harga_beli AS harga
                FROM t_pembelian_detail d (NOLOCK)
                INNER JOIN t_pembelian t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                WHERE t.tanggal BETWEEN @StartDate AND @EndDate AND t.status IN (0, 1)
                """)
                
            if jenis_transaksi in ('Semua', 'Retur Pembelian'):
                cte_parts.append("""
                SELECT t.kd_divisi, d.kd_barang, t.tanggal, 'Retur Pembelian' AS jenis_transaksi, d.no_retur AS no_transaksi, d.qty, d.kd_satuan, d.harga AS harga
                FROM t_pembelian_retur_detail d (NOLOCK)
                INNER JOIN t_pembelian_retur t (NOLOCK) ON d.no_retur = t.no_retur
                WHERE t.tanggal BETWEEN @StartDate AND @EndDate
                """)
                
            if jenis_transaksi in ('Semua', 'Penjualan'):
                cte_parts.append("""
                SELECT t.kd_divisi, d.kd_barang, t.tanggal, 'Penjualan' AS jenis_transaksi, d.no_transaksi, d.qty, d.kd_satuan, d.harga_jual AS harga
                FROM t_penjualan_detail d (NOLOCK)
                INNER JOIN t_penjualan t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                INNER JOIN m_barang b (NOLOCK) ON d.kd_barang = b.kd_barang
                INNER JOIN m_kategori k (NOLOCK) ON b.kd_kategori = k.kd_kategori
                WHERE t.tanggal BETWEEN @StartDate AND @EndDate AND k.status <> 2
                """)
                
            if jenis_transaksi in ('Semua', 'Retur Penjualan'):
                cte_parts.append("""
                SELECT t.kd_divisi, d.kd_barang, t.tanggal, 'Retur Penjualan' AS jenis_transaksi, d.no_retur AS no_transaksi, d.qty, d.kd_satuan, d.harga_jual AS harga
                FROM t_penjualan_retur_detail d (NOLOCK)
                INNER JOIN t_penjualan_retur t (NOLOCK) ON d.no_retur = t.no_retur
                WHERE t.tanggal BETWEEN @StartDate AND @EndDate
                """)
    
            union_query = " UNION ALL ".join(cte_parts)
            
            query = f"""
            DECLARE @StartDate DATETIME = '{start_date}';
            DECLARE @EndDate DATETIME = '{end_date}';
            
            WITH cte_transaksi AS (
                {union_query}
            )
            SELECT 
                ct.kd_divisi,
                div.keterangan AS nama_divisi,
                ct.kd_barang,
                b.nama AS nama_barang,
                ct.tanggal,
                ct.jenis_transaksi,
                ct.no_transaksi,
                ct.qty,
                ct.kd_satuan,
                s.nama AS nama_satuan,
                ct.harga
            FROM cte_transaksi ct
            INNER JOIN m_barang b (NOLOCK) ON ct.kd_barang = b.kd_barang
            LEFT JOIN m_divisi div (NOLOCK) ON ct.kd_divisi = div.kd_divisi
            LEFT JOIN m_satuan s (NOLOCK) ON ct.kd_satuan = s.kd_satuan
            ORDER BY ct.tanggal ASC;
            """
            
            try:
                results = db_manager.execute_query(server_key, query)
                return {'status': 'success', 'data': results}
            except Exception as e:
                return {'status': 'error', 'message': str(e)}

    @classmethod
    def get_semua_barang_stok_awal(cls, server_key, stok_filter='all'):
            """
            Fetch ALL items with their initial stock (stok_awal).
            stok_filter options: 'gt_zero' (> 0), 'gte_zero' (>= 0), 'all' (no filter)
            """
            from app.Models.Database import db_manager
            
            stok_condition = ""
            if stok_filter == 'gt_zero':
                stok_condition = "WHERE bd.stok_awal > 0"
            elif stok_filter == 'gte_zero':
                stok_condition = "WHERE bd.stok_awal >= 0"
    
            query = f"""
            SELECT 
                bd.kd_divisi,
                bd.kd_barang,
                b.nama AS nama_barang,
                bd.stok_awal
            FROM m_barang_divisi bd (NOLOCK)
            INNER JOIN m_barang b (NOLOCK) ON bd.kd_barang = b.kd_barang
            {stok_condition}
            ORDER BY bd.kd_divisi, bd.kd_barang;
            """
            
            try:
                results = db_manager.execute_query(server_key, query)
                return {'status': 'success', 'data': results}
            except Exception as e:
                return {'status': 'error', 'message': str(e)}

    @classmethod
    def get_barang_histori(cls, server_key, kd_barang, kd_divisi, start_date=None, end_date=None):
            """
            Fetch transaction history for ONE item (optional division) from MSSQL.
            Uses the CTE-based 11_barang_histori.sql for a single efficient query.
            """
            from app.Models.Database import db_manager
    
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
    
                # Format output
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
    
                # Date filtering (in Python since SQL already filters by tutup buku)
                if start_date or end_date:
                    filtered_data = []
                    for r in final_data:
                        tgl_str = r['tanggal']
                        if not tgl_str:
                            continue
                        if start_date and tgl_str[:10] < start_date:
                            continue
                        if end_date and tgl_str[:10] > end_date:
                            continue
                        filtered_data.append(r)
                    final_data = filtered_data
    
                return {
                    'status': 'success',
                    'data': final_data,
                    'row_count': len(final_data)
                }
    
            except Exception as e:
                import traceback
                traceback.print_exc()
                return {'status': 'error', 'message': str(e)}

    @classmethod
    def get_item_stock_detail(cls, server_key, kd_barang):
            """
            Get stock detail for ONE item across all divisions + satuan conversions.
            Used by the Opname detail panel to show stock breakdown per divisi per satuan.
            """
            # Try memory cache first for stock data
            with SnapshotState._cache_lock:
                has_cache = server_key in SnapshotState._mem_cache
            
            stok_rows = []
            if has_cache:
                cache = SnapshotState._mem_cache.get(server_key, [])
                stok_rows = [r for r in cache if r.get('kd_barang') == kd_barang]
            else:
                # Fallback to SQLite
                db_path = cls._db_path(server_key)
                if not os.path.exists(db_path):
                    return {'status': 'no_snapshot', 'data': [], 'message': 'Belum ada snapshot.'}
                try:
                    conn = sqlite3.connect(db_path)
                    conn.row_factory = sqlite3.Row
                    rows = conn.execute(
                        'SELECT * FROM stok_snapshot WHERE kd_barang = ?', (kd_barang,)
                    ).fetchall()
                    stok_rows = [dict(r) for r in rows]
                    conn.close()
                except Exception as e:
                    return {'status': 'error', 'message': str(e)}
    
            # Get satuan conversions from SQLite
            satuans = []
            db_path = cls._db_path(server_key)
            if os.path.exists(db_path):
                try:
                    conn = sqlite3.connect(db_path)
                    conn.row_factory = sqlite3.Row
                    try:
                        sat_rows = conn.execute(
                            'SELECT kd_satuan, jumlah, nama_satuan FROM satuan_snapshot WHERE kd_barang = ?', (kd_barang,)
                        ).fetchall()
                        satuans = [dict(r) for r in sat_rows]
                    except sqlite3.OperationalError:
                        # Fallback for old snapshots without nama_satuan column
                        sat_rows = conn.execute(
                            'SELECT kd_satuan, jumlah FROM satuan_snapshot WHERE kd_barang = ?', (kd_barang,)
                        ).fetchall()
                        satuans = [{'kd_satuan': r['kd_satuan'], 'jumlah': r['jumlah'], 'nama_satuan': r['kd_satuan']} for r in sat_rows]
                    conn.close()
                except Exception:
                    pass
    
            # Build per-divisi breakdown
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
    
            # Item info from first row
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

