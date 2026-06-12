-- Ringkasan penjualan per divisi, per barang, per bulan
-- Dijalankan sekali saat Full Refresh, hasilnya disimpan ke SQLite
SET NOCOUNT ON;
DECLARE @tahun_start INT = ?;
DECLARE @tahun_end INT = ?;

SELECT 
    t.kd_divisi, d.kd_barang,
    SUM(d.qty) AS total_qty,
    SUM(d.qty * d.harga_jual) AS total_nominal,
    MONTH(t.tanggal) AS bulan,
    YEAR(t.tanggal) AS tahun
FROM t_penjualan_detail d (NOLOCK)
INNER JOIN t_penjualan t (NOLOCK) ON d.no_transaksi = t.no_transaksi
INNER JOIN m_barang b (NOLOCK) ON d.kd_barang = b.kd_barang
INNER JOIN m_kategori k (NOLOCK) ON b.kd_kategori = k.kd_kategori
WHERE YEAR(t.tanggal) >= @tahun_start AND YEAR(t.tanggal) <= @tahun_end
  AND k.status <> 2
GROUP BY t.kd_divisi, d.kd_barang, MONTH(t.tanggal), YEAR(t.tanggal);
