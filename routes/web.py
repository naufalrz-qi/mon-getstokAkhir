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
        endpoint_name = f"{handler.__name__}_{'-'.join(methods)}"
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

    # ==========================================================
    # Web Routes (HTML Pages)
    # ==========================================================
    Route.get('/', StokController.index_page)
    Route.get('/stok/index', StokController.index_page)
    Route.get('/stok/', StokController.monitoring_page)
    Route.get('/stok/histori', StokController.histori_page)
    Route.get('/stok/servers', AuthController.super_admin_required(ServerController.servers_page))
    Route.get('/stok/mass-refresh', AuthController.admin_required(StokController.mass_refresh_page))

    # ==========================================================
    # API Routes : Authentication
    # ==========================================================
    Route.get('/auth/login', AuthController.login_page)
    Route.post('/auth/login', AuthController.login)
    Route.get('/auth/logout', AuthController.logout)
    
    Route.get('/auth/change-password', AuthController.admin_required(AuthController.change_password_page))
    Route.post('/auth/change-password', AuthController.admin_required(AuthController.change_password))
    
    Route.get('/auth/users', AuthController.super_admin_required(AuthController.users_page))
    Route.get('/api/users', AuthController.super_admin_required(AuthController.api_get_users))
    Route.post('/api/users', AuthController.super_admin_required(AuthController.api_create_user))
    Route.delete('/api/users/<username>', AuthController.super_admin_required(AuthController.api_delete_user))

    # ==========================================================
    # API Routes : Server Session
    # ==========================================================
    Route.get('/stok/server-list', StokController.get_server_list)
    Route.post('/stok/select-server', StokController.select_server)
    Route.get('/stok/get-current-server', StokController.get_current_server)

    # ==========================================================
    # API Routes : Snapshot Management
    # ==========================================================
    Route.post('/stok/snapshot/refresh', AuthController.super_admin_required(StokController.trigger_refresh))
    Route.post('/stok/snapshot/delta', AuthController.admin_required(StokController.trigger_delta_refresh))
    Route.get('/stok/snapshot/status', StokController.snapshot_status)
    Route.post('/stok/snapshot/cancel', StokController.cancel_refresh)
    
    Route.post('/stok/snapshot/refresh/<path:server_key>', AuthController.super_admin_required(StokController.trigger_refresh_target))
    Route.post('/stok/snapshot/delta/<path:server_key>', AuthController.admin_required(StokController.trigger_delta_refresh_target))
    Route.get('/stok/snapshot/status/<path:server_key>', StokController.snapshot_status_target)
    Route.get('/stok/snapshot/status-all', StokController.global_snapshot_status)

    # ==========================================================
    # API Routes : Stok Data (reads from local snapshot)
    # ==========================================================
    Route.get('/stok/monitoring', StokController.fetch_monitoring_data)
    Route.get('/stok/barang-histori', StokController.fetch_barang_histori)
    Route.get('/stok/export/histori', StokController.export_histori_xlsx)
    Route.get('/stok/export/xlsx', StokController.export_xlsx)
    Route.get('/stok/low-stock-alert', StokController.fetch_low_stock_alert)

    # ==========================================================
    # API Routes : Server Management CRUD
    # ==========================================================
    Route.get('/stok/api/servers', AuthController.super_admin_required(ServerController.get_all_servers))
    Route.post('/stok/api/servers', AuthController.super_admin_required(ServerController.create_server))
    Route.put('/stok/api/servers/<server_key>', AuthController.super_admin_required(ServerController.update_server))
    Route.delete('/stok/api/servers/<server_key>', AuthController.super_admin_required(ServerController.delete_server))
