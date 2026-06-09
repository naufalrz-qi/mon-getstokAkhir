import sqlite3
import os
import bcrypt

class UserModel:
    """
    Model untuk mengelola data user dari SQLite (database/app.db)
    """
    DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'database', 'app.db')

    @classmethod
    def _get_connection(cls):
        os.makedirs(os.path.dirname(cls.DB_PATH), exist_ok=True)
        conn = sqlite3.connect(cls.DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    @classmethod
    def initialize_db(cls):
        """Inisialisasi tabel users dan admin default jika belum ada"""
        conn = cls._get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL
            )
        ''')
        
        # Add menus column if not exists
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN menus TEXT DEFAULT '[\"stok_index\", \"stok_histori\", \"stok_opname\", \"master_update\"]'")
        except sqlite3.OperationalError:
            pass # Column already exists
            
        # Add servers column if not exists
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN servers TEXT DEFAULT '[]'")
        except sqlite3.OperationalError:
            pass # Column already exists
        
        # Check if any user exists
        cursor.execute("SELECT COUNT(*) as count FROM users")
        if cursor.fetchone()['count'] == 0:
            # Create default super admin
            default_password = b'admin'
            hashed = bcrypt.hashpw(default_password, bcrypt.gensalt()).decode('utf-8')
            cursor.execute('''
                INSERT INTO users (username, password_hash, role, menus, servers)
                VALUES (?, ?, ?, ?, ?)
            ''', ('admin', hashed, 'super_admin', '[]', '[]'))
            
        conn.commit()
        conn.close()

    @classmethod
    def get_all(cls):
        """Dapatkan semua user"""
        import json
        conn = cls._get_connection()
        cursor = conn.cursor()
        # Fallback to empty if menus column missing/null but we handle it
        cursor.execute("SELECT id, username, role, menus, servers FROM users")
        users = []
        for row in cursor.fetchall():
            d = dict(row)
            try:
                d['menus'] = json.loads(d.get('menus') or '[]')
            except Exception:
                d['menus'] = []
            try:
                d['servers'] = json.loads(d.get('servers') or '[]')
            except Exception:
                d['servers'] = []
            users.append(d)
        conn.close()
        return users

    @classmethod
    def get_by_username(cls, username):
        """Dapatkan user berdasarkan username (termasuk password_hash)"""
        import json
        conn = cls._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        conn.close()
        if row:
            d = dict(row)
            try:
                d['menus'] = json.loads(d.get('menus') or '[]')
            except Exception:
                d['menus'] = []
            try:
                d['servers'] = json.loads(d.get('servers') or '[]')
            except Exception:
                d['servers'] = []
            return d
        return None

    @classmethod
    def create(cls, username, password, role='admin'):
        """Buat user baru"""
        import json
        conn = cls._get_connection()
        cursor = conn.cursor()
        
        default_menus = '[]'
        default_servers = '[]'
        if role == 'admin':
            default_menus = json.dumps(["stok_index", "stok_histori", "stok_opname", "master_update"])
            
        try:
            hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            cursor.execute('''
                INSERT INTO users (username, password_hash, role, menus, servers)
                VALUES (?, ?, ?, ?, ?)
            ''', (username, hashed, role, default_menus, default_servers))
            conn.commit()
            return True, "User berhasil dibuat"
        except sqlite3.IntegrityError:
            return False, "Username sudah terdaftar"
        except Exception as e:
            return False, str(e)
        finally:
            conn.close()

    @classmethod
    def update_password(cls, username, new_password):
        """Update password user"""
        conn = cls._get_connection()
        cursor = conn.cursor()
        
        try:
            hashed = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            cursor.execute('''
                UPDATE users SET password_hash = ? WHERE username = ?
            ''', (hashed, username))
            conn.commit()
            return True, "Password berhasil diubah"
        except Exception as e:
            return False, str(e)
        finally:
            conn.close()

    @classmethod
    def update_access(cls, username, menus_list, servers_list):
        """Update menus and servers user"""
        import json
        conn = cls._get_connection()
        cursor = conn.cursor()
        
        try:
            menus_json = json.dumps(menus_list)
            servers_json = json.dumps(servers_list)
            cursor.execute('''
                UPDATE users SET menus = ?, servers = ? WHERE username = ?
            ''', (menus_json, servers_json, username))
            conn.commit()
            return True, "Hak akses berhasil diubah"
        except Exception as e:
            return False, str(e)
        finally:
            conn.close()

    @classmethod
    def delete(cls, username):
        """Hapus user"""
        if username == 'admin':
            return False, "Tidak dapat menghapus admin default"
            
        conn = cls._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("DELETE FROM users WHERE username = ?", (username,))
            conn.commit()
            return True, "User berhasil dihapus"
        except Exception as e:
            return False, str(e)
        finally:
            conn.close()
