-- Mutasi Stok (keluar dari divisi asal, masuk ke divisi tujuan)
SET NOCOUNT ON;
DECLARE @tanggal DATETIME = ?;

-- Mutasi keluar (kredit)
SELECT t.kd_divisi_asal AS kd_divisi, d.kd_barang, 0 AS debet, d.qty AS kredit, d.kd_satuan
FROM t_mutasi_stok_detail d (NOLOCK)
INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
  AND CAST(t.tanggal AS DATE) <= CAST(@tanggal AS DATE)

UNION ALL

-- Mutasi masuk (debet)
SELECT t.kd_divisi_tujuan AS kd_divisi, d.kd_barang, d.qty AS debet, 0 AS kredit, d.kd_satuan
FROM t_mutasi_stok_detail d (NOLOCK)
INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
  AND CAST(t.tanggal AS DATE) <= CAST(@tanggal AS DATE);
