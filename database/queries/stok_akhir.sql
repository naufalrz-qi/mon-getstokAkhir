-- Query: Fetch ALL stok akhir data from remote MSSQL for snapshot
-- This runs ONCE to build the local SQLite snapshot, no search filter needed
-- All filtering happens locally in Python/SQLite after snapshot is built

SET NOCOUNT ON;
DECLARE @tanggal DATETIME = ?;

WITH cte_pembelian_terakhir AS (
    SELECT 
        kd_barang,
        harga_beli
    FROM (
        SELECT 
            t_pembelian_detail.kd_barang,
            t_pembelian_detail.harga_beli,
            ROW_NUMBER() OVER (PARTITION BY t_pembelian_detail.kd_barang ORDER BY t_pembelian.tanggal DESC) AS rn
        FROM t_pembelian_detail (NOLOCK)
        INNER JOIN t_pembelian (NOLOCK) ON t_pembelian_detail.no_Transaksi = t_pembelian.no_transaksi
    ) ranked
    WHERE rn = 1
),
cte_stok_kalkulasi AS (
    SELECT 
        v_g_barang_histori_detail.kd_divisi,
        v_g_barang_histori_detail.kd_barang,
        m_barang.nama AS nama_barang,
        m_divisi.nama AS nama_divisi,
        m_kategori.nama AS kategori,
        m_merk.nama AS merk,
        m_model.nama AS model,
        m_warna.nama AS warna,
        m_barang.ukuran,
        m_barang_satuan.harga_jual,
        ISNULL(mbs_conv.jumlah, 1) * 
            (v_g_barang_histori_detail.Debet - v_g_barang_histori_detail.Kredit) AS qty_kalkulasi
    FROM m_divisi (NOLOCK)
    INNER JOIN v_g_barang_histori_detail (NOLOCK) 
        ON m_divisi.kd_divisi = v_g_barang_histori_detail.kd_divisi
    LEFT JOIN m_barang_satuan mbs_conv (NOLOCK)
        ON v_g_barang_histori_detail.kd_barang = mbs_conv.kd_barang
        AND v_g_barang_histori_detail.kd_satuan = mbs_conv.kd_satuan
    INNER JOIN m_barang (NOLOCK) 
        ON v_g_barang_histori_detail.kd_barang = m_barang.kd_barang
    INNER JOIN m_kategori (NOLOCK) 
        ON m_barang.kd_kategori = m_kategori.kd_kategori
    INNER JOIN m_barang_satuan (NOLOCK) 
        ON v_g_barang_histori_detail.kd_barang = m_barang_satuan.kd_barang
    INNER JOIN m_model (NOLOCK) 
        ON m_barang.kd_model = m_model.kd_model
    INNER JOIN m_merk (NOLOCK) 
        ON m_barang.kd_merk = m_merk.kd_merk
    INNER JOIN m_warna (NOLOCK) 
        ON m_barang.kd_warna = m_warna.kd_warna
    WHERE CAST(v_g_barang_histori_detail.tanggal AS DATE) < DATEADD(DAY, 1, CAST(@tanggal AS DATE))
        AND m_barang_satuan.jumlah = 1 
        AND m_barang_satuan.status <> 0 
        AND m_kategori.status <> 2
)
SELECT 
    kd_divisi AS [Kode Divisi],
    nama_divisi AS Divisi,
    cte_stok_kalkulasi.kd_barang AS [Kode Barang],
    nama_barang AS Barang,
    kategori AS Kategori,
    merk AS Merk,
    model AS Model,
    warna AS Warna,
    ukuran AS Ukuran,
    SUM(qty_kalkulasi) AS [Stok Akhir],
    harga_jual AS [Harga Jual],
    COALESCE(pb.harga_beli, 
        (SELECT TOP 1 harga_beli_awal FROM m_barang_divisi (NOLOCK) 
         WHERE kd_barang = cte_stok_kalkulasi.kd_barang 
         ORDER BY harga_beli_awal DESC), 0) AS [Harga Beli Akhir]
FROM cte_stok_kalkulasi
LEFT JOIN cte_pembelian_terakhir pb 
    ON cte_stok_kalkulasi.kd_barang = pb.kd_barang
GROUP BY 
    kd_divisi, nama_divisi, cte_stok_kalkulasi.kd_barang, nama_barang, kategori, merk, 
    model, warna, ukuran, harga_jual, pb.harga_beli
ORDER BY nama_divisi, nama_barang
