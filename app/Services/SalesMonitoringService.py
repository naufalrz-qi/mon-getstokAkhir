from app.Services.Snapshot.SalesDuckDBCore import SalesDuckDBCore

class SalesMonitoringService:
    def __init__(self):
        pass

    def get_sales_data(self, server_key, start_date, end_date, view_name):
        """
        Mengambil data penjualan dari DuckDB dengan merender query persis seperti View.
        view_name options: "mon_t_penjualan_per_nota", "mon_t_penjualan_per_user", dll.
        """
        
        query = ""
        params = [start_date, end_date]

        if view_name == "mon_t_penjualan_per_nota":
            query = """
                SELECT 
                    p.no_transaksi AS " NO Nota ",
                    p.tanggal AS tanggal,
                    COALESCE(c.nama, 'Umum') AS Customer,
                    COALESCE(k.nama, 'Unknown') AS Kota,
                    COALESCE(v.nama, 'Unknown') AS Divisi,
                    SUM(d.harga_jual * d.qty) AS " Total Kotor ",
                    SUM(d.subtotal) - p.diskon_uang AS " Total Bersih ",
                    SUM((d.harga_jual * d.qty) - d.subtotal) + p.diskon_uang AS Potongan,
                    COALESCE(vo.nominal, 0) AS Voucher,
                    (SUM(d.subtotal) - p.diskon_uang) - COALESCE(vo.nominal, 0) AS "Total Setelah Voucher",
                    p.pajak AS " Total Pajak ",
                    p.pajak AS " Total Pajak2 ",
                    COALESCE(u.nama, 'Unknown') AS Petugas
                FROM raw_t_penjualan p
                JOIN raw_t_penjualan_detail d ON p.no_transaksi = d.no_transaksi
                LEFT JOIN raw_m_customer c ON p.kd_customer = c.kd_customer
                LEFT JOIN raw_m_kota k ON c.kd_kota = k.kd_kota
                LEFT JOIN raw_m_divisi v ON p.kd_divisi = v.kd_divisi
                LEFT JOIN raw_m_userx u ON p.kd_user = u.kd_user
                LEFT JOIN raw_m_voucher vo ON p.kd_voucher = vo.kd_voucher
                WHERE CAST(p.tanggal AS DATE) >= ? AND CAST(p.tanggal AS DATE) <= ?
                GROUP BY 
                    p.no_transaksi, p.tanggal, c.nama, k.nama, v.nama, 
                    p.diskon_uang, p.pajak, vo.nominal, u.nama
                ORDER BY " NO Nota " DESC
                
            """
        elif view_name == "mon_t_penjualan_per_user":
            query = """
                SELECT 
                    p.no_transaksi AS "No Transaksi",
                    p.tanggal AS Tanggal,
                    COALESCE(v.nama, 'Unknown') AS Divisi,
                    CAST(p.status AS VARCHAR) AS "Status Transaksi",
                    COALESCE(c.nama, 'Umum') AS Customer,
                    SUM(d.subtotal) - p.diskon_uang AS Nominal,
                    COALESCE(u.nama, 'Unknown') AS User
                FROM raw_t_penjualan p
                JOIN raw_t_penjualan_detail d ON p.no_transaksi = d.no_transaksi
                LEFT JOIN raw_m_divisi v ON p.kd_divisi = v.kd_divisi
                LEFT JOIN raw_m_customer c ON p.kd_customer = c.kd_customer
                LEFT JOIN raw_m_userx u ON p.kd_user = u.kd_user
                WHERE CAST(p.tanggal AS DATE) >= ? AND CAST(p.tanggal AS DATE) <= ?
                GROUP BY p.no_transaksi, p.tanggal, v.nama, p.status, c.nama, p.diskon_uang, u.nama
                ORDER BY p.tanggal DESC
                
            """
        elif view_name == "mon_t_penjualan_per_barang":
            query = """
                SELECT 
                    p.tanggal AS tanggal,
                    d.kd_barang AS "Kode Barang",
                    COALESCE(b.nama, 'Unknown') AS Barang,
                    SUM(d.harga_jual * d.qty) AS "Total Kotor",
                    SUM(d.subtotal) AS "Total Bersih",
                    SUM(d.qty) AS Kuantum,
                    COALESCE(v.nama, 'Unknown') AS Divisi,
                    p.keterangan AS keterangan,
                    COALESCE(c.nama, 'Umum') AS nama
                FROM raw_t_penjualan p
                JOIN raw_t_penjualan_detail d ON p.no_transaksi = d.no_transaksi
                LEFT JOIN raw_m_barang b ON d.kd_barang = b.kd_barang
                LEFT JOIN raw_m_divisi v ON p.kd_divisi = v.kd_divisi
                LEFT JOIN raw_m_customer c ON p.kd_customer = c.kd_customer
                WHERE CAST(p.tanggal AS DATE) >= ? AND CAST(p.tanggal AS DATE) <= ?
                GROUP BY p.tanggal, d.kd_barang, b.nama, v.nama, p.keterangan, c.nama
                ORDER BY p.tanggal DESC
                
            """
        elif view_name == "mon_t_penjualan_per_customer":
            query = """
                SELECT 
                    COALESCE(v.nama, 'Unknown') AS Divisi,
                    p.tanggal AS Tanggal,
                    COALESCE(c.nama, 'Umum') AS Customer,
                    CAST(COUNT(DISTINCT p.no_transaksi) AS VARCHAR) AS "Jumlah Nota",
                    SUM(d.subtotal) - SUM(DISTINCT p.diskon_uang) AS "Total Bersih",
                    p.kd_divisi AS kd_divisi,
                    p.no_transaksi AS no_transaksi,
                    1 AS Expr1,
                    p.kd_customer AS kd_customer
                FROM raw_t_penjualan p
                JOIN raw_t_penjualan_detail d ON p.no_transaksi = d.no_transaksi
                LEFT JOIN raw_m_divisi v ON p.kd_divisi = v.kd_divisi
                LEFT JOIN raw_m_customer c ON p.kd_customer = c.kd_customer
                WHERE CAST(p.tanggal AS DATE) >= ? AND CAST(p.tanggal AS DATE) <= ?
                GROUP BY v.nama, p.tanggal, c.nama, p.kd_divisi, p.no_transaksi, p.kd_customer
                ORDER BY p.tanggal DESC
                
            """
        elif view_name == "mon_t_penjualan_per_divisi":
            query = """
                SELECT 
                    'Keterangan' AS "Keterangan Divisi",
                    COALESCE(v.nama, 'Unknown') AS Divisi,
                    '001' AS "Kepala Nota",
                    SUM(d.harga_jual * d.qty) AS "Total Kotor",
                    p.tanggal AS Tanggal,
                    SUM(d.subtotal) - SUM(DISTINCT p.diskon_uang) AS "Total Bersih"
                FROM raw_t_penjualan p
                JOIN raw_t_penjualan_detail d ON p.no_transaksi = d.no_transaksi
                LEFT JOIN raw_m_divisi v ON p.kd_divisi = v.kd_divisi
                WHERE CAST(p.tanggal AS DATE) >= ? AND CAST(p.tanggal AS DATE) <= ?
                GROUP BY v.nama, p.tanggal
                ORDER BY p.tanggal DESC
                
            """
        elif view_name == "mon_t_penjualan_per_hari":
            query = """
                SELECT 
                    CAST(p.tanggal AS DATE) AS Tanggal,
                    SUM(d.subtotal) - SUM(DISTINCT p.diskon_uang) AS "Total Penjualan Bersih",
                    SUM(d.harga_jual * d.qty) AS "Total Penjualan Kotor",
                    SUM((d.harga_jual * d.qty) - d.subtotal) + SUM(DISTINCT p.diskon_uang) AS "Total Diskon Penjualan",
                    SUM(DISTINCT p.pajak) AS "Total Pajak Penjualan"
                FROM raw_t_penjualan p
                JOIN raw_t_penjualan_detail d ON p.no_transaksi = d.no_transaksi
                WHERE CAST(p.tanggal AS DATE) >= ? AND CAST(p.tanggal AS DATE) <= ?
                GROUP BY CAST(p.tanggal AS DATE)
                ORDER BY CAST(p.tanggal AS DATE) DESC
                
            """
        elif view_name == "mon_t_penjualan_per_hari_divisi":
            query = """
                SELECT 
                    CAST(p.tanggal AS DATE) AS Tanggal,
                    COALESCE(v.nama, 'Unknown') AS divisi,
                    SUM(d.subtotal) - SUM(DISTINCT p.diskon_uang) AS "Total Penjualan Bersih"
                FROM raw_t_penjualan p
                JOIN raw_t_penjualan_detail d ON p.no_transaksi = d.no_transaksi
                LEFT JOIN raw_m_divisi v ON p.kd_divisi = v.kd_divisi
                WHERE CAST(p.tanggal AS DATE) >= ? AND CAST(p.tanggal AS DATE) <= ?
                GROUP BY CAST(p.tanggal AS DATE), v.nama
                ORDER BY CAST(p.tanggal AS DATE) DESC
                
            """
        elif view_name == "mon_t_penjualan_per_bulan":
            query = """
                SELECT 
                    STRFTIME(CAST(p.tanggal AS DATE), '%Y-%m') AS Bulan,
                    SUM(d.subtotal) - SUM(DISTINCT p.diskon_uang) AS "Total Bersih"
                FROM raw_t_penjualan p
                JOIN raw_t_penjualan_detail d ON p.no_transaksi = d.no_transaksi
                -- NOTE: We apply date filter directly to the raw table
                WHERE CAST(p.tanggal AS DATE) >= ? AND CAST(p.tanggal AS DATE) <= ?
                GROUP BY STRFTIME(CAST(p.tanggal AS DATE), '%Y-%m')
                ORDER BY Bulan DESC
                
            """
        else:
            raise ValueError(f"View {view_name} tidak valid")

        try:
            conn = SalesDuckDBCore.get_connection(server_key)
            cursor = conn.cursor()
            
            cursor.execute(query, params)
            columns = [col[0] for col in cursor.description]
            rows = cursor.fetchall()
            
            results = []
            for row in rows:
                results.append(dict(zip(columns, row)))
            
            conn.close()
            
            return {
                "columns": columns,
                "data": results
            }
        except Exception as e:
            print(f"DuckDB Query Error: {e}")
            return {"columns": [], "data": []}
