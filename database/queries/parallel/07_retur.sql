-- Retur Penjualan (barang kembali = debet) + Retur Pembelian (barang dikembalikan = kredit)
SET NOCOUNT ON;
DECLARE @tanggal DATETIME = ?;

-- Retur penjualan (stok masuk kembali)
SELECT t.kd_divisi, d.kd_barang, d.qty AS debet, 0 AS kredit, d.kd_satuan
FROM t_penjualan_retur_detail d (NOLOCK)
INNER JOIN t_penjualan_retur t (NOLOCK) ON d.no_retur = t.no_retur
WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
  AND CAST(t.tanggal AS DATE) <= CAST(@tanggal AS DATE)

UNION ALL

-- Retur pembelian (stok keluar kembali)
SELECT t.kd_divisi, d.kd_barang, 0 AS debet, d.qty AS kredit, d.kd_satuan
FROM t_pembelian_retur_detail d (NOLOCK)
INNER JOIN t_pembelian_retur t (NOLOCK) ON d.no_retur = t.no_retur
WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
  AND CAST(t.tanggal AS DATE) <= CAST(@tanggal AS DATE);
