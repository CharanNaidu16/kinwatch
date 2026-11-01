-- Per-person language for messages that aren't replies to something they wrote (camera notices,
-- button replies). `language` comes from FAMILY_MEMBERS_JSON when set; `last_language` is learned
-- from what the person writes. Neither set: the household language.
ALTER TABLE members ADD COLUMN language TEXT;
ALTER TABLE members ADD COLUMN last_language TEXT;
