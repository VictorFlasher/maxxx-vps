import socket
import threading

def handle_client(conn, addr):
    print(f"[+] Подключился {addr}")
    try:
        data = conn.recv(1024)
        print(f"Получено: {data.decode()}")
        conn.send(data)  # отправить обратно
    finally:
        conn.close()


def start_server():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', 9000))
    server.listen(5)
    print("TCP Echo-сервер запущен на 127.0.0.1:9000")

    try:
        while True:
            conn, addr = server.accept()
            thread = threading.Thread(target=handle_client, args=(conn, addr))
            thread.start()
    except KeyboardInterrupt:
        print("\nСервер остановлен")
    finally:
        server.close()

if __name__ == "__main__":
    start_server()