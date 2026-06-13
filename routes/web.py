from app.Http.Controllers.StokController import StokController
from app.Http.Controllers.ServerController import ServerController
from app.Http.Controllers.AuthController import AuthController

class Route:
    """
    Laravel-style Router untuk Flask.
    """
    _app = None

    @classmethod
    def init_app(cls, app):
        cls._app = app

    @classmethod
    def _register(cls, url, handler, methods):
        endpoint_name = f"{handler.__name__}_{url.replace('/', '_').strip('_')}_{'-'.join(methods)}"
        cls._app.add_url_rule(url, endpoint=endpoint_name, view_func=handler, methods=methods)

    @classmethod
    def get(cls, url, handler):
        cls._register(url, handler, ['GET'])

    @classmethod
    def post(cls, url, handler):
        cls._register(url, handler, ['POST'])

    @classmethod
    def put(cls, url, handler):
        cls._register(url, handler, ['PUT'])

    @classmethod
    def delete(cls, url, handler):
        cls._register(url, handler, ['DELETE'])


def register_routes(app):
    """Registrasi semua endpoint routing aplikasi"""
    Route.init_app(app)

    # Shorthand decorators
    login = AuthController.login_required
    admin = AuthController.admin_required
    superadmin = AuthController.super_admin_required
    menu = AuthController.menu_required

    # ==========================================================
    # Web Routes (HTML Pages) — semua butuh login
    # ==========================================================
    Route.get('/dashboard', menu('dashboard_bisnis')(StokController.dashboard_page))
    Route.get('/dashboard-stok', menu('dashboard_stok')(StokController.dashboard_stok_page))
    Route.get('/', login(StokController.index_page))
    Route.get('/stok/index', login(StokController.index_page))
    Route.get('/stok/', menu('stok_index')(StokController.monitoring_page))
    Route.get('/stok/histori', menu('stok_histori')(StokController.histori_page))
    Route.get('/mon/transaksi', menu('stok_data_transaksi')(StokController.monitoring_transaksi_page))
    Route.get('/mon/penjualan', menu('stok_data_transaksi')(StokController.monitoring_penjualan_page))
    Route.get('/stok/opname', menu('stok_opname')(StokController.opname_page))
    Route.get('/master/update-barang', menu('master_update')(StokController.update_barang_page))
    Route.get('/stok/servers', superadmin(ServerController.servers_page))
    Route.get('/stok/mass-refresh', admin(StokController.mass_refresh_page))
    Route.get('/mon/sync-duckdb', admin(StokController.sync_duckdb_page))
    Route.get('/mon/mass-sync-duckdb', admin(StokController.mass_sync_duckdb_page))

    # API endpoints
    Route.get('/api/dashboard/summary', login(StokController.fetch_dashboard_summary))
    Route.get('/api/dashboard/trend', menu('dashboard_bisnis')(StokController.dashboard_analytics_trend))
    Route.get('/api/dashboard/retention', menu('dashboard_bisnis')(StokController.dashboard_analytics_retention))
    Route.get('/api/dashboard/heatmap', menu('dashboard_bisnis')(StokController.dashboard_analytics_heatmap))
    Route.get('/api/dashboard/validate', menu('dashboard_bisnis')(StokController.dashboard_analytics_validate))
    Route.get('/api/dashboard/radar', menu('dashboard_bisnis')(StokController.dashboard_analytics_radar))
    Route.get('/api/dashboard/basket', menu('dashboard_bisnis')(StokController.dashboard_analytics_basket))
    Route.get('/api/dashboard/stock-predict', menu('dashboard_bisnis')(StokController.dashboard_analytics_stock_predict))

    Route.get('/stok/api/monitoring', menu('stok_index')(StokController.fetch_monitoring_data))
    Route.get('/api/monitoring/penjualan', menu('stok_data_transaksi')(StokController.fetch_sales_monitoring))
    Route.get('/stok/api/histori', menu('stok_histori')(StokController.fetch_barang_histori))
    Route.get('/master/api/barang', login(StokController.fetch_barang_data))
    Route.post('/master/api/barang/update', menu('master_update')(StokController.update_barang))
    
    Route.post('/api/duckdb/sync', admin(StokController.trigger_duckdb_sync))
    Route.get('/api/duckdb/status', login(StokController.check_duckdb_status))
    Route.post('/api/duckdb/mass-sync', admin(StokController.trigger_mass_duckdb_sync))
    Route.get('/api/duckdb/mass-status', login(StokController.check_mass_duckdb_status))
    # ==========================================================
    # API Routes : Authentication (publik untuk login/logout)
    # ==========================================================
    Route.get('/auth/login', AuthController.login_page)
    Route.post('/auth/login', AuthController.login)
    Route.get('/auth/logout', AuthController.logout)
    
    Route.get('/auth/change-password', login(AuthController.change_password_page))
    Route.post('/auth/change-password', login(AuthController.change_password))
    
    Route.get('/auth/users', superadmin(AuthController.users_page))
    Route.get('/api/users', superadmin(AuthController.api_get_users))
    Route.post('/api/users', superadmin(AuthController.api_create_user))
    Route.delete('/api/users/<username>', superadmin(AuthController.api_delete_user))
    
    Route.get('/auth/access', superadmin(AuthController.user_access_page))
    Route.post('/api/users/access', superadmin(AuthController.api_update_user_access))

    # ==========================================================
    # API Routes : Server Session — butuh login
    # ==========================================================
    Route.get('/stok/server-list', login(StokController.get_server_list))
    Route.post('/stok/select-server', login(StokController.select_server))
    Route.get('/stok/get-current-server', login(StokController.get_current_server))

    # ==========================================================
    # API Routes : Snapshot Management
    # ==========================================================
    Route.post('/stok/snapshot/refresh', superadmin(StokController.trigger_refresh))
    Route.post('/stok/snapshot/delta', admin(StokController.trigger_delta_refresh))
    Route.post('/stok/snapshot/yearly', admin(StokController.trigger_yearly_refresh))
    Route.post('/stok/snapshot/weekly', admin(StokController.trigger_weekly_refresh))
    Route.get('/stok/snapshot/status', login(StokController.snapshot_status))
    Route.post('/stok/snapshot/cancel', login(StokController.cancel_refresh))
    
    Route.post('/stok/snapshot/refresh/<path:server_key>', superadmin(StokController.trigger_refresh_target))
    Route.post('/stok/snapshot/delta/<path:server_key>', admin(StokController.trigger_delta_refresh_target))
    Route.post('/stok/snapshot/yearly/<path:server_key>', admin(StokController.trigger_yearly_refresh_target))
    Route.post('/stok/snapshot/weekly/<path:server_key>', admin(StokController.trigger_weekly_refresh_target))
    Route.get('/stok/snapshot/status/<path:server_key>', login(StokController.snapshot_status_target))
    Route.get('/stok/snapshot/status-all', login(StokController.global_snapshot_status))

    # ==========================================================
    # API Routes : Stok Data (reads from local snapshot) — butuh login
    # ==========================================================
    Route.get('/stok/monitoring', menu('stok_index')(StokController.fetch_monitoring_data))
    Route.get('/stok/api/divisi-list', login(StokController.fetch_divisi_list))
    Route.get('/stok/api/opname', menu('stok_opname')(StokController.fetch_opname_data))
    Route.get('/stok/api/item-stock/<kd_barang>', login(StokController.fetch_item_stock_detail))
    Route.get('/stok/barang-histori', menu('stok_histori')(StokController.fetch_barang_histori))
    Route.get('/stok/barang-tanpa-transaksi', menu('stok_data_transaksi')(StokController.fetch_barang_tanpa_transaksi))
    Route.get('/stok/barang-dengan-transaksi', menu('stok_data_transaksi')(StokController.fetch_barang_dengan_transaksi))
    Route.get('/stok/semua-barang-stok-awal', menu('stok_data_transaksi')(StokController.fetch_semua_barang_stok_awal))
    Route.get('/stok/export/histori', menu('stok_histori')(StokController.export_histori_xlsx))
    Route.get('/stok/export/barang-tanpa-transaksi', menu('stok_data_transaksi')(StokController.export_barang_tanpa_transaksi_xlsx))
    Route.get('/stok/export/barang-dengan-transaksi', menu('stok_data_transaksi')(StokController.export_barang_dengan_transaksi_xlsx))
    Route.get('/stok/export/bulk-transaksi', menu('stok_data_transaksi')(StokController.export_bulk_transaksi_xlsx))
    Route.get('/stok/export/semua-barang-stok-awal', menu('stok_data_transaksi')(StokController.export_semua_barang_stok_awal_xlsx))
    Route.get('/stok/export/xlsx', menu('stok_index')(StokController.export_xlsx))
    Route.get('/stok/low-stock-alert', menu('stok_index')(StokController.fetch_low_stock_alert))


    # ==========================================================
    # API Routes : Server Management CRUD
    # ==========================================================
    Route.get('/stok/api/servers', superadmin(ServerController.get_all_servers))
    Route.post('/stok/api/servers', superadmin(ServerController.create_server))
    Route.put('/stok/api/servers/<server_key>', superadmin(ServerController.update_server))
    Route.delete('/stok/api/servers/<server_key>', superadmin(ServerController.delete_server))
