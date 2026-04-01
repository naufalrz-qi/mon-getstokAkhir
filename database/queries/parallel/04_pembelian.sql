-- Pembelian (debet / stok masuk)
SET NOCOUNT ON;
DECLARE @tanggal DATETIME = ?;

SELECT t.kd_divisi, d.kd_barang, d.qty AS debet, 0 AS kredit, d.kd_satuan
FROM t_pembelian_detail d (NOLOCK)
INNER JOIN t_pembelian t (NOLOCK) ON d.no_transaksi = t.no_transaksi
WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
  AND CAST(t.tanggal AS DATE) <= CAST(@tanggal AS DATE);
