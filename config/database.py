import os
from datetime import timedelta

class Config:
    """Base configuration"""
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-key-change-in-production'
    SESSION_COOKIE_HTTPONLY = True
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    
    # Daftar server MSSQL yang bisa dipilih
    DATABASE_SERVERS = {
        'server_1': {
            'name': 'Server 1 - Pusat',
            'host': '192.168.1.10',
            'port': 1433,
            'database': 'POS_DB',
            'username': 'sa',
            'password': 'your_password_here',  # Jangan hardcode! Pakai env var
        },
        'server_2': {
            'name': 'Server 2 - Cabang A',
            'host': '192.168.1.11',
            'port': 1433,
            'database': 'POS_DB',
            'username': 'sa',
            'password': 'your_password_here',
        },
        'server_3': {
            'name': 'Server 3 - Cabang B',
            'host': '192.168.1.12',
            'port': 1433,
            'database': 'POS_DB',
            'username': 'sa',
            'password': 'your_password_here',
        },
    }

class DevelopmentConfig(Config):
    """Development config"""
    DEBUG = True
    TESTING = False

class ProductionConfig(Config):
    """Production config"""
    DEBUG = False
    TESTING = False

class TestingConfig(Config):
    """Testing config"""
    DEBUG = True
    TESTING = True

# Select config based on env
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}

def get_config():
    """Get config berdasarkan FLASK_ENV"""
    env = os.environ.get('FLASK_ENV', 'development')
    return config.get(env, config['default'])
