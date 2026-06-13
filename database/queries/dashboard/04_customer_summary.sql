-- Ringkasan omset pelanggan per divisi per bulan
-- Dijalankan sekali saat Full Refresh, hasilnya disimpan ke SQLite
SET NOCOUNT ON;
DECLARE @tahun_start INT = ?;
DECLARE @tahun_end INT = ?;

SELECT 
    t.kd_divisi,
    t.kd_customer,
    c.nama as nama_customer,
    COUNT(DISTINCT t.no_transaksi) AS total_transaksi,
    SUM(d.qty * d.harga_jual) AS total_belanja,
    MONTH(t.tanggal) AS bulan,
    YEAR(t.tanggal) AS tahun
FROM t_penjualan t (NOLOCK)
INNER JOIN t_penjualan_detail d (NOLOCK) ON t.no_transaksi = d.no_transaksi
INNER JOIN m_customer c (NOLOCK) ON t.kd_customer = c.kd_customer
WHERE YEAR(t.tanggal) >= @tahun_start AND YEAR(t.tanggal) <= @tahun_end
  AND t.status <> 2
GROUP BY t.kd_divisi, t.kd_customer, c.nama, MONTH(t.tanggal), YEAR(t.tanggal);
