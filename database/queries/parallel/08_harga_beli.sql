-- Harga beli terakhir per barang
SET NOCOUNT ON;

SELECT kd_barang, harga_beli
FROM (
    SELECT d.kd_barang, d.harga_beli,
           ROW_NUMBER() OVER (PARTITION BY d.kd_barang ORDER BY t.tanggal DESC) AS rn
    FROM t_pembelian_detail d (NOLOCK)
    INNER JOIN t_pembelian t (NOLOCK) ON d.no_transaksi = t.no_transaksi
) ranked
WHERE rn = 1;
