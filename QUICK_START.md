# 🚀 Quick Start Guide

Setup POS Stok Monitoring dalam 5 menit!

## 1️⃣ Prerequisites

Pastikan sudah install:
- Python 3.8+ (https://www.python.org/downloads/)
- ODBC Driver 17 for SQL Server
  - Windows: Download dari https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server
  - Linux: `sudo apt-get install odbc-postgresql` (atau yang sesuai distro)

Verifikasi:
```bash
python --version          # Python 3.8+
odbcconf /q              # Check ODBC drivers (Windows)
```

## 2️⃣ Clone & Install Dependencies

```bash
# Clone project (atau extract zip)
cd pos-monitoring

# Create virtual environment
python -m venv venv

# Activate (Windows)
venv\Scripts\activate

# Activate (Linux/Mac)
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

Output yang diharapkan:
```
Successfully installed Flask-2.3.3 pyodbc-5.0.1 ...
```

## 3️⃣ Configure Database Servers

Edit `app/config.py` line 11-30:

```python
DATABASE_SERVERS = {
    'server_1': {
        'name': 'Server 1 - Pusat',
        'host': '192.168.1.10',          # ← Update IP server kamu
        'port': 1433,
        'database': 'POS_DB',             # ← Nama database
        'username': 'sa',                 # ← Username MSSQL
        'password': 'your_password_here', # ← Password MSSQL
    },
}
```

**Find your server IP:**
```bash
# Di server MSSQL
ipconfig /all          # Windows - cari "IPv4 Address"
ifconfig              # Linux/Mac - cari "inet addr"
```

**Test connection dari command line:**
```bash
python -c "
from app.models.database import db_manager
try:
    conn = db_manager.get_connection('server_1')
    print('✅ Connection successful!')
except Exception as e:
    print(f'❌ Connection failed: {e}')
"
```

## 4️⃣ Run Application

```bash
python run.py
```

Output yang diharapkan:
```
╔════════════════════════════════════════╗
║   POS Stok Monitoring System           ║
║   Flask Application                    ║
╚════════════════════════════════════════╝

🚀 Starting server...
📍 Host: 127.0.0.1
🔌 Port: 5000
🔧 Debug: True

Open browser: http://127.0.0.1:5000
```

## 5️⃣ Open in Browser

Buka: **http://localhost:5000**

### First Time User:
1. Halaman akan menampilkan list server
2. Click "Pilih Server Ini" pada server yang diinginkan
3. Tunggu redirect ke dashboard
4. Select tanggal, filter, tekan "Load Data"
5. Data stok akan tampil dalam table

## 📊 Expected Output

### Dashboard Page
```
Monitor Stok Barang

[Statistics Cards]
- Total Item: 1,250
- Divisi: 3
- Total Nominal: Rp 500,000,000
- Avg Stok: 45

[Filter Section]
- Tanggal: 2024-04-01
- Divisi: [dropdown]
- Stok Minimal: 10

[Data Table]
- Divisi | Kode Barang | Barang | ... | Stok | Nominal
- Pusat | BR001 | Barang A | ... | 50 | Rp 5,000,000
- ...

[Low Stock Alert]
- Barang X: Stok 5 (Rendah)
- Barang Y: Stok 0 (Habis)
```

## 🔧 Troubleshooting

### ❌ Error: "No module named 'pyodbc'"
```bash
# Solution:
pip install -r requirements.txt
```

### ❌ Error: "Failed to connect to server"
```
Cek:
1. IP server benar?           → ping 192.168.1.10
2. SQL Server running?        → Check Services
3. Firewall allow port 1433?  → Check Windows Firewall
4. Username/password benar?   → Test di SSMS
```

### ❌ Error: "Column 'xyz' not found"
```
Cek:
1. Database tables exist?     → Open SSMS
2. View v_g_barang_histori_detail exist?
3. Custom functions exist?    → Check sp_helptext '[dbo].[GetKuantitasSatuanTerkecil]'
```

### ❌ Page loading slow
```
Cek:
1. Execute query manual di SSMS, berapa lama?
2. Lihat execution plan, ada index scan?
3. Table besar berapa rows?
4. Network latency?
```

## 📝 Next Steps

### Development
1. Explore endpoints: http://localhost:5000/stok/server-list
2. Check browser DevTools (F12) untuk melihat API calls
3. Modify templates untuk customize tampilan
4. Add filter lebih kompleks

### Production
1. Change `FLASK_ENV=production` di env
2. Use production database credentials
3. Setup HTTPS (SSL certificate)
4. Deploy ke server (IIS, Apache, Nginx)

## 🎓 Learning

Struktur kode:
- `run.py` - Entry point
- `app/__init__.py` - Flask factory
- `app/config.py` - Konfigurasi server
- `app/models/database.py` - Database connection
- `app/controllers/stok_controller.py` - Business logic
- `app/routes/stok_routes.py` - API endpoints
- `app/templates/` - Frontend UI

## ✨ Tips

1. **Change port**: `python run.py --port 8080`
2. **Listen all interfaces**: `python run.py --host 0.0.0.0`
3. **Check database**: `python -c "from app.models.database import db_manager; print(db_manager.get_available_servers())"`
4. **Test query**: Buka queries/stok_akhir.sql di SSMS, run manual

## 💡 Advanced

Untuk setup production:
- Gunakan Gunicorn/uWSGI sebagai server
- Setup Nginx sebagai reverse proxy
- Use environment variables untuk credentials
- Setup logging dan monitoring
- Enable CORS jika needed

```bash
# Contoh production run:
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 "app:create_app()"
```

---

**Stuck?** Cek README.md untuk dokumentasi lengkap!

**Ready to monitor?** Go to http://localhost:5000 🚀
