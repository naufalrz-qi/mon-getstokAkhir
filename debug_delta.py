"""Debug delta directly using Flask test client (bypasses auth)"""
import os, sys, time, json
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

from bootstrap.app import create_app

app = create_app()
client = app.test_client()

with app.test_request_context():
    # Set session
    with client.session_transaction() as sess:
        sess['username'] = 'admin'
        sess['role'] = 'super_admin'
        sess['is_admin'] = True
        sess['selected_server'] = 'server-lotim'

    # Check status
    r = client.get('/stok/snapshot/status')
    st = json.loads(r.data)
    print(f"Status: {st['state']}, items: {st.get('mem_count',0)}")
    print(f"Last refresh: {st.get('snapshot_info',{}).get('last_refresh','?')}")
    
    if not st['has_snapshot']:
        print("No snapshot! Run full refresh first.")
        sys.exit(1)

    # Trigger delta
    print("\n=== Triggering Quick Update ===")
    t0 = time.time()
    r = client.post('/stok/snapshot/delta?tanggal=2026-04-30')
    trigger = json.loads(r.data)
    print(f"Trigger: {trigger}")

    # Poll
    for _ in range(180):
        time.sleep(0.5)
        r = client.get('/stok/snapshot/status')
        st = json.loads(r.data)
        state = st['state']
        msg = st.get('message', '')
        elapsed = time.time() - t0
        print(f"  [{elapsed:6.1f}s] {st.get('progress',0):3d}% [{state}] {msg}")
        if state in ('ready', 'error', 'cancelled'):
            break

    print(f"\n=== Total: {time.time()-t0:.1f}s ===")
