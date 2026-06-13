import os
import duckdb

SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'database', 'snapshots')

class SalesDuckDBCore:
    @classmethod
    def _db_path(cls, server_key):
        os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
        safe_key = server_key.replace('/', '_').replace('\\', '_')
        return os.path.join(SNAPSHOTS_DIR, f'sales_{safe_key}.duckdb')

    @classmethod
    def _init_db(cls, db_path):
        conn = duckdb.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_meta (
                key VARCHAR PRIMARY KEY,
                value VARCHAR
            );
            
            CREATE TABLE IF NOT EXISTS raw_m_barang (
                kd_barang VARCHAR,
                nama VARCHAR
            );
            
            CREATE TABLE IF NOT EXISTS raw_m_customer (
                kd_customer VARCHAR,
                kd_kota VARCHAR,
                nama VARCHAR
            );
            
            CREATE TABLE IF NOT EXISTS raw_m_pegawai (
                kd_pegawai VARCHAR,
                nama VARCHAR
            );
            
            CREATE TABLE IF NOT EXISTS raw_m_divisi (
                kd_divisi VARCHAR,
                nama VARCHAR
            );
            
            CREATE TABLE IF NOT EXISTS raw_m_kota (
                kd_kota VARCHAR,
                nama VARCHAR
            );
            
            CREATE TABLE IF NOT EXISTS raw_m_userx (
                kd_user VARCHAR,
                nama VARCHAR
            );
            
            CREATE TABLE IF NOT EXISTS raw_m_voucher (
                kd_voucher VARCHAR,
                nominal DOUBLE
            );
            
            CREATE TABLE IF NOT EXISTS raw_t_penjualan (
                no_transaksi VARCHAR,
                tanggal TIMESTAMP,
                kd_customer VARCHAR,
                kd_divisi VARCHAR,
                kd_user VARCHAR,
                kd_voucher VARCHAR,
                status INTEGER,
                diskon_uang DOUBLE,
                pajak DOUBLE,
                keterangan VARCHAR
            );
            
            CREATE TABLE IF NOT EXISTS raw_t_penjualan_detail (
                no_transaksi VARCHAR,
                kd_barang VARCHAR,
                kd_pegawai VARCHAR,
                qty DOUBLE,
                harga_jual DOUBLE,
                diskon1 DOUBLE,
                diskon2 DOUBLE,
                diskon3 DOUBLE,
                diskon4 DOUBLE,
                subtotal DOUBLE
            );
            
            CREATE TABLE IF NOT EXISTS sales_detail (
                no_transaksi VARCHAR,
                tanggal TIMESTAMP,
                customer VARCHAR,
                divisi VARCHAR,
                pegawai VARCHAR,
                kd_barang VARCHAR,
                barang VARCHAR,
                satuan VARCHAR,
                qty DOUBLE,
                subtotal DOUBLE,
                total_penjualan DOUBLE
            );
        """)
        # DuckDB is columnar and fast, we don't necessarily need explicit indexes for standard grouping 
        # but we can add them for filtering by date if needed.
        # But DuckDB handles this efficiently without indexes usually.
        conn.close()

    @classmethod
    def get_connection(cls, server_key):
        db_path = cls._db_path(server_key)
        cls._init_db(db_path)
        return duckdb.connect(db_path)
