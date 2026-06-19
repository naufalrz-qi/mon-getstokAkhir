import threading

class SnapshotState:
    _perhitungan_threads = {}
    _perhitungan_status = {}
    _perhitungan_cache = {}
    _cache_lock = threading.RLock()
