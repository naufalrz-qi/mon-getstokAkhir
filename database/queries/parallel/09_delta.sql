-- Delta refresh: fetch only NEW transactions since last snapshot
-- Uses tanggal_server to detect changes after @last_refresh
-- Each section uses IF OBJECT_ID to skip tables that don't exist
SET NOCOUNT ON;
DECLARE @last_refresh DATETIME = ?;
DECLARE @tanggal DATETIME = ?;

-- Temp table to collect all delta rows
CREATE TABLE #delta (
    kd_divisi VARCHAR(10),
    kd_barang VARCHAR(50),
    debet FLOAT,
    kredit FLOAT,
    kd_satuan VARCHAR(10),
    source VARCHAR(20)
);

-- New penjualan
IF OBJECT_ID('t_penjualan_detail') IS NOT NULL AND OBJECT_ID('t_penjualan') IS NOT NULL
BEGIN
    INSERT INTO #delta
    SELECT t.kd_divisi, d.kd_barang, 0 AS debet, d.qty AS kredit, d.kd_satuan, 'penjualan'
    FROM t_penjualan_detail d (NOLOCK)
    INNER JOIN t_penjualan t (NOLOCK) ON d.no_transaksi = t.no_transaksi
    WHERE t.tanggal_server > @last_refresh
      AND t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
      AND CAST(t.tanggal AS DATE) <= CAST(@tanggal AS DATE);
END

-- New pembelian
IF OBJECT_ID('t_pembelian_detail') IS NOT NULL AND OBJECT_ID('t_pembelian') IS NOT NULL
BEGIN
    INSERT INTO #delta
    SELECT t.kd_divisi, d.kd_barang, d.qty AS debet, 0, d.kd_satuan, 'pembelian'
    FROM t_pembelian_detail d (NOLOCK)
    INNER JOIN t_pembelian t (NOLOCK) ON d.no_transaksi = t.no_transaksi
    WHERE t.tanggal_server > @last_refresh
      AND t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
      AND CAST(t.tanggal AS DATE) <= CAST(@tanggal AS DATE);
END

-- New mutasi keluar + masuk
IF OBJECT_ID('t_mutasi_stok_detail') IS NOT NULL AND OBJECT_ID('t_mutasi_stok') IS NOT NULL
BEGIN
    INSERT INTO #delta
    SELECT t.kd_divisi_asal, d.kd_barang, 0, d.qty, d.kd_satuan, 'mutasi_out'
    FROM t_mutasi_stok_detail d (NOLOCK)
    INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
    WHERE t.tanggal_server > @last_refresh
      AND t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
      AND CAST(t.tanggal AS DATE) <= CAST(@tanggal AS DATE);

    INSERT INTO #delta
    SELECT t.kd_divisi_tujuan, d.kd_barang, d.qty, 0, d.kd_satuan, 'mutasi_in'
    FROM t_mutasi_stok_detail d (NOLOCK)
    INNER JOIN t_mutasi_stok t (NOLOCK) ON d.no_transaksi = t.no_transaksi
    WHERE t.tanggal_server > @last_refresh
      AND t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
      AND CAST(t.tanggal AS DATE) <= CAST(@tanggal AS DATE);
END

-- New retur penjualan
IF OBJECT_ID('t_penjualan_retur_detail') IS NOT NULL AND OBJECT_ID('t_penjualan_retur') IS NOT NULL
BEGIN
    INSERT INTO #delta
    SELECT t.kd_divisi, d.kd_barang, d.qty, 0, d.kd_satuan, 'retur_jual'
    FROM t_penjualan_retur_detail d (NOLOCK)
    INNER JOIN t_penjualan_retur t (NOLOCK) ON d.no_retur = t.no_retur
    WHERE t.tanggal_server > @last_refresh
      AND t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
      AND CAST(t.tanggal AS DATE) <= CAST(@tanggal AS DATE);
END

-- New retur pembelian
IF OBJECT_ID('t_pembelian_retur_detail') IS NOT NULL AND OBJECT_ID('t_pembelian_retur') IS NOT NULL
BEGIN
    INSERT INTO #delta
    SELECT t.kd_divisi, d.kd_barang, 0, d.qty, d.kd_satuan, 'retur_beli'
    FROM t_pembelian_retur_detail d (NOLOCK)
    INNER JOIN t_pembelian_retur t (NOLOCK) ON d.no_retur = t.no_retur
    WHERE t.tanggal_server > @last_refresh
      AND t.tanggal > dbo.GetTanggalTerakhirTutupBuku()
      AND CAST(t.tanggal AS DATE) <= CAST(@tanggal AS DATE);
END

-- New opname stok
IF OBJECT_ID('t_opname_stok') IS NOT NULL
BEGIN
    INSERT INTO #delta
    SELECT kd_divisi, kd_barang, qty AS debet, 0 AS kredit, kd_satuan, 'opname_in'
    FROM t_opname_stok (NOLOCK)
    WHERE status = 2
      AND tanggal_server > @last_refresh
      AND tanggal > dbo.GetTanggalTerakhirTutupBuku()
      AND CAST(tanggal AS DATE) <= CAST(@tanggal AS DATE);

    INSERT INTO #delta
    SELECT kd_divisi, kd_barang, 0 AS debet, qty AS kredit, kd_satuan, 'opname_out'
    FROM t_opname_stok (NOLOCK)
    WHERE status <> 2
      AND tanggal_server > @last_refresh
      AND tanggal > dbo.GetTanggalTerakhirTutupBuku()
      AND CAST(tanggal AS DATE) <= CAST(@tanggal AS DATE);
END

-- Return all delta rows
SELECT * FROM #delta;

DROP TABLE #delta;
