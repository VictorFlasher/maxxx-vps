-- Миграция: добавление таблицы message_reports
-- Выполняется вручную или через миграционный скрипт

-- Таблица жалоб на сообщения (если не существует)
CREATE TABLE IF NOT EXISTS maxxx_local.message_reports (
    report_id SERIAL PRIMARY KEY,
    message_id INTEGER REFERENCES maxxx_local.messages(message_id) ON DELETE CASCADE,
    reporter_id INTEGER REFERENCES maxxx_local.users(user_id) ON DELETE CASCADE,
    reason TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'reviewed', 'resolved')),
    reviewed_by INTEGER REFERENCES maxxx_local.users(user_id) ON DELETE SET NULL,
    reviewed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Индексы для производительности
CREATE INDEX IF NOT EXISTS idx_message_reports_status ON maxxx_local.message_reports(status);
CREATE INDEX IF NOT EXISTS idx_message_reports_message_id ON maxxx_local.message_reports(message_id);
CREATE INDEX IF NOT EXISTS idx_message_reports_reporter_id ON maxxx_local.message_reports(reporter_id);
