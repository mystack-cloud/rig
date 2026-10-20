"""
Control Plane API endpoints
"""

from flask import Flask, jsonify, request
import os

app = Flask(__name__)

# In-memory storage for demo purposes
environments = {}
sessions = {}

@app.route('/environments', methods=['POST'])
def create_environment():
    """Create a new remote environment"""
    data = request.get_json()
    env_id = str(len(environments) + 1)
    
    # Mock environment creation
    environments[env_id] = {
        'id': env_id,
        'name': data.get('name', f'env-{env_id}'),
        'status': 'created',
        'created_at': '2026-10-20T00:00:00Z'
    }
    
    return jsonify(environments[env_id]), 201

@app.route('/environments/<env_id>', methods=['GET'])
def get_environment(env_id):
    """Get environment details"""
    if env_id in environments:
        return jsonify(environments[env_id])
    else:
        return jsonify({'error': 'Environment not found'}), 404

@app.route('/environments/<env_id>/health', methods=['GET'])
def get_health(env_id):
    """Get environment health status"""
    if env_id in environments:
        return jsonify({
            'id': env_id,
            'status': 'healthy',
            'cpu_usage': '15%',
            'memory_usage': '30%',
            'disk_space': '2GB/10GB'
        })
    else:
        return jsonify({'error': 'Environment not found'}), 404

@app.route('/environments/<env_id>/sessions', methods=['POST'])
def create_session(env_id):
    """Create a collaboration session"""
    if env_id in environments:
        session_id = str(len(sessions) + 1)
        sessions[session_id] = {
            'id': session_id,
            'environment_id': env_id,
            'created_at': '2026-10-20T00:00:00Z'
        }
        return jsonify(sessions[session_id]), 201
    else:
        return jsonify({'error': 'Environment not found'}), 404

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8080)