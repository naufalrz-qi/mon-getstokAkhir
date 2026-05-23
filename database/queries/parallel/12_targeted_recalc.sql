-- Targeted recalculation: compute stok_akhir from scratch for specific items
-- This computes: SUM(debet) - SUM(kredit) per (kd_divisi, kd_barang), with satuan conversion done in SQL
SET NOCOUNT ON;

SELECT kd_divisi, kd_barang,
       ROUND(SUM(debet_conv) - SUM(kredit_conv), 4) AS stok_akhir
FROM (
    -- 1. Stok Awal (already in base unit)
    SELECT bd.kd_divisi, bd.kd_barang,
           bd.stok_awal AS debet_conv, 0.0 AS kredit_conv
    FROM m_barang_divisi bd (NOLOCK)
    INNER JOIN m_barang b (NOLOCK) ON bd.kd_barang = b.kd_barang
    INNER JOIN m_kategori k (NOLOCK) ON b.kd_kategori = k.kd_kategori
    WHERE k.status <> 2
      AND ? IS NULL  -- If base_date is NOT NULL, skip stok_awal from DB (use checkpoint in python)
      AND bd.kd_barang IN ({placeholders})

    UNION ALL

    -- 2. Penjualan (kredit)
    SELECT t.kd_divisi, d.kd_barang,
           0.0, d.qty * ISNULL(mbs.jumlah, 1)
    FROM t_penjualan_detail d (NOLOCK)
    INNER JOIN t_penjualan t (NOLOCK) ON d.no_transaksi = t.no_transaksi
    INNER JOIN m_barang b (NOLOCK) ON d.kd_barang = b.kd_barang
    INNER JOIN m_kategori k (NOLOCK) ON b.kd_kategori = k.kd_kategori
    LEFT JOIN m_barang_satuan mbs (NOLOCK) ON d.kd_barang = mbs.kd_barang AND d.kd_satuan = mbs.kd_satuan
    WHERE t.tanggal > COALESCE(?, dbo.GetTanggalTerakhirTutupBuku())
      AND CAST(t.tanggal AS DATE) <= CAST({tanggal_placeholder} AS DATE)
      AND k.status <> 2
      AND d.kd_barang IN ({placeholders})

    UNION ALL

    -- 3. Pembelian (debet)
    SELECT t.kd_divisi, d.kd_barang,
           d.qty * ISNULL(mbs.jumlah, 1), 0.0
    FROM t_pembelian_detail d (NOLOCK)
    INNER JOIN t_pembelian t (NOLOCK) ON d.no_transaksi = t.no_transaksi
    LEFT JOIN m_barang_satuan mbs (NOLOCK) ON d.kd_barang = mbs.kd_barang AND d.kd_satuan = mbs.kd_satuan
    WHERE t.tanggal > COALESCE(?, dbo.GetTanggalTerakhirTutupBuku())
      AND t.status IN (0, 1)
      AND CAST(t.tanggal AS DATE) <= CAST({tanggal_placeholder} AS DATE)
      AND d.kd_barang IN ({placeholders})

    UNION ALL

    -- 4. Opname masuk (debet, status=2)
    SELECT d.kd_divisi, d.kd_barang,
           d.qty * ISNULL(mbs.jumlah, 1), 0.0
    FROM t_opname_stok d (NOLOCK)
    LEFT JOIN m_barang_satuan mbs (NOLOCK) ON d.kd_barang = mbs.kd_barang AND d.kd_satuan = mbs.kd_satuan
    WHERE d.status = 2
      AND d.tanggal > COALESCE(?, dbo.GetTanggalTerakhirTutupBuku())
      AND CAST(d.tanggal AS DATE) <= CAST({tanggal_placeholder} AS DATE)
      AND d.kd_barang IN ({placeholders})

    UNION ALL

    -- 5. Opname keluar (kredit, status<>2)
    SELECT d.kd_divisi, d.kd_barang,
           0.0, d.qty * ISNULL(mbs.jumlah, 1)
    FROM t_opname_stok d (NOLOCK)
    LEFT JOIN m_barang_satuan mbs (NOLOCK) ON d.kd_barang = mbs.kd_barang AND d.kd_satuan = mbs.kd_satuan
    WHERE d.status <> 2
      AND d.tanggal > COALESCE(?, dbo.GetTanggalTerakhirTutupBuku())
      AND CAST(d.tanggal AS DATE) <= CAST({tanggal_placeholder} AS DATE)
      AND d.kd_barang IN ({placeholders})

    UNION ALL

    -- 6. Mutasi keluar (kredit dari divisi asal)
    SELECT t.kd_divisi_asal, d.kd_barang,
           0.0, d.qty * ISNULL(mbs.jumlah, 1)
    FROM t_mutasi_stok_detail d (NOLOCK)
    INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
    LEFT JOIN m_barang_satuan mbs (NOLOCK) ON d.kd_barang = mbs.kd_barang AND d.kd_satuan = mbs.kd_satuan
    WHERE t.tanggal > COALESCE(?, dbo.GetTanggalTerakhirTutupBuku())
      AND CAST(t.tanggal AS DATE) <= CAST({tanggal_placeholder} AS DATE)
      AND d.kd_barang IN ({placeholders})

    UNION ALL

    -- 7. Mutasi masuk (debet ke divisi tujuan)
    SELECT t.kd_divisi_tujuan, d.kd_barang,
           d.qty * ISNULL(mbs.jumlah, 1), 0.0
    FROM t_mutasi_stok_detail d (NOLOCK)
    INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
    LEFT JOIN m_barang_satuan mbs (NOLOCK) ON d.kd_barang = mbs.kd_barang AND d.kd_satuan = mbs.kd_satuan
    WHERE t.tanggal > COALESCE(?, dbo.GetTanggalTerakhirTutupBuku())
      AND CAST(t.tanggal AS DATE) <= CAST({tanggal_placeholder} AS DATE)
      AND d.kd_barang IN ({placeholders})

    UNION ALL

    -- 8. Retur penjualan (debet - barang kembali)
    SELECT t.kd_divisi, d.kd_barang,
           d.qty * ISNULL(mbs.jumlah, 1), 0.0
    FROM t_penjualan_retur_detail d (NOLOCK)
    INNER JOIN t_penjualan_retur t (NOLOCK) ON d.no_retur = t.no_retur
    LEFT JOIN m_barang_satuan mbs (NOLOCK) ON d.kd_barang = mbs.kd_barang AND d.kd_satuan = mbs.kd_satuan
    WHERE t.tanggal > COALESCE(?, dbo.GetTanggalTerakhirTutupBuku())
      AND CAST(t.tanggal AS DATE) <= CAST({tanggal_placeholder} AS DATE)
      AND d.kd_barang IN ({placeholders})

    UNION ALL

    -- 9. Retur pembelian (kredit - dikembalikan ke supplier)
    SELECT t.kd_divisi, d.kd_barang,
           0.0, d.qty * ISNULL(mbs.jumlah, 1)
    FROM t_pembelian_retur_detail d (NOLOCK)
    INNER JOIN t_pembelian_retur t (NOLOCK) ON d.no_retur = t.no_retur
    LEFT JOIN m_barang_satuan mbs (NOLOCK) ON d.kd_barang = mbs.kd_barang AND d.kd_satuan = mbs.kd_satuan
    WHERE t.tanggal > COALESCE(?, dbo.GetTanggalTerakhirTutupBuku())
      AND CAST(t.tanggal AS DATE) <= CAST({tanggal_placeholder} AS DATE)
      AND d.kd_barang IN ({placeholders})
) AS all_txn
GROUP BY kd_divisi, kd_barang;
