#!/usr/bin/env python
"""
POS Stok Monitoring - Flask Application Entry Point

Usage:
    python run.py                    # Development mode
    python run.py --host 0.0.0.0    # Listen on all interfaces
    python run.py --port 8080       # Custom port
"""

import os
import sys
from dotenv import load_dotenv

# Load .env BEFORE anything reads os.environ
load_dotenv()

from bootstrap.app import create_app
from app.Models.Database import db_manager

def main():
    """Main entry point"""
    
    # Create Flask app
    app = create_app()
    
    # Get host and port from args or env
    host = os.environ.get('FLASK_HOST', '127.0.0.1')
    port = int(os.environ.get('FLASK_PORT', 5000))
    debug = os.environ.get('FLASK_ENV', 'development') == 'development'
    
    # Parse command line args
    if '--host' in sys.argv:
        idx = sys.argv.index('--host')
        if idx + 1 < len(sys.argv):
            host = sys.argv[idx + 1]
    
    if '--port' in sys.argv:
        idx = sys.argv.index('--port')
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])
    
    print(f"""
    ========================================
    |   POS Stok Monitoring System         |
    |   Flask Application                  |
    ========================================
    
    > Starting server...
    Host:  {host}
    Port:  {port}
    Debug:  {debug}
    
    Open browser: http://{host}:{port}
    """)
    
    try:
        app.run(
            host=host,
            port=port,
            debug=debug,
            use_reloader=debug
        )
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        db_manager.close_all()
        sys.exit(0)

if __name__ == '__main__':
    main()
