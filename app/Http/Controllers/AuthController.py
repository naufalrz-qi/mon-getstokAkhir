import bcrypt
from flask import request, jsonify, session, render_template, redirect, url_for
from functools import wraps

class AuthController:
    """
    Controller untuk Admin Authentication.
    Sistem login sederhana menggunakan Flask session.
    """
    
    # Default admin credentials (admin / admin)
    ADMIN_USERNAME = 'admin'
    ADMIN_PASSWORD_HASH = '$2b$12$RUi8Z0isbRZavQ03boJOQ.//XMvVj1YPFdsNkbCCk6/BpUX8njnTS'

    @staticmethod
    def _verify_password(password, hashed):
        """Verifikasi password plain text dengan bcrypt hash"""
        try:
            return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
        except Exception:
            return False

    @staticmethod
    def login_page():
        """HTML Page: Admin Login"""
        if session.get('is_admin'):
            return redirect('/stok/servers')
        return render_template('login.html')

    @staticmethod
    def login():
        """API/POST Handler: Handle login form submission"""
        if request.is_json:
            data = request.get_json()
            username = data.get('username')
            password = data.get('password')
        else:
            username = request.form.get('username')
            password = request.form.get('password')

        if username == AuthController.ADMIN_USERNAME and AuthController._verify_password(password, AuthController.ADMIN_PASSWORD_HASH):
            session['is_admin'] = True
            session.permanent = True  # Mengikuti PERMANENT_SESSION_LIFETIME di config
            if request.is_json:
                return jsonify({'status': 'success', 'message': 'Login berhasil'})
            return redirect('/stok/servers')
        
        if request.is_json:
            return jsonify({'status': 'error', 'message': 'Username atau password salah'}), 401
        return render_template('login.html', error='Username atau password salah')

    @staticmethod
    def logout():
        """API: Logout dan hapus session"""
        session.pop('is_admin', None)
        return redirect('/auth/login')

    @staticmethod
    def admin_required(f):
        """Decorator untuk memproteksi routes khusus admin"""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('is_admin'):
                # Jika request expects JSON (API)
                if request.is_json or request.path.startswith('/stok/api/') or request.path.startswith('/stok/snapshot/'):
                    return jsonify({'status': 'error', 'message': 'Authentication required. Mohon login sebagai admin.'}), 401
                # Jika request standard browser
                return redirect('/auth/login')
            return f(*args, **kwargs)
        return decorated_function
