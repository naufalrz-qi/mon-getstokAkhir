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
from datetime import datetime
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal


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
        except:
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
    def _do_delta_refresh(cls, server_key, tanggal, last_refresh):
        """
        Background worker for delta refresh:
        1. Fetch only NEW transactions since last_refresh (single query)
        2. Apply debet/kredit changes to in-memory cache
        3. Update SQLite snapshot for affected items
        """
        from app.Models.Database import db_manager

        cancel = cls._refresh_cancel.get(server_key)
        status = cls._refresh_status[server_key]

        try:
            status['state'] = 'fetching'
            status['progress'] = 10
            status['message'] = 'Mengambil transaksi baru...'

            # Fetch delta from MSSQL
            sql = _load_sql('09_delta.sql')
            # Convert ISO timestamp to Python datetime for pyodbc
            last_refresh_dt = datetime.fromisoformat(last_refresh)
            conn_mssql = db_manager.create_new_connection(server_key)
            try:
                cursor = conn_mssql.cursor()
                cursor.execute(sql, [last_refresh_dt, tanggal])
                columns = [desc[0] for desc in cursor.description]
                delta_rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                cursor.close()
            finally:
                conn_mssql.close()

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

            # Get satuan konversi for conversion
            # Load from existing master cache or fetch fresh
            satuan_map = {}
            try:
                sql_sat = "SET NOCOUNT ON; SELECT kd_barang, kd_satuan, jumlah FROM m_barang_satuan (NOLOCK)"
                conn_sat = db_manager.create_new_connection(server_key)
                cursor = conn_sat.cursor()
                cursor.execute(sql_sat)
                for row in cursor.fetchall():
                    satuan_map[(row[0], row[1])] = float(row[2] or 1)
                cursor.close()
                conn_sat.close()
            except:
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

            status['progress'] = 70
            status['message'] = f'Memperbarui {len(delta_accum)} item...'

            # Apply delta to in-memory cache
            if server_key not in cls._mem_cache:
                cls._load_to_memory(server_key)

            cache = cls._mem_cache.get(server_key, [])

            # Build lookup for fast update
            cache_index = {}
            for i, row in enumerate(cache):
                key = (row.get('kd_divisi', ''), row.get('kd_barang', ''))
                cache_index[key] = i

            updated_keys = set()
            for (kd_divisi, kd_barang), delta_stok in delta_accum.items():
                key = (kd_divisi, kd_barang)
                if key in cache_index:
                    idx = cache_index[key]
                    cache[idx]['stok_akhir'] = round(cache[idx].get('stok_akhir', 0) + delta_stok, 4)
                    updated_keys.add(key)
                # If item not in cache, it might be new — skip for delta (full refresh will catch it)

            cls._mem_cache[server_key] = cache
            cls._mem_cache_ts[server_key] = time.time()

            # Update SQLite
            status['progress'] = 85
            status['message'] = 'Menyimpan perubahan ke snapshot...'

            db_path = cls._db_path(server_key)
            conn_db = None
            try:
                conn_db = cls._init_db(db_path)
                for (kd_divisi, kd_barang) in updated_keys:
                    idx = cache_index.get((kd_divisi, kd_barang))
                    if idx is not None:
                        new_stok = cache[idx]['stok_akhir']
                        conn_db.execute(
                            'UPDATE stok_snapshot SET stok_akhir = ? WHERE kd_divisi = ? AND kd_barang = ?',
                            (new_stok, kd_divisi, kd_barang),
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

            elapsed = round(time.time() - status['started_at'], 1)
            status['state'] = 'ready'
            status['progress'] = 100
            status['row_count'] = len(delta_rows)
            status['message'] = f'Quick update selesai! {len(delta_rows)} transaksi, {len(updated_keys)} item diupdate ({elapsed}s)'

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

            final_rows = cls._aggregate(fetch_results)

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
        return final_rows

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
    def search(cls, server_key, search_kode=None, search_nama=None, divisi=None):
        # Try memory cache first
        if server_key in cls._mem_cache:
            data = cls._filter_memory(server_key, search_kode, search_nama, divisi)
            return cls._build_result(data, source='memory')

        # Try loading from SQLite
        db_path = cls._db_path(server_key)
        if os.path.exists(db_path):
            cls._load_to_memory(server_key)
            if server_key in cls._mem_cache:
                data = cls._filter_memory(server_key, search_kode, search_nama, divisi)
                return cls._build_result(data, source='sqlite')

        return {
            'status': 'no_snapshot',
            'data': [],
            'summary': {'total_items': 0, 'divisi_count': 0, 'total_nominal': 0, 'avg_stok': 0},
            'message': 'Belum ada snapshot. Klik Refresh untuk memuat data.',
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
    def _build_result(cls, data, source='memory'):
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

        total_nominal = sum(r.get('Nominal', 0) for r in mapped)
        total_stok = sum(r.get('Stok Akhir', 0) for r in mapped)
        divisi_set = set(r.get('Divisi') for r in mapped)

        return {
            'status': 'success',
            'data': mapped,
            'summary': {
                'total_items': len(mapped),
                'total_nominal': round(total_nominal, 2),
                'divisi_count': len(divisi_set),
                'avg_stok': round(total_stok / len(mapped), 2) if mapped else 0,
                'divisi_list': sorted(list(divisi_set)),
            },
            'row_count': len(mapped),
            'source': source,
        }

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
            except:
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
        Fetch direct transaction history for ONE item in ONE division from MSSQL.
        This queries all tables in parallel to bypass slow views (mimicking mon_g_barang_histori).
        """
        from app.Models.Database import db_manager
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        try:
            queries = {
                'stok_awal': '''
                    SELECT bd.kd_divisi, dbo.GetTanggalTerakhirTutupBuku() AS tanggal, '0' AS no_transaksi, 
                           'Stok Awal' AS Transaksi, bd.stok_awal AS Debet, 0.0 AS Kredit, 
                           bs.kd_satuan, bd.harga_beli_awal as harga
                    FROM m_barang_divisi bd (NOLOCK)
                    INNER JOIN m_barang_satuan bs (NOLOCK) ON bd.kd_barang = bs.kd_barang
                    INNER JOIN m_barang b (NOLOCK) ON bd.kd_barang = b.kd_barang
                    INNER JOIN m_kategori k (NOLOCK) ON b.kd_kategori = k.kd_kategori
                    WHERE bs.jumlah = 1 AND k.status <> 2 AND bd.kd_barang = ?
                      AND (? = '' OR bd.kd_divisi = ?)
                ''',
                'mutasi_keluar': '''
                    SELECT t.kd_divisi_asal AS kd_divisi, t.tanggal, d.no_transaksi, 
                           'Mutasi Keluar' AS Transaksi, 0.0 AS Debet, d.qty AS Kredit, 
                           d.kd_satuan, 0.0 AS harga
                    FROM t_mutasi_stok_detail d (NOLOCK)
                    INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                    WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku() AND d.kd_barang = ?
                      AND (? = '' OR t.kd_divisi_asal = ?)
                ''',
                'mutasi_masuk': '''
                    SELECT t.kd_divisi_tujuan AS kd_divisi, t.tanggal, d.no_transaksi, 
                           'Mutasi Masuk' AS Transaksi, d.qty AS Debet, 0.0 AS Kredit, 
                           d.kd_satuan, 0.0 AS harga
                    FROM t_mutasi_stok_detail d (NOLOCK)
                    INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                    WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku() AND d.kd_barang = ?
                      AND (? = '' OR t.kd_divisi_tujuan = ?)
                ''',
                'opname_masuk': '''
                    SELECT kd_divisi, tanggal, no_transaksi, 'Opname Masuk' AS Transaksi, 
                           QTY AS Debet, 0.0 AS Kredit, kd_satuan, 0.0 AS harga
                    FROM t_opname_stok (NOLOCK)
                    WHERE status = 2 AND tanggal > dbo.GetTanggalTerakhirTutupBuku() AND kd_barang = ?
                      AND (? = '' OR kd_divisi = ?)
                ''',
                'opname_keluar': '''
                    SELECT kd_divisi, tanggal, no_transaksi, 'Opname Keluar' AS Transaksi, 
                           0.0 AS Debet, qty AS Kredit, kd_satuan, 0.0 AS harga
                    FROM t_opname_stok (NOLOCK)
                    WHERE status <> 2 AND tanggal > dbo.GetTanggalTerakhirTutupBuku() AND kd_barang = ?
                      AND (? = '' OR kd_divisi = ?)
                ''',
                'pembelian': '''
                    SELECT t.kd_divisi, t.tanggal, d.no_transaksi, 'Pembelian' AS Transaksi, 
                           d.qty AS Debet, 0.0 AS Kredit, d.kd_satuan, d.harga_beli AS harga
                    FROM t_pembelian_detail d (NOLOCK)
                    INNER JOIN t_pembelian t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                    WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku() AND t.status IN (0, 1) AND d.kd_barang = ?
                      AND (? = '' OR t.kd_divisi = ?)
                ''',
                'retur_pembelian': '''
                    SELECT t.kd_divisi, t.tanggal, d.no_retur AS no_transaksi, 'Retur Pembelian' AS Transaksi, 
                           0.0 AS Debet, d.qty AS Kredit, d.kd_satuan, d.harga AS harga
                    FROM t_pembelian_retur_detail d (NOLOCK)
                    INNER JOIN t_pembelian_retur t (NOLOCK) ON d.no_retur = t.no_retur
                    WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku() AND d.kd_barang = ?
                      AND (? = '' OR t.kd_divisi = ?)
                ''',
                'penjualan': '''
                    SELECT t.kd_divisi, t.tanggal, d.no_transaksi, 'Penjualan' AS Transaksi, 
                           0.0 AS Debet, d.qty AS Kredit, d.kd_satuan, d.harga_jual AS harga
                    FROM t_penjualan_detail d (NOLOCK)
                    INNER JOIN t_penjualan t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                    INNER JOIN m_barang b (NOLOCK) ON d.kd_barang = b.kd_barang
                    INNER JOIN m_kategori k (NOLOCK) ON b.kd_kategori = k.kd_kategori
                    WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku() AND k.status <> 2 AND d.kd_barang = ?
                      AND (? = '' OR t.kd_divisi = ?)
                ''',
                'retur_penjualan': '''
                    SELECT t.kd_divisi, t.tanggal, d.no_retur AS no_transaksi, 'Retur Penjualan Dengan Nota' AS Transaksi, 
                           d.qty AS Debet, 0.0 AS Kredit, d.kd_satuan, d.harga_jual AS harga
                    FROM t_penjualan_retur_detail d (NOLOCK)
                    INNER JOIN t_penjualan_retur t (NOLOCK) ON d.no_retur = t.no_retur
                    WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku() AND d.kd_barang = ?
                      AND (? = '' OR t.kd_divisi = ?)
                ''',
                'master_barang': 'SELECT nama, kd_barang FROM m_barang (NOLOCK) WHERE kd_barang = ?',
                'master_divisi': 'SELECT kd_divisi, keterangan, kepala_nota FROM m_divisi (NOLOCK)',
                'master_satuan': 'SELECT kd_satuan, nama FROM m_satuan (NOLOCK)',
                'satuan_konversi': 'SELECT kd_satuan, jumlah FROM m_barang_satuan (NOLOCK) WHERE kd_barang = ?'
            }

            def _fetch_component(name, sql):
                conn = None
                try:
                    conn = db_manager.create_new_connection(server_key)
                    cursor = conn.cursor()
                    if name in ['master_divisi', 'master_satuan']:
                        cursor.execute(sql)
                    elif name in ['master_barang', 'satuan_konversi']:
                        cursor.execute(sql, [kd_barang])
                    else:
                        cursor.execute(sql, [kd_barang, kd_divisi or '', kd_divisi or ''])
                    
                    columns = [desc[0] for desc in cursor.description]
                    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                    cursor.close()
                    return name, rows
                except Exception as e:
                    return name, f"ERROR: {str(e)}"
                finally:
                    if conn:
                        try:
                            conn.close()
                        except:
                            pass

            results = {}
            with ThreadPoolExecutor(max_workers=6) as executor:
                futures = {executor.submit(_fetch_component, name, sql): name for name, sql in queries.items()}
                for future in as_completed(futures):
                    name = futures[future]
                    res = future.result()
                    if isinstance(res[1], str) and res[1].startswith('ERROR'):
                        results[name] = []
                    else:
                        results[name] = res[1]

            # Collect Master Data
            barang_nama = results['master_barang'][0]['nama'] if results.get('master_barang') else ''
            
            divisi_map = {}
            for d in results.get('master_divisi', []):
                divisi_map[d['kd_divisi']] = {
                    'keterangan': d.get('keterangan', ''),
                    'kepala_nota': d.get('kepala_nota', '')
                }
                
            satuan_map = {}
            for s in results.get('master_satuan', []):
                satuan_map[s['kd_satuan']] = s.get('nama', '')
                
            konversi_map = {}
            for k in results.get('satuan_konversi', []):
                konversi_map[k['kd_satuan']] = float(k.get('jumlah') or 1.0)

            # Combine transaction tables
            all_transactions = []
            transaction_keys = [
                'stok_awal', 'mutasi_keluar', 'mutasi_masuk', 'opname_masuk', 
                'opname_keluar', 'pembelian', 'retur_pembelian', 'penjualan', 'retur_penjualan'
            ]
            for key in transaction_keys:
                all_transactions.extend(results.get(key, []))

            # Assemble Final Output
            final_data = []
            for row in all_transactions:
                kd_div = row.get('kd_divisi') or ''
                div_info = divisi_map.get(kd_div, {})
                sat_nama = satuan_map.get(row.get('kd_satuan'), '')
                konversi = konversi_map.get(row.get('kd_satuan'), 1.0)

                tgl = row.get('tanggal')
                if isinstance(tgl, datetime):
                    tgl = tgl.strftime('%Y-%m-%d %H:%M:%S')

                debet = float(row.get('Debet') or 0)
                kredit = float(row.get('Kredit') or 0)
                harga = float(row.get('harga') or 0)

                final_data.append({
                    'Kd_Divisi': kd_div,
                    'Divisi': div_info.get('keterangan', ''),
                    'K.Nota': div_info.get('kepala_nota', ''),
                    'tanggal': tgl,
                    'Transaksi': row.get('Transaksi', ''),
                    'no_transaksi': row.get('no_transaksi', ''),
                    'kd_barang': kd_barang,
                    'barang': barang_nama,
                    'Debet': debet,
                    'Kredit': kredit,
                    'kd_satuan': row.get('kd_satuan', ''),
                    'satuan': sat_nama,
                    'harga': harga,
                    'Konversi': konversi
                })

            if start_date or end_date:
                filtered_data = []
                for r in final_data:
                    tgl_str = r['tanggal']
                    if not tgl_str:
                        continue
                    # Lexical comparison on YYYY-MM-DD
                    if start_date and tgl_str[:10] < start_date:
                        continue
                    if end_date and tgl_str[:10] > end_date:
                        continue
                    filtered_data.append(r)
                final_data = filtered_data

            # Sort mimicking: ORDER BY dbo.m_divisi.kd_divisi, dbo.v_g_barang_histori_detail.tanggal
            final_data.sort(key=lambda x: (x['Kd_Divisi'], x['tanggal'] or ''))

            return {
                'status': 'success',
                'data': final_data,
                'row_count': len(final_data)
            }
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return {'status': 'error', 'message': str(e)}
