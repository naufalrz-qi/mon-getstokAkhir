-- Opname / Penyesuaian Stok
SET NOCOUNT ON;
DECLARE @tanggal DATETIME = ?;

-- Opname yang menambah stok (status = 2 = approved)
SELECT kd_divisi, kd_barang, qty AS debet, 0 AS kredit, kd_satuan
FROM t_opname_stok (NOLOCK)
WHERE status = 2
  AND tanggal > dbo.GetTanggalTerakhirTutupBuku()
  AND CAST(tanggal AS DATE) <= CAST(@tanggal AS DATE)

UNION ALL

-- Opname yang mengurangkan stok (status <> 2)
SELECT kd_divisi, kd_barang, 0 AS debet, qty AS kredit, kd_satuan
FROM t_opname_stok (NOLOCK)
WHERE status <> 2
  AND tanggal > dbo.GetTanggalTerakhirTutupBuku()
  AND CAST(tanggal AS DATE) <= CAST(@tanggal AS DATE);
