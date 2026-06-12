-- Ringkasan total transaksi per divisi, per bulan
-- Dijalankan sekali saat Full Refresh, hasilnya disimpan ke SQLite
SET NOCOUNT ON;
DECLARE @tahun_start INT = ?;
DECLARE @tahun_end INT = ?;

SELECT 
    kd_divisi,
    COUNT(no_transaksi) AS total_transaksi,
    MONTH(tanggal) AS bulan,
    YEAR(tanggal) AS tahun
FROM t_penjualan (NOLOCK)
WHERE YEAR(tanggal) >= @tahun_start AND YEAR(tanggal) <= @tahun_end
GROUP BY kd_divisi, MONTH(tanggal), YEAR(tanggal);
