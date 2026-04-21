from flask import Flask, redirect
from config.database import get_config
from routes.web import register_routes

def create_app(config_name=None):
    """
    Flask app factory
    """
    app = Flask(__name__, template_folder='../resources/views')
    
    # Load config
    config = get_config()
    app.config.from_object(config)
    
    # Register Laravel-style routes
    register_routes(app)
    
    # Initialize Database
    from app.Models.UserModel import UserModel
    UserModel.initialize_db()
    
    return app
