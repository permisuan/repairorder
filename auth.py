import os
from passlib.context import CryptContext
from itsdangerous import URLSafeSerializer

# Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-keep-it-safe")
serializer = URLSafeSerializer(SECRET_KEY)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str):
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str):
    return pwd_context.verify(plain_password, hashed_password)

def get_session_data(token: str):
    try:
        return serializer.loads(token)
    except:
        return None

def create_session_token(data: dict):
    return serializer.dumps(data)