-- 0029_learning_incident.sql
--
-- One more kind of incident: the Learning Module's pipeline has stalled --
-- records are queueing on the bot or decisions stopped arriving while the
-- strategy kept producing signals. Opened by the worker, shown on the
-- dashboard, never paged: it is a data problem, not a trading one.

alter type public.incident_kind add value if not exists 'learning_stalled';
