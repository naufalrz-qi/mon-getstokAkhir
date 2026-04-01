import json
import os

class ServerModel:
    """
    Model untuk mengelola data server dari file JSON
    """
    FILE_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'database', 'servers.json')
    
    @staticmethod
    def get_all():
        """Dapatkan semua server"""
        if not os.path.exists(ServerModel.FILE_PATH):
            return {}
        try:
            with open(ServerModel.FILE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
            
    @staticmethod
    def get_by_key(server_key):
        """Dapatkan server spesifik by key"""
        servers = ServerModel.get_all()
        return servers.get(server_key)
        
    @staticmethod
    def save_all(servers_data):
        """Simpan semua server ke file"""
        try:
            # Pastikan directory exists
            os.makedirs(os.path.dirname(ServerModel.FILE_PATH), exist_ok=True)
            with open(ServerModel.FILE_PATH, 'w', encoding='utf-8') as f:
                json.dump(servers_data, f, indent=4)
            return True
        except Exception as e:
            raise Exception(f"Gagal menyimpan data: {str(e)}")
            
    @staticmethod
    def create(server_key, data):
        """Buat server baru"""
        servers = ServerModel.get_all()
        if server_key in servers:
            raise ValueError(f"Server ID '{server_key}' sudah ada")
            
        servers[server_key] = data
        return ServerModel.save_all(servers)
        
    @staticmethod
    def update(server_key, data):
        """Update server"""
        servers = ServerModel.get_all()
        if server_key not in servers:
            raise ValueError(f"Server '{server_key}' tidak ditemukan")
            
        # Update fields
        servers[server_key].update(data)
        return ServerModel.save_all(servers)
        
    @staticmethod
    def delete(server_key):
        """Hapus server"""
        servers = ServerModel.get_all()
        if server_key not in servers:
            raise ValueError(f"Server '{server_key}' tidak ditemukan")
            
        del servers[server_key]
        return ServerModel.save_all(servers)
