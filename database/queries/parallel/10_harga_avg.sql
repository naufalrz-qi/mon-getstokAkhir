-- Harga average (weighted average purchase price per item)
SET NOCOUNT ON;

SELECT kd_barang,
       CASE WHEN SUM(qty) > 0 THEN SUM(qty * harga_beli) / SUM(qty) ELSE 0 END AS harga_avg
FROM t_pembelian_detail (NOLOCK)
GROUP BY kd_barang;
