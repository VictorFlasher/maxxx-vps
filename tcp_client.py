import socket
import time

def test_echo():
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(('127.0.0.1', 9000))

    message = b"ECHO"
    start = time.time()
    client.send(message)
    response = client.recv(1024)
    end = time.time()

    print(f"Отправлено: {message.decode()}")
    print(f"Получено:  {response.decode()}")
    print(f"Задержка: {(end - start) * 1000:.2f} мс")

    client.close()


if __name__ == "__main__":
    test_echo()