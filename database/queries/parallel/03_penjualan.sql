-- Penjualan (kredit / stok keluar)
SET NOCOUNT ON;
DECLARE @tanggal DATETIME = ?;

SELECT t.kd_divisi, d.kd_barang, 0 AS debet, d.qty AS kredit, d.kd_satuan
FROM t_penjualan_detail d (NOLOCK)
INNER JOIN t_penjualan t (NOLOCK) ON d.no_transaksi = t.no_transaksi
INNER JOIN m_barang b (NOLOCK) ON d.kd_barang = b.kd_barang
INNER JOIN m_kategori k (NOLOCK) ON b.kd_kategori = k.kd_kategori
WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
  AND CAST(t.tanggal AS DATE) <= CAST(@tanggal AS DATE)
  AND k.status <> 2;
