-- Миграция: Добавляем поддержку оригинальных имён файлов
-- Выполняется после добавления колонок file_path и file_type

-- Добавляем колонку original_filename для хранения оригинального имени файла
ALTER TABLE maxxx_local.messages 
ADD COLUMN IF NOT EXISTS original_filename VARCHAR(255);

-- Индекс для поиска по оригинальному имени (опционально)
CREATE INDEX IF NOT EXISTS idx_messages_original_filename 
ON maxxx_local.messages(original_filename) 
WHERE original_filename IS NOT NULL;

-- Комментарий к колонке
COMMENT ON COLUMN maxxx_local.messages.original_filename IS 'Оригинальное имя файла для отображения и скачивания';
