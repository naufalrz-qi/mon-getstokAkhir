-- Detail Opname Stok untuk fitur histori/opname read-only
SET NOCOUNT ON;

SELECT 
    d.no_transaksi, 
    d.kd_divisi, 
    d.kd_barang, 
    d.kd_satuan, 
    s.nama AS satuan,
    d.tanggal, 
    d.qty, 
    d.keterangan, 
    u.nama AS petugas, 
    d.status, 
    d.tanggal_server
FROM t_opname_stok d (NOLOCK)
LEFT JOIN m_userx u (NOLOCK) ON d.kd_user = u.kd_user
LEFT JOIN m_satuan s (NOLOCK) ON d.kd_satuan = s.kd_satuan
WHERE d.tanggal > dbo.GetTanggalTerakhirTutupBuku();
