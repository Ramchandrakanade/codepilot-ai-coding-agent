from __future__ import annotations

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

from .agent.orchestrator import run_agent

app = Flask(__name__)

@app.get('/')
def index():
    return render_template('index.html')


@app.get('/api/health')
def health():
    return jsonify({'status': 'ok', 'service': 'CodePilot AI'})


@app.post('/api/agent/run')
def agent_run():
    payload = request.get_json(silent=True) or {}
    try:
        result = run_agent(payload.get('task', ''))
        return jsonify(result)
    except Exception as exc:
        return jsonify({'error': str(exc)}), 400


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
