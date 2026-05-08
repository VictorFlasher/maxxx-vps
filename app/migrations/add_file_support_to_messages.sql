-- Миграция: добавление поддержки файлов в сообщениях
-- Выполните: psql -U postgres -d postgres -f app/migrations/add_file_support_to_messages.sql

-- Переключаемся на схему maxxx_local (или укажите вашу схему)
SET search_path TO maxxx_local;

-- Добавляем колонку file_path для хранения пути/URL к файлу
ALTER TABLE messages 
ADD COLUMN IF NOT EXISTS file_path VARCHAR(500);

-- Добавляем колонку file_type для хранения типа файла (расширение)
ALTER TABLE messages 
ADD COLUMN IF NOT EXISTS file_type VARCHAR(50);

-- Индексы для производительности
CREATE INDEX IF NOT EXISTS idx_messages_file_path ON messages(file_path) WHERE file_path IS NOT NULL;

COMMENT ON COLUMN messages.file_path IS 'Путь или URL к загруженному файлу';
COMMENT ON COLUMN messages.file_type IS 'Тип файла (расширение): pdf, jpg, png, docx и т.д.';

COMMIT;
