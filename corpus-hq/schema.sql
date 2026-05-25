-- ============================================================
-- CORPUS HQ — MANDATORY READ PROTOCOL
-- ============================================================
-- SURFACE SCAN IS FORBIDDEN.
-- Every query touching master_corpus for content analysis
-- MUST include the transcript column.
--
-- summary = "Imported from NoteX" does NOT mean empty.
-- It means the real content is in transcript. Always read it.
--
-- Types that ALWAYS have content in transcript:
-- voice_memo, voice_note, Voice Note, audio, Meeting Recording,
-- NoteX, transcript, claude_conversation, gpt_conversation,
-- youtube_video, podcast
--
-- CORRECT pattern:
-- SELECT title, transcript, summary, type, tags, date
-- FROM master_corpus
-- WHERE transcript IS NOT NULL AND transcript != ''
--
-- WRONG pattern (FORBIDDEN):
-- SELECT title, summary FROM master_corpus
-- ============================================================

-- ============================================================
-- CORPUS HQ — CANONICAL SCHEMA
-- Supabase Project: lbvaosyfikkpvcwksiph
-- Last locked: 2026-05-25
-- SOURCE OF TRUTH: This file. Not a diagram. Not memory.
-- Update this file every time the live DB structure changes.
-- ============================================================

-- CORE CORPUS
CREATE TABLE IF NOT EXISTS master_corpus (id text NOT NULL, title text NOT NULL, type text, tags text, summary text, transcript text, date text, source_url text, origin text DEFAULT 'notion'::text, collection text, created_at timestamp with time zone DEFAULT now(), normalized_date date, source_device text, search_doc tsvector);

CREATE TABLE IF NOT EXISTS corpus_chunks (id bigint NOT NULL DEFAULT nextval('corpus_chunks_id_seq'::regclass), corpus_type text NOT NULL, source_file text NOT NULL, chunk_index integer NOT NULL, title text, content text NOT NULL, content_length integer, embedding vector, created_at timestamp with time zone DEFAULT now(), entry_date date, entry_time time without time zone, source_device text);

CREATE TABLE IF NOT EXISTS scribbly_entries (id text NOT NULL, title text NOT NULL, type text, tags text, summary text, transcript text, date text, notion_url text, created_at timestamp with time zone DEFAULT now(), collection_id text);

CREATE TABLE IF NOT EXISTS scribbly_collections (id text NOT NULL, name text NOT NULL, source_url text, type text DEFAULT 'playlist'::text, total_videos integer DEFAULT 0, saved_videos integer DEFAULT 0, skipped_videos integer DEFAULT 0, failed_videos integer DEFAULT 0, tags text, created_at timestamp with time zone DEFAULT now(), batch_number integer);

-- CORPUS HEALTH & REGISTRY
CREATE TABLE IF NOT EXISTS corpus_source_registry (id bigint NOT NULL DEFAULT nextval('corpus_source_registry_id_seq'::regclass), origin text NOT NULL, display_name text, expected_min_rows integer DEFAULT 0, actual_rows integer DEFAULT 0, last_ingest_at timestamp with time zone, last_verified_at timestamp with time zone, status text DEFAULT 'healthy'::text, notes text, created_at timestamp with time zone DEFAULT now());

CREATE TABLE IF NOT EXISTS corpus_health_log (id bigint NOT NULL DEFAULT nextval('corpus_health_log_id_seq'::regclass), checked_at timestamp with time zone DEFAULT now(), origin text, expected integer, actual integer, delta integer, status text);

CREATE TABLE IF NOT EXISTS corpus_maintenance_log (id bigint NOT NULL DEFAULT nextval('corpus_maintenance_log_id_seq'::regclass), issue_type text, source_file text, detail jsonb, flagged_at timestamp with time zone DEFAULT now());

-- CHANNEL BRAIN
CREATE TABLE IF NOT EXISTS cb_channels (id uuid NOT NULL DEFAULT gen_random_uuid(), channel_url text NOT NULL, channel_id text NOT NULL, channel_name text, description text, video_count integer DEFAULT 0, status text DEFAULT 'pending'::text, created_at timestamp with time zone DEFAULT now(), updated_at timestamp with time zone DEFAULT now());

CREATE TABLE IF NOT EXISTS cb_videos (id uuid NOT NULL DEFAULT gen_random_uuid(), channel_id text NOT NULL, video_id text NOT NULL, title text, published_at timestamp with time zone, duration_seconds integer, transcript_status text DEFAULT 'pending'::text, created_at timestamp with time zone DEFAULT now());

CREATE TABLE IF NOT EXISTS cb_chunks (id uuid NOT NULL DEFAULT gen_random_uuid(), channel_id text NOT NULL, video_id text NOT NULL, video_title text, chunk_index integer, content text NOT NULL, embedding vector, created_at timestamp with time zone DEFAULT now());

-- CLIFF AI ASSISTANT
CREATE TABLE IF NOT EXISTS cliff_persona (id integer NOT NULL DEFAULT 1, identity text NOT NULL, tone_rules text NOT NULL, response_style text NOT NULL, hard_limits text NOT NULL, context_about_boss text NOT NULL, voice_specific text, chat_specific text, autonomous_specific text, briefing_specific text, updated_at timestamp with time zone DEFAULT now(), updated_by text DEFAULT 'system'::text, master_prompt_override text, active_context_digest text, active_context_digest_updated_at timestamp with time zone);

CREATE TABLE IF NOT EXISTS cliff_tasks (id uuid NOT NULL DEFAULT gen_random_uuid(), parent_id uuid, root_goal text, description text NOT NULL, status text NOT NULL DEFAULT 'pending'::text, urgency integer NOT NULL DEFAULT 5, blocker_of uuid, owner_phone text, owner_name text, context jsonb DEFAULT '{}'::jsonb, result text, created_at timestamp with time zone DEFAULT now(), updated_at timestamp with time zone DEFAULT now(), completed_at timestamp with time zone);

CREATE TABLE IF NOT EXISTS cliff_memory (id bigint NOT NULL DEFAULT nextval('cliff_memory_id_seq'::regclass), created_at timestamp with time zone DEFAULT now(), memory_type text NOT NULL, content text NOT NULL, related_to text, related_phone text, conversation_id text, source text DEFAULT 'cliff_call'::text, importance integer DEFAULT 5, expires_at timestamp with time zone);

CREATE TABLE IF NOT EXISTS cliff_contacts (phone_number text NOT NULL, display_name text, relationship text, first_contact_at timestamp with time zone DEFAULT now(), last_contact_at timestamp with time zone DEFAULT now(), total_calls integer DEFAULT 0, total_messages integer DEFAULT 0, recent_summary text, notes text, created_at timestamp with time zone DEFAULT now(), updated_at timestamp with time zone DEFAULT now());

CREATE TABLE IF NOT EXISTS cliff_message_threads (id uuid NOT NULL DEFAULT gen_random_uuid(), contact_phone text NOT NULL, contact_name text, last_msg_from text, last_msg_at timestamp with time zone NOT NULL DEFAULT now(), last_msg_text text, last_msg_preview text, unreplied_since timestamp with time zone, thread_score integer DEFAULT 0, status text DEFAULT 'active'::text, snoozed_until timestamp with time zone, is_group boolean DEFAULT false, group_participants text[], total_messages_seen integer DEFAULT 0, created_at timestamp with time zone NOT NULL DEFAULT now(), updated_at timestamp with time zone NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS cliff_message_log (id uuid NOT NULL DEFAULT gen_random_uuid(), thread_id uuid, contact_phone text NOT NULL, contact_name text, direction text NOT NULL, content text NOT NULL, attachments text[], message_at timestamp with time zone NOT NULL, raw_payload jsonb, created_at timestamp with time zone NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS cliff_message_drafts (id uuid NOT NULL DEFAULT gen_random_uuid(), thread_id uuid, contact_phone text NOT NULL, contact_name text, drafted_at timestamp with time zone NOT NULL DEFAULT now(), draft_variants jsonb NOT NULL, selected_variant_index integer, cliff_reasoning text, status text DEFAULT 'pending'::text, approved_at timestamp with time zone, sent_at timestamp with time zone, send_response jsonb, ack_code text);

CREATE TABLE IF NOT EXISTS cliff_message_filter (id uuid NOT NULL DEFAULT gen_random_uuid(), pattern text NOT NULL, pattern_type text NOT NULL, action text NOT NULL, reason text, created_at timestamp with time zone NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS cliff_outbound_queue (id bigint NOT NULL DEFAULT nextval('cliff_outbound_queue_id_seq'::regclass), event_type text NOT NULL, entity_id text, entity_name text, event_payload jsonb, detected_at timestamp with time zone DEFAULT now(), status text DEFAULT 'pending'::text, composed_at timestamp with time zone, sent_at timestamp with time zone, message_id bigint);

CREATE TABLE IF NOT EXISTS cliff_outbound_log (id bigint NOT NULL DEFAULT nextval('cliff_outbound_log_id_seq'::regclass), sent_at timestamp with time zone DEFAULT now(), channel text DEFAULT 'imessage'::text, recipient text NOT NULL, message_content text NOT NULL, events_included bigint[], trigger text, sendblue_response jsonb);

CREATE TABLE IF NOT EXISTS cliff_heartbeat_log (id uuid NOT NULL DEFAULT gen_random_uuid(), fired_at timestamp with time zone DEFAULT now(), new_tasks_found integer DEFAULT 0, open_tasks integer DEFAULT 0, actions_taken integer DEFAULT 0, summary text, raw jsonb);

CREATE TABLE IF NOT EXISTS cliff_persona_patches (id integer NOT NULL DEFAULT nextval('cliff_persona_patches_id_seq'::regclass), patch_code text NOT NULL, source text NOT NULL, source_call_id text, field_name text NOT NULL, mode text NOT NULL DEFAULT 'append'::text, patch_content text NOT NULL, reasoning text, status text NOT NULL DEFAULT 'pending'::text, created_at timestamp with time zone DEFAULT now(), responded_at timestamp with time zone, applied_at timestamp with time zone, rejection_reason text);

CREATE TABLE IF NOT EXISTS cliff_persona_history (id uuid NOT NULL DEFAULT gen_random_uuid(), changed_at timestamp with time zone DEFAULT now(), changed_by text, changes jsonb, snapshot jsonb);

CREATE TABLE IF NOT EXISTS cliff_style_profile (id integer NOT NULL DEFAULT nextval('cliff_style_profile_id_seq'::regclass), version integer NOT NULL, source_count integer, source_types text[], cadence_notes text, opener_patterns text, closer_patterns text, filler_words text[], topic_shift_pattern text, context_variations jsonb, vocabulary_signatures text[], raw_analysis jsonb, is_active boolean DEFAULT true, created_at timestamp with time zone DEFAULT now(), sample_ids integer[], connor_turn_count integer, style_code text, status text DEFAULT 'pending'::text, responded_at timestamp with time zone, applied_at timestamp with time zone, rejection_reason text, persona_injection text);

CREATE TABLE IF NOT EXISTS cliff_voice_profile (id uuid NOT NULL DEFAULT gen_random_uuid(), updated_at timestamp with time zone NOT NULL DEFAULT now(), call_count integer DEFAULT 0, avg_turn_length_words double precision, min_turn_length_words double precision, max_turn_length_words double precision, avg_response_latency_sec double precision, avg_energy_register double precision, avg_formality double precision, top_phrases jsonb DEFAULT '[]'::jsonb, opener_patterns jsonb DEFAULT '[]'::jsonb, topic_shift_style text, call_history jsonb DEFAULT '[]'::jsonb, profile_summary text, lock_col boolean DEFAULT true);

CREATE TABLE IF NOT EXISTS cliff_connor_voice_samples (id integer NOT NULL DEFAULT nextval('cliff_connor_voice_samples_id_seq'::regclass), call_id text, call_direction text, duration_sec integer, connor_turns text[] NOT NULL, full_transcript text, call_summary text, used_in_profile_version integer, created_at timestamp with time zone DEFAULT now());

CREATE TABLE IF NOT EXISTS cliff_sms_threads (id uuid NOT NULL DEFAULT gen_random_uuid(), phone text NOT NULL, role text NOT NULL, content text NOT NULL, created_at timestamp with time zone DEFAULT now());

CREATE TABLE IF NOT EXISTS cliff_status (active_tasks bigint, waiting_tasks bigint, blocked_tasks bigint, done_24h bigint, pulses_24h bigint, actions_24h bigint, new_memories_24h bigint, persona_last_updated timestamp with time zone);

-- SESSION STATE
CREATE TABLE IF NOT EXISTS claude_session_state (id text NOT NULL DEFAULT 'singleton'::text, last_boot timestamp with time zone, tool_manifest jsonb, notion_page_raw jsonb, boot_source text, status text DEFAULT 'ok'::text, error text, updated_at timestamp with time zone DEFAULT now());

CREATE TABLE IF NOT EXISTS claude_boot_log (id bigint NOT NULL DEFAULT nextval('claude_boot_log_id_seq'::regclass), boot_source text, status text, tool_count integer, error text, fired_at timestamp with time zone DEFAULT now());

CREATE TABLE IF NOT EXISTS ai_sessions (id uuid NOT NULL DEFAULT gen_random_uuid(), source text NOT NULL, external_id text NOT NULL, title text, status text, model text, turn_count integer DEFAULT 0, last_summary text, credit_usage numeric DEFAULT 0, external_url text, raw_payload jsonb, source_created_at timestamp with time zone, source_updated_at timestamp with time zone, ingested_at timestamp with time zone DEFAULT now());

-- SYSTEM
CREATE TABLE IF NOT EXISTS system_glossary (term text NOT NULL, definition text NOT NULL, is_forbidden boolean DEFAULT false, notes text, created_at timestamp with time zone DEFAULT now(), updated_at timestamp with time zone DEFAULT now());

CREATE TABLE IF NOT EXISTS credentials_registry (id bigint NOT NULL DEFAULT nextval('credentials_registry_id_seq'::regclass), service text NOT NULL, credential_type text NOT NULL, label text, value text NOT NULL, notes text, created_at timestamp with time zone DEFAULT now(), updated_at timestamp with time zone DEFAULT now(), is_active boolean DEFAULT true);

-- PROJECTS & GOALS
CREATE TABLE IF NOT EXISTS live_projects (id uuid NOT NULL DEFAULT gen_random_uuid(), project_name text NOT NULL, status text DEFAULT 'Building'::text, category text[], priority text DEFAULT 'This Week'::text, next_action text, accountability_notes text, claude_confirmed boolean DEFAULT false, source text DEFAULT 'Claude Scan'::text, dead_since date, escalation_sent boolean DEFAULT false, last_touched date, last_conversation_summary text, blocking_issue text, revenue_type text DEFAULT 'Unknown'::text, created_at timestamp with time zone DEFAULT now(), updated_at timestamp with time zone DEFAULT now());

CREATE TABLE IF NOT EXISTS goals (id uuid NOT NULL DEFAULT gen_random_uuid(), goal_text text NOT NULL, horizon text NOT NULL, period_start date NOT NULL, period_end date NOT NULL, status text DEFAULT 'active'::text, project_id uuid, sort_order integer DEFAULT 0, created_at timestamp with time zone DEFAULT now(), completed_at timestamp with time zone);

CREATE TABLE IF NOT EXISTS key_tasks_today (id uuid, title text, status text, priority text, category text[], next_action text, blocking_issue text, last_touched date, created_at timestamp with time zone);

-- PERSONAL TRACKING
CREATE TABLE IF NOT EXISTS daily_habits (id uuid NOT NULL DEFAULT gen_random_uuid(), habit_date date NOT NULL DEFAULT CURRENT_DATE, habit_key text NOT NULL, habit_label text NOT NULL, subtasks jsonb NOT NULL DEFAULT '[]'::jsonb, completed boolean NOT NULL DEFAULT false, sort_order integer DEFAULT 0, created_at timestamp with time zone DEFAULT now(), updated_at timestamp with time zone DEFAULT now());

CREATE TABLE IF NOT EXISTS daily_journal (id uuid NOT NULL DEFAULT gen_random_uuid(), journal_date date NOT NULL DEFAULT CURRENT_DATE, raw_transcript text NOT NULL, summary text, mood text, themes text[], source text DEFAULT 'web'::text, audio_url text, created_at timestamp with time zone DEFAULT now());

CREATE TABLE IF NOT EXISTS finance_pulse (id uuid NOT NULL DEFAULT gen_random_uuid(), snapshot_date date NOT NULL DEFAULT CURRENT_DATE, net_worth numeric NOT NULL, liquid_cash numeric, crypto numeric, investments numeric, receivables numeric, liabilities numeric, daily_delta numeric, monthly_delta numeric, raw_payload jsonb, synced_at timestamp with time zone DEFAULT now());

-- PROSPECT PLUS (ProspectPlus)
CREATE TABLE IF NOT EXISTS pp_scraper_signals (id bigint NOT NULL DEFAULT nextval('pp_scraper_signals_id_seq'::regclass), captured_at timestamp with time zone DEFAULT now(), source_slug text NOT NULL, raw_address text, raw_owner_name text, raw_phone text, raw_url text, raw_payload jsonb DEFAULT '{}'::jsonb, signal_type text, score integer DEFAULT 0, county text, city text);

CREATE TABLE IF NOT EXISTS pp_buyer_events (id bigint NOT NULL DEFAULT nextval('pp_buyer_events_id_seq'::regclass), created_at timestamp with time zone DEFAULT now(), visitor_id text NOT NULL, session_id text, agent_id text DEFAULT '00000000-0000-0000-0000-000000000001'::text, event_type text NOT NULL, url text, listing_id text, zip5 text, city text, price_cents bigint, form_name text, form_email text, form_phone text, form_address text, sms_opt_in boolean DEFAULT false, call_opt_in boolean DEFAULT false, consent_text text, ip_address text, user_agent text, metadata jsonb DEFAULT '{}'::jsonb);

CREATE TABLE IF NOT EXISTS pp_run_log (id bigint NOT NULL DEFAULT nextval('pp_run_log_id_seq'::regclass), source_slug text NOT NULL, run_at timestamp with time zone NOT NULL DEFAULT now(), signal_count integer NOT NULL DEFAULT 0, status text NOT NULL DEFAULT 'success'::text, error_msg text, run_number integer DEFAULT 0, created_at timestamp with time zone NOT NULL DEFAULT now());

-- GROK
CREATE TABLE IF NOT EXISTS grok_call_logs (id uuid NOT NULL DEFAULT gen_random_uuid(), created_at timestamp with time zone DEFAULT now(), conversation_id text, agent_id text, caller_number text, recipient_number text, call_purpose text, call_outcome text, summary text, next_steps text, followup_required boolean DEFAULT false, raw_payload jsonb);

CREATE TABLE IF NOT EXISTS grok_imessage_queue (id uuid NOT NULL DEFAULT gen_random_uuid(), created_at timestamp with time zone DEFAULT now(), sent_at timestamp with time zone, status text DEFAULT 'pending'::text, recipient text NOT NULL, message text NOT NULL, conversation_id text, error text);

-- MOMENTUM LANDSCAPING
CREATE TABLE IF NOT EXISTS momentum_landscaping (id bigint NOT NULL DEFAULT nextval('momentum_landscaping_id_seq'::regclass), tag text NOT NULL DEFAULT 'momentum_landscaping'::text, category text NOT NULL, subcategory text, title text NOT NULL, content text NOT NULL, status text, source text, created_at timestamp with time zone DEFAULT now(), updated_at timestamp with time zone DEFAULT now());

-- YOUTUBE
CREATE TABLE IF NOT EXISTS youtube_urls (id text, title text, url text, date date, tags text, summary text, collection text, created_at timestamp with time zone);

-- CLIFF OUTBOUND PENDING (view)
CREATE TABLE IF NOT EXISTS cliff_outbound_pending (id bigint, event_type text, entity_id text, entity_name text, event_payload jsonb, detected_at timestamp with time zone);
