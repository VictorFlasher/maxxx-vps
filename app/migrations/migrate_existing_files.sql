-- Миграция: конвертация старых записей "[Файл]: URL" в новые колонки
-- Выполните ПОСЛЕ применения миграции add_file_support_to_messages.sql
-- psql -U postgres -d postgres -f app/migrations/migrate_existing_files.sql

SET search_path TO maxxx_local;

-- Обновляем file_path для сообщений, где content содержит "[Файл]: URL"
UPDATE messages
SET 
    file_path = TRIM(SUBSTRING(content FROM 9)),
    file_type = CASE 
        WHEN TRIM(SUBSTRING(content FROM 9)) LIKE '%.png' THEN '.png'
        WHEN TRIM(SUBSTRING(content FROM 9)) LIKE '%.jpg' THEN '.jpg'
        WHEN TRIM(SUBSTRING(content FROM 9)) LIKE '%.jpeg' THEN '.jpeg'
        WHEN TRIM(SUBSTRING(content FROM 9)) LIKE '%.gif' THEN '.gif'
        WHEN TRIM(SUBSTRING(content FROM 9)) LIKE '%.pdf' THEN '.pdf'
        WHEN TRIM(SUBSTRING(content FROM 9)) LIKE '%.doc' THEN '.doc'
        WHEN TRIM(SUBSTRING(content FROM 9)) LIKE '%.docx' THEN '.docx'
        WHEN TRIM(SUBSTRING(content FROM 9)) LIKE '%.txt' THEN '.txt'
        ELSE LOWER(RIGHT(TRIM(SUBSTRING(content FROM 9)), 4))
    END
WHERE content LIKE '[Файл]: %'
  AND file_path IS NULL;

-- Проверяем результат
SELECT message_id, content, file_path, file_type 
FROM messages 
WHERE file_path IS NOT NULL 
LIMIT 10;

COMMIT;
