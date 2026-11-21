-- Removes the accounts and messages created by tests/run_prototype_tests.py on a live database.
-- The harness creates users named Thandi / Sipho / Intruder with emails like thandi.1a2b3c@example.com.
-- Real users and the demo users (thandi@lingualink.example.com ...) do NOT match the pattern.
--
-- Run on the VM (from the folder with docker-compose.prod.yml):
--   docker compose -f docker-compose.prod.yml exec -T db psql -U lingualink -d lingualink < tests/cleanup_test_users.sql
-- First check what would be removed:
--   SELECT id, name, email FROM users WHERE email ~ '^(thandi|sipho|intruder)\.[0-9a-f]{6}@example\.com$';

BEGIN;
CREATE TEMP TABLE t AS SELECT id FROM users WHERE email ~ '^(thandi|sipho|intruder)\.[0-9a-f]{6}@example\.com$';
CREATE TEMP TABLE m AS SELECT id FROM messages WHERE sender_id IN (SELECT id FROM t) OR recipient_id IN (SELECT id FROM t);
DELETE FROM message_translations WHERE message_id IN (SELECT id FROM m) OR user_id IN (SELECT id FROM t);
DELETE FROM message_hidden_for WHERE message_id IN (SELECT id FROM m) OR user_id IN (SELECT id FROM t);
DELETE FROM messages WHERE id IN (SELECT id FROM m);
DELETE FROM blocked_users WHERE user_id IN (SELECT id FROM t) OR other_id IN (SELECT id FROM t);
DELETE FROM hidden_users WHERE user_id IN (SELECT id FROM t) OR other_id IN (SELECT id FROM t);
DELETE FROM chat_clears WHERE user_id IN (SELECT id FROM t) OR other_id IN (SELECT id FROM t);
DELETE FROM users WHERE id IN (SELECT id FROM t);
SELECT count(*) AS users_left FROM users;
COMMIT;
