-- Penjualan (kredit / stok keluar)
SET NOCOUNT ON;
DECLARE @tanggal DATETIME = ?;

SELECT t.kd_divisi, d.kd_barang, 0 AS debet, d.qty AS kredit, d.kd_satuan
FROM t_penjualan_detail d (NOLOCK)
INNER JOIN t_penjualan t (NOLOCK) ON d.no_transaksi = t.no_transaksi
WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
  AND CAST(t.tanggal AS DATE) <= CAST(@tanggal AS DATE);
