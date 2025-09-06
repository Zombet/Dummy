# app.py
import os
import datetime
import jwt
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import mysql.connector

# ---------------- Config ----------------
app = Flask(__name__, static_folder=None)
CORS(app)  # allow all origins for development; lock this down in production

app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'replace_this_secret')
JWT_ALGORITHM = 'HS256'
JWT_EXPIRE_HOURS = int(os.getenv('JWT_EXPIRE_HOURS', '24'))

# MySQL config - ensure your friend creates the schema/tables
DB_CONFIG = {
    'host': os.getenv('MYSQL_HOST', 'localhost'),
    'user': os.getenv('MYSQL_USER', 'root'),
    'password': os.getenv('MYSQL_PASSWORD', 'password'),
    'database': os.getenv('MYSQL_DB', 'ecofinds'),
    'auth_plugin': os.getenv('MYSQL_AUTH', 'mysql_native_password')
}

# ------------- Helpers -------------------
def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)

def jwt_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.headers.get('Authorization', None)
        if not auth or not auth.startswith('Bearer '):
            return jsonify({'error': 'Authorization header missing'}), 401
        token = auth.split(' ', 1)[1]
        try:
            payload = jwt.decode(token, app.config['SECRET_KEY'], algorithms=[JWT_ALGORITHM])
            user_id = int(payload.get('user_id'))
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except Exception:
            return jsonify({'error': 'Invalid token'}), 401
        return f(user_id, *args, **kwargs)
    return wrapper

def create_token(user_id):
    exp = datetime.datetime.utcnow() + datetime.timedelta(hours=JWT_EXPIRE_HOURS)
    return jwt.encode({'user_id': user_id, 'exp': exp}, app.config['SECRET_KEY'], algorithm=JWT_ALGORITHM)

# -------------- Auth ---------------------
@app.route('/signup', methods=['POST'])
def signup():
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not (username and email and password):
        return jsonify({'error': 'username, email and password required'}), 400

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            return jsonify({'error': 'email already registered'}), 400

        password_hash = generate_password_hash(password)
        cur.execute("INSERT INTO users (username, email, password) VALUES (%s,%s,%s)",
                    (username, email, password_hash))
        conn.commit()
        user_id = cur.lastrowid
        token = create_token(user_id)
        return jsonify({'token': token, 'user': {'id': user_id, 'username': username, 'email': email}}), 201
    except Exception as e:
        return jsonify({'error': 'server error', 'detail': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''

    if not (email and password):
        return jsonify({'error': 'email and password required'}), 400

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, username, email, password FROM users WHERE email=%s", (email,))
        user = cur.fetchone()
        if not user or not check_password_hash(user['password'], password):
            return jsonify({'error': 'invalid credentials'}), 401
        token = create_token(user['id'])
        return jsonify({'token': token, 'user': {'id': user['id'], 'username': user['username'], 'email': user['email']}})
    except Exception as e:
        return jsonify({'error': 'server error', 'detail': str(e)}), 500
    finally:
        cur.close()
        conn.close()

# -------------- Products ------------------
# Required product columns (as agreed with frontend): id, user_id, title, description, price, category, image
@app.route('/products', methods=['POST'])
@jwt_required
def create_product(current_user):
    data = request.get_json() or {}
    title = data.get('title', '').strip()
    description = data.get('description', '').strip()
    price = data.get('price')
    category = data.get('category', '').strip()
    image = data.get('image', '').strip()  # image URL (frontend provides) - change to file upload if you want

    if not (title and price is not None):
        return jsonify({'error': 'title and price required'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO products (user_id, title, description, price, category, image) VALUES (%s,%s,%s,%s,%s,%s)",
            (current_user, title, description, float(price), category, image)
        )
        conn.commit()
        return jsonify({'message': 'product created', 'id': cur.lastrowid}), 201
    except Exception as e:
        return jsonify({'error': 'server error', 'detail': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/products', methods=['GET'])
def list_products():
    # optional filters: q, category
    q = request.args.get('q', '').strip()
    category = request.args.get('category', '').strip()

    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        base = "SELECT id, user_id, title, description, price, category, image FROM products"
        conditions = []
        params = []
        if q:
            conditions.append("(title LIKE %s OR description LIKE %s)")
            like = f"%{q}%"
            params.extend([like, like])
        if category:
            conditions.append("category=%s")
            params.append(category)
        if conditions:
            base += " WHERE " + " AND ".join(conditions)
        base += " ORDER BY id DESC"
        cur.execute(base, tuple(params))
        rows = cur.fetchall()
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': 'server error', 'detail': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/products/<int:pid>', methods=['GET'])
def get_product(pid):
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, user_id, title, description, price, category, image FROM products WHERE id=%s", (pid,))
        prod = cur.fetchone()
        if not prod:
            return jsonify({'error': 'not found'}), 404
        return jsonify(prod)
    except Exception as e:
        return jsonify({'error': 'server error', 'detail': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/products/<int:pid>', methods=['PUT'])
@jwt_required
def update_product(current_user, pid):
    data = request.get_json() or {}
    title = data.get('title')
    description = data.get('description')
    price = data.get('price')
    category = data.get('category')
    image = data.get('image')

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # Ensure owner
        cur.execute("SELECT user_id FROM products WHERE id=%s", (pid,))
        owner = cur.fetchone()
        if not owner:
            return jsonify({'error': 'not found'}), 404
        if owner[0] != current_user:
            return jsonify({'error': 'forbidden'}), 403

        fields = []
        params = []
        if title is not None:
            fields.append("title=%s"); params.append(title)
        if description is not None:
            fields.append("description=%s"); params.append(description)
        if price is not None:
            fields.append("price=%s"); params.append(float(price))
        if category is not None:
            fields.append("category=%s"); params.append(category)
        if image is not None:
            fields.append("image=%s"); params.append(image)

        if not fields:
            return jsonify({'error': 'nothing to update'}), 400

        params.append(pid)
        cur.execute(f"UPDATE products SET {', '.join(fields)} WHERE id=%s", tuple(params))
        conn.commit()
        return jsonify({'message': 'updated'})
    except Exception as e:
        return jsonify({'error': 'server error', 'detail': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/products/<int:pid>', methods=['DELETE'])
@jwt_required
def delete_product(current_user, pid):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT user_id FROM products WHERE id=%s", (pid,))
        owner = cur.fetchone()
        if not owner:
            return jsonify({'error': 'not found'}), 404
        if owner[0] != current_user:
            return jsonify({'error': 'forbidden'}), 403
        cur.execute("DELETE FROM products WHERE id=%s", (pid,))
        conn.commit()
        return jsonify({'message': 'deleted'})
    except Exception as e:
        return jsonify({'error': 'server error', 'detail': str(e)}), 500
    finally:
        cur.close()
        conn.close()

# -------------- Cart ----------------------
@app.route('/cart', methods=['POST'])
@jwt_required
def add_to_cart(current_user):
    data = request.get_json() or {}
    product_id = data.get('product_id')
    quantity = int(data.get('quantity', 1))

    if not product_id:
        return jsonify({'error': 'product_id required'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # If item exists, increment; else insert
        cur.execute("SELECT id, quantity FROM cart WHERE user_id=%s AND product_id=%s", (current_user, product_id))
        existing = cur.fetchone()
        if existing:
            cur.execute("UPDATE cart SET quantity = quantity + %s WHERE id=%s", (quantity, existing[0]))
        else:
            cur.execute("INSERT INTO cart (user_id, product_id, quantity) VALUES (%s,%s,%s)",
                        (current_user, product_id, quantity))
        conn.commit()
        return jsonify({'message': 'added to cart'})
    except Exception as e:
        return jsonify({'error': 'server error', 'detail': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/cart', methods=['GET'])
@jwt_required
def view_cart(current_user):
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT c.id as cart_id, p.id as product_id, p.title, p.price, p.image, c.quantity
            FROM cart c JOIN products p ON c.product_id = p.id
            WHERE c.user_id=%s
        """, (current_user,))
        rows = cur.fetchall()
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': 'server error', 'detail': str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ------------- Orders / Purchases ----------
@app.route('/checkout', methods=['POST'])
@jwt_required
def checkout(current_user):
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        # get cart items
        cur.execute("""
            SELECT c.product_id, c.quantity, p.price
            FROM cart c JOIN products p ON c.product_id = p.id
            WHERE c.user_id=%s
        """, (current_user,))
        items = cur.fetchall()
        if not items:
            return jsonify({'error': 'cart empty'}), 400

        # create order
        now = datetime.datetime.utcnow()
        cur.execute("INSERT INTO orders (user_id, created_at) VALUES (%s, %s)", (current_user, now))
        order_id = cur.lastrowid

        # insert order_items
        for it in items:
            cur.execute("INSERT INTO order_items (order_id, product_id, quantity, price) VALUES (%s,%s,%s,%s)",
                        (order_id, it['product_id'], it['quantity'], it['price']))

        # clear cart
        cur.execute("DELETE FROM cart WHERE user_id=%s", (current_user,))
        conn.commit()
        return jsonify({'message': 'checkout complete', 'order_id': order_id})
    except Exception as e:
        return jsonify({'error': 'server error', 'detail': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/purchases', methods=['GET'])
@jwt_required
def purchases(current_user):
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT o.id AS order_id, o.created_at, oi.product_id, p.title, p.image, oi.quantity, oi.price
            FROM orders o
            JOIN order_items oi ON oi.order_id = o.id
            JOIN products p ON p.id = oi.product_id
            WHERE o.user_id=%s
            ORDER BY o.created_at DESC
        """, (current_user,))
        rows = cur.fetchall()
        return jsonify(rows)
    except Exception as e:
        return jsonify({'error': 'server error', 'detail': str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ------------- Profile -------------------
@app.route('/profile', methods=['GET'])
@jwt_required
def profile(current_user):
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, username, email FROM users WHERE id=%s", (current_user,))
        user = cur.fetchone()
        if not user:
            return jsonify({'error': 'not found'}), 404
        return jsonify(user)
    except Exception as e:
        return jsonify({'error': 'server error', 'detail': str(e)}), 500
    finally:
        cur.close()
        conn.close()

@app.route('/profile', methods=['PUT'])
@jwt_required
def update_profile(current_user):
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    email = data.get('email', '').strip().lower()
    if not (username and email):
        return jsonify({'error': 'username and email required'}), 400

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        # check email conflict
        cur.execute("SELECT id FROM users WHERE email=%s AND id!=%s", (email, current_user))
        if cur.fetchone():
            return jsonify({'error': 'email already in use'}), 400
        cur.execute("UPDATE users SET username=%s, email=%s WHERE id=%s", (username, email, current_user))
        conn.commit()
        return jsonify({'message': 'profile updated'})
    except Exception as e:
        return jsonify({'error': 'server error', 'detail': str(e)}), 500
    finally:
        cur.close()
        conn.close()

# ------------- Categories (simple) -----------
@app.route('/categories', methods=['GET'])
def categories():
    # can be replaced with DB-driven categories if you create a table
    return jsonify(["Clothing", "Electronics", "Furniture", "Books", "Home", "Other"])

# ------------- Static (optional) -----------
# If you decide to support uploaded image file serving via local uploads dir, you can add:
# @app.route('/uploads/<path:filename>') -> send_from_directory(...)

# ------------- Run -------------------------
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.getenv('PORT', 5000)), debug=(os.getenv('FLASK_ENV', 'development') == 'development'))