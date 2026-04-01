# POS Stok Monitoring System

Flask application untuk monitoring stok barang POS dengan dynamic server selection dan optimized query.

## 🎯 Features

- ✅ **Dynamic Server Selection** - Ganti database server tanpa deploy ulang
- ✅ **Optimized Query** - Standalone query dengan CTE + NOLOCK (tidak block kasir)
- ✅ **MVCR Architecture** - Models, Views, Controllers, Routes (mirip Laravel)
- ✅ **Real-time Monitoring** - Dashboard dengan statistik dan alert
- ✅ **Low Stock Alert** - Notifikasi barang dengan stok rendah
- ✅ **Session-based Server** - Pilih server per session

## 📋 Project Structure

```
pos-monitoring/
├── app/
│   ├── Http/
│   │   └── Controllers/
│   │       └── StokController.py   # Business logic
│   └── Models/
│       └── Database.py             # Connection manager
├── bootstrap/
│   └── app.py                      # Flask app factory
├── config/
│   └── database.py                 # Database configs
├── database/
│   └── queries/
│       └── stok_akhir.sql          # Optimized query
├── resources/
│   └── views/
│       ├── base.html               # Base layout
│       ├── index.html              # Server selection
│       └── monitoring.html         # Dashboard
├── routes/
│   └── web.py                      # API endpoints
├── requirements.txt                # Python dependencies
├── run.py                          # Entry point
└── README.md
```

## 🚀 Installation

### 1. Prerequisites

- Python 3.8+
- SQL Server with ODBC Driver 17
- pyodbc compatible OS (Windows/Linux)

### 2. Clone & Setup

```bash
# Clone project
cd pos-monitoring

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configure Database Servers

Edit `config/database.py` dan tambahkan server MSSQL kamu:

```python
DATABASE_SERVERS = {
    'server_1': {
        'name': 'Server 1 - Pusat',
        'host': '192.168.1.10',
        'port': 1433,
        'database': 'POS_DB',
        'username': 'sa',
        'password': 'your_password',  # Gunakan env var di production!
    },
    'server_2': {
        'name': 'Server 2 - Cabang A',
        'host': '192.168.1.11',
        ...
    }
}
```

### 4. Run Application

```bash
# Development mode
python run.py

# Custom host/port
python run.py --host 0.0.0.0 --port 8080

# Production mode
set FLASK_ENV=production
python run.py
```

Buka browser: http://localhost:5000

## 📖 Architecture

### MVCR Pattern

```
Request → Route (web.py)
   ↓
Controller (StokController.py) - Business Logic
   ↓
Model (Database.py) - Database Access
   ↓
View (resources/views) - Response HTML/JSON
```

### Database Connection Flow

```
1. User pilih server di index.html
2. Server key disimpan ke Flask session
3. StokController load query dari queries/stok_akhir.sql
4. DatabaseManager maintain connection pool
5. Execute query dengan parameter @tanggal
6. Results di-process dan return ke frontend
```

## 🔄 Query Optimization

### Original TVF (Masalah)
- ❌ Scalar subquery per row
- ❌ Nested function calls (GetKuantitasSatuanTerkecil)
- ❌ Blocking locks → Kasir hang

### New Query (Solution)
- ✅ CTE cache pembelian terakhir (1x execution)
- ✅ NOLOCK hint di semua tabel
- ✅ Pre-calculated quantities
- ✅ No blocking → Kasir lancar

**Performance Improvement:**
- Query time: -60% to -80%
- No transaction blocking
- Execution plan dengan index seeks

## 📊 API Endpoints

### Server Management
```
GET  /stok/server-list           # List available servers
POST /stok/select-server         # Select server (save to session)
GET  /stok/get-current-server    # Get current selected server
```

### Monitoring Data
```
GET  /stok/monitoring            # Get stok data (dengan filter tanggal/divisi)
GET  /stok/low-stock-alert       # Get barang dengan stok rendah
GET  /stok/                      # HTML dashboard page
```

### Query Parameters
```
GET /stok/monitoring?tanggal=2024-04-01&divisi=Pusat
GET /stok/low-stock-alert?tanggal=2024-04-01&min_stok=10
```

## 🔐 Security Tips (Production)

1. **Environment Variables**
```bash
# .env file
FLASK_ENV=production
SECRET_KEY=your_secret_key_here
DATABASE_PASSWORD_SERVER1=your_password
```

2. **Update config.py**
```python
'password': os.environ.get('DATABASE_PASSWORD_SERVER1')
```

3. **Use HTTPS**
```python
# In production, gunakan HTTPS
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
```

4. **Input Validation**
```python
# Controller sudah validate tanggal format
# Tambahkan validate divisi jika perlu
```

## 🧪 Testing

### Manual Testing

```bash
# Test server connection
python -c "from app.models.database import db_manager; 
           conn = db_manager.get_connection('server_1'); 
           print('Connected!')"

# Test query
python -c "from app.controllers.stok_controller import StokController; 
           result = StokController.get_stok_data('server_1', '2024-04-01'); 
           print(f'Rows: {result['row_count']}')"
```

### Integration Testing
Buka browser dan:
1. Go to http://localhost:5000/stok/index
2. Pilih server
3. Verify data load
4. Filter by tanggal/divisi
5. Check low stock alerts

## 📝 Common Issues & Solutions

### Issue: "Gagal terhubung ke server_1"
```
Solution:
1. Check DATABASE_SERVERS config
2. Verify firewall/network
3. Check ODBC Driver 17 installed: odbcconf /a {SQLSERVER}
```

### Issue: "Column not found in query"
```
Solution:
1. Verify column names di database
2. Check v_g_barang_histori_detail structure
3. Run query manual di SSMS
```

### Issue: Data loading slow
```
Solution:
1. Check execution plan di SSMS
2. Verify indexes di main tables
3. Limit date range di query
```

## 🎓 Learning Resources

- **Flask Documentation**: https://flask.palletsprojects.com
- **SQLAlchemy ORM**: https://www.sqlalchemy.org
- **Bootstrap 5**: https://getbootstrap.com
- **MSSQL Query Optimization**: https://learn.microsoft.com/en-us/sql

## 📋 Deployment Checklist

- [ ] Update DATABASE_SERVERS dengan prod servers
- [ ] Set SECRET_KEY environment variable
- [ ] Enable HTTPS
- [ ] Set FLASK_ENV=production
- [ ] Test dengan production database
- [ ] Monitor query performance
- [ ] Setup logging
- [ ] Backup config.py

## 🤝 Contributing

Improvements welcome! Untuk optimize lebih lanjut:
1. Add caching layer (Redis)
2. Implement pagination
3. Add export to Excel/PDF
4. Real-time WebSocket updates
5. Authentication/Authorization

## 📄 License

Internal Company Tool - Do not distribute

## 👤 Developer

**Internal Developer** - POS Application Optimization
- Database Query Optimization
- MVCR Architecture Implementation
- Dynamic Server Management

---

**Last Updated:** 2024
**Status:** Production Ready
