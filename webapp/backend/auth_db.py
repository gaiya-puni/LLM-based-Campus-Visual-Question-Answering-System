
"""MySQL connection handling, password hashing and user CRUD."""

import os
import threading

import pymysql
import paths  # noqa: F401
from flask_login import LoginManager, UserMixin
from flask_app import app
from werkzeug.security import check_password_hash, generate_password_hash

DB_CONFIG = {
    'host': os.getenv('MYSQL_HOST', 'localhost'),
    'port': int(os.getenv('MYSQL_PORT', '3306')),
    'user': os.getenv('MYSQL_USER', 'root'),
    'password': os.getenv('MYSQL_PASSWORD', ''),
    'database': os.getenv('MYSQL_DATABASE', 'test'),
    'charset': os.getenv('MYSQL_CHARSET', 'utf8mb4'),
}

# 用线程本地存储代替全局变量，每个请求线程拥有独立连接，避免竞态条件
_local = threading.local()


def get_cursor():
    if not hasattr(_local, 'db') or _local.db is None:
        _local.db = pymysql.connect(**DB_CONFIG)
    else:
        _local.db.ping(reconnect=True)
    _local.cursor = _local.db.cursor()
    return _local.cursor


def commit():
    """提交当前线程连接上的事务。

    `get_cursor()` 刻意不自动提交（保持既有语义），因此需要事务的调用方
    （如评论子系统的"表态 + 计数"、"举报 + 计数 + 折叠"）显式调它。
    """
    database = getattr(_local, 'db', None)
    if database is not None:
        database.commit()


def rollback():
    """回滚当前线程连接上的事务；用于多表写入失败时保持计数一致。"""
    database = getattr(_local, 'db', None)
    if database is not None:
        database.rollback()


@app.teardown_appcontext
def close_database(_error=None):
    cursor = getattr(_local, 'cursor', None)
    database = getattr(_local, 'db', None)
    if cursor is not None:
        cursor.close()
        _local.cursor = None
    if database is not None:
        database.close()
        _local.db = None

# 创建登录管理对象
login_manager = LoginManager()
login_manager.init_app(app)


# 创建用户类，继承UserMixin
class User(UserMixin):
    def __init__(self, id, username, password):
        self.id = id
        self.username = username
        self.password = password


PASSWORD_HASH_PREFIXES = ('pbkdf2:', 'scrypt:')


def is_password_hash(password):
    return bool(password and password.startswith(PASSWORD_HASH_PREFIXES))


def hash_password(password):
    return generate_password_hash(password)


def verify_password(stored_password, provided_password):
    if not stored_password or not provided_password:
        return False
    if is_password_hash(stored_password):
        return check_password_hash(stored_password, provided_password)
    return stored_password == provided_password


def update_user_password(username, password):
    sql = "UPDATE users SET password=%s WHERE username=%s"
    c = get_cursor()
    c.execute(sql, (hash_password(password), username))
    _local.db.commit()


def create_user(username, password):
    sql = "insert into users (username, password) values (%s, %s)"
    c = get_cursor()
    c.execute(sql, (username, hash_password(password)))
    _local.db.commit()


def get_user(username):
    sql = "select * from users where username=%s"
    c = get_cursor()
    c.execute(sql, username)
    result = c.fetchone()
    if result:
        return User(result[0], result[1], result[2])
    return None


@login_manager.user_loader
def load_user(user_id):
    sql = "select * from users where id=%s"
    c = get_cursor()
    c.execute(sql, user_id)
    result = c.fetchone()
    if result:
        return User(result[0], result[1], result[2])
    return None
