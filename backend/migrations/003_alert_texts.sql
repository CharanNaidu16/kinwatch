-- Each recipient reads an alert in their own language: the written text per language, as JSON
-- ({"en": "...", "hi": "..."}). alerts.text/language stay the rule's own language.
ALTER TABLE alerts ADD COLUMN texts_json TEXT NOT NULL DEFAULT '{}';
