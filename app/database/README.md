# 📁 Инициализация базы данных Maxxx Local

Этот файл содержит SQL-скрипт для полной инициализации базы данных с правильной структурой.

## 🚀 Быстрый старт

### Вариант 1: Полная инициализация с нуля (рекомендуется для новой установки)

```bash
psql -U postgres -d postgres -f app/database/init_db.sql
```

### Вариант 2: Через pgAdmin или другой GUI-клиент
1. Откройте файл `app/database/init_db.sql`
2. Выполните весь скрипт в SQL-редакторе

---

## 📊 Структура базы данных

Скрипт создаёт схему **`maxxx_local`** и следующие таблицы:

| Таблица | Описание |
|---------|----------|
| `users` | Пользователи системы |
| `chats` | Чаты (личные и групповые) |
| `chat_members` | Участники чатов |
| `messages` | Сообщения (с поддержкой файлов) |
| `bans` | Активные баны |
| `ban_history` | История банов |
| `message_reports` | Жалобы на сообщения |
| `connection_logs` | Логи подключений WebSocket |

### ✨ Особенности таблицы `messages`:
- **`file_path`** — путь к файлу или URL (VARCHAR(500))
- **`file_type`** — MIME-тип файла (VARCHAR(50))
- Поддержка шифрования (`encrypted_key`, `iv`)
- Отслеживание редактирования (`is_edited`, `edited_at`)

---

## 🔄 Если база уже существует

### Сценарий A: База существует, но нет колонок для файлов

Выполните миграцию:
```bash
psql -U postgres -d postgres -f app/migrations/add_file_support_to_messages.sql
```

### Сценарий B: База существует, есть старые файлы в формате "[Файл]: URL"

Конвертируйте старые записи:
```bash
psql -U postgres -d postgres -f app/migrations/migrate_existing_files.sql
```

### Сценарий C: Полная пересоздание базы

⚠️ **Внимание!** Это удалит все данные!

```sql
-- Удалить схему и всё содержимое
DROP SCHEMA IF EXISTS maxxx_local CASCADE;

-- Затем выполнить инициализацию заново
\i app/database/init_db.sql
```

Или одной командой:
```bash
psql -U postgres -d postgres -c "DROP SCHEMA IF EXISTS maxxx_local CASCADE;" && \
psql -U postgres -d postgres -f app/database/init_db.sql
```

---

## 🔍 Проверка установки

После выполнения скрипта проверьте создание таблиц:

```sql
SET search_path TO maxxx_local;

-- Показать все таблицы
\dt

-- Проверить структуру messages
\d messages

-- Проверить наличие индексов
\di
```

Ожидаемый результат для `\d messages`:
```
                                        Table "maxxx_local.messages"
   Column    |           Type           | Collation | Nullable |                  Default
-------------+--------------------------+-----------+----------+-------------------------------------------
 message_id  | integer                  |           | not null | nextval('messages_message_id_seq'::regclass)
 chat_id     | integer                  |           |          |
 sender_id   | integer                  |           |          |
 content     | text                     |           | not null |
 file_path   | character varying(500)   |           |          |
 file_type   | character varying(50)    |           |          |
 encrypted_key | bytea                  |           |          |
 iv          | bytea                    |           |          |
 created_at  | timestamp with time zone |           |          | CURRENT_TIMESTAMP
 is_edited   | boolean                  |           |          | false
 edited_at   | timestamp with time zone |           |          |
Indexes:
    "messages_pkey" PRIMARY KEY, btree (message_id)
    "idx_messages_chat_id" btree (chat_id)
    "idx_messages_created_at" btree (created_at)
    "idx_messages_file_path" btree (file_path) WHERE file_path IS NOT NULL
Foreign-key constraints:
    "messages_chat_id_fkey" FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
    "messages_sender_id_fkey" FOREIGN KEY (sender_id) REFERENCES users(user_id) ON DELETE SET NULL
```

---

## 📝 Примечания

1. **Схема**: Все таблицы создаются в схеме `maxxx_local`
2. **Кодировка**: Убедитесь, что база использует UTF-8
3. **Временные зоны**: Используется `TIMESTAMP WITH TIME ZONE` для всех временных меток
4. **Внешние ключи**: Настроены каскадные удаления где это уместно
5. **Индексы**: Созданы оптимальные индексы для производительности

---

## 🆘 Решение проблем

### Ошибка: "schema 'maxxx_local' does not exist"
Убедитесь, что первая строка скрипта выполнилась:
```sql
CREATE SCHEMA IF NOT EXISTS maxxx_local;
```

### Ошибка: "permission denied for schema public"
Предоставьте права:
```sql
GRANT ALL ON SCHEMA maxxx_local TO ваш_пользователь;
GRANT ALL ON ALL TABLES IN SCHEMA maxxx_local TO ваш_пользователь;
```

### Ошибка: "relation already exists"
Таблицы уже созданы. Используйте `IF NOT EXISTS` или удалите схему и создайте заново.

---

## 📚 Дополнительные файлы

- `app/migrations/add_file_support_to_messages.sql` — добавляет колонки файлов в существующую БД
- `app/migrations/migrate_existing_files.sql` — конвертирует старые записи файлов
- `app/migrations/README_migration.md` — подробная документация по миграциям
