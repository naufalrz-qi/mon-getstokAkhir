-- Query: Ambil data mentah untuk kalkulasi Harga Average di Python
-- Menggantikan TVF GetHargaAverageBarangPerTanggal sepenuhnya
-- Semua UDF sudah di-inline sebagai JOIN/expression
SET NOCOUNT ON;
DECLARE @tanggal DATETIME = ?;
DECLARE @tutup_buku DATETIME = dbo.GetTanggalTerakhirTutupBuku();

-- ============================================================
-- BAGIAN 1: Stok akhir per barang (untuk tahu barang mana yg stok > 0)
-- ============================================================
SELECT 
    vh.kd_barang,
    SUM(ISNULL(mbs.jumlah, 1) * (vh.Debet - vh.Kredit)) AS stok_akhir
INTO #stok_akhir_temp
FROM v_g_barang_histori_detail vh (NOLOCK)
LEFT JOIN m_barang_satuan mbs (NOLOCK) 
    ON vh.kd_barang = mbs.kd_barang AND vh.kd_satuan = mbs.kd_satuan
WHERE CAST(vh.tanggal AS DATE) <= CAST(@tanggal AS DATE)
GROUP BY vh.kd_barang;

-- Return stok akhir
SELECT kd_barang, stok_akhir FROM #stok_akhir_temp;

-- ============================================================
-- BAGIAN 2: Histori harga masuk (stok awal + opname + pembelian)
-- ============================================================

-- Stok Awal
SELECT
    bd.kd_barang,
    @tutup_buku AS tanggal,
    'STOK_AWAL' AS jenis,
    ISNULL(SUM(ISNULL(bd.stok_awal, 0)), 0) AS stok,
    ISNULL(AVG(CASE WHEN bd.stok_awal > 0 THEN bd.harga_beli_awal END), 0) AS harga
FROM m_barang_divisi bd (NOLOCK)
GROUP BY bd.kd_barang

UNION ALL

-- Opname Stok (yang menambah stok)
SELECT
    os.kd_barang,
    os.tanggal,
    'OPNAME' AS jenis,
    ISNULL(mbs.jumlah, 1) * os.qty AS stok,
    0 AS harga
FROM t_opname_stok os (NOLOCK)
LEFT JOIN m_barang_satuan mbs (NOLOCK) 
    ON os.kd_barang = mbs.kd_barang AND os.kd_satuan = mbs.kd_satuan
WHERE os.status = 2 
    AND CAST(os.tanggal AS DATE) BETWEEN CAST(@tutup_buku AS DATE) AND CAST(@tanggal AS DATE)

UNION ALL

-- Pembelian (harga mentah + diskon untuk diproses Python)
SELECT
    d.kd_barang,
    b.tanggal,
    'PEMBELIAN' AS jenis,
    ISNULL(mbs.jumlah, 1) * d.qty AS stok,
    -- Inline harga bersih: apply item discounts then header discounts
    -- Step 1: item-level discounts (d.diskon1-4, no tax)
    -- Step 2: header-level discounts + tax + ppnbm
    -- Step 3: divide by unit conversion
    CASE WHEN ISNULL(mbs.jumlah, 1) = 0 THEN 0
    ELSE
        -- Harga setelah diskon item
        (CASE 
            WHEN d.harga_beli <= 0 THEN 0
            ELSE d.harga_beli
                * (CASE WHEN ABS(ISNULL(d.diskon1,0)) < 1 THEN (1 - ISNULL(d.diskon1,0)) ELSE 1 END)
                * (CASE WHEN ABS(ISNULL(d.diskon2,0)) < 1 THEN (1 - ISNULL(d.diskon2,0)) ELSE 1 END)
                * (CASE WHEN ABS(ISNULL(d.diskon3,0)) < 1 THEN (1 - ISNULL(d.diskon3,0)) ELSE 1 END)
                * (CASE WHEN ABS(ISNULL(d.diskon4,0)) < 1 THEN (1 - ISNULL(d.diskon4,0)) ELSE 1 END)
                - (CASE WHEN ABS(ISNULL(d.diskon1,0)) >= 1 THEN ISNULL(d.diskon1,0) ELSE 0 END)
                - (CASE WHEN ABS(ISNULL(d.diskon2,0)) >= 1 THEN ISNULL(d.diskon2,0) ELSE 0 END)
                - (CASE WHEN ABS(ISNULL(d.diskon3,0)) >= 1 THEN ISNULL(d.diskon3,0) ELSE 0 END)
                - (CASE WHEN ABS(ISNULL(d.diskon4,0)) >= 1 THEN ISNULL(d.diskon4,0) ELSE 0 END)
        END)
        -- Diskon header + pajak + ppnbm (simplified, applied on result)
        * (CASE WHEN ABS(ISNULL(b.diskon1,0)) < 1 THEN (1 - ISNULL(b.diskon1,0)) ELSE 1 END)
        * (CASE WHEN ABS(ISNULL(b.diskon2,0)) < 1 THEN (1 - ISNULL(b.diskon2,0)) ELSE 1 END)
        * (CASE WHEN ABS(ISNULL(b.diskon3,0)) < 1 THEN (1 - ISNULL(b.diskon3,0)) ELSE 1 END)
        * (CASE WHEN ABS(ISNULL(b.diskon4,0)) < 1 THEN (1 - ISNULL(b.diskon4,0)) ELSE 1 END)
        * (1 + ISNULL(b.pajak, 0))
        * (1 + ISNULL(b.ppnbm, 0))
        / ISNULL(mbs.jumlah, 1)
    END AS harga
FROM t_pembelian_detail d (NOLOCK)
INNER JOIN t_pembelian b (NOLOCK) ON d.no_transaksi = b.no_transaksi
LEFT JOIN m_barang_satuan mbs (NOLOCK) 
    ON d.kd_barang = mbs.kd_barang AND d.kd_satuan = mbs.kd_satuan
WHERE CAST(b.tanggal AS DATE) BETWEEN CAST(@tutup_buku AS DATE) AND CAST(@tanggal AS DATE);

-- Cleanup
DROP TABLE IF EXISTS #stok_akhir_temp;
