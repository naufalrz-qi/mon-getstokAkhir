from flask import request, jsonify, render_template
from app.Models.ServerModel import ServerModel

class ServerController:
    """
    Controller untuk manajemen Server CRUD
    """
    
    @staticmethod
    def servers_page():
        """HTML Page: Manage Servers CRUD"""
        return render_template('servers.html')
        
    @staticmethod
    def get_all_servers():
        return jsonify({
            'status': 'success',
            'data': ServerModel.get_all()
        })
    
    @staticmethod
    def create_server():
        data = request.get_json()
        try:
            # Validasi input dasar
            required_fields = ['key', 'name', 'host', 'port', 'database', 'username', 'password']
            for field in required_fields:
                if field not in data or not str(data[field]).strip():
                    return {'status': 'error', 'message': f'Field {field} diperlukan'}
            
            server_key = data.pop('key')
            # Handle port to int
            data['port'] = int(data['port'])
            
            ServerModel.create(server_key, data)
            return jsonify({'status': 'success', 'message': 'Server berhasil ditambahkan'})
            
        except ValueError as e:
            return jsonify({'status': 'error', 'message': str(e)})
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'System error: {str(e)}'})

    @staticmethod
    def update_server(server_key):
        data = request.get_json()
        try:
            if 'port' in data:
                data['port'] = int(data['port'])
            
            # Jangan biarkan update merubah field key, filter
            if 'key' in data:
                del data['key']
                
            ServerModel.update(server_key, data)
            return jsonify({'status': 'success', 'message': 'Server berhasil diupdate'})
            
        except ValueError as e:
            return jsonify({'status': 'error', 'message': str(e)})
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'System error: {str(e)}'})

    @staticmethod
    def delete_server(server_key):
        try:
            ServerModel.delete(server_key)
            return jsonify({'status': 'success', 'message': 'Server berhasil dihapus'})
            
        except ValueError as e:
            return jsonify({'status': 'error', 'message': str(e)})
        except Exception as e:
            return jsonify({'status': 'error', 'message': f'System error: {str(e)}'})
