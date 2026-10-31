-- PostgreSQL only. SQLite users do not need this: the app adds the same columns by itself on startup.
-- Safe to run more than once; existing rows are kept (old students get email = NULL, email_verified = 0).
ALTER TABLE students ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE students ADD COLUMN IF NOT EXISTS email_verified INTEGER DEFAULT 0;
CREATE UNIQUE INDEX IF NOT EXISTS students_email_uq ON students(email);   -- several NULLs are allowed
CREATE TABLE IF NOT EXISTS email_otps(id SERIAL PRIMARY KEY, email TEXT, code_hash TEXT, expires DOUBLE PRECISION,
                                      attempts INTEGER DEFAULT 0, created DOUBLE PRECISION, ip TEXT);
CREATE INDEX IF NOT EXISTS otps_email ON email_otps(email);
