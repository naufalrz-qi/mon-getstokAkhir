"""
SnapshotManager: Local SQLite snapshot storage per server.

Uses PARALLEL per-table queries to bypass the slow v_g_barang_histori_detail view.
Fetches each transaction table in its own thread, merges in Python, stores in SQLite.

Each server gets its own .db file in database/snapshots/
"""

import os
import sqlite3
import threading
import time
import logging
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('app.log')
    ]
)

QUERIES_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'database', 'queries')
PARALLEL_DIR = os.path.join(QUERIES_DIR, 'parallel')
SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', 'database', 'snapshots')

def _load_sql(filename):
    path = os.path.join(PARALLEL_DIR, filename)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


class SnapshotManager:
    """Manages per-server SQLite snapshots with parallel MSSQL fetching."""

    _refresh_threads = {}
    _refresh_cancel = {}
    _refresh_status = {}
    _mem_cache = {}
    _mem_cache_ts = {}
    _cache_lock = threading.RLock()
    _auto_update_thread = None
    _auto_update_stop_event = None

    # ──────────── Paths ────────────

    @classmethod
    def _db_path(cls, server_key):
        os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
        safe_key = server_key.replace('/', '_').replace('\\', '_')
        return os.path.join(SNAPSHOTS_DIR, f'{safe_key}.db')

    # ──────────── SQLite Schema ────────────

    @classmethod
    def _init_db(cls, db_path):
        conn = sqlite3.connect(db_path)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS stok_snapshot (
                kd_divisi       TEXT,
                divisi          TEXT,
                kd_barang       TEXT,
                barang          TEXT,
                kategori        TEXT,
                merk            TEXT,
                model           TEXT,
                warna           TEXT,
                ukuran          TEXT,
                stok_akhir      REAL,
                harga_jual      REAL,
                harga_beli_akhir REAL,
                harga_avg       REAL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS snapshot_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_barang ON stok_snapshot(barang)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_kd_barang ON stok_snapshot(kd_barang)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_divisi ON stok_snapshot(divisi)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_stok_divisi_barang ON stok_snapshot(kd_divisi, kd_barang)')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS opname_snapshot (
                no_transaksi    TEXT,
                kd_divisi       TEXT,
                divisi          TEXT,
                kd_barang       TEXT,
                barang          TEXT,
                kd_satuan       TEXT,
                satuan          TEXT,
                tanggal         TEXT,
                qty             REAL,
                keterangan      TEXT,
                petugas         TEXT,
                status_text     TEXT,
                tanggal_server  TEXT
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_opname_barang ON opname_snapshot(barang)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_opname_kd_barang ON opname_snapshot(kd_barang)')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS satuan_snapshot (
                kd_barang       TEXT,
                kd_satuan       TEXT,
                jumlah          REAL,
                nama_satuan     TEXT
            )
        ''')
        try:
            conn.execute('ALTER TABLE satuan_snapshot ADD COLUMN nama_satuan TEXT')
        except sqlite3.OperationalError:
            pass
        conn.execute('CREATE INDEX IF NOT EXISTS idx_satuan_kd_barang ON satuan_snapshot(kd_barang)')

        conn.commit()
        return conn

    # ──────────── Trigger / Cancel ────────────

    @classmethod
    def trigger_refresh(cls, server_key, tanggal=None):
        if server_key in cls._refresh_threads and cls._refresh_threads[server_key].is_alive():
            return {'status': 'already_running', 'message': 'Refresh sedang berjalan'}

        if not tanggal:
            tanggal = datetime.now().strftime('%Y-%m-%d')

        cls._refresh_cancel[server_key] = threading.Event()
        cls._refresh_status[server_key] = {
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
        cls._refresh_threads[server_key] = t
        t.start()
        return {'status': 'started', 'message': 'Refresh dimulai'}

    @classmethod
    def cancel_refresh(cls, server_key):
        if server_key in cls._refresh_cancel:
            cls._refresh_cancel[server_key].set()
            cls._refresh_status[server_key] = {
                'state': 'cancelled', 'progress': 0,
                'message': 'Refresh dibatalkan',
                'started_at': 0, 'row_count': 0,
            }
            return {'status': 'cancelled', 'message': 'Refresh dibatalkan'}
        return {'status': 'not_running'}

    @classmethod
    def trigger_delta_refresh(cls, server_key, tanggal=None):
        """Quick update: only fetch new transactions since last refresh."""
        if server_key in cls._refresh_threads and cls._refresh_threads[server_key].is_alive():
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

        cls._refresh_cancel[server_key] = threading.Event()
        cls._refresh_status[server_key] = {
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
        cls._refresh_threads[server_key] = t
        t.start()
        return {'status': 'started', 'message': 'Quick update dimulai'}

    @classmethod
    def _do_delta_refresh(cls, server_key, tanggal, last_refresh, is_sequential=False):
        """
        Background worker for delta refresh:
        1. Fetch only NEW transactions since last_refresh (parallel)
        2. Apply debet/kredit changes to in-memory cache
        3. Update SQLite snapshot for affected items
        """
        from app.Models.Database import db_manager

        cancel = cls._refresh_cancel.get(server_key)
        status = cls._refresh_status[server_key]

        try:
            status['state'] = 'fetching'
            status['progress'] = 10
            status['message'] = 'Mengambil transaksi baru (parallel)...'
            _t0 = time.time()

            last_refresh_dt = datetime.fromisoformat(last_refresh)

            # Define delta queries — each runs in its own connection
            delta_queries = {
                'penjualan': """
                    SET NOCOUNT ON;
                    SELECT t.kd_divisi, d.kd_barang, 0 AS debet, d.qty AS kredit, d.kd_satuan, 'penjualan' AS source
                    FROM t_penjualan_detail d (NOLOCK)
                    INNER JOIN t_penjualan t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                    WHERE t.tanggal_server > ?
                      AND t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
                      AND CAST(t.tanggal AS DATE) <= CAST(? AS DATE)
                """,
                'pembelian': """
                    SET NOCOUNT ON;
                    SELECT t.kd_divisi, d.kd_barang, d.qty AS debet, 0 AS kredit, d.kd_satuan, 'pembelian' AS source
                    FROM t_pembelian_detail d (NOLOCK)
                    INNER JOIN t_pembelian t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                    WHERE t.tanggal_server > ?
                      AND t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
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
            base_params = [last_refresh_dt, tanggal]
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

            # Get satuan konversi from LOCAL SQLite snapshot (no network needed)
            satuan_map = {}
            db_path = cls._db_path(server_key)
            try:
                conn_local = sqlite3.connect(db_path)
                for row in conn_local.execute('SELECT kd_barang, kd_satuan, jumlah FROM satuan_snapshot').fetchall():
                    satuan_map[(row[0], row[1])] = float(row[2] or 1)
                conn_local.close()
            except Exception:
                pass

            # Accumulate delta per (kd_divisi, kd_barang)
            delta_accum = defaultdict(lambda: 0.0)
            for row in delta_rows:
                kd_barang = row.get('kd_barang', '')
                kd_divisi = row.get('kd_divisi', '')
                kd_satuan = row.get('kd_satuan', '')
                conv = satuan_map.get((kd_barang, kd_satuan), 1.0)
                debet = float(row.get('debet', 0) or 0) * conv
                kredit = float(row.get('kredit', 0) or 0) * conv
                delta_accum[(kd_divisi, kd_barang)] += (debet - kredit)

            _t2 = time.time()
            print(f'[DELTA TIMING] Satuan + accumulate: {_t2-_t1:.2f}s ({len(delta_accum)} unique items)')
            status['progress'] = 60
            status['message'] = f'Memperbarui {len(delta_accum)} item...'

            # ── Step 1: Quick lock to identify existing vs missing items ──
            with cls._cache_lock:
                if server_key not in cls._mem_cache:
                    cls._load_to_memory(server_key)
                cache = cls._mem_cache.get(server_key, [])
                cache_index = {}
                for i, row in enumerate(cache):
                    key = (row.get('kd_divisi', ''), row.get('kd_barang', ''))
                    cache_index[key] = i

            # Classify keys (no lock needed, just reading local vars)
            updated_keys = set()
            missing_keys = set()
            for key in delta_accum:
                if key in cache_index:
                    updated_keys.add(key)
                else:
                    missing_keys.add(key)

            # ── Step 2: Fetch missing item info from MSSQL (OUTSIDE lock) ──
            new_rows = []
            new_items_data = {}
            div_map = {}
            if missing_keys:
                status['progress'] = 65
                status['message'] = f'Mengambil info {len(missing_keys)} item baru...'
                missing_kd_barangs = list(set(k[1] for k in missing_keys))
                
                conn_mssql = None
                try:
                    conn_mssql = db_manager.create_new_connection(server_key)
                    cursor = conn_mssql.cursor()
                    cursor.execute("SELECT kd_divisi, nama FROM m_divisi (NOLOCK)")
                    div_map = {row[0]: row[1] for row in cursor.fetchall()}
                    
                    chunk_size = 1000
                    for i in range(0, len(missing_kd_barangs), chunk_size):
                        chunk = missing_kd_barangs[i:i + chunk_size]
                        placeholders = ','.join('?' for _ in chunk)
                        sql_new_items = f"""
                            SELECT 
                                b.kd_barang, b.nama, b.kd_kategori, b.kd_merk, b.kd_model, b.kd_warna, b.ukuran,
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
                        cursor.execute(sql_new_items, chunk)
                        cols = [d[0] for d in cursor.description]
                        for row in cursor.fetchall():
                            new_items_data[row[0]] = dict(zip(cols, row))
                            
                    cursor.close()
                except Exception as e:
                    import logging
                    logging.error(f"[DELTA] Failed to fetch missing info: {e}")
                finally:
                    if conn_mssql:
                        conn_mssql.close()

                for (kd_divisi, kd_barang) in missing_keys:
                    delta_stok = delta_accum[(kd_divisi, kd_barang)]
                    master = new_items_data.get(kd_barang, {})
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
                        'stok_akhir': round(delta_stok, 4),
                        'harga_jual': float(master.get('harga_jual', 0) or 0),
                        'harga_beli_akhir': 0.0,
                        'harga_avg': 0.0,
                    })

            # ── Step 3: Brief lock to apply all changes to memory ──
            _t3 = time.time()
            print(f'[DELTA TIMING] Fetch missing items: {_t3-_t2:.2f}s ({len(missing_keys)} missing)')
            status['progress'] = 75
            status['message'] = 'Memperbarui cache...'
            with cls._cache_lock:
                cache = cls._mem_cache.get(server_key, [])
                # Re-build index (cache may have been reloaded)
                cache_index = {}
                for i, row in enumerate(cache):
                    key = (row.get('kd_divisi', ''), row.get('kd_barang', ''))
                    cache_index[key] = i

                for key in updated_keys:
                    idx = cache_index.get(key)
                    if idx is not None:
                        cache[idx]['stok_akhir'] = round(cache[idx].get('stok_akhir', 0) + delta_accum[key], 4)

                cache.extend(new_rows)
                cls._mem_cache[server_key] = cache
                cls._mem_cache_ts[server_key] = time.time()

            # Update SQLite
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
                    idx = cache_index.get((kd_divisi, kd_barang))
                    if idx is not None:
                        new_stok = cache[idx]['stok_akhir']
                        batch_update.append((new_stok, kd_divisi, kd_barang))
                
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
            status['message'] = f'Quick update selesai! {len(delta_rows)} transaksi, {len(updated_keys)} item diupdate, {len(new_rows)} item baru ditambahkan ({elapsed}s)'

        except Exception as e:
            status['state'] = 'error'
            status['progress'] = 0
            status['message'] = f'Delta error: {str(e)}'
            print(f'[DELTA ERROR] {server_key}: {e}')
            import traceback
            traceback.print_exc()

    # ──────────── Parallel Refresh ────────────

    @classmethod
    def _do_parallel_refresh(cls, server_key, tanggal):
        """
        Background worker:
        1. Fetch master data + all transaction tables in PARALLEL threads
        2. Merge & aggregate in Python (SUM debet-kredit per divisi+barang)
        3. Write to SQLite snapshot
        """
        from app.Models.Database import db_manager

        cancel = cls._refresh_cancel.get(server_key)
        status = cls._refresh_status[server_key]

        try:
            status['state'] = 'fetching'
            status['progress'] = 5
            status['message'] = 'Mengambil data dari SQL Server (parallel)...'

            # ── Phase 1: Parallel fetch from MSSQL ──
            # Each query gets its OWN connection (thread safety)
            query_tasks = {
                'master':     ('01_master.sql',     None),
                'stok_awal':  ('02_stok_awal.sql',  [tanggal]),
                'penjualan':  ('03_penjualan.sql',  [tanggal]),
                'pembelian':  ('04_pembelian.sql',   [tanggal]),
                'opname':     ('05_opname.sql',      [tanggal]),
                'mutasi':     ('06_mutasi.sql',      [tanggal]),
                'retur':      ('07_retur.sql',       [tanggal]),
                'harga_beli': ('08_harga_beli.sql',  None),
                'harga_avg':  ('10_harga_avg.sql',   None),
                'opname_detail': ('11_opname_detail.sql', None),
            }

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

    # ──────────── Python Aggregation ────────────

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

    # ──────────── Memory Cache ────────────

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
            with cls._cache_lock:
                cls._mem_cache[server_key] = [dict(r) for r in rows]
                cls._mem_cache_ts[server_key] = time.time()
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

    # ──────────── Search ────────────

    @classmethod
    def search(cls, server_key, search_kode=None, search_nama=None, divisi=None, limit=None, offset=None):
        # Try memory cache first
        with cls._cache_lock:
            has_cache = server_key in cls._mem_cache
        if has_cache:
            data = cls._filter_memory(server_key, search_kode, search_nama, divisi)
            return cls._build_result(data, source='memory', limit=limit, offset=offset)

        # Try loading from SQLite
        db_path = cls._db_path(server_key)
        if os.path.exists(db_path):
            cls._load_to_memory(server_key)
            if server_key in cls._mem_cache:
                data = cls._filter_memory(server_key, search_kode, search_nama, divisi)
                return cls._build_result(data, source='sqlite', limit=limit, offset=offset)

        return {
            'status': 'no_snapshot',
            'data': [],
            'summary': {'total_items': 0, 'divisi_count': 0, 'total_nominal': 0, 'avg_stok': 0},
            'message': 'Belum ada snapshot. Klik Refresh untuk memuat data.',
        }

    @classmethod
    def search_opname(cls, server_key, search_kode=None, search_nama=None, divisi=None):
        db_path = cls._db_path(server_key)
        if not os.path.exists(db_path):
            return {
                'status': 'no_snapshot',
                'data': [],
                'message': 'Belum ada snapshot. Klik Refresh untuk memuat data.',
            }
            
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            
            # Fetch opname data
            query = "SELECT * FROM opname_snapshot WHERE 1=1"
            params = []
            
            if search_kode:
                query += " AND kd_barang LIKE ?"
                params.append(f"%{search_kode}%")
                
            if search_nama:
                query += " AND barang LIKE ?"
                params.append(f"%{search_nama}%")
                
            if divisi:
                query += " AND divisi = ?"
                params.append(divisi)
                
            query += " ORDER BY tanggal DESC"
            
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
    def _filter_memory(cls, server_key, search_kode=None, search_nama=None, divisi=None):
        data = cls._mem_cache.get(server_key, [])
        filtered = []

        for row in data:
            # Search filter
            if search_kode or search_nama:
                match = False
                if search_kode and cls._like_match(row.get('kd_barang', ''), search_kode):
                    match = True
                if search_nama and cls._like_match(row.get('barang', ''), search_nama):
                    match = True
                if not match:
                    continue

            # Divisi filter
            if divisi and row.get('divisi', '').lower() != divisi.lower():
                continue

            filtered.append(row)

        return filtered

    @classmethod
    def _build_result(cls, data, source='memory', limit=None, offset=None):
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
        with cls._cache_lock:
            if server_key in cls._mem_cache:
                divisi_set = set(r.get('divisi', '') for r in cls._mem_cache[server_key] if r.get('divisi'))
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

    # ──────────── Status ────────────

    @classmethod
    def get_status(cls, server_key):
        status = cls._refresh_status.get(server_key)

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

        in_memory = server_key in cls._mem_cache
        mem_count = len(cls._mem_cache.get(server_key, []))

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
        with cls._cache_lock:
            has_cache = server_key in cls._mem_cache
        
        stok_rows = []
        if has_cache:
            cache = cls._mem_cache.get(server_key, [])
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

    # ──────────── Auto Update ────────────

    @classmethod
    def start_auto_update(cls, interval=60):
        if cls._auto_update_thread and cls._auto_update_thread.is_alive():
            return
        cls._auto_update_stop_event = threading.Event()
        cls._auto_update_thread = threading.Thread(
            target=cls._auto_update_loop,
            args=(interval,),
            daemon=True
        )
        cls._auto_update_thread.start()
        logging.info("[AUTO UPDATE] Background sequential auto-update started.")

    @classmethod
    def stop_auto_update(cls):
        if cls._auto_update_stop_event:
            cls._auto_update_stop_event.set()
        logging.info("[AUTO UPDATE] Background sequential auto-update stopped.")

    @classmethod
    def _auto_update_loop(cls, interval):
        from app.Models.Database import db_manager
        while cls._auto_update_stop_event and not cls._auto_update_stop_event.is_set():
            try:
                servers = db_manager.get_available_servers()
                tanggal = datetime.now().strftime('%Y-%m-%d')
                for server in servers:
                    if cls._auto_update_stop_event and cls._auto_update_stop_event.is_set():
                        break
                    
                    server_key = server['key']
                    
                    # Jangan ganggu jika sedang ada refresh manual / parallel
                    if server_key in cls._refresh_threads and cls._refresh_threads[server_key].is_alive():
                        continue
                    
                    db_path = cls._db_path(server_key)
                    if not os.path.exists(db_path):
                        continue # Perlu refresh awal secara manual terlebih dahulu
                        
                    # Get last_refresh
                    try:
                        conn = sqlite3.connect(db_path)
                        row = conn.execute("SELECT value FROM snapshot_meta WHERE key='last_refresh'").fetchone()
                        conn.close()
                        if not row:
                            continue
                        last_refresh = row[0]
                    except Exception:
                        continue
                    
                    old_status = cls._refresh_status.get(server_key)
                    # Hanya jalankan jika state saat ini ready, error, empty, atau cancelled (sedang idle)
                    if not old_status or old_status.get('state') in ('ready', 'error', 'empty', 'cancelled'):
                        cls._refresh_cancel[server_key] = threading.Event()
                        cls._refresh_status[server_key] = {
                            'state': 'starting',
                            'progress': 0,
                            'message': 'Memulai auto update (sequential)...',
                            'started_at': time.time(),
                            'row_count': 0,
                            'is_delta': True,
                        }
                        
                        try:
                            # Jalan sequential agar tidak menggunakan banyak pool connection ke DB utama
                            cls._do_delta_refresh(server_key, tanggal, last_refresh, is_sequential=True)
                        except Exception as e:
                            logging.error(f"[AUTO UPDATE] Error updating {server_key}: {e}")
                            
                    # Beri jeda antar server
                    time.sleep(2)
            except Exception as e:
                logging.error(f"[AUTO UPDATE] Loop error: {e}")
            
            if cls._auto_update_stop_event:
                cls._auto_update_stop_event.wait(interval)

