import os
import sqlite3
from flask import Flask, render_template, g, jsonify, request

app = Flask(__name__)
DATABASE = os.path.join(os.path.dirname(__file__), 'data', 'music.db')

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

@app.route('/')
def index():
    return render_template('index.HTML')

@app.route('/api/lounges', methods=['GET'])
def get_lounges():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM lounges")
    rows = [dict(row) for row in cur.fetchall()]
    return jsonify(rows)

@app.route('/api/playlists', methods=['GET'])
def get_playlists():
    db = get_db()
    cur = db.cursor()
    cur.execute("SELECT * FROM playlists")
    rows = [dict(row) for row in cur.fetchall()]
    return jsonify(rows)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
