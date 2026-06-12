import os
import sqlite3
from collections import defaultdict
from app.Services.Snapshot.SnapshotCore import SnapshotCore

class DashboardService:
    @classmethod
    def get_summary(cls, server_key, tahun=None):
        """
        Baca dari SQLite snapshot, return summary dashboard.
        Filter: tahun (default: None, berarti tidak ada filter khusus, tapi default kita ambil semua di SQLite)
        """
        db_path = SnapshotCore._db_path(server_key)
        if not os.path.exists(db_path):
            return {"error": "Snapshot belum ada. Silakan Refresh data terlebih dahulu."}

        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 1. KPI & Inventori dari stok_snapshot
            cursor.execute('''
                SELECT 
                    COUNT(DISTINCT kd_barang) as total_sku,
                    SUM(stok_akhir * harga_avg) as nilai_inventori,
                    SUM(CASE WHEN stok_akhir <= 0 THEN 1 ELSE 0 END) as stok_habis,
                    SUM(CASE WHEN stok_akhir > 0 AND stok_akhir < 10 THEN 1 ELSE 0 END) as stok_rendah
                FROM stok_snapshot
            ''')
            kpi_stok = dict(cursor.fetchone() or {})
            
            # Status stok pie chart
            cursor.execute('''
                SELECT 
                    SUM(CASE WHEN stok_akhir <= 0 THEN 1 ELSE 0 END) as habis,
                    SUM(CASE WHEN stok_akhir > 0 AND stok_akhir < 10 THEN 1 ELSE 0 END) as rendah,
                    SUM(CASE WHEN stok_akhir >= 10 THEN 1 ELSE 0 END) as aman
                FROM stok_snapshot
            ''')
            status_stok = dict(cursor.fetchone() or {})

            # Inventori per divisi
            cursor.execute('''
                SELECT divisi, SUM(stok_akhir * harga_avg) as nilai
                FROM stok_snapshot
                GROUP BY divisi
                ORDER BY nilai DESC
            ''')
            inventori_per_divisi = [dict(row) for row in cursor.fetchall()]

            # Top Margin
            cursor.execute('''
                SELECT barang, harga_avg, harga_jual, 
                       (harga_jual - harga_avg) as selisih,
                       CASE WHEN harga_avg > 0 THEN ((harga_jual - harga_avg) / harga_avg) * 100 ELSE 100 END as margin_persen,
                       stok_akhir
                FROM stok_snapshot
                WHERE stok_akhir > 0 AND harga_jual > harga_avg AND harga_avg > 0
                ORDER BY selisih DESC
                LIMIT 10
            ''')
            top_margin = [dict(row) for row in cursor.fetchall()]
            
            # Stok Kritis
            cursor.execute('''
                SELECT barang, kategori, stok_akhir
                FROM stok_snapshot
                WHERE stok_akhir < 10
                ORDER BY stok_akhir ASC
                LIMIT 20
            ''')
            stok_kritis = [dict(row) for row in cursor.fetchall()]

            # 2. Penjualan
            query_penjualan = "SELECT * FROM dashboard_penjualan"
            params = []
            if tahun:
                query_penjualan += " WHERE tahun = ?"
                params.append(tahun)
            
            cursor.execute(query_penjualan, params)
            rows_penjualan = cursor.fetchall()
            
            total_penjualan = sum(row['total_nominal'] for row in rows_penjualan)
            
            # Aggregate penjualan per divisi (perlu join dengan stok_snapshot untuk nama divisi)
            cursor.execute('''
                SELECT s.divisi, SUM(p.total_nominal) as nilai
                FROM dashboard_penjualan p
                JOIN stok_snapshot s ON p.kd_barang = s.kd_barang
                {}
                GROUP BY s.divisi
                ORDER BY nilai DESC
            '''.format("WHERE p.tahun = ?" if tahun else ""), params)
            penjualan_per_divisi = [dict(row) for row in cursor.fetchall()]

            # Aggregate top barang terlaris
            cursor.execute('''
                SELECT s.barang, s.kategori, SUM(p.total_qty) as total_qty, SUM(p.total_nominal) as total_nominal
                FROM dashboard_penjualan p
                JOIN stok_snapshot s ON p.kd_barang = s.kd_barang
                {}
                GROUP BY s.barang, s.kategori
                ORDER BY total_qty DESC
                LIMIT 10
            '''.format("WHERE p.tahun = ?" if tahun else ""), params)
            top_barang_terlaris = [dict(row) for row in cursor.fetchall()]
            
            # Aggregate top kategori laris
            cursor.execute('''
                SELECT s.kategori, SUM(p.total_qty) as total_qty
                FROM dashboard_penjualan p
                JOIN stok_snapshot s ON p.kd_barang = s.kd_barang
                {}
                GROUP BY s.kategori
                ORDER BY total_qty DESC
                LIMIT 10
            '''.format("WHERE p.tahun = ?" if tahun else ""), params)
            top_kategori_laris = [dict(row) for row in cursor.fetchall()]

            # Aggregate top merk laris
            cursor.execute('''
                SELECT s.merk, SUM(p.total_qty) as total_qty
                FROM dashboard_penjualan p
                JOIN stok_snapshot s ON p.kd_barang = s.kd_barang
                {}
                GROUP BY s.merk
                ORDER BY total_qty DESC
                LIMIT 10
            '''.format("WHERE p.tahun = ?" if tahun else ""), params)
            top_merk_laris = [dict(row) for row in cursor.fetchall()]

            # 3. Pembelian
            query_pembelian = "SELECT * FROM dashboard_pembelian"
            if tahun:
                query_pembelian += " WHERE tahun = ?"
                
            cursor.execute(query_pembelian, params)
            rows_pembelian = cursor.fetchall()
            
            total_pembelian = sum(row['total_nominal'] for row in rows_pembelian)

            # 4. Dead stock (Ada stok, tapi tidak ada penjualan di dashboard_penjualan)
            cursor.execute('''
                SELECT s.barang, s.kategori, s.stok_akhir
                FROM stok_snapshot s
                LEFT JOIN dashboard_penjualan p ON s.kd_barang = p.kd_barang {}
                WHERE s.stok_akhir > 0 AND p.kd_barang IS NULL
                ORDER BY s.stok_akhir DESC
                LIMIT 20
            '''.format("AND p.tahun = ?" if tahun else ""), params)
            dead_stock = [dict(row) for row in cursor.fetchall()]

            conn.close()

            laba_kotor = total_penjualan - total_pembelian
            margin_persen = 0
            if total_penjualan > 0:
                margin_persen = (laba_kotor / total_penjualan) * 100

            # Hitung mutasi dan retur (karena kita tidak menyimpan tabelnya, kita estimasi atau hardcode dulu,
            # Atau bisa skip komposisi transaksi jika tidak lengkap)
            # Untuk sekarang kita pakai penjualan vs pembelian
            komposisi_transaksi = {
                "Penjualan": total_penjualan,
                "Pembelian": total_pembelian
            }

            return {
                "kpi": {
                    "total_penjualan": total_penjualan,
                    "total_pembelian": total_pembelian,
                    "laba_kotor": laba_kotor,
                    "margin_persen": round(margin_persen, 2),
                    "nilai_inventori": kpi_stok.get("nilai_inventori") or 0,
                    "total_sku": kpi_stok.get("total_sku") or 0,
                    "stok_habis": kpi_stok.get("stok_habis") or 0,
                    "stok_rendah": kpi_stok.get("stok_rendah") or 0,
                },
                "penjualan_per_divisi": penjualan_per_divisi,
                "top_kategori_laris": top_kategori_laris,
                "top_merk_laris": top_merk_laris,
                "komposisi_transaksi": komposisi_transaksi,
                "inventori_per_divisi": inventori_per_divisi,
                "status_stok": status_stok,
                "top_barang_terlaris": top_barang_terlaris,
                "top_margin": top_margin,
                "stok_kritis": stok_kritis,
                "dead_stock": dead_stock,
            }

        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                return {"error": "Tabel dashboard belum ada. Lakukan Refresh data terlebih dahulu."}
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}
