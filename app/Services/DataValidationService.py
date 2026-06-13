from app.Models.ServerModel import ServerModel
from app.Services.Snapshot.SalesDuckDBCore import SalesDuckDBCore
from app.Models.Database import db_manager

class DataValidationService:
    @classmethod
    def validate_accuracy(cls, server_key, tahun):
        try:
            duck_conn = SalesDuckDBCore.get_connection(server_key)
            
            # Query the actual server database
            # This fetches the raw values directly from MSSQL
            server = ServerModel.get_all().get(server_key)
            if not server:
                return {'error': 'Server tidak ditemukan.'}
                
            conn_sq = db_manager.get_connection(server_key)
            
            cursor_sq = conn_sq.cursor()
            
            # 1. Total Transaksi (Tahun)
            cursor_sq.execute("""
                SELECT COUNT(DISTINCT No_Transaksi) FROM t_penjualan WHERE YEAR(tanggal) = ?
            """, [tahun])
            sq_tx = cursor_sq.fetchone()[0] or 0
            
            duck_tx = duck_conn.execute("""
                SELECT COUNT(DISTINCT no_transaksi) FROM sales_detail WHERE YEAR(tanggal) = ?
            """, [tahun]).fetchone()[0] or 0
            
            # 2. Total Omset (Tahun)
            cursor_sq.execute("""
                SELECT SUM(d.total) FROM t_penjualan p JOIN t_penjualan_detail d ON p.no_transaksi = d.no_transaksi WHERE YEAR(p.tanggal) = ?
            """, [tahun])
            sq_omset = cursor_sq.fetchone()[0] or 0
            
            duck_omset = duck_conn.execute("""
                SELECT SUM(total_penjualan) FROM sales_detail WHERE YEAR(tanggal) = ?
            """, [tahun]).fetchone()[0] or 0
            
            # Subtotal duckdb is detail subtotal. Total is total penjualan.
            # But wait, DuckDB sales_detail has 'subtotal'. Let's sum subtotal.
            duck_omset = duck_conn.execute("""
                SELECT SUM(subtotal) FROM sales_detail WHERE YEAR(tanggal) = ?
            """, [tahun]).fetchone()[0] or 0

            duck_conn.close()

            def rp(val):
                return f"Rp {int(val):,}".replace(',', '.')

            metrics = [
                {'name': 'Total Transaksi', 'sqlite': str(sq_tx), 'duckdb': str(duck_tx), 'match': int(sq_tx) == int(duck_tx)},
                {'name': 'Total Omset', 'sqlite': rp(sq_omset), 'duckdb': rp(duck_omset), 'match': int(sq_omset) == int(duck_omset)}
            ]
            
            overall_match = all(m['match'] for m in metrics)
            recommendation = "Data konsisten." if overall_match else "Ada selisih. Silakan lakukan 'Sinkronisasi Ulang (Mass Refresh)' dari menu Pengaturan Snapshot."

            return {
                'metrics': metrics,
                'overall_match': overall_match,
                'recommendation': recommendation
            }
        except Exception as e:
            return {'error': f'Validasi gagal: {str(e)}'}
