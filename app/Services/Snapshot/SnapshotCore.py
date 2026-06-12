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

class SnapshotCore:
    @classmethod
    def _db_path(cls, server_key):
            os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
            safe_key = server_key.replace('/', '_').replace('\\', '_')
            return os.path.join(SNAPSHOTS_DIR, f'{safe_key}.db')

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
    
            # Tabel baru untuk menyimpan Base Data stok per rentang waktu (Local Checkpoint)
            conn.execute('''
                CREATE TABLE IF NOT EXISTS stok_checkpoints (
                    kd_divisi       TEXT,
                    kd_barang       TEXT,
                    checkpoint_type TEXT,   -- e.g., 'yearly', 'weekly'
                    checkpoint_date TEXT,   -- e.g., '2025-12-31', '2026-W42'
                    stok_akhir      REAL,
                    PRIMARY KEY (kd_divisi, kd_barang, checkpoint_type, checkpoint_date)
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_satuan_kd_barang ON satuan_snapshot(kd_barang)')
    
            conn.execute('''
                CREATE TABLE IF NOT EXISTS dashboard_penjualan (
                    kd_divisi       TEXT,
                    kd_barang       TEXT,
                    total_qty       REAL,
                    total_nominal   REAL,
                    bulan           INTEGER,
                    tahun           INTEGER
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_dashboard_penjualan_tahun ON dashboard_penjualan(tahun)')

            conn.execute('''
                CREATE TABLE IF NOT EXISTS dashboard_pembelian (
                    kd_divisi       TEXT,
                    kd_barang       TEXT,
                    total_qty       REAL,
                    total_nominal   REAL,
                    bulan           INTEGER,
                    tahun           INTEGER
                )
            ''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_dashboard_pembelian_tahun ON dashboard_pembelian(tahun)')

            conn.commit()
            return conn

    @classmethod
    def get_checkpoints(cls, server_key, checkpoint_type):
            """Retrieve checkpoints from SQLite. Returns dict: (kd_divisi, kd_barang) -> stok_akhir, checkpoint_date"""
            db_path = cls._db_path(server_key)
            if not os.path.exists(db_path):
                return {}, None
            
            conn = sqlite3.connect(db_path)
            # Get the latest checkpoint_date for this type
            date_row = conn.execute(
                'SELECT MAX(checkpoint_date) FROM stok_checkpoints WHERE checkpoint_type = ?', 
                (checkpoint_type,)
            ).fetchone()
            
            if not date_row or not date_row[0]:
                conn.close()
                return {}, None
                
            latest_date = date_row[0]
            rows = conn.execute(
                'SELECT kd_divisi, kd_barang, stok_akhir FROM stok_checkpoints WHERE checkpoint_type = ? AND checkpoint_date = ?',
                (checkpoint_type, latest_date)
            ).fetchall()
            conn.close()
            
            checkpoint_map = {(r[0], r[1]): r[2] for r in rows}
            return checkpoint_map, latest_date

    @classmethod
    def save_checkpoints(cls, server_key, checkpoint_type, checkpoint_date, stok_map):
            """Save a snapshot of stock to SQLite as base data."""
            db_path = cls._db_path(server_key)
            conn = sqlite3.connect(db_path)
            batch = []
            for (kd_div, kd_brg), stok in stok_map.items():
                batch.append((kd_div, kd_brg, checkpoint_type, checkpoint_date, stok))
                
            conn.executemany('''
                INSERT OR REPLACE INTO stok_checkpoints 
                (kd_divisi, kd_barang, checkpoint_type, checkpoint_date, stok_akhir) 
                VALUES (?, ?, ?, ?, ?)
            ''', batch)
            conn.commit()
            conn.close()

