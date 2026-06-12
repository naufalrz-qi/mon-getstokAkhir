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

class SnapshotRunner:
    @classmethod
    def trigger_refresh(cls, server_key, tanggal=None):
            if server_key in SnapshotState._refresh_threads and SnapshotState._refresh_threads[server_key].is_alive():
                return {'status': 'already_running', 'message': 'Refresh sedang berjalan'}
    
            if not tanggal:
                tanggal = datetime.now().strftime('%Y-%m-%d')
    
            SnapshotState._refresh_cancel[server_key] = threading.Event()
            SnapshotState._refresh_status[server_key] = {
                'state': 'starting',
                'progress': 0,
                'message': 'Memulai parallel fetch...',
                'started_at': time.time(),
                'row_count': 0,
            }
    
            t = threading.Thread(
                target=cls._do_parallel_refresh,
                args=(server_key, tanggal),
                daemon=True,
            )
            SnapshotState._refresh_threads[server_key] = t
            t.start()
            return {'status': 'started', 'message': 'Refresh dimulai'}

    @classmethod
    def cancel_refresh(cls, server_key):
            if server_key in SnapshotState._refresh_cancel:
                SnapshotState._refresh_cancel[server_key].set()
                SnapshotState._refresh_status[server_key] = {
                    'state': 'cancelled', 'progress': 0,
                    'message': 'Refresh dibatalkan',
                    'started_at': 0, 'row_count': 0,
                }
                return {'status': 'cancelled', 'message': 'Refresh dibatalkan'}
            return {'status': 'not_running'}

    @classmethod
    def trigger_weekly_refresh(cls, server_key, tanggal=None):
            """Weekly update: calculate delta from the weekly checkpoint."""
            if server_key in SnapshotState._refresh_threads and SnapshotState._refresh_threads[server_key].is_alive():
                return {'status': 'already_running', 'message': 'Refresh sedang berjalan'}
    
            db_path = cls._db_path(server_key)
            if not os.path.exists(db_path):
                return cls.trigger_refresh(server_key, tanggal)  # No base, do full refresh
    
            if not tanggal:
                tanggal = datetime.now().strftime('%Y-%m-%d')
    
            SnapshotState._refresh_cancel[server_key] = threading.Event()
            SnapshotState._refresh_status[server_key] = {
                'state': 'starting',
                'progress': 0,
                'message': 'Memulai weekly update...',
                'started_at': time.time(),
                'row_count': 0,
                'is_delta': True,
            }
    
            # Retrieve checkpoint to get the base_date (last_refresh)
            _, base_date = cls.get_checkpoints(server_key, 'weekly')
            if not base_date:
                return cls.trigger_refresh(server_key, tanggal)
    
            t = threading.Thread(
                target=cls._do_delta_refresh,
                args=(server_key, tanggal, base_date, False, 'weekly'),
                daemon=True,
            )
            SnapshotState._refresh_threads[server_key] = t
            t.start()
            return {'status': 'started', 'message': 'Weekly update dimulai'}

    @classmethod
    def trigger_yearly_refresh(cls, server_key, tanggal=None):
            """Yearly update: calculate delta from the yearly checkpoint."""
            if server_key in SnapshotState._refresh_threads and SnapshotState._refresh_threads[server_key].is_alive():
                return {'status': 'already_running', 'message': 'Refresh sedang berjalan'}
    
            db_path = cls._db_path(server_key)
            if not os.path.exists(db_path):
                return cls.trigger_refresh(server_key, tanggal)  # No base, do full refresh
    
            if not tanggal:
                tanggal = datetime.now().strftime('%Y-%m-%d')
    
            SnapshotState._refresh_cancel[server_key] = threading.Event()
            SnapshotState._refresh_status[server_key] = {
                'state': 'starting',
                'progress': 0,
                'message': 'Memulai yearly update...',
                'started_at': time.time(),
                'row_count': 0,
                'is_delta': True,
            }
    
            # Retrieve checkpoint to get the base_date (last_refresh)
            _, base_date = cls.get_checkpoints(server_key, 'yearly')
            if not base_date:
                return cls.trigger_refresh(server_key, tanggal)
    
            t = threading.Thread(
                target=cls._do_delta_refresh,
                args=(server_key, tanggal, base_date, False, 'yearly'),
                daemon=True,
            )
            SnapshotState._refresh_threads[server_key] = t
            t.start()
            return {'status': 'started', 'message': 'Yearly update dimulai'}

    @classmethod
    def trigger_delta_refresh(cls, server_key, tanggal=None):
            """Quick update: only fetch new transactions since last refresh."""
            if server_key in SnapshotState._refresh_threads and SnapshotState._refresh_threads[server_key].is_alive():
                return {'status': 'already_running', 'message': 'Refresh sedang berjalan'}
    
            # Check if we have a base snapshot to delta from
            db_path = cls._db_path(server_key)
            if not os.path.exists(db_path):
                return cls.trigger_refresh(server_key, tanggal)  # No base, do full refresh
    
            # Get last_refresh timestamp from metadata
            try:
                conn = sqlite3.connect(db_path)
                row = conn.execute("SELECT value FROM snapshot_meta WHERE key='last_refresh'").fetchone()
                conn.close()
                if not row:
                    return cls.trigger_refresh(server_key, tanggal)
                last_refresh = row[0]
            except Exception:
                return cls.trigger_refresh(server_key, tanggal)
    
            if not tanggal:
                tanggal = datetime.now().strftime('%Y-%m-%d')
    
            SnapshotState._refresh_cancel[server_key] = threading.Event()
            SnapshotState._refresh_status[server_key] = {
                'state': 'starting',
                'progress': 0,
                'message': 'Memulai quick update...',
                'started_at': time.time(),
                'row_count': 0,
                'is_delta': True,
            }
    
            t = threading.Thread(
                target=cls._do_delta_refresh,
                args=(server_key, tanggal, last_refresh),
                daemon=True,
            )
            SnapshotState._refresh_threads[server_key] = t
            t.start()
            return {'status': 'started', 'message': 'Quick update dimulai'}

    @classmethod
    def _do_delta_refresh(cls, server_key, tanggal, last_refresh, is_sequential=False, checkpoint_type=None):
            """
            Background worker for delta refresh:
            1. Fetch only NEW transactions since last_refresh (parallel)
            2. Apply debet/kredit changes to in-memory cache
            3. Update SQLite snapshot for affected items
            """
            from app.Models.Database import db_manager
    
            cancel = SnapshotState._refresh_cancel.get(server_key)
            status = SnapshotState._refresh_status[server_key]
    
            checkpoint_map = None
            base_date = None
            if checkpoint_type:
                checkpoint_map, base_date = cls.get_checkpoints(server_key, checkpoint_type)
    
            try:
                status['state'] = 'fetching'
                status['progress'] = 10
                status['message'] = 'Mengambil transaksi baru (parallel)...'
                _t0 = time.time()
    
                last_refresh_dt = datetime.fromisoformat(last_refresh)
                # Opsi 4: Buffer 5 menit untuk menangkap transaksi yang mungkin terlewat
                # karena race condition NOLOCK / mid-commit
                last_refresh_buffered = last_refresh_dt - timedelta(minutes=5)
    
                # Define delta queries — each runs in its own connection
                delta_queries = {
                    'penjualan': """
                        SET NOCOUNT ON;
                        SELECT t.kd_divisi, d.kd_barang, 0 AS debet, d.qty AS kredit, d.kd_satuan, 'penjualan' AS source
                        FROM t_penjualan_detail d (NOLOCK)
                        INNER JOIN t_penjualan t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                        INNER JOIN m_barang b (NOLOCK) ON d.kd_barang = b.kd_barang
                        INNER JOIN m_kategori k (NOLOCK) ON b.kd_kategori = k.kd_kategori
                        WHERE t.tanggal_server > ?
                          AND t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
                          AND CAST(t.tanggal AS DATE) <= CAST(? AS DATE)
                          AND k.status <> 2
                    """,
                    'pembelian': """
                        SET NOCOUNT ON;
                        SELECT t.kd_divisi, d.kd_barang, d.qty AS debet, 0 AS kredit, d.kd_satuan, 'pembelian' AS source
                        FROM t_pembelian_detail d (NOLOCK)
                        INNER JOIN t_pembelian t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                        WHERE t.tanggal_server > ?
                          AND t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
                          AND t.status IN (0, 1)
                          AND CAST(t.tanggal AS DATE) <= CAST(? AS DATE)
                    """,
                    'mutasi': """
                        SET NOCOUNT ON;
                        SELECT t.kd_divisi_asal AS kd_divisi, d.kd_barang, 0 AS debet, d.qty AS kredit, d.kd_satuan, 'mutasi_out' AS source
                        FROM t_mutasi_stok_detail d (NOLOCK)
                        INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                        WHERE t.tanggal_server > ?
                          AND t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
                          AND CAST(t.tanggal AS DATE) <= CAST(? AS DATE)
                        UNION ALL
                        SELECT t.kd_divisi_tujuan AS kd_divisi, d.kd_barang, d.qty AS debet, 0 AS kredit, d.kd_satuan, 'mutasi_in' AS source
                        FROM t_mutasi_stok_detail d (NOLOCK)
                        INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                        WHERE t.tanggal_server > ?
                          AND t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
                          AND CAST(t.tanggal AS DATE) <= CAST(? AS DATE)
                    """,
                    'retur_jual': """
                        SET NOCOUNT ON;
                        SELECT t.kd_divisi, d.kd_barang, d.qty AS debet, 0 AS kredit, d.kd_satuan, 'retur_jual' AS source
                        FROM t_penjualan_retur_detail d (NOLOCK)
                        INNER JOIN t_penjualan_retur t (NOLOCK) ON d.no_retur = t.no_retur
                        WHERE t.tanggal_server > ?
                          AND t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
                          AND CAST(t.tanggal AS DATE) <= CAST(? AS DATE)
                    """,
                    'retur_beli': """
                        SET NOCOUNT ON;
                        SELECT t.kd_divisi, d.kd_barang, 0 AS debet, d.qty AS kredit, d.kd_satuan, 'retur_beli' AS source
                        FROM t_pembelian_retur_detail d (NOLOCK)
                        INNER JOIN t_pembelian_retur t (NOLOCK) ON d.no_retur = t.no_retur
                        WHERE t.tanggal_server > ?
                          AND t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
                          AND CAST(t.tanggal AS DATE) <= CAST(? AS DATE)
                    """,
                    'opname': """
                        SET NOCOUNT ON;
                        SELECT kd_divisi, kd_barang, qty AS debet, 0 AS kredit, kd_satuan, 'opname_in' AS source
                        FROM t_opname_stok (NOLOCK)
                        WHERE status = 2
                          AND tanggal_server > ?
                          AND tanggal > dbo.GetTanggalTerakhirTutupBuku()
                          AND CAST(tanggal AS DATE) <= CAST(? AS DATE)
                        UNION ALL
                        SELECT kd_divisi, kd_barang, 0 AS debet, qty AS kredit, kd_satuan, 'opname_out' AS source
                        FROM t_opname_stok (NOLOCK)
                        WHERE status <> 2
                          AND tanggal_server > ?
                          AND tanggal > dbo.GetTanggalTerakhirTutupBuku()
                          AND CAST(tanggal AS DATE) <= CAST(? AS DATE)
                    """,
                }
    
                # Build params per query (mutasi and opname use UNION ALL so need 4 params)
                base_params = [last_refresh_buffered, tanggal]
                query_params = {
                    'penjualan': base_params,
                    'pembelian': base_params,
                    'mutasi': base_params + base_params,  # 2x for UNION ALL
                    'retur_jual': base_params,
                    'retur_beli': base_params,
                    'opname': base_params + base_params,   # 2x for UNION ALL
                }
    
                # Parallel fetch
                all_delta_rows = []
                errors = []
    
                def _fetch_delta(name, sql, params):
                    conn = None
                    try:
                        conn = db_manager.create_new_connection(server_key)
                        cursor = conn.cursor()
                        cursor.execute(sql, params)
                        cols = [d[0] for d in cursor.description]
                        rows = [dict(zip(cols, r)) for r in cursor.fetchall()]
                        cursor.close()
                        return name, rows
                    except Exception as e:
                        err_msg = str(e)
                        if 'Invalid object name' in err_msg:
                            return name, []  # Table doesn't exist, skip silently
                        return name, f'ERROR: {err_msg}'
                    finally:
                        if conn:
                            conn.close()
    
                if is_sequential:
                    status['message'] = 'Mengambil transaksi baru (sequential)...'
                    completed = 0
                    for name, sql in delta_queries.items():
                        if cancel and cancel.is_set():
                            return
                        name, result = _fetch_delta(name, sql, query_params[name])
                        if isinstance(result, str) and result.startswith('ERROR'):
                            errors.append(f'{name}: {result}')
                            print(f'[DELTA] {name} failed: {result}')
                        else:
                            all_delta_rows.extend(result)
                        completed += 1
                        status['progress'] = 10 + int((completed / len(delta_queries)) * 35)
                        status['message'] = f'Fetched {completed}/{len(delta_queries)} tables...'
                else:
                    with ThreadPoolExecutor(max_workers=6) as executor:
                        futures = {}
                        for name, sql in delta_queries.items():
                            f = executor.submit(_fetch_delta, name, sql, query_params[name])
                            futures[f] = name
    
                        completed = 0
                        for future in as_completed(futures):
                            if cancel and cancel.is_set():
                                return
                            name, result = future.result()
                            if isinstance(result, str) and result.startswith('ERROR'):
                                errors.append(f'{name}: {result}')
                                print(f'[DELTA] {name} failed: {result}')
                            else:
                                all_delta_rows.extend(result)
                            completed += 1
                            status['progress'] = 10 + int((completed / len(delta_queries)) * 35)
                            status['message'] = f'Fetched {completed}/{len(delta_queries)} tables...'
    
                delta_rows = all_delta_rows
                _t1 = time.time()
                print(f'[DELTA TIMING] Parallel fetch: {_t1-_t0:.2f}s ({len(delta_rows)} rows)')
    
                if cancel and cancel.is_set():
                    return
    
                status['progress'] = 50
                status['message'] = f'{len(delta_rows)} transaksi baru ditemukan'
    
                if len(delta_rows) == 0:
                    # No changes
                    now_str = datetime.now().isoformat()
                    db_path = cls._db_path(server_key)
                    conn_db = None
                    try:
                        conn_db = sqlite3.connect(db_path)
                        conn_db.execute("INSERT OR REPLACE INTO snapshot_meta VALUES ('last_refresh', ?)", (now_str,))
                        conn_db.commit()
                    except sqlite3.DatabaseError as e:
                        if 'malformed' in str(e).lower() or 'corrupt' in str(e).lower():
                            if conn_db:
                                try:
                                    conn_db.close()
                                except:
                                    pass
                            for ext in ['', '-wal', '-shm']:
                                try:
                                    if os.path.exists(db_path + ext):
                                        os.remove(db_path + ext)
                                except OSError:
                                    pass
                            raise Exception("Database lokal korup. Silakan Refresh ulang (full refresh akan dipicu otomatis).")
                        else:
                            raise
                    finally:
                        if conn_db:
                            try:
                                conn_db.close()
                            except:
                                pass
    
                    elapsed = round(time.time() - status['started_at'], 1)
                    status['state'] = 'ready'
                    status['progress'] = 100
                    status['message'] = f'Tidak ada perubahan baru ({elapsed}s)'
                    return
    
                # ── Extract unique kd_barang yang terdampak ──
                affected_kd_barangs = list(set(r.get('kd_barang', '') for r in delta_rows if r.get('kd_barang')))
                _t2 = time.time()
                print(f'[DELTA TIMING] Identified {len(affected_kd_barangs)} affected items from {len(delta_rows)} rows')
                status['progress'] = 55
                status['message'] = f'Menghitung ulang stok {len(affected_kd_barangs)} item dari awal...'
    
                if cancel and cancel.is_set():
                    return
    
                # ── Targeted Recalculation: hitung stok dari NOL untuk item terdampak ──
                recalc_map, master_info, div_map = cls._targeted_recalculate(
                    server_key, affected_kd_barangs, tanggal, db_manager,
                    base_date=base_date, checkpoint_map=checkpoint_map
                )
                _t3 = time.time()
                print(f'[DELTA TIMING] Targeted recalc: {_t3-_t2:.2f}s ({len(recalc_map)} results)')
    
                if cancel and cancel.is_set():
                    return
    
                # ── Classify: existing vs new items ──
                status['progress'] = 70
                status['message'] = 'Memperbarui cache...'
                with SnapshotState._cache_lock:
                    if server_key not in SnapshotState._mem_cache:
                        cls._load_to_memory(server_key)
                    cache = SnapshotState._mem_cache.get(server_key, [])
                    cache_index = {}
                    for i, row in enumerate(cache):
                        key = (row.get('kd_divisi', ''), row.get('kd_barang', ''))
                        cache_index[key] = i
    
                updated_keys = set()
                new_rows = []
                for (kd_divisi, kd_barang), new_stok in recalc_map.items():
                    if (kd_divisi, kd_barang) in cache_index:
                        updated_keys.add((kd_divisi, kd_barang))
                    else:
                        # Item baru — perlu master info
                        master = master_info.get(kd_barang, {})
                        if not master:
                            continue
                        new_rows.append({
                            'kd_divisi': kd_divisi,
                            'divisi': div_map.get(kd_divisi, kd_divisi),
                            'kd_barang': kd_barang,
                            'barang': master.get('nama', ''),
                            'kategori': master.get('kategori', ''),
                            'merk': master.get('merk', ''),
                            'model': master.get('model', ''),
                            'warna': master.get('warna', ''),
                            'ukuran': master.get('ukuran', ''),
                            'stok_akhir': new_stok,
                            'harga_jual': float(master.get('harga_jual', 0) or 0),
                            'harga_beli_akhir': float(master.get('harga_beli', 0) or 0),
                            'harga_avg': float(master.get('harga_avg', 0) or 0),
                        })
    
                # ── Apply to memory cache (REPLACE, bukan tambah) ──
                with SnapshotState._cache_lock:
                    cache = SnapshotState._mem_cache.get(server_key, [])
                    cache_index = {}
                    for i, row in enumerate(cache):
                        key = (row.get('kd_divisi', ''), row.get('kd_barang', ''))
                        cache_index[key] = i
    
                    for key in updated_keys:
                        idx = cache_index.get(key)
                        if idx is not None:
                            cache[idx]['stok_akhir'] = recalc_map[key]
    
                    cache.extend(new_rows)
                    SnapshotState._mem_cache[server_key] = cache
                    SnapshotState._mem_cache_ts[server_key] = time.time()
    
                # ── Update SQLite ──
                _t4 = time.time()
                print(f'[DELTA TIMING] Memory update: {_t4-_t3:.2f}s')
                status['progress'] = 85
                status['message'] = 'Menyimpan perubahan ke snapshot...'
    
                db_path = cls._db_path(server_key)
                conn_db = None
                try:
                    conn_db = cls._init_db(db_path)
                    batch_update = []
                    for (kd_divisi, kd_barang) in updated_keys:
                        batch_update.append((recalc_map[(kd_divisi, kd_barang)], kd_divisi, kd_barang))
    
                    if batch_update:
                        conn_db.executemany(
                            'UPDATE stok_snapshot SET stok_akhir = ? WHERE kd_divisi = ? AND kd_barang = ?',
                            batch_update
                        )
    
                    if new_rows:
                        batch_insert = []
                        for row in new_rows:
                            batch_insert.append((
                                row['kd_divisi'], row['divisi'], row['kd_barang'],
                                row['barang'], row['kategori'], row['merk'],
                                row['model'], row['warna'], row['ukuran'],
                                row['stok_akhir'], row['harga_jual'], row['harga_beli_akhir'],
                                row['harga_avg'],
                            ))
                        conn_db.executemany(
                            'INSERT INTO stok_snapshot VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            batch_insert
                        )
    
                    now_str = datetime.now().isoformat()
                    conn_db.execute("INSERT OR REPLACE INTO snapshot_meta VALUES ('last_refresh', ?)", (now_str,))
                    conn_db.commit()
                except sqlite3.DatabaseError as e:
                    if 'malformed' in str(e).lower() or 'corrupt' in str(e).lower():
                        print(f"[DELTA ERROR] Corrupted DB detected for {server_key}. Removing...")
                        if conn_db:
                            try:
                                conn_db.close()
                            except:
                                pass
                        for ext in ['', '-wal', '-shm']:
                            try:
                                if os.path.exists(db_path + ext):
                                    os.remove(db_path + ext)
                            except OSError:
                                pass
                        raise Exception("Database lokal korup. Silakan Refresh ulang (full refresh akan dipicu otomatis).")
                    else:
                        raise
                finally:
                    if conn_db:
                        try:
                            conn_db.close()
                        except:
                            pass
    
                _t5 = time.time()
                print(f'[DELTA TIMING] SQLite write: {_t5-_t4:.2f}s')
                print(f'[DELTA TIMING] TOTAL: {_t5-_t0:.2f}s')
                elapsed = round(time.time() - status['started_at'], 1)
                status['state'] = 'ready'
                status['progress'] = 100
                status['row_count'] = len(delta_rows)
                status['message'] = f'Quick update selesai! {len(affected_kd_barangs)} item dihitung ulang, {len(new_rows)} item baru ({elapsed}s)'
    
            except Exception as e:
                status['state'] = 'error'
                status['progress'] = 0
                status['message'] = f'Delta error: {str(e)}'
                print(f'[DELTA ERROR] {server_key}: {e}')
                import traceback
                traceback.print_exc()

    @classmethod
    def _targeted_recalculate(cls, server_key, kd_barangs, tanggal, db_manager, base_date=None, checkpoint_map=None):
            """
            Recalculate stok from scratch for specific items via MSSQL.
            Returns:
                recalc_map: dict of (kd_divisi, kd_barang) -> stok_akhir (float)
                master_info: dict of kd_barang -> {nama, kategori, merk, ...}
                div_map: dict of kd_divisi -> nama
            """
            recalc_map = {}
            master_info = {}
            div_map = {}
    
            if not kd_barangs:
                return recalc_map, master_info, div_map
    
            # Load the recalculation SQL template
            recalc_sql_template = _load_sql('12_targeted_recalc.sql')
    
            conn = None
            try:
                conn = db_manager.create_new_connection(server_key)
                cursor = conn.cursor()
    
                # Fetch divisi map
                cursor.execute("SELECT kd_divisi, nama FROM m_divisi (NOLOCK)")
                div_map = {row[0]: row[1] for row in cursor.fetchall()}
    
                # Process in chunks of 200 to stay under SQL Server's 2100 parameter limit
                # (9 UNION ALL sections × N items + 9 base_date + 8 tanggal = 9N + 17 ≤ 2100)
                chunk_size = 200
                for i in range(0, len(kd_barangs), chunk_size):
                    chunk = kd_barangs[i:i + chunk_size]
                    placeholders = ','.join('?' for _ in chunk)
    
                    # Build the recalculation query from template
                    # The template has {placeholders} (appears 9 times) and {tanggal_placeholder}
                    sql = recalc_sql_template.replace('{placeholders}', placeholders)
                    sql = sql.replace('{tanggal_placeholder}', '?')
    
                    # Build params: each UNION ALL section needs its own set of params
                    # Section 1 (stok_awal): base_date + chunk
                    # Sections 2-9 (transactions): base_date + tanggal + chunk each
                    params = [base_date]
                    params.extend(chunk)
                    for _ in range(8):
                        params.append(base_date)
                        params.append(tanggal)
                        params.extend(chunk)
    
                    cursor.execute(sql, params)
                    if cursor.description:
                        for row in cursor.fetchall():
                            kd_divisi, kd_barang, stok = row[0], row[1], float(row[2] or 0)
                            
                            if checkpoint_map:
                                stok += float(checkpoint_map.get((kd_divisi, kd_barang), 0))
                                
                            recalc_map[(kd_divisi, kd_barang)] = round(stok, 4)
    
                # Fetch master info + harga for all affected items
                for i in range(0, len(kd_barangs), chunk_size):
                    chunk = kd_barangs[i:i + chunk_size]
                    placeholders = ','.join('?' for _ in chunk)
    
                    # Master data
                    sql_master = f"""
                        SELECT b.kd_barang, b.nama, b.ukuran,
                               k.nama AS kategori, mk.nama AS merk, mo.nama AS model, w.nama AS warna,
                               bs.harga_jual
                        FROM m_barang b (NOLOCK)
                        LEFT JOIN m_kategori k (NOLOCK) ON b.kd_kategori = k.kd_kategori
                        LEFT JOIN m_merk mk (NOLOCK) ON b.kd_merk = mk.kd_merk
                        LEFT JOIN m_model mo (NOLOCK) ON b.kd_model = mo.kd_model
                        LEFT JOIN m_warna w (NOLOCK) ON b.kd_warna = w.kd_warna
                        LEFT JOIN m_barang_satuan bs (NOLOCK) ON b.kd_barang = bs.kd_barang AND bs.jumlah = 1
                        WHERE b.kd_barang IN ({placeholders})
                    """
                    cursor.execute(sql_master, chunk)
                    cols = [d[0] for d in cursor.description]
                    for row in cursor.fetchall():
                        master_info[row[0]] = dict(zip(cols, row))
    
                    # Harga beli terakhir
                    sql_hb = f"""
                        SELECT kd_barang, harga_beli FROM (
                            SELECT d.kd_barang, d.harga_beli,
                                   ROW_NUMBER() OVER (PARTITION BY d.kd_barang ORDER BY t.tanggal DESC) AS rn
                            FROM t_pembelian_detail d (NOLOCK)
                            INNER JOIN t_pembelian t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                            WHERE d.kd_barang IN ({placeholders})
                        ) ranked WHERE rn = 1
                    """
                    cursor.execute(sql_hb, chunk)
                    for row in cursor.fetchall():
                        if row[0] in master_info:
                            master_info[row[0]]['harga_beli'] = float(row[1] or 0)
    
                    # Harga average
                    sql_ha = f"""
                        SELECT kd_barang,
                               CASE WHEN SUM(qty) > 0 THEN SUM(qty * harga_beli) / SUM(qty) ELSE 0 END AS harga_avg
                        FROM t_pembelian_detail (NOLOCK)
                        WHERE kd_barang IN ({placeholders})
                        GROUP BY kd_barang
                    """
                    cursor.execute(sql_ha, chunk)
                    for row in cursor.fetchall():
                        if row[0] in master_info:
                            master_info[row[0]]['harga_avg'] = round(float(row[1] or 0), 2)
    
                cursor.close()
            except Exception as e:
                logging.error(f"[TARGETED RECALC] Error for {server_key}: {e}")
                import traceback
                traceback.print_exc()
            finally:
                if conn:
                    try:
                        conn.close()
                    except:
                        pass
    
            return recalc_map, master_info, div_map

    @classmethod
    def _do_parallel_refresh(cls, server_key, tanggal):
            """
            Background worker:
            1. Fetch master data + all transaction tables in PARALLEL threads
            2. Merge & aggregate in Python (SUM debet-kredit per divisi+barang)
            3. Write to SQLite snapshot
            """
            from app.Models.Database import db_manager
            from app.Models.ServerModel import ServerModel

            cancel = SnapshotState._refresh_cancel.get(server_key)
            status = SnapshotState._refresh_status[server_key]

            # Cek tipe server, jika eceran/retail, lewati snapshot
            server_config = ServerModel.get_by_key(server_key)
            server_type = server_config.get('type', 'grosir') if server_config else 'grosir'
            
            if server_type in ['eceran', 'retail']:
                status['state'] = 'done'
                status['progress'] = 100
                status['message'] = 'Snapshot dilewati (tidak diperlukan untuk server retail/eceran)'
                return

            try:
                status['state'] = 'fetching'
                status['progress'] = 5
                status['message'] = 'Mengambil data dari SQL Server (parallel)...'
    
                # ── Phase 1: Parallel fetch from MSSQL ──
                # Tiap query mendapat connection sendiri (thread safety)
                tahun_ini = datetime.strptime(tanggal, '%Y-%m-%d').year if tanggal else datetime.now().year
                query_tasks = {
                    'master':     ('01_master.sql',     None),
                    'stok_awal':  ('02_stok_awal.sql',  [tanggal]),
                    'penjualan':  ('03_penjualan.sql',  [tanggal, None]),
                    'pembelian':  ('04_pembelian.sql',  [tanggal, None]),
                    'opname':     ('05_opname.sql',     [tanggal, None]),
                    'mutasi':     ('06_mutasi.sql',     [tanggal, None]),
                    'retur':      ('07_retur.sql',      [tanggal, None]),
                    'harga_beli': ('08_harga_beli.sql',  None),
                    'harga_avg':  ('10_harga_avg.sql',   None),
                    'opname_detail': ('11_opname_detail.sql', None),
                }
                query_tasks['dashboard_penjualan'] = ('../dashboard/01_penjualan_summary.sql', [tahun_ini, tahun_ini])
                query_tasks['dashboard_pembelian'] = ('../dashboard/02_pembelian_summary.sql', [tahun_ini, tahun_ini])

                fetch_results = {}
                errors = []
    
                def _fetch_one(name, sql_file, params):
                    """Fetch a single query in its own dedicated connection."""
                    conn = None
                    try:
                        sql = _load_sql(sql_file)
                        conn = db_manager.create_new_connection(server_key)
                        cursor = conn.cursor()
    
                        if params:
                            cursor.execute(sql, params)
                        else:
                            cursor.execute(sql)
    
                        if name == 'master':
                            # Multi result set
                            all_results = []
                            while True:
                                if cursor.description:
                                    columns = [desc[0] for desc in cursor.description]
                                    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                                    all_results.append(rows)
                                if not cursor.nextset():
                                    break
                            cursor.close()
                            return name, all_results
                        else:
                            columns = [desc[0] for desc in cursor.description]
                            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                            cursor.close()
                            return name, rows
                    except Exception as e:
                        return name, f'ERROR: {e}'
                    finally:
                        if conn:
                            try:
                                conn.close()
                            except:
                                pass
    
                with ThreadPoolExecutor(max_workers=6) as executor:
                    futures = {}
                    for name, (sql_file, params) in query_tasks.items():
                        if cancel and cancel.is_set():
                            return
                        f = executor.submit(_fetch_one, name, sql_file, params)
                        futures[f] = name
    
                    completed = 0
                    for future in as_completed(futures):
                        if cancel and cancel.is_set():
                            return
                        name, result = future.result()
                        if isinstance(result, str) and result.startswith('ERROR'):
                            errors.append(f'{name}: {result}')
                            print(f'[SNAPSHOT] {name} failed: {result}')
                        else:
                            fetch_results[name] = result
                        completed += 1
                        status['progress'] = 5 + int((completed / len(query_tasks)) * 45)
                        status['message'] = f'Fetched {completed}/{len(query_tasks)} tables...'
    
                if cancel and cancel.is_set():
                    return
    
                if not fetch_results.get('master'):
                    status['state'] = 'error'
                    status['message'] = 'Gagal fetch master data'
                    return
    
                # ── Phase 2: Python aggregation ──
                status['state'] = 'writing'
                status['progress'] = 55
                status['message'] = 'Mengolah data...'
    
                final_rows, opname_rows, satuan_rows = cls._aggregate(fetch_results)
    
                if cancel and cancel.is_set():
                    return
    
                status['progress'] = 75
                status['message'] = f'Menyimpan {len(final_rows)} item ke snapshot lokal...'
    
                # ── Phase 3: Write to SQLite ──
                db_path = cls._db_path(server_key)
                conn = None
                try:
                    conn = cls._init_db(db_path)
                    conn.execute('DELETE FROM stok_snapshot')
                except sqlite3.DatabaseError as e:
                    if 'malformed' in str(e).lower() or 'corrupt' in str(e).lower():
                        print(f"[SNAPSHOT] Corrupted database detected for {server_key}, rebuilding '{db_path}'...")
                        if conn:
                            try:
                                conn.close()
                            except:
                                pass
                        for ext in ['', '-wal', '-shm']:
                            try:
                                if os.path.exists(db_path + ext):
                                    os.remove(db_path + ext)
                            except OSError as oe:
                                print(f"[SNAPSHOT] Warning: could not remove {db_path + ext}: {oe}")
                        
                        conn = cls._init_db(db_path)
                        conn.execute('DELETE FROM stok_snapshot')
                    else:
                        raise
    
                try:
                    batch = []
                    for row in final_rows:
                        batch.append((
                            row['kd_divisi'], row['divisi'], row['kd_barang'],
                            row['barang'], row['kategori'], row['merk'],
                            row['model'], row['warna'], row['ukuran'],
                            row['stok_akhir'], row['harga_jual'], row['harga_beli_akhir'],
                            row['harga_avg'],
                        ))
    
                    conn.executemany(
                        'INSERT INTO stok_snapshot VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        batch,
                    )
    
                    # Insert opname_snapshot
                    opname_batch = []
                    for row in opname_rows:
                        opname_batch.append((
                            row['no_transaksi'], row['kd_divisi'], row['divisi'],
                            row['kd_barang'], row['barang'], row['kd_satuan'],
                            row['satuan'], row['tanggal'], row['qty'],
                            row['keterangan'], row['petugas'], row['status_text'],
                            row['tanggal_server']
                        ))
                    conn.execute('DELETE FROM opname_snapshot')
                    conn.executemany(
                        'INSERT INTO opname_snapshot VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        opname_batch
                    )
    
                    # Insert satuan_snapshot
                    satuan_batch = []
                    for row in satuan_rows:
                        satuan_batch.append((
                            row['kd_barang'], row['kd_satuan'], row['jumlah'], row.get('nama_satuan')
                        ))
                    conn.execute('DELETE FROM satuan_snapshot')
                    conn.executemany(
                        'INSERT INTO satuan_snapshot VALUES (?,?,?,?)',
                        satuan_batch
                    )

                    # Insert dashboard_penjualan
                    if 'dashboard_penjualan' in fetch_results:
                        penjualan_batch = []
                        for row in fetch_results['dashboard_penjualan']:
                            penjualan_batch.append((
                                row.get('kd_divisi'), row.get('kd_barang'),
                                row.get('total_qty', 0), row.get('total_nominal', 0),
                                row.get('bulan'), row.get('tahun')
                            ))
                        conn.execute('DELETE FROM dashboard_penjualan')
                        conn.executemany(
                            'INSERT INTO dashboard_penjualan VALUES (?,?,?,?,?,?)',
                            penjualan_batch
                        )

                    # Insert dashboard_pembelian
                    if 'dashboard_pembelian' in fetch_results:
                        pembelian_batch = []
                        for row in fetch_results['dashboard_pembelian']:
                            pembelian_batch.append((
                                row.get('kd_divisi'), row.get('kd_barang'),
                                row.get('total_qty', 0), row.get('total_nominal', 0),
                                row.get('bulan'), row.get('tahun')
                            ))
                        conn.execute('DELETE FROM dashboard_pembelian')
                        conn.executemany(
                            'INSERT INTO dashboard_pembelian VALUES (?,?,?,?,?,?)',
                            pembelian_batch
                        )

                    now_str = datetime.now().isoformat()
                    for k, v in [('last_refresh', now_str), ('tanggal', tanggal), ('row_count', str(len(final_rows)))]:
                        conn.execute('INSERT OR REPLACE INTO snapshot_meta VALUES (?,?)', (k, v))
    
                    conn.commit()
                finally:
                    if conn:
                        try:
                            conn.close()
                        except:
                            pass
                            
                # ── Create Checkpoints (Base Data) ──
                status['progress'] = 90
                status['message'] = 'Membuat Checkpoint Base Data...'
                stok_map = {(r['kd_divisi'], r['kd_barang']): r['stok_akhir'] for r in final_rows}
                cls.save_checkpoints(server_key, 'yearly', tanggal, stok_map)
                cls.save_checkpoints(server_key, 'weekly', tanggal, stok_map)
    
                # ── Phase 4: Load into memory ──
                status['progress'] = 95
                status['message'] = 'Memuat ke memory cache...'
                cls._load_to_memory(server_key)
    
                elapsed = round(time.time() - status['started_at'], 1)
                status['state'] = 'ready'
                status['progress'] = 100
                status['row_count'] = len(final_rows)
                status['message'] = f'Selesai! {len(final_rows)} item dimuat dalam {elapsed}s.'
                if errors:
                    status['message'] += f' (Warning: {len(errors)} tabel error)'
    
            except Exception as e:
                status['state'] = 'error'
                status['progress'] = 0
                status['message'] = f'Error: {str(e)}'
                print(f'[SNAPSHOT ERROR] {server_key}: {e}')
                import traceback
                traceback.print_exc()

    @classmethod
    def _aggregate(cls, fetch_results):
            """
            Merge parallel fetch results:
            1. Build master lookups (barang, satuan konversi, divisi)
            2. Accumulate debet/kredit per (kd_divisi, kd_barang)
            3. Output final rows with names joined from master
            """
            # ── Parse master data ──
            master_sets = fetch_results.get('master', [])
            barang_list = master_sets[0] if len(master_sets) > 0 else []
            satuan_list = master_sets[1] if len(master_sets) > 1 else []
            divisi_list = master_sets[2] if len(master_sets) > 2 else []
    
            # Barang lookup: kd_barang -> {nama, kategori, merk, ...}
            barang_map = {}
            for b in barang_list:
                barang_map[b['kd_barang']] = {
                    'nama': b['nama'],
                    'kategori': b.get('kategori', ''),
                    'merk': b.get('merk', ''),
                    'model': b.get('model', ''),
                    'warna': b.get('warna', ''),
                    'ukuran': b.get('ukuran', ''),
                    'harga_jual': float(b.get('harga_jual', 0) or 0),
                }
    
            # Satuan konversi: (kd_barang, kd_satuan) -> jumlah
            satuan_map = {}
            for s in satuan_list:
                key = (s['kd_barang'], s['kd_satuan'])
                satuan_map[key] = float(s.get('jumlah', 1) or 1)
    
            # Divisi: kd_divisi -> nama
            divisi_map = {}
            for d in divisi_list:
                divisi_map[d['kd_divisi']] = d['nama']
    
            # Harga beli terakhir: kd_barang -> harga
            harga_beli_map = {}
            for h in fetch_results.get('harga_beli', []):
                harga_beli_map[h['kd_barang']] = float(h.get('harga_beli', 0) or 0)
    
            # Harga average: kd_barang -> weighted avg
            harga_avg_map = {}
            for h in fetch_results.get('harga_avg', []):
                harga_avg_map[h['kd_barang']] = float(h.get('harga_avg', 0) or 0)
    
            # ── Accumulate debet/kredit ──
            # Key: (kd_divisi, kd_barang) -> {'debet': float, 'kredit': float}
            accum = defaultdict(lambda: {'debet': 0.0, 'kredit': 0.0})
    
            transaction_tables = ['stok_awal', 'penjualan', 'pembelian', 'opname', 'mutasi', 'retur']
    
            for table_name in transaction_tables:
                rows = fetch_results.get(table_name, [])
                for row in rows:
                    kd_barang = row.get('kd_barang', '')
                    kd_divisi = row.get('kd_divisi', '')
                    kd_satuan = row.get('kd_satuan', '')
    
                    # Skip barang that aren't in master (inactive/deleted)
                    if kd_barang not in barang_map:
                        continue
    
                    # Get satuan conversion factor
                    conv = satuan_map.get((kd_barang, kd_satuan), 1.0)
    
                    debet = float(row.get('debet', 0) or 0) * conv
                    kredit = float(row.get('kredit', 0) or 0) * conv
    
                    key = (kd_divisi, kd_barang)
                    accum[key]['debet'] += debet
                    accum[key]['kredit'] += kredit
    
            # ── Build final rows ──
            final_rows = []
            for (kd_divisi, kd_barang), vals in accum.items():
                stok = vals['debet'] - vals['kredit']
                master = barang_map.get(kd_barang, {})
                if not master:
                    continue
    
                final_rows.append({
                    'kd_divisi': kd_divisi,
                    'divisi': divisi_map.get(kd_divisi, kd_divisi),
                    'kd_barang': kd_barang,
                    'barang': master.get('nama', ''),
                    'kategori': master.get('kategori', ''),
                    'merk': master.get('merk', ''),
                    'model': master.get('model', ''),
                    'warna': master.get('warna', ''),
                    'ukuran': master.get('ukuran', ''),
                    'stok_akhir': round(stok, 4),
                    'harga_jual': master.get('harga_jual', 0),
                    'harga_beli_akhir': harga_beli_map.get(kd_barang, 0),
                    'harga_avg': round(harga_avg_map.get(kd_barang, 0), 2),
                })
    
            # Sort by divisi, barang name
            final_rows.sort(key=lambda r: (r['divisi'], r['barang']))
    
            # ── Process opname detail ──
            opname_rows = []
            for row in fetch_results.get('opname_detail', []):
                kd_barang = row.get('kd_barang', '')
                kd_divisi = row.get('kd_divisi', '')
                master = barang_map.get(kd_barang, {})
                
                # Map status
                status_val = row.get('status')
                status_text = 'Lain-Lain'
                if status_val == 0:
                    status_text = 'Hilang'
                elif status_val == 1:
                    status_text = 'Rusak'
                elif status_val == 2 or str(status_val) == '2':
                    status_text = 'Lain-Lain(+)'
                elif status_val == 3:
                    status_text = 'Lain-Lain (-)'
    
                opname_rows.append({
                    'no_transaksi': row.get('no_transaksi', ''),
                    'kd_divisi': kd_divisi,
                    'divisi': divisi_map.get(kd_divisi, kd_divisi),
                    'kd_barang': kd_barang,
                    'barang': master.get('nama', '') or kd_barang,
                    'kd_satuan': row.get('kd_satuan', ''),
                    'satuan': row.get('satuan', ''),
                    'tanggal': row.get('tanggal', ''),
                    'qty': row.get('qty', 0),
                    'keterangan': row.get('keterangan', ''),
                    'petugas': row.get('petugas', ''),
                    'status_text': status_text,
                    'tanggal_server': row.get('tanggal_server', '')
                })
    
            # Process satuan_rows (from m_barang_satuan in master_sets[1])
            satuan_rows = []
            for s in satuan_list:
                satuan_rows.append({
                    'kd_barang': s['kd_barang'],
                    'kd_satuan': s['kd_satuan'],
                    'jumlah': float(s.get('jumlah', 1) or 1),
                    'nama_satuan': s.get('nama_satuan') or s.get('kd_satuan')
                })
    
            return final_rows, opname_rows, satuan_rows

    @classmethod
    def _load_to_memory(cls, server_key):
            db_path = cls._db_path(server_key)
            if not os.path.exists(db_path):
                return
            conn = None
            try:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                rows = conn.execute('SELECT * FROM stok_snapshot').fetchall()
                with SnapshotState._cache_lock:
                    SnapshotState._mem_cache[server_key] = [dict(r) for r in rows]
                    SnapshotState._mem_cache_ts[server_key] = time.time()
            except sqlite3.DatabaseError as e:
                if 'malformed' in str(e).lower() or 'corrupt' in str(e).lower():
                    print(f"[MEMORY CACHE] Corrupted DB detected for {server_key}. Removing...")
                    if conn:
                        try:
                            conn.close()
                        except:
                            pass
                    for ext in ['', '-wal', '-shm']:
                        try:
                            if os.path.exists(db_path + ext):
                                os.remove(db_path + ext)
                        except OSError:
                            pass
            finally:
                if conn:
                    try:
                        conn.close()
                    except:
                        pass

