import os
import sqlite3
import threading
import time
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal

from app.Services.Snapshot.SnapshotState import SnapshotState

QUERIES_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'database', 'queries')
PARALLEL_DIR = os.path.join(QUERIES_DIR, 'parallel')
SNAPSHOTS_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'database', 'snapshots')

def _load_sql(filename):
    path = os.path.join(PARALLEL_DIR, filename)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

class SnapshotAutoUpdate:
    @classmethod
    def start_auto_update(cls, interval=60):
            if SnapshotState._auto_update_thread and SnapshotState._auto_update_thread.is_alive():
                return
            SnapshotState._auto_update_stop_event = threading.Event()
            SnapshotState._auto_update_thread = threading.Thread(
                target=cls._auto_update_loop,
                args=(interval,),
                daemon=True
            )
            SnapshotState._auto_update_thread.start()
            logging.info("[AUTO UPDATE] Background sequential auto-update started.")

    @classmethod
    def stop_auto_update(cls):
            if SnapshotState._auto_update_stop_event:
                SnapshotState._auto_update_stop_event.set()
            logging.info("[AUTO UPDATE] Background sequential auto-update stopped.")

    @classmethod
    def _auto_update_loop(cls, interval):
            """
            Auto-update loop: melakukan FULL recalculation stok untuk semua item
            menggunakan targeted_recalculate (1 round-trip SQL, bukan 10 parallel).
            Ini memastikan data selalu akurat seperti full refresh.
            """
            from app.Models.Database import db_manager
            while SnapshotState._auto_update_stop_event and not SnapshotState._auto_update_stop_event.is_set():
                try:
                    servers = db_manager.get_available_servers()
                    tanggal = datetime.now().strftime('%Y-%m-%d')
                    for server in servers:
                        if SnapshotState._auto_update_stop_event and SnapshotState._auto_update_stop_event.is_set():
                            break
    
                        server_key = server['key']
    
                        # Jangan ganggu jika sedang ada refresh manual / parallel
                        if server_key in SnapshotState._refresh_threads and SnapshotState._refresh_threads[server_key].is_alive():
                            continue
    
                        db_path = cls._db_path(server_key)
                        if not os.path.exists(db_path):
                            continue  # Perlu refresh awal secara manual terlebih dahulu
    
                        old_status = SnapshotState._refresh_status.get(server_key)
                        if old_status and old_status.get('state') in ('starting', 'fetching', 'writing'):
                            continue
    
                        SnapshotState._refresh_status[server_key] = {
                            'state': 'fetching',
                            'progress': 10,
                            'message': 'Auto recalc: mengambil daftar item...',
                            'started_at': time.time(),
                            'row_count': 0,
                            'is_delta': True,
                        }
                        status = SnapshotState._refresh_status[server_key]
    
                        try:
                            # Get last_refresh timestamp from metadata
                            conn_local = sqlite3.connect(db_path)
                            row = conn_local.execute("SELECT value FROM snapshot_meta WHERE key='last_refresh'").fetchone()
                            conn_local.close()
                            
                            if not row:
                                status['state'] = 'ready'
                                status['progress'] = 100
                                status['message'] = 'Belum ada last_refresh, perlu full refresh manual'
                                continue
                                
                            last_refresh = row[0]
                            
                            # Jalankan delta refresh secara sequential (agar tidak terlalu membebani network)
                            cls._do_delta_refresh(server_key, tanggal, last_refresh, is_sequential=True, checkpoint_type='weekly')
    
                        except Exception as e:
                            status['state'] = 'error'
                            status['progress'] = 0
                            status['message'] = f'Auto recalc error: {str(e)}'
                            logging.error(f"[AUTO UPDATE] Error for {server_key}: {e}")
    
                        # Beri jeda antar server
                        time.sleep(2)
                except Exception as e:
                    logging.error(f"[AUTO UPDATE] Loop error: {e}")
    
                if SnapshotState._auto_update_stop_event:
                    SnapshotState._auto_update_stop_event.wait(interval)

