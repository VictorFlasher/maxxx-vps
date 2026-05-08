-- Исправляем таблицу message_reports в схеме maxxx_local

-- Удаляем старую таблицу если она есть (сначала удаляем зависимости)
DROP TABLE IF EXISTS maxxx_local.message_reports CASCADE;

-- Создаём таблицу заново с правильными статусами
CREATE TABLE maxxx_local.message_reports (
    report_id SERIAL PRIMARY KEY,
    message_id INTEGER REFERENCES maxxx_local.messages(message_id) ON DELETE CASCADE,
    reporter_id INTEGER REFERENCES maxxx_local.users(user_id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'reviewed', 'resolved', 'actioned', 'dismissed')),
    reviewed_by INTEGER REFERENCES maxxx_local.users(user_id) ON DELETE SET NULL,
    reviewed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Создаём индексы для производительности
CREATE INDEX IF NOT EXISTS idx_message_reports_message_id ON maxxx_local.message_reports(message_id);
CREATE INDEX IF NOT EXISTS idx_message_reports_reporter_id ON maxxx_local.message_reports(reporter_id);
CREATE INDEX IF NOT EXISTS idx_message_reports_status ON maxxx_local.message_reports(status);
CREATE INDEX IF NOT EXISTS idx_message_reports_created_at ON maxxx_local.message_reports(created_at);

-- Проверяем структуру
\d maxxx_local.message_reports
