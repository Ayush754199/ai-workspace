import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import Base, engine, SessionLocal, get_db
from app.models.user import User as UserModel
from app.schemas.user import UserCreate, UserResponse, Token, LoginRequest
from app.core import security
from app.api.deps import get_current_user
from datetime import datetime, UTC


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    # Create all tables once per test module
    Base.metadata.create_all(bind=engine)
    yield
    # Drop all tables after tests
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="module")
def client(setup_db):
    # Override the get_db dependency to use the real (test) database
    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    # Clean up dependency override after test
    app.dependency_overrides.clear()


def test_register_user(client):
    user_in = UserCreate(email="test@example.com", password="secret123", full_name="Test User")
    response = client.post("/auth/register", json=user_in.model_dump())
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "test@example.com"
    assert data["full_name"] == "Test User"
    assert "id" in data


def test_login_success(client):
    # First register a user
    user_in = UserCreate(email="login@example.com", password="password123", full_name="Login User")
    client.post("/auth/register", json=user_in.model_dump())

    # Now login
    login_data = LoginRequest(email="login@example.com", password="password123")
    response = client.post("/auth/login", json=login_data.model_dump())
    assert response.status_code == 200
    token_data = response.json()
    assert "access_token" in token_data
    assert token_data["token_type"] == "bearer"


def test_login_wrong_password(client):
    # Register a user first
    user_in = UserCreate(email="wrongpass@example.com", password="correctpass", full_name="WrongPass User")
    client.post("/auth/register", json=user_in.model_dump())

    # Try login with wrong password
    login_data = LoginRequest(email="wrongpass@example.com", password="wrongpassword")
    response = client.post("/auth/login", json=login_data.model_dump())
    assert response.status_code == 401


def test_me_with_valid_token(client):
    # Register a user
    user_in = UserCreate(email="me@example.com", password="mypass", full_name="Me User")
    client.post("/auth/register", json=user_in.model_dump())

    # Login to get token
    login_data = LoginRequest(email="me@example.com", password="mypass")
    login_response = client.post("/auth/login", json=login_data.model_dump())
    token = login_response.json()["access_token"]

    # Call /me with the token
    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/auth/me", headers=headers)
    assert response.status_code == 200
    me_data = response.json()
    assert me_data["email"] == "me@example.com"
    assert me_data["full_name"] == "Me User"


def test_me_without_token(client):
    # Try to access /me without any token
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_user_data_isolation(client):
    """Test that users can only access their own data."""
    # Register two users
    user1_in = UserCreate(email="user1@example.com", password="password123", full_name="User One")
    user2_in = UserCreate(email="user2@example.com", password="password123", full_name="User Two")
    
    client.post("/auth/register", json=user1_in.model_dump())
    client.post("/auth/register", json=user2_in.model_dump())
    
    # Login as user1 and get token
    login1 = LoginRequest(email="user1@example.com", password="password123")
    response1 = client.post("/auth/login", json=login1.model_dump())
    token1 = response1.json()["access_token"]
    
    # Login as user2 and get token
    login2 = LoginRequest(email="user2@example.com", password="password123")
    response2 = client.post("/auth/login", json=login2.model_dump())
    token2 = response2.json()["access_token"]
    
    # User1 tries to access user2's /me endpoint - should get 401 or different data
    headers1 = {"Authorization": f"Bearer {token1}"}
    headers2 = {"Authorization": f"Bearer {token2}"}
    
    response1_me = client.get("/auth/me", headers=headers1)
    response2_me = client.get("/auth/me", headers=headers2)
    
    # User1 should see their own data
    assert response1_me.status_code == 200
    data1 = response1_me.json()
    assert data1["email"] == "user1@example.com"
    
    # User2 should see their own data
    assert response2_me.status_code == 200
    data2 = response2_me.json()
    assert data2["email"] == "user2@example.com"
    
    # Verify data isolation - user1 should NOT see user2's data
    assert data1["email"] != data2["email"]