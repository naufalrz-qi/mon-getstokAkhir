-- Opname / Penyesuaian Stok
SET NOCOUNT ON;
DECLARE @tanggal DATETIME = ?;

-- Opname yang menambah stok (status = 2 = approved)
SELECT '00' AS kd_divisi, kd_barang, qty AS debet, 0 AS kredit, kd_satuan
FROM t_opname_stok (NOLOCK)
WHERE status = 2
  AND tanggal > dbo.GetTanggalTerakhirTutupBuku()
  AND CAST(tanggal AS DATE) <= CAST(@tanggal AS DATE);
