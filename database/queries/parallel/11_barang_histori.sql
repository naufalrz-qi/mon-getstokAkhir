-- Query: Fetch transaction history for a specific item (optional division)
-- Parameters: @kd_barang (VARCHAR), @kd_divisi (VARCHAR)
SET NOCOUNT ON;
DECLARE @kd_barang VARCHAR(50) = ?;
DECLARE @kd_divisi VARCHAR(10) = ?;

-- Collect all transaction rows into a CTE
WITH cte_histori AS (
    -- 1. Stok awal dari barang divisi
    SELECT 
        bd.kd_divisi,
        bd.kd_barang,
        dbo.GetTanggalTerakhirTutupBuku() AS tanggal, 
        'Stok Awal' AS Transaksi, 
        '0' AS no_transaksi, 
        bd.stok_awal AS Debet, 
        0.0 AS Kredit, 
        (SELECT TOP 1 kd_satuan FROM m_barang_satuan (NOLOCK) WHERE kd_barang = bd.kd_barang AND jumlah = 1) AS kd_satuan, 
        bd.harga_beli_awal as harga
    FROM m_barang_divisi bd (NOLOCK)
    INNER JOIN m_barang b (NOLOCK) ON bd.kd_barang = b.kd_barang
    INNER JOIN m_kategori k (NOLOCK) ON b.kd_kategori = k.kd_kategori
    WHERE k.status <> 2
      AND bd.kd_barang = @kd_barang
      AND (@kd_divisi IS NULL OR @kd_divisi = '' OR bd.kd_divisi = @kd_divisi)

    UNION ALL

    -- 2. Pengirim mutasi stok (Mutasi Keluar)
    SELECT 
        t.kd_divisi_asal AS kd_divisi,
        d.kd_barang,
        t.tanggal, 
        'Mutasi Keluar' AS Transaksi, 
        d.no_transaksi AS no_transaksi, 
        0.0 AS Debet, 
        d.qty AS Kredit, 
        d.kd_satuan, 
        0.0 AS harga
    FROM t_mutasi_stok_detail d (NOLOCK)
    INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
    WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
      AND d.kd_barang = @kd_barang
      AND (@kd_divisi IS NULL OR @kd_divisi = '' OR t.kd_divisi_asal = @kd_divisi)

    UNION ALL 

    -- 3. Penerima mutasi stok (Mutasi Masuk)
    SELECT 
        t.kd_divisi_tujuan AS kd_divisi,
        d.kd_barang,
        t.tanggal, 
        'Mutasi Masuk' AS Transaksi, 
        d.no_transaksi AS no_transaksi, 
        d.qty AS Debet, 
        0.0 AS Kredit, 
        d.kd_satuan, 
        0.0 AS harga
    FROM t_mutasi_stok_detail d (NOLOCK)
    INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
    WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
      AND d.kd_barang = @kd_barang
      AND (@kd_divisi IS NULL OR @kd_divisi = '' OR t.kd_divisi_tujuan = @kd_divisi)

    UNION ALL

    -- 4. Opname stok yang menambahkan stok
    SELECT 
        kd_divisi,
        kd_barang,
        tanggal, 
        'Opname Masuk' AS Transaksi, 
        no_transaksi AS no_transaksi, 
        QTY AS Debet, 
        0.0 AS Kredit, 
        kd_satuan, 
        0.0 AS harga
    FROM t_opname_stok (NOLOCK)
    WHERE status = 2 
      AND tanggal > dbo.GetTanggalTerakhirTutupBuku()
      AND kd_barang = @kd_barang
      AND (@kd_divisi IS NULL OR @kd_divisi = '' OR kd_divisi = @kd_divisi)

    UNION ALL

    -- 5. Opname stok yang mengurangkan stok
    SELECT 
        kd_divisi,
        kd_barang,
        tanggal, 
        'Opname Keluar' AS Transaksi, 
        no_transaksi AS no_transaksi, 
        0.0 AS Debet, 
        qty AS Kredit, 
        kd_satuan, 
        0.0 AS harga
    FROM t_opname_stok (NOLOCK)
    WHERE status <> 2 
      AND tanggal > dbo.GetTanggalTerakhirTutupBuku()
      AND kd_barang = @kd_barang
      AND (@kd_divisi IS NULL OR @kd_divisi = '' OR kd_divisi = @kd_divisi)

    UNION ALL

    -- 6. Pembelian barang
    SELECT 
        t.kd_divisi,
        d.kd_barang,
        t.tanggal, 
        'Pembelian' AS Transaksi, 
        d.no_transaksi AS no_transaksi, 
        d.qty AS Debet, 
        0.0 AS Kredit, 
        d.kd_satuan, 
        d.harga_beli AS harga
    FROM t_pembelian_detail d (NOLOCK)
    INNER JOIN t_pembelian t (NOLOCK) ON d.no_transaksi = t.no_transaksi
    WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku() 
      AND t.status IN (0, 1)
      AND d.kd_barang = @kd_barang
      AND (@kd_divisi IS NULL OR @kd_divisi = '' OR t.kd_divisi = @kd_divisi)

    UNION ALL

    -- 7. Retur pembelian (mengurangi stok)
    SELECT 
        t.kd_divisi,
        d.kd_barang,
        t.tanggal, 
        'Retur Pembelian' AS Transaksi, 
        d.no_retur AS no_transaksi, 
        0.0 AS Debet, 
        d.qty AS Kredit, 
        d.kd_satuan, 
        d.harga AS harga
    FROM t_pembelian_retur_detail d (NOLOCK)
    INNER JOIN t_pembelian_retur t (NOLOCK) ON d.no_retur = t.no_retur
    WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
      AND d.kd_barang = @kd_barang
      AND (@kd_divisi IS NULL OR @kd_divisi = '' OR t.kd_divisi = @kd_divisi)

    UNION ALL

    -- 8. Penjualan (mengurangi stok)
    SELECT 
        t.kd_divisi,
        d.kd_barang,
        t.tanggal, 
        'Penjualan' AS Transaksi, 
        d.no_transaksi AS no_transaksi, 
        0.0 AS Debet, 
        d.qty AS Kredit, 
        d.kd_satuan, 
        d.harga_jual AS harga
    FROM t_penjualan_detail d (NOLOCK)
    INNER JOIN t_penjualan t (NOLOCK) ON d.no_transaksi = t.no_transaksi
    INNER JOIN m_barang b (NOLOCK) ON d.kd_barang = b.kd_barang
    INNER JOIN m_kategori k (NOLOCK) ON b.kd_kategori = k.kd_kategori
    WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku() 
      AND k.status <> 2
      AND d.kd_barang = @kd_barang
      AND (@kd_divisi IS NULL OR @kd_divisi = '' OR t.kd_divisi = @kd_divisi)

    UNION ALL

    -- 9. Retur penjualan barang (menambah stok)
    SELECT 
        t.kd_divisi,
        d.kd_barang,
        t.tanggal, 
        'Retur Penjualan' AS Transaksi, 
        d.no_retur AS no_transaksi, 
        d.qty AS Debet, 
        0.0 AS Kredit, 
        d.kd_satuan, 
        d.harga_jual AS harga
    FROM t_penjualan_retur_detail d (NOLOCK)
    INNER JOIN t_penjualan_retur t (NOLOCK) ON d.no_retur = t.no_retur
    WHERE t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
      AND d.kd_barang = @kd_barang
      AND (@kd_divisi IS NULL OR @kd_divisi = '' OR t.kd_divisi = @kd_divisi)
)
SELECT 
    h.kd_divisi AS [Kd_Divisi],
    m_divisi.keterangan AS [Divisi],
    m_divisi.kepala_nota AS [K.Nota],
    h.tanggal,
    h.Transaksi,
    h.no_transaksi,
    h.kd_barang,
    m_barang.nama AS [barang],
    h.Debet,
    h.Kredit,
    h.kd_satuan,
    m_satuan.nama AS [satuan],
    h.harga
FROM cte_histori h
INNER JOIN m_divisi (NOLOCK) ON h.kd_divisi = m_divisi.kd_divisi
INNER JOIN m_barang (NOLOCK) ON h.kd_barang = m_barang.kd_barang
LEFT JOIN m_satuan (NOLOCK) ON h.kd_satuan = m_satuan.kd_satuan
ORDER BY h.tanggal ASC;
