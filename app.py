import json
from flask import Flask, request, jsonify, render_template, redirect, session, Response
from chatbot import get_response, stream_response
import sqlite3
import hashlib
import os

app = Flask(__name__)
app.secret_key = "thinkora-secret"

DB_PATH = os.path.join(os.path.dirname(__file__), "users.db")


def init_db():
    """Create the users table if it doesn't exist."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def add_user(username, password):
    """Add a new user. Returns True on success, False if username exists."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (username, password) VALUES (?, ?)",
                  (username, hash_password(password)))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def check_user(username, password):
    """Check if username/password combo is valid."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT password FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()
    if row and row[0] == hash_password(password):
        return True
    return False


# Initialize the database on startup
init_db()


@app.route("/")
def home():
    if "user" in session:
        return redirect("/chat-ui")
    return render_template("login.html")


@app.route("/login", methods=["POST", "GET"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if check_user(username, password):
            session["user"] = username
            return redirect("/chat-ui")

        return render_template("login.html", error="Invalid username or password")

    return render_template("login.html")


@app.route("/signup", methods=["POST", "GET"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not username or not password:
            return render_template("signup.html", error="All fields are required")

        if len(username) < 3:
            return render_template("signup.html", error="Username must be at least 3 characters")

        if len(password) < 4:
            return render_template("signup.html", error="Password must be at least 4 characters")

        if password != confirm:
            return render_template("signup.html", error="Passwords do not match")

        if add_user(username, password):
            session["user"] = username
            return redirect("/chat-ui")
        else:
            return render_template("signup.html", error="Username already taken")

    return render_template("signup.html")


@app.route("/clear-history", methods=["POST"])
def clear_history():
    session["chat_history"] = []
    session.modified = True
    return jsonify({"status": "cleared"})


@app.route("/edit-history", methods=["POST"])
def edit_history():
    data = request.get_json()
    message_to_edit = data.get("message")
    chat_history = session.get("chat_history", [])
    
    # Find the last occurrence of this message and truncate the history there
    for i in range(len(chat_history) - 1, -1, -1):
        if chat_history[i]["role"] == "user" and chat_history[i]["content"] == message_to_edit:
            session["chat_history"] = chat_history[:i]
            session.modified = True
            return jsonify({"status": "success", "truncated_at": i})
            
    return jsonify({"status": "not found"})


@app.route("/logout")
def logout():
    session.pop("user", None)
    session.pop("chat_history", None)
    return redirect("/")


@app.route("/chat-ui")
def chat_ui():
    if "user" not in session:
        return redirect("/")

    if "chat_history" not in session:
        session["chat_history"] = []

    return render_template("chat.html", username=session.get("user", "User"))


@app.route("/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message")

    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    chat_history = session.get("chat_history", [])
    reply = get_response(user_message, chat_history=chat_history)

    chat_history.append({"role": "user", "content": user_message})
    chat_history.append({"role": "bot", "content": reply})
    session["chat_history"] = chat_history[-10:]
    session.modified = True

    return jsonify({"user": user_message, "reply": reply})


@app.route("/chat-stream", methods=["POST"])
def chat_stream():
    data = request.get_json()
    user_message = data.get("message", "")

    if not user_message:
        return jsonify({"error": "No message provided"}), 400

    chat_history = session.get("chat_history", [])

    def generate():
        full_reply = ""
        for token in stream_response(user_message, chat_history=chat_history):
            full_reply += token
            yield f"data: {json.dumps({'token': token})}\n\n"

        yield f"data: {json.dumps({'done': True, 'full_reply': full_reply})}\n\n"

        chat_history.append({"role": "user", "content": user_message})
        chat_history.append({"role": "bot", "content": full_reply.strip()})
        session["chat_history"] = chat_history[-10:]
        session.modified = True

    return Response(generate(), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(debug=True)
