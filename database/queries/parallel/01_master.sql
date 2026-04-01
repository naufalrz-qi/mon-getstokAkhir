-- Master tables: barang, kategori, merk, model, warna, divisi, satuan konversi
SET NOCOUNT ON;

SELECT 
    b.kd_barang, b.nama, b.kd_kategori, b.kd_merk, b.kd_model, b.kd_warna, b.ukuran,
    k.nama AS kategori, mk.nama AS merk, mo.nama AS model, w.nama AS warna,
    bs.harga_jual, bs.kd_satuan AS kd_satuan_terkecil
FROM m_barang b (NOLOCK)
INNER JOIN m_kategori k (NOLOCK) ON b.kd_kategori = k.kd_kategori
INNER JOIN m_merk mk (NOLOCK) ON b.kd_merk = mk.kd_merk
INNER JOIN m_model mo (NOLOCK) ON b.kd_model = mo.kd_model
INNER JOIN m_warna w (NOLOCK) ON b.kd_warna = w.kd_warna
INNER JOIN m_barang_satuan bs (NOLOCK) ON b.kd_barang = bs.kd_barang
WHERE bs.jumlah = 1 AND bs.status <> 0 AND k.status <> 2;

-- Satuan konversi (semua satuan per barang)
SELECT kd_barang, kd_satuan, jumlah
FROM m_barang_satuan (NOLOCK);

-- Divisi
SELECT kd_divisi, nama FROM m_divisi (NOLOCK);
