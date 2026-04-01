from app.Http.Controllers.StokController import StokController
from app.Http.Controllers.ServerController import ServerController

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
    Route.get('/stok/servers', ServerController.servers_page)

    # ==========================================================
    # API Routes : Server Session
    # ==========================================================
    Route.get('/stok/server-list', StokController.get_server_list)
    Route.post('/stok/select-server', StokController.select_server)
    Route.get('/stok/get-current-server', StokController.get_current_server)

    # ==========================================================
    # API Routes : Snapshot Management
    # ==========================================================
    Route.post('/stok/snapshot/refresh', StokController.trigger_refresh)
    Route.post('/stok/snapshot/delta', StokController.trigger_delta_refresh)
    Route.get('/stok/snapshot/status', StokController.snapshot_status)
    Route.post('/stok/snapshot/cancel', StokController.cancel_refresh)

    # ==========================================================
    # API Routes : Stok Data (reads from local snapshot)
    # ==========================================================
    Route.get('/stok/monitoring', StokController.fetch_monitoring_data)
    Route.get('/stok/low-stock-alert', StokController.fetch_low_stock_alert)

    # ==========================================================
    # API Routes : Server Management CRUD
    # ==========================================================
    Route.get('/stok/api/servers', ServerController.get_all_servers)
    Route.post('/stok/api/servers', ServerController.create_server)
    Route.put('/stok/api/servers/<server_key>', ServerController.update_server)
    Route.delete('/stok/api/servers/<server_key>', ServerController.delete_server)
