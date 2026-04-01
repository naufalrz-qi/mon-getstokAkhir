import pyodbc
from config.database import get_config
from app.Models.ServerModel import ServerModel

class DatabaseManager:
    """
    Manager untuk koneksi MSSQL ke berbagai server
    Mendukung pooling dan caching connection
    """
    
    def __init__(self):
        self.config = get_config()
        self._connections = {}  # Cache connections per server
    
    def get_connection_string(self, server_key):
        """
        Buat connection string MSSQL dari config
        
        Args:
            server_key: Key dari server list
            
        Returns:
            Connection string untuk pyodbc
        """
        servers = ServerModel.get_all()
        if server_key not in servers:
            raise ValueError(f"Server '{server_key}' tidak ditemukan")
        
        server_config = servers[server_key]
        
        # Connection string format: 
        # Driver={ODBC Driver 17 for SQL Server};Server=...;Database=...;UID=...;PWD=...
        connection_string = (
            f"Driver={{ODBC Driver 17 for SQL Server}};"
            f"Server={server_config['host']},{server_config['port']};"
            f"Database={server_config['database']};"
            f"UID={server_config['username']};"
            f"PWD={server_config['password']};"
            f"Encrypt=yes;TrustServerCertificate=yes;Connection Timeout=60"
        )
        
        return connection_string
    
    def get_connection(self, server_key):
        """
        Dapatkan koneksi ke server, dengan pooling/caching
        """
        try:
            if server_key in self._connections:
                conn = self._connections[server_key]
                try:
                    conn.cursor().execute("SELECT 1")
                    return conn
                except:
                    del self._connections[server_key]
            
            connection_string = self.get_connection_string(server_key)
            conn = pyodbc.connect(connection_string)
            conn.setdecoding(pyodbc.SQL_CHAR, encoding='utf-8')
            conn.setdecoding(pyodbc.SQL_WCHAR, encoding='utf-8')
            
            self._connections[server_key] = conn
            return conn
            
        except pyodbc.DatabaseError as e:
            raise ConnectionError(f"Gagal terhubung ke {server_key}: {str(e)}")
    
    def create_new_connection(self, server_key):
        """
        Buat koneksi BARU (non-cached) untuk thread-safe parallel queries.
        Caller harus close() sendiri setelah selesai.
        """
        try:
            connection_string = self.get_connection_string(server_key)
            conn = pyodbc.connect(connection_string)
            conn.setdecoding(pyodbc.SQL_CHAR, encoding='utf-8')
            conn.setdecoding(pyodbc.SQL_WCHAR, encoding='utf-8')
            return conn
        except pyodbc.DatabaseError as e:
            raise ConnectionError(f"Gagal terhubung ke {server_key}: {str(e)}")
    
    def execute_query(self, server_key, query, params=None):
        """
        Execute query ke server tertentu
        
        Args:
            server_key: Key dari server list
            query: SQL query string
            params: Parameter query (optional)
            
        Returns:
            List of dictionaries (row results)
        """
        conn = self.get_connection(server_key)
        cursor = conn.cursor()
        
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            
            # Get column names
            columns = [description[0] for description in cursor.description]
            
            # Convert rows ke list of dicts
            results = []
            for row in cursor.fetchall():
                results.append(dict(zip(columns, row)))
            
            return results
            
        except pyodbc.Error as e:
            raise Exception(f"Query error: {str(e)}")
        finally:
            cursor.close()
    
    def execute_multi_query(self, server_key, query, params=None):
        """
        Execute query yang return multiple result sets
        
        Returns:
            List of List of dicts (satu list per result set)
        """
        conn = self.get_connection(server_key)
        cursor = conn.cursor()
        
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            
            all_results = []
            
            while True:
                if cursor.description:
                    columns = [desc[0] for desc in cursor.description]
                    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                    all_results.append(rows)
                
                if not cursor.nextset():
                    break
            
            return all_results
            
        except pyodbc.Error as e:
            raise Exception(f"Query error: {str(e)}")
        finally:
            cursor.close()
    
    def close_all(self):
        """Close semua connections"""
        for conn in self._connections.values():
            try:
                conn.close()
            except:
                pass
        self._connections.clear()
    
    def get_available_servers(self):
        """Dapatkan list server yang available"""
        servers = []
        for key, config_s in ServerModel.get_all().items():
            servers.append({
                'key': key,
                'name': config_s['name'],
                'host': config_s['host']
            })
        return servers

# Global instance
db_manager = DatabaseManager()
