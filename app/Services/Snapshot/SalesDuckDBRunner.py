import threading
import time
from datetime import datetime, timedelta
from app.Models.Database import DatabaseManager
from app.Services.Snapshot.SalesDuckDBCore import SalesDuckDBCore

class SalesDuckDBRunner:
    _sync_threads = {}
    _sync_status = {}


    @classmethod
    def trigger_delta_sync(cls, server_key):
        """
        Delta sync: ambil data baru sejak last_sync_date.
        Jika belum pernah sync, fallback ke full sync 30 hari.
        """
        if server_key in cls._sync_threads and cls._sync_threads[server_key].is_alive():
            return {'status': 'already_running', 'message': 'Sinkronisasi penjualan sedang berjalan'}

        cls._sync_status[server_key] = {
            'state': 'starting',
            'progress': 0,
            'message': 'Memulai Delta Sync DuckDB...',
            'started_at': time.time(),
        }

        t = threading.Thread(
            target=cls._do_delta_sync,
            args=(server_key,),
            daemon=True,
        )
        cls._sync_threads[server_key] = t
        t.start()
        return {'status': 'started', 'message': 'Delta Sync DuckDB dimulai'}

    @classmethod
    def _do_delta_sync(cls, server_key):
        from app.Services.Snapshot.SalesDuckDBCore import SalesDuckDBCore
        try:
            duck_conn = SalesDuckDBCore.get_connection(server_key)
            # Make sure sync_meta exists (should be created by init_db)
            row = duck_conn.execute("SELECT value FROM sync_meta WHERE key='last_sync_date'").fetchone()
            duck_conn.close()
            
            if not row:
                # Belum pernah sync -> fallback ke full sync 30 hari
                cls._do_sync(server_key, days_back=30)
                return
                
            last_date = row[0]
            today = datetime.now().strftime('%Y-%m-%d')
            
            cls._sync_status[server_key]['message'] = f'Memulai Delta Sync ({last_date} s.d {today})...'
            cls._sync_status[server_key]['progress'] = 10
            
            success = cls.sync_date_range(server_key, last_date, today)
            
            if success:
                cls._sync_status[server_key]['state'] = 'finished'
                cls._sync_status[server_key]['progress'] = 100
                cls._sync_status[server_key]['message'] = 'Selesai: Delta Sync berhasil.'
            else:
                cls._sync_status[server_key]['state'] = 'error'
                cls._sync_status[server_key]['message'] = 'Gagal melakukan Delta Sync.'
                
        except Exception as e:
            try: duck_conn.execute("ROLLBACK")
            except: pass
            cls._sync_status[server_key]['state'] = 'error'
            cls._sync_status[server_key]['message'] = f'Error Delta Sync: {str(e)}'

    @classmethod
    def sync_sales_data(cls, server_key, days_back=30):
        """
        Tarik data penjualan dari SQL Server (beberapa hari ke belakang) dan simpan ke DuckDB.
        """
        if server_key in cls._sync_threads and cls._sync_threads[server_key].is_alive():
            return {'status': 'already_running', 'message': 'Sinkronisasi penjualan sedang berjalan'}

        cls._sync_status[server_key] = {
            'state': 'starting',
            'progress': 0,
            'message': 'Memulai sinkronisasi DuckDB...',
            'started_at': time.time(),
        }

        t = threading.Thread(
            target=cls._do_sync,
            args=(server_key, days_back),
            daemon=True,
        )
        cls._sync_threads[server_key] = t
        t.start()
        return {'status': 'started', 'message': 'Sinkronisasi DuckDB dimulai'}

    @classmethod
    def _sync_table_csv(cls, sql_conn, duck_conn, duck_table, sql_query, query_params=None):
        import os
        import uuid
        import csv
        from app.Services.Snapshot.SalesDuckDBCore import SNAPSHOTS_DIR
        
        cursor = sql_conn.cursor()
        if query_params:
            cursor.execute(sql_query, query_params)
        else:
            cursor.execute(sql_query)
        
        rows = cursor.fetchall()
        if not rows:
            return False
            
        columns = [column[0] for column in cursor.description]

        temp_path = os.path.join(SNAPSHOTS_DIR, f"temp_{uuid.uuid4().hex}.csv")
        with open(temp_path, 'w', newline='', encoding='utf-8', errors='replace') as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(rows)
            
        duck_conn.execute(f"INSERT INTO {duck_table} SELECT * FROM read_csv_auto('{temp_path}', header=True)")
        
        try:
            os.remove(temp_path)
        except:
            pass
        return True


    @classmethod
    def sync_date_range(cls, server_key, start_date_str, end_date_str):
        from app.Models.Database import DatabaseManager
        from app.Services.Snapshot.SalesDuckDBCore import SalesDuckDBCore
        try:
            db_manager = DatabaseManager()
            sql_conn = db_manager.create_new_connection(server_key)
            duck_conn = SalesDuckDBCore.get_connection(server_key)
            
            start_str = start_date_str + ' 00:00:00.000'
            end_str = end_date_str + ' 23:59:59.999'
            
            duck_conn.execute("BEGIN TRANSACTION")
            
            # --- 1. EXTRACT TRANSACTIONS ---
            duck_conn.execute(f"DELETE FROM raw_t_penjualan WHERE tanggal >= '{start_date_str}' AND tanggal <= '{end_date_str}'")
            q_header = """
                SELECT no_transaksi, tanggal, kd_customer, kd_divisi, kd_user, kd_voucher, status, diskon_uang, pajak, keterangan 
                FROM t_penjualan 
                WHERE tanggal >= ? AND tanggal <= ?
            """
            cls._sync_table_csv(sql_conn, duck_conn, 'raw_t_penjualan', q_header, [start_str, end_str])
            
            duck_conn.execute(f"DELETE FROM raw_t_penjualan_detail WHERE no_transaksi IN (SELECT no_transaksi FROM raw_t_penjualan WHERE tanggal >= '{start_date_str}' AND tanggal <= '{end_date_str}')")
            q_detail = """
                SELECT d.no_transaksi, d.kd_barang, d.kd_pegawai, d.qty, d.harga_jual, d.diskon1, d.diskon2, d.diskon3, d.diskon4, d.total as subtotal 
                FROM t_penjualan_detail d
                JOIN t_penjualan p ON d.no_transaksi = p.no_transaksi
                WHERE p.tanggal >= ? AND p.tanggal <= ?
            """
            cls._sync_table_csv(sql_conn, duck_conn, 'raw_t_penjualan_detail', q_detail, [start_str, end_str])
            
            # Master data sync optionally, we'll sync masters if they are completely empty to save time
            check_master = duck_conn.execute("SELECT COUNT(*) FROM raw_m_barang").fetchone()[0]
            if check_master == 0:
                cls._sync_table_csv(sql_conn, duck_conn, 'raw_m_barang', "SELECT kd_barang, nama FROM m_barang")
                cls._sync_table_csv(sql_conn, duck_conn, 'raw_m_customer', "SELECT kd_customer, kd_kota, nama FROM m_customer")
                cls._sync_table_csv(sql_conn, duck_conn, 'raw_m_pegawai', "SELECT kd_pegawai, nama FROM m_pegawai")
                cls._sync_table_csv(sql_conn, duck_conn, 'raw_m_divisi', "SELECT kd_divisi, nama FROM m_divisi")
                cls._sync_table_csv(sql_conn, duck_conn, 'raw_m_kota', "SELECT kd_kota, nama FROM m_kota")
                cls._sync_table_csv(sql_conn, duck_conn, 'raw_m_userx', "SELECT kd_user, nama FROM m_userx")
                cls._sync_table_csv(sql_conn, duck_conn, 'raw_m_voucher', "SELECT kd_voucher, nominal FROM m_voucher")
            
            # Rebuild sales_detail for this range
            duck_conn.execute(f"DELETE FROM sales_detail WHERE CAST(tanggal AS DATE) >= '{start_date_str}' AND CAST(tanggal AS DATE) <= '{end_date_str}'")
            duck_conn.execute(f"""
                INSERT INTO sales_detail
                SELECT 
                    p.no_transaksi,
                    p.tanggal,
                    COALESCE(c.nama, 'Umum') as customer,
                    COALESCE(v.nama, 'Unknown') as divisi,
                    COALESCE(pg.nama, 'Unknown') as pegawai,
                    d.kd_barang,
                    COALESCE(b.nama, 'Unknown') as barang,
                    '-' as satuan,
                    d.qty,
                    d.subtotal,
                    SUM(d.subtotal) OVER (PARTITION BY p.no_transaksi) as total_penjualan
                FROM raw_t_penjualan p
                JOIN raw_t_penjualan_detail d ON p.no_transaksi = d.no_transaksi
                LEFT JOIN raw_m_barang b ON d.kd_barang = b.kd_barang
                LEFT JOIN raw_m_pegawai pg ON d.kd_pegawai = pg.kd_pegawai
                LEFT JOIN raw_m_customer c ON p.kd_customer = c.kd_customer
                LEFT JOIN raw_m_divisi v ON p.kd_divisi = v.kd_divisi
                WHERE CAST(p.tanggal AS DATE) >= '{start_date_str}' AND CAST(p.tanggal AS DATE) <= '{end_date_str}'
            """)

            # Update sync_meta
            iso_now = datetime.now().isoformat()
            duck_conn.execute("""
                INSERT OR REPLACE INTO sync_meta VALUES ('last_sync_date', ?), 
                                                        ('last_sync_timestamp', ?)
            """, [end_date_str, iso_now])

            duck_conn.execute("COMMIT")
            
            sql_conn.close()
            duck_conn.close()
            return True
        except Exception as e:
            try: duck_conn.execute("ROLLBACK")
            except: pass
            print(f'Quick Sync Error: {e}')
            return False

    @classmethod
    def _do_sync(cls, server_key, days_back):
        try:
            db_manager = DatabaseManager()
            sql_conn = db_manager.create_new_connection(server_key)
            duck_conn = SalesDuckDBCore.get_connection(server_key)
            
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days_back)
            
            start_str = start_date.strftime('%Y-%m-%d 00:00:00.000')
            end_str = end_date.strftime('%Y-%m-%d 23:59:59.999')
            
            # --- 1. EXTRACT MASTER DATA (JSON Pipeline) ---
            cls._sync_status[server_key]['message'] = '1/6 [Memulai] Menarik Master Barang (CSV)...'
            duck_conn.execute("BEGIN TRANSACTION")
            duck_conn.execute("DELETE FROM raw_m_barang")
            cls._sync_table_csv(sql_conn, duck_conn, 'raw_m_barang', "SELECT kd_barang, nama FROM m_barang")
            cls._sync_status[server_key]['progress'] = 5
            
            cls._sync_status[server_key]['message'] = '2/6 [Memulai] Menarik Master Customer (CSV)...'
            duck_conn.execute("DELETE FROM raw_m_customer")
            cls._sync_table_csv(sql_conn, duck_conn, 'raw_m_customer', "SELECT kd_customer, kd_kota, nama FROM m_customer")
            cls._sync_status[server_key]['progress'] = 10
            
            cls._sync_status[server_key]['message'] = '3/6 [Memulai] Menarik Master Pegawai (CSV)...'
            duck_conn.execute("DELETE FROM raw_m_pegawai")
            cls._sync_table_csv(sql_conn, duck_conn, 'raw_m_pegawai', "SELECT kd_pegawai, nama FROM m_pegawai")
            cls._sync_status[server_key]['progress'] = 15
            
            cls._sync_status[server_key]['message'] = '4/6 [Memulai] Menarik Master Divisi (CSV)...'
            duck_conn.execute("DELETE FROM raw_m_divisi")
            cls._sync_table_csv(sql_conn, duck_conn, 'raw_m_divisi', "SELECT kd_divisi, nama FROM m_divisi")
            cls._sync_status[server_key]['progress'] = 20
            
            duck_conn.execute("DELETE FROM raw_m_kota")
            cls._sync_table_csv(sql_conn, duck_conn, 'raw_m_kota', "SELECT kd_kota, nama FROM m_kota")
            
            duck_conn.execute("DELETE FROM raw_m_userx")
            cls._sync_table_csv(sql_conn, duck_conn, 'raw_m_userx', "SELECT kd_user, nama FROM m_userx")
            
            duck_conn.execute("DELETE FROM raw_m_voucher")
            cls._sync_table_csv(sql_conn, duck_conn, 'raw_m_voucher', "SELECT kd_voucher, nominal FROM m_voucher")
            
            # --- 2. EXTRACT TRANSACTIONS (JSON Pipeline) ---
            cls._sync_status[server_key]['message'] = f'5/6 [Mengunduh] Transaksi Induk SQL Server ({start_str[:10]} s.d {end_str[:10]})...'
            duck_conn.execute(f"DELETE FROM raw_t_penjualan WHERE tanggal >= '{start_str[:10]}' AND tanggal <= '{end_str[:10]}'")
            
            q_header = """
                SELECT no_transaksi, tanggal, kd_customer, kd_divisi, kd_user, kd_voucher, status, diskon_uang, pajak, keterangan 
                FROM t_penjualan 
                WHERE tanggal >= ? AND tanggal <= ?
            """
            has_header = cls._sync_table_csv(sql_conn, duck_conn, 'raw_t_penjualan', q_header, [start_str, end_str])
            cls._sync_status[server_key]['progress'] = 40
            
            cls._sync_status[server_key]['message'] = f'6/6 [Mengunduh] Transaksi Detail SQL Server ({start_str[:10]} s.d {end_str[:10]})...'
            duck_conn.execute(f"DELETE FROM raw_t_penjualan_detail WHERE no_transaksi IN (SELECT no_transaksi FROM raw_t_penjualan WHERE tanggal >= '{start_str[:10]}' AND tanggal <= '{end_str[:10]}')")
            
            q_detail = """
                SELECT d.no_transaksi, d.kd_barang, d.kd_pegawai, d.qty, d.harga_jual, d.diskon1, d.diskon2, d.diskon3, d.diskon4, d.total as subtotal 
                FROM t_penjualan_detail d
                JOIN t_penjualan p ON d.no_transaksi = p.no_transaksi
                WHERE p.tanggal >= ? AND p.tanggal <= ?
            """
            cls._sync_table_csv(sql_conn, duck_conn, 'raw_t_penjualan_detail', q_detail, [start_str, end_str])
            cls._sync_status[server_key]['progress'] = 60
            
            if not has_header:
                # Update sync_meta even if no data
                iso_now = datetime.now().isoformat()
                duck_conn.execute("""
                    INSERT OR REPLACE INTO sync_meta VALUES ('last_sync_date', ?), 
                                                            ('last_sync_timestamp', ?)
                """, [end_str[:10], iso_now])
                duck_conn.execute("COMMIT")
                sql_conn.close()
                duck_conn.close()
                cls._sync_status[server_key]['state'] = 'finished'
                cls._sync_status[server_key]['message'] = 'Selesai (Tidak ada data transaksi baru)'
                return
                
            sql_conn.close()
                
            cls._sync_status[server_key]['progress'] = 80
            
            # --- 3. TRANSFORM (Local DuckDB JOIN) ---
            cls._sync_status[server_key]['message'] = 'Tahap Akhir: Merakit Data (JOIN lokal super cepat)...'
            duck_conn.execute(f"DELETE FROM sales_detail WHERE CAST(tanggal AS DATE) >= '{start_str[:10]}' AND CAST(tanggal AS DATE) <= '{end_str[:10]}'")
            duck_conn.execute(f"""
                INSERT INTO sales_detail
                SELECT 
                    p.no_transaksi,
                    p.tanggal,
                    COALESCE(c.nama, 'Umum') as customer,
                    COALESCE(v.nama, 'Unknown') as divisi,
                    COALESCE(pg.nama, 'Unknown') as pegawai,
                    d.kd_barang,
                    COALESCE(b.nama, 'Unknown') as barang,
                    '-' as satuan,
                    d.qty,
                    d.subtotal,
                    SUM(d.subtotal) OVER (PARTITION BY p.no_transaksi) as total_penjualan
                FROM raw_t_penjualan p
                JOIN raw_t_penjualan_detail d ON p.no_transaksi = d.no_transaksi
                LEFT JOIN raw_m_barang b ON d.kd_barang = b.kd_barang
                LEFT JOIN raw_m_pegawai pg ON d.kd_pegawai = pg.kd_pegawai
                LEFT JOIN raw_m_customer c ON p.kd_customer = c.kd_customer
                LEFT JOIN raw_m_divisi v ON p.kd_divisi = v.kd_divisi
                WHERE CAST(p.tanggal AS DATE) >= '{start_str[:10]}' AND CAST(p.tanggal AS DATE) <= '{end_str[:10]}'
            """)
            
            # Update sync_meta
            iso_now = datetime.now().isoformat()
            duck_conn.execute("""
                INSERT OR REPLACE INTO sync_meta VALUES ('last_sync_date', ?), 
                                                        ('last_sync_timestamp', ?)
            """, [end_str[:10], iso_now])

            duck_conn.execute("COMMIT")

            duck_conn.close()
            
            cls._sync_status[server_key]['state'] = 'finished'
            cls._sync_status[server_key]['progress'] = 100
            cls._sync_status[server_key]['message'] = 'Selesai: Data berhasil dirakit ke analitik lokal.'
            
        except Exception as e:
            try: duck_conn.execute("ROLLBACK")
            except: pass
            cls._sync_status[server_key]['state'] = 'error'
            cls._sync_status[server_key]['message'] = f'Error: {str(e)}'

    @classmethod
    def get_status(cls, server_key):
        return cls._sync_status.get(server_key, {'state': 'idle'})

    @classmethod
    def get_all_status(cls):
        return cls._sync_status
