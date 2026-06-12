-- Ringkasan pembelian per divisi, per barang, per bulan
SET NOCOUNT ON;
DECLARE @tahun_start INT = ?;
DECLARE @tahun_end INT = ?;

SELECT 
    t.kd_divisi, d.kd_barang,
    SUM(d.qty) AS total_qty,
    SUM(d.qty * d.harga_beli) AS total_nominal,
    MONTH(t.tanggal) AS bulan,
    YEAR(t.tanggal) AS tahun
FROM t_pembelian_detail d (NOLOCK)
INNER JOIN t_pembelian t (NOLOCK) ON d.no_transaksi = t.no_transaksi
WHERE YEAR(t.tanggal) >= @tahun_start AND YEAR(t.tanggal) <= @tahun_end
  AND t.status IN (0, 1)
GROUP BY t.kd_divisi, d.kd_barang, MONTH(t.tanggal), YEAR(t.tanggal);
