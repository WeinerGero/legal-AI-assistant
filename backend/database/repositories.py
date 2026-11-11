"""Репозитории, реализующие порты ядра поверх PostgreSQL (ARCHITECTURE.md §4).

Таблицы корпуса: ``corpora``, ``documents``, ``document_versions``, ``clauses``,
``chunks``, ``chunk_clause_links``, ``chunk_embeddings``, ``document_relations``,
``index_generations``, ``index_generation_chunks``.

Таблицы пользователей и диалогов: ``users``, ``auth_sessions``, ``conversations``,
``messages``, ``dialog_context``, ``dialog_summaries``, ``pipeline_runs``,
``answers``, ``answer_blocks``, ``answer_claims``, ``answer_sources``,
``claim_evidence``, ``jobs``, ``audit_events``.
"""
