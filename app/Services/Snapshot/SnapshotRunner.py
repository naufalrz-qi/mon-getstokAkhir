import threading
import time
import uuid
import datetime

from app.Services.Snapshot.SnapshotState import SnapshotState
from app.Models.Database import db_manager

class SnapshotRunner:

    @classmethod
    def trigger_perhitungan_stok(cls, server_key, start_date, end_date, use_stok_awal=False):
        if server_key in SnapshotState._perhitungan_threads and SnapshotState._perhitungan_threads[server_key].is_alive():
            return {'status': 'already_running', 'message': 'Perhitungan stok sedang berjalan'}

        SnapshotState._perhitungan_status[server_key] = {
            'state': 'starting',
            'progress': 0,
            'message': 'Menghitung stok...',
            'started_at': time.time()
        }

        t = threading.Thread(
            target=cls._do_perhitungan_stok,
            args=(server_key, start_date, end_date, use_stok_awal),
            daemon=True
        )
        SnapshotState._perhitungan_threads[server_key] = t
        t.start()
        return {'status': 'started', 'message': 'Perhitungan stok dimulai'}

    @classmethod
    def _do_perhitungan_stok(cls, server_key, start_date, end_date, use_stok_awal=False):
        status = SnapshotState._perhitungan_status[server_key]
        
        try:
            status['state'] = 'fetching'
            status['progress'] = 10
            status['message'] = 'Menyiapkan perhitungan...'
            _t0 = time.time()
            
            # If using stok awal, the absolute truth starts strictly after Tutup Buku.
            if use_stok_awal:
                conn = None
                try:
                    conn = db_manager.create_new_connection(server_key)
                    cursor = conn.cursor()
                    cursor.execute("SELECT CONVERT(VARCHAR(10), DATEADD(DAY, 1, dbo.GetTanggalTerakhirTutupBuku()), 120)")
                    row = cursor.fetchone()
                    if row and row[0]:
                        start_date = row[0]
                    cursor.close()
                except Exception:
                    pass
                finally:
                    if conn:
                        try: conn.close()
                        except: pass

            status['progress'] = 20
            status['message'] = 'Mengambil data master barang & divisi...'

            barang_map = {}
            divisi_map = {}
            harga_beli_map = {}
            harga_avg_map = {}
            satuan_map = {}

            conn = None
            try:
                conn = db_manager.create_new_connection(server_key)
                cursor = conn.cursor()

                # Master Divisi
                cursor.execute("SELECT kd_divisi, nama FROM m_divisi (NOLOCK)")
                for r in cursor.fetchall():
                    divisi_map[r[0]] = r[1]

                # Master Barang
                cursor.execute("""
                    SELECT b.kd_barang, b.barang as nama, b.merk, b.model, b.warna, b.ukuran, 
                           ISNULL((SELECT TOP 1 harga_jual FROM m_barang_satuan s (NOLOCK) WHERE s.kd_barang = b.kd_barang), 0) as harga_jual, 
                           b.kategori
                    FROM v_m_barang b (NOLOCK)
                    WHERE b.status <> 2 OR b.status IS NULL
                """)
                for r in cursor.fetchall():
                    barang_map[r[0]] = {
                        'nama': r[1] or '',
                        'merk': r[2] or '',
                        'model': r[3] or '',
                        'warna': r[4] or '',
                        'ukuran': r[5] or '',
                        'harga_jual': float(r[6] or 0),
                        'kategori': r[7] or ''
                    }

                # Harga Beli Akhir
                cursor.execute("""
                    SELECT kd_barang, harga_beli FROM (
                        SELECT kd_barang, harga_beli, ROW_NUMBER() OVER(PARTITION BY kd_barang ORDER BY no_transaksi DESC) as rn 
                        FROM t_pembelian_detail (NOLOCK)
                    ) x WHERE rn=1
                """)
                for r in cursor.fetchall():
                    harga_beli_map[r[0]] = float(r[1] or 0)

                # Harga Avg
                cursor.execute("""
                    SELECT kd_barang, sum(total)/sum(qty) 
                    FROM t_pembelian_detail (NOLOCK) 
                    GROUP BY kd_barang HAVING sum(qty) > 0
                """)
                for r in cursor.fetchall():
                    harga_avg_map[r[0]] = float(r[1] or 0)

                # Satuan Konversi
                cursor.execute("SELECT kd_barang, kd_satuan, jumlah, satuan as nama_satuan FROM v_m_barang_satuan (NOLOCK)")
                for r in cursor.fetchall():
                    satuan_map[(r[0], r[1])] = {
                        'jumlah': float(r[2] or 1),
                        'nama_satuan': r[3] or r[1]
                    }

                cursor.close()
            finally:
                if conn:
                    try: conn.close()
                    except: pass

            status['progress'] = 50
            status['message'] = f'Mengambil mutasi transaksi dari {start_date} hingga {end_date}...'

            query = """
            SET NOCOUNT ON;
            SELECT kd_divisi, kd_barang, SUM(debet) - SUM(kredit) AS net_stok
            FROM (
                -- Pembelian
                SELECT t.kd_divisi, d.kd_barang, d.qty * COALESCE(s.jumlah, 1) AS debet, 0 AS kredit
                FROM t_pembelian_detail d (NOLOCK)
                INNER JOIN t_pembelian t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                LEFT JOIN m_barang_satuan s (NOLOCK) ON d.kd_barang = s.kd_barang AND d.kd_satuan = s.kd_satuan
                WHERE CAST(t.tanggal AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                  AND t.status IN (0, 1)

                UNION ALL
                -- Penjualan
                SELECT t.kd_divisi, d.kd_barang, 0 AS debet, d.qty * COALESCE(s.jumlah, 1) AS kredit
                FROM t_penjualan_detail d (NOLOCK)
                INNER JOIN t_penjualan t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                LEFT JOIN m_barang_satuan s (NOLOCK) ON d.kd_barang = s.kd_barang AND d.kd_satuan = s.kd_satuan
                WHERE CAST(t.tanggal AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                  AND t.status IN (0, 1)

                UNION ALL
                -- Mutasi Out
                SELECT t.kd_divisi_asal AS kd_divisi, d.kd_barang, 0 AS debet, d.qty * COALESCE(s.jumlah, 1) AS kredit
                FROM t_mutasi_stok_detail d (NOLOCK)
                INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                LEFT JOIN m_barang_satuan s (NOLOCK) ON d.kd_barang = s.kd_barang AND d.kd_satuan = s.kd_satuan
                WHERE CAST(t.tanggal AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)

                UNION ALL
                -- Mutasi In
                SELECT t.kd_divisi_tujuan AS kd_divisi, d.kd_barang, d.qty * COALESCE(s.jumlah, 1) AS debet, 0 AS kredit
                FROM t_mutasi_stok_detail d (NOLOCK)
                INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
                LEFT JOIN m_barang_satuan s (NOLOCK) ON d.kd_barang = s.kd_barang AND d.kd_satuan = s.kd_satuan
                WHERE CAST(t.tanggal AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)

                UNION ALL
                -- Retur Jual
                SELECT t.kd_divisi, d.kd_barang, d.qty * COALESCE(s.jumlah, 1) AS debet, 0 AS kredit
                FROM t_penjualan_retur_detail d (NOLOCK)
                INNER JOIN t_penjualan_retur t (NOLOCK) ON d.no_retur = t.no_retur
                LEFT JOIN m_barang_satuan s (NOLOCK) ON d.kd_barang = s.kd_barang AND d.kd_satuan = s.kd_satuan
                WHERE CAST(t.tanggal AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)

                UNION ALL
                -- Retur Beli
                SELECT t.kd_divisi, d.kd_barang, 0 AS debet, d.qty * COALESCE(s.jumlah, 1) AS kredit
                FROM t_pembelian_retur_detail d (NOLOCK)
                INNER JOIN t_pembelian_retur t (NOLOCK) ON d.no_retur = t.no_retur
                LEFT JOIN m_barang_satuan s (NOLOCK) ON d.kd_barang = s.kd_barang AND d.kd_satuan = s.kd_satuan
                WHERE CAST(t.tanggal AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)


                UNION ALL
                -- Opname Masuk
                SELECT d.kd_divisi, d.kd_barang, d.qty * COALESCE(s.jumlah, 1) AS debet, 0 AS kredit
                FROM t_opname_stok d (NOLOCK)
                LEFT JOIN m_barang_satuan s (NOLOCK) ON d.kd_barang = s.kd_barang AND d.kd_satuan = s.kd_satuan
                WHERE CAST(d.tanggal AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                  AND d.status = 2

                UNION ALL
                -- Opname Keluar
                SELECT d.kd_divisi, d.kd_barang, 0 AS debet, d.qty * COALESCE(s.jumlah, 1) AS kredit
                FROM t_opname_stok d (NOLOCK)
                LEFT JOIN m_barang_satuan s (NOLOCK) ON d.kd_barang = s.kd_barang AND d.kd_satuan = s.kd_satuan
                WHERE CAST(d.tanggal AS DATE) BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
                  AND d.status <> 2
            ) AS all_trans
            GROUP BY kd_divisi, kd_barang
            """
            
            params = [start_date, end_date] * 8

            stok_awal_query = """
            UNION ALL
            -- Stok Awal Aktual
            SELECT bd.kd_divisi, bd.kd_barang, bd.stok_awal AS debet, 0 AS kredit
            FROM m_barang_divisi bd (NOLOCK)
            INNER JOIN m_barang b (NOLOCK) ON bd.kd_barang = b.kd_barang
            INNER JOIN m_kategori k (NOLOCK) ON b.kd_kategori = k.kd_kategori
            WHERE k.status <> 2 OR k.status IS NULL
            """
            query = query.replace(") AS all_trans", stok_awal_query + "\n            ) AS all_trans")
            
            net_stok_map = {}
            conn = None
            try:
                conn = db_manager.create_new_connection(server_key)
                cursor = conn.cursor()
                cursor.execute(query, params)
                for row in cursor.fetchall():
                    kd_divisi, kd_barang, net_stok = row[0], row[1], float(row[2] or 0)
                    net_stok_map[(kd_divisi, kd_barang)] = round(net_stok, 4)
                cursor.close()
            finally:
                if conn:
                    try: conn.close()
                    except: pass
            
            status['progress'] = 80
            status['message'] = 'Menyusun hasil akhir...'

            final_rows = []
            
            # Combine net_stok with master
            # To ensure we get ALL barang even with 0 stock (like stok_awal logic sometimes wants), 
            # we should iterate over barang_map and cross with divisi? No, we only return what has stock or was in master_cache.
            # Wait, previously we iterated over master_cache which had ALL kd_divisi and kd_barang combinations from stok_snapshot.
            # If we don't have stok_snapshot, we only know kd_divisi, kd_barang from net_stok_map!
            # If a barang has 0 net stok and no transactions, it won't appear. Is that okay?
            # Yes, usually we only care about barang that have transactions or stock.
            # Wait, stok awal will be included because of the use_stok_awal query (UNION ALL m_barang_divisi).
            # What if someone selects a range that doesn't use_stok_awal? They only see items with mutations. That's correct for "Perhitungan Range".
            
            # So iterating over net_stok_map is CORRECT.
            for (kd_divisi, kd_barang), net_stok in net_stok_map.items():
                if kd_barang not in barang_map:
                    continue
                
                master = barang_map[kd_barang]
                hpp = harga_beli_map.get(kd_barang, 0)
                
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
                    'stok_akhir': net_stok,
                    'harga_jual': master.get('harga_jual', 0),
                    'harga_beli_akhir': hpp,
                    'harga_avg': harga_avg_map.get(kd_barang, 0),
                    'nominal': net_stok * hpp
                })

            final_rows.sort(key=lambda r: (r['divisi'], r['barang']))

            # Extract satuan rows from satuan_map
            satuan_rows = []
            for (kd_barang, kd_satuan), sat in satuan_map.items():
                satuan_rows.append({
                    'kd_barang': kd_barang,
                    'kd_satuan': kd_satuan,
                    'jumlah': sat['jumlah'],
                    'nama_satuan': sat['nama_satuan']
                })

            with SnapshotState._cache_lock:
                SnapshotState._perhitungan_cache[server_key] = {
                    'data': final_rows,
                    'satuan': satuan_rows,
                    'opname': [] # Not populated for simple monitoring anymore
                }

            _t_end = time.time()
            elapsed = round(_t_end - _t0, 2)
            status['state'] = 'ready'
            status['progress'] = 100
            status['row_count'] = len(final_rows)
            status['message'] = f'Selesai! {len(final_rows)} baris dihitung dalam {elapsed}s.'

        except Exception as e:
            status['state'] = 'error'
            status['progress'] = 0
            status['message'] = f"Error: {str(e)}"
            print(f"[PERHITUNGAN ERROR] {e}")

