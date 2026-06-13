import time
from datetime import datetime
from app.Services.Snapshot.SalesDuckDBCore import SalesDuckDBCore

class DashboardAnalyticsService:
    _cache = {}

    @classmethod
    def get_monthly_trend(cls, server_key, tahun):
        try:
            conn = SalesDuckDBCore.get_connection(server_key)
            query = """
                SELECT MONTH(tanggal) as bulan, SUM(subtotal) as omset
                FROM sales_detail
                WHERE YEAR(tanggal) = ?
                GROUP BY MONTH(tanggal)
                ORDER BY bulan
            """
            rows = conn.execute(query, [tahun]).fetchall()
            conn.close()
            result = [{'bulan': row[0], 'omset': row[1] or 0} for row in rows]
            return result
        except Exception as e:
            return {'error': str(e)}

    @classmethod
    def get_customer_retention(cls, server_key, tahun):
        try:
            conn = SalesDuckDBCore.get_connection(server_key)
            query = """
                WITH cust_counts AS (
                    SELECT customer, COUNT(DISTINCT no_transaksi) as total_tx
                    FROM sales_detail
                    WHERE YEAR(tanggal) = ? AND customer != 'Umum'
                    GROUP BY customer
                )
                SELECT 
                    SUM(CASE WHEN total_tx = 1 THEN 1 ELSE 0 END) as new_customers,
                    SUM(CASE WHEN total_tx > 1 THEN 1 ELSE 0 END) as repeat_customers
                FROM cust_counts
            """
            row = conn.execute(query, [tahun]).fetchone()
            conn.close()
            if row:
                new_c = row[0] or 0
                rep_c = row[1] or 0
                total = new_c + rep_c
                rate = round((rep_c / total * 100), 2) if total > 0 else 0
                return {'new_customers': new_c, 'repeat_customers': rep_c, 'retention_rate': rate}
            return {'new_customers': 0, 'repeat_customers': 0, 'retention_rate': 0}
        except Exception as e:
            return {'error': str(e)}

    @classmethod
    def get_traffic_heatmap(cls, server_key, tahun):
        try:
            conn = SalesDuckDBCore.get_connection(server_key)
            query = """
                SELECT DAYOFWEEK(tanggal) as day_num, DAYNAME(tanggal) as day_name, EXTRACT(HOUR FROM tanggal) as hour, 
                       COUNT(DISTINCT no_transaksi) as count
                FROM sales_detail
                WHERE YEAR(tanggal) = ?
                GROUP BY DAYOFWEEK(tanggal), DAYNAME(tanggal), EXTRACT(HOUR FROM tanggal)
                ORDER BY day_num, hour
            """
            rows = conn.execute(query, [tahun]).fetchall()
            conn.close()
            result = [{'day': row[1], 'hour': row[2], 'count': row[3]} for row in rows]
            return result
        except Exception as e:
            return {'error': str(e)}

    @classmethod
    def get_cross_branch_omset(cls, tahun):
        from app.Models.ServerModel import ServerModel
        servers = ServerModel.get_all()
        result = []
        for srv_key, srv in servers.items():
            try:
                conn = SalesDuckDBCore.get_connection(srv_key)
                row = conn.execute("""
                    SELECT SUM(subtotal) FROM sales_detail WHERE YEAR(tanggal) = ?
                """, [tahun]).fetchone()
                conn.close()
                omset = row[0] or 0
                result.append({'cabang': srv.get('name', srv_key), 'omset': omset})
            except:
                pass
        return result

    @classmethod
    def get_basket_composition(cls, server_key, tahun):
        try:
            conn = SalesDuckDBCore.get_connection(server_key)
            query = """
                WITH tx_items AS (
                    SELECT no_transaksi, barang
                    FROM sales_detail
                    WHERE YEAR(tanggal) = ? AND barang != 'Unknown'
                )
                SELECT t1.barang as item_A, t2.barang as item_B, COUNT(*) as frequency
                FROM tx_items t1
                JOIN tx_items t2 ON t1.no_transaksi = t2.no_transaksi AND t1.barang < t2.barang
                GROUP BY t1.barang, t2.barang
                ORDER BY frequency DESC
                LIMIT 10
            """
            rows = conn.execute(query, [tahun]).fetchall()
            conn.close()
            return [{'item_A': r[0], 'item_B': r[1], 'frequency': r[2]} for r in rows]
        except Exception as e:
            return {'error': str(e)}

    @classmethod
    def get_stock_prediction(cls, server_key):
        from app.Services.Snapshot.SnapshotCore import SnapshotCore
        import sqlite3
        import os
        try:
            conn = SalesDuckDBCore.get_connection(server_key)
            query = """
                SELECT barang, SUM(qty)/30.0 as daily_velocity
                FROM sales_detail
                WHERE tanggal >= current_date() - INTERVAL 30 DAY
                GROUP BY barang
                HAVING daily_velocity > 0.5
            """
            velocity_rows = conn.execute(query).fetchall()
            conn.close()
            velocity_map = {r[0]: r[1] for r in velocity_rows}
            if not velocity_map: return []
            
            db_path = SnapshotCore._db_path(server_key)
            if not os.path.exists(db_path): return {'error': 'Snapshot stok tidak ditemukan'}
            sq_conn = sqlite3.connect(db_path)
            sq_conn.row_factory = sqlite3.Row
            cursor = sq_conn.cursor()
            cursor.execute("SELECT barang, stok_akhir FROM stok_snapshot WHERE stok_akhir > 0")
            stok_rows = cursor.fetchall()
            sq_conn.close()
            
            predictions = []
            for sr in stok_rows:
                barang = sr['barang']
                stok = sr['stok_akhir']
                if barang in velocity_map:
                    velocity = velocity_map[barang]
                    days_left = stok / velocity
                    if days_left <= 14:
                        predictions.append({'barang': barang, 'stok': stok, 'velocity': round(velocity, 2), 'days_left': round(days_left, 1)})
            predictions.sort(key=lambda x: x['days_left'])
            return predictions[:10]
        except Exception as e:
            return {'error': str(e)}
