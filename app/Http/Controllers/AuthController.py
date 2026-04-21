import os
import bcrypt
from flask import request, jsonify, session, render_template, redirect, url_for
from functools import wraps
from app.Models.UserModel import UserModel

class AuthController:
    """
    Controller untuk Authentication dengan RBAC.
    """

    @staticmethod
    def _verify_password(password, hashed):
        """Verifikasi password plain text dengan bcrypt hash"""
        try:
            return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))
        except Exception:
            return False

    @staticmethod
    def login_page():
        """HTML Page: Login"""
        if session.get('username'):
            return redirect('/stok/')
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

        user = UserModel.get_by_username(username)

        if user and AuthController._verify_password(password, user['password_hash']):
            session['username'] = user['username']
            session['role'] = user['role']
            session['is_admin'] = True # backward comp
            session.permanent = True  # Mengikuti PERMANENT_SESSION_LIFETIME di config
            if request.is_json:
                return jsonify({'status': 'success', 'message': 'Login berhasil', 'role': user['role']})
            
            # Jika admin, mungkin redirect_to dashboard, jika super_admin ke servers
            if user['role'] == 'super_admin':
                return redirect('/stok/servers')
            return redirect('/stok/')
        
        if request.is_json:
            return jsonify({'status': 'error', 'message': 'Username atau password salah'}), 401
        return render_template('login.html', error='Username atau password salah')

    @staticmethod
    def change_password_page():
        return render_template('change_password.html')

    @staticmethod
    def change_password():
        old_password = request.form.get('old_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        username = session.get('username')
        if not username:
            return redirect('/auth/login')

        user = UserModel.get_by_username(username)

        if not user or not AuthController._verify_password(old_password, user['password_hash']):
            return render_template('change_password.html', error='Password lama salah.')
        
        if new_password != confirm_password:
            return render_template('change_password.html', error='Password baru dan konfirmasi tidak cocok.')
            
        if len(new_password) < 6:
            return render_template('change_password.html', error='Password minimal 6 karakter.')

        success, msg = UserModel.update_password(username, new_password)
        
        if success:
            return render_template('change_password.html', success='Password berhasil diubah.')
        else:
            return render_template('change_password.html', error=msg)

    @staticmethod
    def logout():
        """API: Logout dan hapus session"""
        session.clear()
        return redirect('/auth/login')

    @staticmethod
    def admin_required(f):
        """Decorator untuk proteksi rute dasar (Bisa diakses super_admin dan admin)"""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('username'):
                if request.is_json or request.path.startswith('/stok/api/') or request.path.startswith('/stok/snapshot/'):
                    return jsonify({'status': 'error', 'message': 'Authentication required. Mohon login.'}), 401
                return redirect('/auth/login')
            return f(*args, **kwargs)
        return decorated_function

    @staticmethod
    def super_admin_required(f):
        """Decorator untuk proteksi rute khusus super admin"""
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('username'):
                if request.is_json or request.path.startswith('/stok/api/') or request.path.startswith('/stok/snapshot/'):
                    return jsonify({'status': 'error', 'message': 'Authentication required. Mohon login.'}), 401
                return redirect('/auth/login')
            
            if session.get('role') != 'super_admin':
                if request.is_json or request.path.startswith('/stok/api/') or request.path.startswith('/stok/snapshot/'):
                    return jsonify({'status': 'error', 'message': 'Akses Ditolak. Membutuhkan izin Super Admin.'}), 403
                return redirect('/stok/')
                
            return f(*args, **kwargs)
        return decorated_function
        
    # --- CRUD ADMIN ---
    
    @staticmethod
    def users_page():
        return render_template('users.html')
        
    @staticmethod
    def api_get_users():
        return jsonify({'status': 'success', 'data': UserModel.get_all()})
        
    @staticmethod
    def api_create_user():
        data = request.get_json()
        if not data or not data.get('username') or not data.get('password') or not data.get('role'):
            return jsonify({'status': 'error', 'message': 'Data tidak lengkap'}), 400
            
        success, msg = UserModel.create(data['username'], data['password'], data['role'])
        if success:
            return jsonify({'status': 'success', 'message': msg})
        return jsonify({'status': 'error', 'message': msg}), 400
        
    @staticmethod
    def api_delete_user(username):
        success, msg = UserModel.delete(username)
        if success:
            return jsonify({'status': 'success', 'message': msg})
        return jsonify({'status': 'error', 'message': msg}), 400
