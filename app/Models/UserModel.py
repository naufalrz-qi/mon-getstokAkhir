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
        
        # Check if any user exists
        cursor.execute("SELECT COUNT(*) as count FROM users")
        if cursor.fetchone()['count'] == 0:
            # Create default super admin
            default_password = b'admin'
            hashed = bcrypt.hashpw(default_password, bcrypt.gensalt()).decode('utf-8')
            cursor.execute('''
                INSERT INTO users (username, password_hash, role)
                VALUES (?, ?, ?)
            ''', ('admin', hashed, 'super_admin'))
            
        conn.commit()
        conn.close()

    @classmethod
    def get_all(cls):
        """Dapatkan semua user"""
        conn = cls._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, role FROM users")
        users = [dict(row) for row in cursor.fetchall()]
        conn.close()
        return users

    @classmethod
    def get_by_username(cls, username):
        """Dapatkan user berdasarkan username (termasuk password_hash)"""
        conn = cls._get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    @classmethod
    def create(cls, username, password, role='admin'):
        """Buat user baru"""
        conn = cls._get_connection()
        cursor = conn.cursor()
        
        try:
            hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            cursor.execute('''
                INSERT INTO users (username, password_hash, role)
                VALUES (?, ?, ?)
            ''', (username, hashed, role))
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
