-- Stok Awal (dari tutup buku terakhir)
SET NOCOUNT ON;
DECLARE @tanggal DATETIME = ?;

SELECT bd.kd_divisi, bd.kd_barang, 
       bd.stok_awal AS debet, 0 AS kredit,
       (SELECT TOP 1 kd_satuan FROM m_barang_satuan (NOLOCK) WHERE kd_barang = bd.kd_barang AND jumlah = 1) AS kd_satuan
FROM m_barang_divisi bd (NOLOCK)
INNER JOIN m_barang b (NOLOCK) ON bd.kd_barang = b.kd_barang
INNER JOIN m_kategori k (NOLOCK) ON b.kd_kategori = k.kd_kategori
WHERE k.status <> 2;
