import requests
import time
import jwt
from datetime import datetime, timedelta

# === Настройки ===
BASE_URL = "http://127.0.0.1:8000/api"
EMAIL = "test_user@example.com"
USERNAME = "testuser"
PASSWORD = "securepassword123"
# Очисти тестовых пользователей вручную перед запуском!


def test_1_1_register_unique():
    """1.1 Регистрация с уникальными данными"""
    response = requests.post(f"{BASE_URL}/register", json={
        "username": USERNAME,
        "email": EMAIL,
        "password": PASSWORD
    })
    assert response.status_code == 200, f"Ожидался 200, получен {response.status_code}"
    assert "Пользователь создан" in response.json()["message"]
    print("✅ 1.1 Пройден")


def test_1_2_register_duplicate():
    """1.2 Повторная регистрация"""
    response = requests.post(f"{BASE_URL}/register", json={
        "username": USERNAME,
        "email": EMAIL,
        "password": "anotherpass"
    })
    assert response.status_code == 400, f"Ожидался 400, получен {response.status_code}"
    print("✅ 1.2 Пройден")


def test_1_3_login_valid():
    """1.3 Вход с верным email/паролем"""
    global valid_token
    response = requests.post(f"{BASE_URL}/login", json={
        "email": EMAIL,
        "password": PASSWORD
    })
    assert response.status_code == 200, f"Ожидался 200, получен {response.status_code}"
    data = response.json()
    assert "access_token" in data
    valid_token = data["access_token"]
    print("✅ 1.3 Пройден")


def test_1_4_login_wrong_password():
    """1.4 Вход с неверным паролем"""
    response = requests.post(f"{BASE_URL}/login", json={
        "email": EMAIL,
        "password": "wrongpass"
    })
    assert response.status_code == 401, f"Ожидался 401, получен {response.status_code}"
    print("✅ 1.4 Пройден")


def test_1_5_login_nonexistent_email():
    """1.5 Вход с несуществующим email"""
    response = requests.post(f"{BASE_URL}/login", json={
        "email": "nonexistent@example.com",
        "password": "any"
    })
    assert response.status_code == 401, f"Ожидался 401, получен {response.status_code}"
    print("✅ 1.5 Пройден")


def test_1_6_token_after_ban():
    """1.6 Использование токена после бана"""
    # Сначала получим user_id из токена
    payload = jwt.decode(valid_token, options={"verify_signature": False})
    user_id = payload["user_id"]

    # Найдём админа (вручную создай его заранее!)
    admin_response = requests.post(f"{BASE_URL}/login", json={
        "email": "admin@example.com",
        "password": "adminpass"
    })
    assert admin_response.status_code == 200
    admin_token = admin_response.json()["access_token"]

    # Забаним пользователя
    ban_response = requests.post(
        f"{BASE_URL}/admin/ban",
        json={"user_id": user_id},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert ban_response.status_code == 200, "Не удалось забанить пользователя"

    # Попробуем использовать старый токен
    protected_response = requests.get(
        f"{BASE_URL}/chats/me",
        headers={"Authorization": f"Bearer {valid_token}"}
    )
    assert protected_response.status_code == 403, "Ожидался 403 после бана"
    print("✅ 1.6 Пройден")


def test_1_7_expired_token():
    """1.7 Использование просроченного токена"""
    # Создадим токен с прошлой датой
    expired_payload = {
        "sub": EMAIL,
        "user_id": 999,
        "exp": datetime.utcnow() - timedelta(minutes=10)
    }
    secret_key = "mysecretkey"  # Должен совпадать с SECRET_KEY в .env
    expired_token = jwt.encode(expired_payload, secret_key, algorithm="HS256")

    response = requests.get(
        f"{BASE_URL}/chats/me",
        headers={"Authorization": f"Bearer {expired_token}"}
    )
    assert response.status_code == 401, "Ожидался 401 для просроченного токена"
    print("✅ 1.7 Пройден")


if __name__ == "__main__":
    print("🚀 Запуск тестов аутентификации...\n")

    try:
        test_1_1_register_unique()
        test_1_2_register_duplicate()
        test_1_3_login_valid()
        test_1_4_login_wrong_password()
        test_1_5_login_nonexistent_email()
        test_1_6_token_after_ban()
        test_1_7_expired_token()

        print("\n🎉 Все тесты пройдены!")
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")