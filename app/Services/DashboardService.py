import os
import sqlite3
from collections import defaultdict
from app.Services.Snapshot.SnapshotCore import SnapshotCore

import time

class DashboardService:
    _cache = {}

    @classmethod
    def get_summary(cls, server_key, tahun=None):
        """
        Baca dari SQLite snapshot, return summary dashboard.
        Filter: tahun (default: None, berarti tidak ada filter khusus, tapi default kita ambil semua di SQLite)
        """
        from app.Services.Snapshot.SnapshotState import SnapshotState
        last_refresh = SnapshotState._mem_cache_ts.get(server_key, 0)
        cache_key = f"{server_key}_{tahun}"
        
        cached = cls._cache.get(cache_key)
        if cached and cached['ts'] >= last_refresh:
            return cached['data']
            
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
            
            # Aggregate penjualan (Python in-memory for speed)
            cursor.execute('''
                SELECT p.total_qty, p.total_nominal, s.divisi, s.barang, s.kategori, s.merk
                FROM dashboard_penjualan p
                LEFT JOIN stok_snapshot s ON p.kd_barang = s.kd_barang
                {}
            '''.format("WHERE p.tahun = ?" if tahun else ""), params)
            rows_jual_joined = cursor.fetchall()
            
            from collections import defaultdict
            divisi_dict = defaultdict(float)
            barang_dict = defaultdict(lambda: [0, 0])
            kategori_dict = defaultdict(float)
            merk_dict = defaultdict(float)
            
            for row in rows_jual_joined:
                qty = row['total_qty'] or 0
                nom = row['total_nominal'] or 0
                div = row['divisi'] or 'Lainnya'
                kat = row['kategori'] or 'Lainnya'
                mrk = row['merk'] or 'Lainnya'
                bar = row['barang'] or 'Lainnya'
                
                divisi_dict[div] += nom
                b_key = (bar, kat)
                barang_dict[b_key][0] += qty
                barang_dict[b_key][1] += nom
                kategori_dict[kat] += qty
                merk_dict[mrk] += qty
                
            penjualan_per_divisi = [{'divisi': k, 'nilai': v} for k, v in divisi_dict.items()]
            penjualan_per_divisi.sort(key=lambda x: x['nilai'], reverse=True)
            
            top_barang_terlaris = [{'barang': k[0], 'kategori': k[1], 'total_qty': v[0], 'total_nominal': v[1]} for k, v in barang_dict.items()]
            top_barang_terlaris.sort(key=lambda x: x['total_qty'], reverse=True)
            top_barang_terlaris = top_barang_terlaris[:10]
            
            top_kategori_laris = [{'kategori': k, 'total_qty': v} for k, v in kategori_dict.items()]
            top_kategori_laris.sort(key=lambda x: x['total_qty'], reverse=True)
            top_kategori_laris = top_kategori_laris[:10]
            
            top_merk_laris = [{'merk': k, 'total_qty': v} for k, v in merk_dict.items()]
            top_merk_laris.sort(key=lambda x: x['total_qty'], reverse=True)
            top_merk_laris = top_merk_laris[:10]

            # 3. Pembelian
            query_pembelian = "SELECT * FROM dashboard_pembelian"
            if tahun:
                query_pembelian += " WHERE tahun = ?"
                
            cursor.execute(query_pembelian, params)
            rows_pembelian = cursor.fetchall()
            
            total_pembelian = sum(row['total_nominal'] for row in rows_pembelian)

            # 4. Total Transaksi
            total_transaksi = 0
            try:
                query_trans = "SELECT SUM(total_transaksi) as total_transaksi FROM dashboard_transaksi"
                if tahun:
                    query_trans += " WHERE tahun = ?"
                cursor.execute(query_trans, params)
                row_trans = cursor.fetchone()
                if row_trans and row_trans['total_transaksi']:
                    total_transaksi = row_trans['total_transaksi']
            except sqlite3.OperationalError:
                pass

            # 5. Dead stock (Ada stok, tapi tidak ada penjualan di dashboard_penjualan)
            cursor.execute('''
                SELECT barang, kategori, stok_akhir
                FROM stok_snapshot
                WHERE stok_akhir > 0
                  AND kd_barang NOT IN (
                      SELECT kd_barang FROM dashboard_penjualan {}
                  )
                ORDER BY stok_akhir DESC
                LIMIT 20
            '''.format("WHERE tahun = ?" if tahun else ""), params)
            dead_stock = [dict(row) for row in cursor.fetchall()]

            conn.close()

            laba_kotor = total_penjualan - total_pembelian
            margin_persen = 0
            if total_penjualan > 0:
                margin_persen = (laba_kotor / total_penjualan) * 100
                
            avg_basket_size = total_penjualan / total_transaksi if total_transaksi > 0 else 0

            # Hitung mutasi dan retur (karena kita tidak menyimpan tabelnya, kita estimasi atau hardcode dulu,
            # Atau bisa skip komposisi transaksi jika tidak lengkap)
            # Untuk sekarang kita pakai penjualan vs pembelian
            komposisi_transaksi = {
                "Penjualan": total_penjualan,
                "Pembelian": total_pembelian
            }

            result = {
                "kpi": {
                    "total_penjualan": total_penjualan,
                    "total_pembelian": total_pembelian,
                    "laba_kotor": laba_kotor,
                    "margin_persen": round(margin_persen, 2),
                    "total_transaksi": total_transaksi,
                    "avg_basket_size": avg_basket_size,
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
            
            cls._cache[cache_key] = {
                'ts': time.time(),
                'data': result
            }
            return result

        except sqlite3.OperationalError as e:
            if "no such table" in str(e):
                return {"error": "Tabel dashboard belum ada. Lakukan Refresh data terlebih dahulu."}
            return {"error": str(e)}
        except Exception as e:
            return {"error": str(e)}

    @classmethod
    def get_global_summary(cls, tahun=None):
        from app.Models.Database import db_manager
        from collections import defaultdict
        servers = db_manager.get_available_servers()
        
        global_kpi = {
            "total_penjualan": 0, "total_pembelian": 0, "laba_kotor": 0, "total_transaksi": 0, 
            "nilai_inventori": 0, "total_sku": 0, "stok_habis": 0, "stok_rendah": 0
        }
        cabang_leaderboard = []
        all_top_barang = defaultdict(lambda: [0, 0])
        all_top_kategori = defaultdict(float)
        all_top_merk = defaultdict(float)
        all_dead_stock = []
        all_stok_kritis = []
        
        for s in servers:
            if s.get('type') in ['retail', 'eceran']:
                continue
            
            # Abaikan jika user secara eksplisit mematikan opsi include_in_dashboard
            if s.get('include_in_dashboard', True) is False:
                continue
                
            server_key = s['key']
            server_name = s['name']
            
            res = cls.get_summary(server_key, tahun)
            if "error" in res:
                continue
                
            kpi = res['kpi']
            
            # Jika tipe bukan gudang, tambahkan ke sales KPI dan leaderboard
            if s.get('type') != 'gudang':
                for k in ["total_penjualan", "total_pembelian", "laba_kotor", "total_transaksi"]:
                    global_kpi[k] += kpi.get(k, 0)
                    
                cabang_leaderboard.append({
                    'divisi': server_name,
                    'nilai': kpi.get('total_penjualan', 0)
                })
                
                for b in res.get('top_barang_terlaris', []):
                    k = (b['barang'], b['kategori'])
                    all_top_barang[k][0] += b['total_qty']
                    all_top_barang[k][1] += b['total_nominal']
                    
                for kat in res.get('top_kategori_laris', []):
                    all_top_kategori[kat['kategori']] += kat['total_qty']
                    
                for mrk in res.get('top_merk_laris', []):
                    all_top_merk[mrk['merk']] += mrk['total_qty']
            
            # Stok selalu digabungkan (baik gudang maupun grosir)
            for k in ["nilai_inventori", "total_sku", "stok_habis", "stok_rendah"]:
                global_kpi[k] += kpi.get(k, 0)
                
            for ds in res.get('dead_stock', []):
                ds['cabang'] = server_name
                all_dead_stock.append(ds)
                
            for sk in res.get('stok_kritis', []):
                sk['cabang'] = server_name
                all_stok_kritis.append(sk)
                
        cabang_leaderboard.sort(key=lambda x: x['nilai'], reverse=True)
        
        top_barang = [{'barang': k[0], 'kategori': k[1], 'total_qty': v[0], 'total_nominal': v[1]} for k, v in all_top_barang.items()]
        top_barang.sort(key=lambda x: x['total_qty'], reverse=True)
        
        top_kategori = [{'kategori': k, 'total_qty': v} for k, v in all_top_kategori.items()]
        top_kategori.sort(key=lambda x: x['total_qty'], reverse=True)
        
        top_merk = [{'merk': k, 'total_qty': v} for k, v in all_top_merk.items()]
        top_merk.sort(key=lambda x: x['total_qty'], reverse=True)
        
        all_dead_stock.sort(key=lambda x: x['stok_akhir'], reverse=True)
        
        if global_kpi["total_penjualan"] > 0:
            global_kpi["margin_persen"] = round((global_kpi["laba_kotor"] / global_kpi["total_penjualan"] * 100), 2)
        else:
            global_kpi["margin_persen"] = 0
            
        if global_kpi["total_transaksi"] > 0:
            global_kpi["avg_basket_size"] = global_kpi["total_penjualan"] / global_kpi["total_transaksi"]
        else:
            global_kpi["avg_basket_size"] = 0
        
        return {
            "kpi": global_kpi,
            "penjualan_per_divisi": cabang_leaderboard,
            "top_barang_terlaris": top_barang[:10],
            "top_kategori_laris": top_kategori[:10],
            "top_merk_laris": top_merk[:10],
            "dead_stock": all_dead_stock[:20],
            "stok_kritis": all_stok_kritis[:20],
            "status_stok": {
                "sehat": global_kpi["total_sku"] - global_kpi["stok_habis"] - global_kpi["stok_rendah"],
                "rendah": global_kpi["stok_rendah"],
                "habis": global_kpi["stok_habis"]
            }
        }
