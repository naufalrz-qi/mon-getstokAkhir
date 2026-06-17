import threading

class SnapshotState:
    _refresh_threads = {}
    _refresh_cancel = {}
    _refresh_status = {}
    _mem_cache = {}
    _mem_cache_ts = {}
    _cache_lock = threading.RLock()
    _auto_update_thread = None
    _auto_update_stop_event = None
    
    # Perhitungan Stok Temporary Cache
    _perhitungan_threads = {}
    _perhitungan_status = {}
    _perhitungan_cache = {}
