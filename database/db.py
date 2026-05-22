"""
database/db.py
==============
SQLite setup for the RGPD Multi-Agent Platform.

- DB file lives at: data/rgpd.db
- Call `init_db()` once on startup to create all tables.
- Call `get_connection()` anywhere to get a thread-safe connection.
"""

import sqlite3
import os
from pathlib import Path

# Always resolve path relative to project root, not the caller's location
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "data" / "rgpd.db"


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with row_factory set so rows behave like dicts."""
    os.makedirs(DB_PATH.parent, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row   # lets us do row["column_name"] instead of row[0]
    conn.execute("PRAGMA journal_mode=WAL")  # better concurrency for FastAPI
    return conn


def _ensure_column(cursor: sqlite3.Cursor, table: str, column: str, definition: str) -> None:
    """Add a column when upgrading an existing local SQLite database."""
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    """
    Create all tables if they don't already exist.
    Safe to call multiple times (idempotent).
    """
    conn = get_connection()
    cursor = conn.cursor()

    # ------------------------------------------------------------------
    # TREATMENTS
    # Every call to /analyser, /risques, /workflow, /gouvernance
    # saves the input treatment + the full agent output as JSON.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS treatments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            id_traitement   TEXT,
            nom_traitement  TEXT,
            systeme         TEXT,
            responsable     TEXT,
            finalite        TEXT,
            base_legale     TEXT,
            donnees_sensibles INTEGER DEFAULT 0,
            score_conformite  REAL,
            niveau_risque   TEXT,
            nb_violations   INTEGER,
            input_json      TEXT,   -- full Traitement payload as JSON string
            output_json     TEXT    -- full agent A output as JSON string
        )
    """)

    # ------------------------------------------------------------------
    # DSARS (Data Subject Access Requests)
    # Every call to /droits saves the request + qualification + deadlines.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dsars (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            id_demande      TEXT    UNIQUE,
            nom_demandeur   TEXT,
            type_droit      TEXT,
            systeme_concerne TEXT,
            date_reception  TEXT,
            date_limite     TEXT,
            qualification   TEXT,   -- valide / abusive / invalide / exception_legale
            statut          TEXT,   -- A traiter / Refus motive / En attente / Cloture
            jours_restants  INTEGER,
            input_json      TEXT,
            output_json     TEXT
        )
    """)

    # ------------------------------------------------------------------
    # INCIDENTS / VIOLATIONS
    # Every incident coming through /risques is logged here (Art. 33.5).
    # This is the mandatory violation register.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS violations (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
            id_incident             TEXT    UNIQUE,
            date_detection          TEXT,
            type_incident           TEXT,
            description             TEXT,
            nb_personnes_affectees  INTEGER DEFAULT 0,
            gravite                 INTEGER DEFAULT 1,
            donnees_sensibles       INTEGER DEFAULT 0,
            qualification           TEXT,   -- 'Violation averee - Risque eleve', etc.
            notifier_cnil           INTEGER DEFAULT 0,
            notifier_personnes      INTEGER DEFAULT 0,
            statut                  TEXT DEFAULT 'Ouvert',  -- Ouvert / Cloture
            notification_envoyee    INTEGER DEFAULT 0,
            input_json              TEXT,
            output_json             TEXT
        )
    """)

    # ------------------------------------------------------------------
    # DPIA DOSSIERS (AIPD)
    # Every call to /generate_aipd_dossier saves the generated dossier.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dpia_dossiers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            nom_traitement  TEXT,
            niveau_risque   TEXT,
            aipd_decision   TEXT,
            dossier_text    TEXT,   -- the LLM-generated markdown dossier
            input_json      TEXT,
            statut          TEXT DEFAULT 'genere'  -- genere / valide / rejete
        )
    """)

    # ------------------------------------------------------------------
    # CONSENTS (Q3 — new feature)
    # Tracks consent status per person/treatment with full audit trail.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS consents (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            id_consent      TEXT    UNIQUE,
            nom_personne    TEXT    NOT NULL,
            email_personne  TEXT,
            id_traitement   TEXT    NOT NULL,
            nom_traitement  TEXT,
            finalite        TEXT,
            base_legale     TEXT    DEFAULT 'consentement',
            statut          TEXT    DEFAULT 'actif',   -- actif / retire / expire
            date_collecte   TEXT,
            date_expiration TEXT,   -- NULL = pas d'expiration
            preuve          TEXT,   -- e.g. "formulaire web signé le 2025-01-01"
            retire_par      TEXT,   -- NULL if not withdrawn
            date_retrait    TEXT    -- NULL if not withdrawn
        )
    """)

    # ------------------------------------------------------------------
    # CNIL NOTIFICATIONS
    # Stores every generated CNIL notification dossier.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS cnil_notifications (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            notification_id TEXT    UNIQUE,
            id_incident     TEXT,
            responsable     TEXT,
            systeme         TEXT,
            notifier_autorite INTEGER DEFAULT 1,
            notifier_personnes INTEGER DEFAULT 0,
            notification_text TEXT,  -- LLM-generated formal document
            statut          TEXT DEFAULT 'pret_a_soumettre',  -- pret / soumis / cloture
            input_json      TEXT,
            output_json     TEXT
        )
    """)

    # ------------------------------------------------------------------
    # RGPD REGISTER (Article 30 - operationalized)
    # One record per analyzed treatment snapshot.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS rgpd_register (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            id_traitement       TEXT,
            nom_traitement      TEXT,
            systeme             TEXT,
            responsable         TEXT,
            finalite            TEXT,
            base_legale         TEXT,
            categories_donnees  TEXT,   -- JSON array
            personnes_concernees TEXT,  -- JSON array
            destinataires       TEXT,   -- JSON array
            duree_conservation  TEXT,
            mesures_securite    TEXT,   -- JSON array
            risk_level          TEXT,
            missing_info        INTEGER DEFAULT 0,
            last_checked        TEXT,
            source_analysis_id  INTEGER,
            source_json         TEXT
        )
    """)

    # ------------------------------------------------------------------
    # CORRECTIVE ACTIONS (operational workflow)
    # Generated from Q2 gaps and tracked by owner/status/deadline.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS actions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            title               TEXT    NOT NULL,
            description         TEXT,
            severity            TEXT,   -- Critique / Eleve / Moyen / Faible
            owner               TEXT,
            due_date            TEXT,
            status              TEXT    DEFAULT 'A faire', -- A faire / En cours / Cloturee
            linked_treatment_id TEXT,
            linked_register_id  INTEGER,
            source_regle_id     TEXT,
            recommendation      TEXT,
            metadata_json       TEXT
        )
    """)
    _ensure_column(cursor, "actions", "proof_status", "TEXT DEFAULT 'Aucune preuve'")
    _ensure_column(cursor, "actions", "proof_summary", "TEXT")
    _ensure_column(cursor, "actions", "closed_at", "TEXT")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS action_proofs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            proof_id            TEXT    UNIQUE,
            action_id           INTEGER NOT NULL,
            proof_type          TEXT,
            title               TEXT,
            description         TEXT,
            file_name           TEXT,
            file_path           TEXT,
            mime_type           TEXT,
            file_size           INTEGER,
            checksum            TEXT,
            submitted_by        TEXT,
            verification_status TEXT    DEFAULT 'A verifier',
            verification_score  INTEGER DEFAULT 0,
            verification_notes  TEXT,
            verified_by         TEXT,
            verified_at         TEXT,
            metadata_json       TEXT,
            FOREIGN KEY(action_id) REFERENCES actions(id)
        )
    """)

    # ------------------------------------------------------------------
    # UNSTRUCTURED SCANS
    # Stores evidence from uploaded file scans used by Q1.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS unstructured_scans (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            filename            TEXT,
            file_type           TEXT,
            extraction_method   TEXT,
            nb_findings         INTEGER DEFAULT 0,
            criticite_globale   TEXT,
            linked_treatment_id TEXT,
            systeme             TEXT,
            module              TEXT,
            source_analysis_id  INTEGER,
            rgpd_impact_json    TEXT,
            findings_json       TEXT,
            result_json         TEXT
        )
    """)

    # ------------------------------------------------------------------
    # CENTRAL RGPD INVENTORY
    # Shared backbone used across agents for Q1..Q8.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory_treatments (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            inventory_key       TEXT    UNIQUE,
            id_traitement       TEXT,
            nom_traitement      TEXT,
            systeme             TEXT,
            module              TEXT,
            responsable         TEXT,
            finalite            TEXT,
            base_legale         TEXT,
            personnes_concernees TEXT,  -- JSON array
            categories_donnees  TEXT,   -- JSON array
            duree_conservation  TEXT,
            risk_level          TEXT,
            unstructured_files_count INTEGER DEFAULT 0,
            unstructured_findings_count INTEGER DEFAULT 0,
            source_analysis_id  INTEGER,
            register_id         INTEGER,
            snapshot_json       TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory_fields (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            inventory_treatment_id INTEGER NOT NULL,
            field_name          TEXT    NOT NULL,
            source_kind         TEXT    NOT NULL,   -- structured / unstructured
            data_type           TEXT,
            criticite           TEXT,
            is_sensitive        INTEGER DEFAULT 0,
            origin_module       TEXT,
            evidence_count      INTEGER DEFAULT 0,
            metadata_json       TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory_flows (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at              TEXT    NOT NULL DEFAULT (datetime('now')),
            inventory_treatment_id  INTEGER NOT NULL,
            etape                   TEXT    NOT NULL,   -- collecte / utilisation / stockage / partage / archivage
            source                  TEXT,
            cible                   TEXT,
            flux_type               TEXT,              -- interne / externe
            criticite               TEXT,
            donnees_json            TEXT,
            metadata_json           TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory_alerts (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at              TEXT    NOT NULL DEFAULT (datetime('now')),
            inventory_treatment_id  INTEGER NOT NULL,
            alert_code              TEXT    NOT NULL,
            titre                   TEXT,
            message                 TEXT,
            severity                TEXT,
            statut                  TEXT    DEFAULT 'ouverte',
            metadata_json           TEXT
        )
    """)

    # ------------------------------------------------------------------
    # APPLICATION USERS / SESSIONS / AUDIT
    # Lightweight RBAC layer for the DPO platform itself. This is separate
    # from QALITAS/GMAO credentials, which stay connector-only.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_users (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            username            TEXT    NOT NULL UNIQUE,
            password_hash       TEXT    NOT NULL,
            full_name           TEXT,
            email               TEXT,
            role                TEXT    NOT NULL DEFAULT 'auditeur',
            is_active           INTEGER DEFAULT 1,
            last_login_at       TEXT,
            metadata_json       TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_sessions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            token_hash          TEXT    NOT NULL UNIQUE,
            user_id             INTEGER NOT NULL,
            expires_at          TEXT    NOT NULL,
            revoked_at          TEXT,
            user_agent          TEXT,
            metadata_json       TEXT,
            FOREIGN KEY(user_id) REFERENCES app_users(id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_events (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            event_id            TEXT    UNIQUE,
            actor_user_id       INTEGER,
            actor_username      TEXT,
            actor_role          TEXT,
            action              TEXT    NOT NULL,
            target_type         TEXT,
            target_id           TEXT,
            details_json        TEXT
        )
    """)

    # ------------------------------------------------------------------
    # DPO VALIDATIONS
    # Final human decisions on top of agent recommendations.
    # This audit trail makes validations traceable and opposable.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dpo_validations (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            validation_id       TEXT    UNIQUE,
            target_type         TEXT    NOT NULL,
            target_id           TEXT,
            target_label        TEXT,
            decision            TEXT    NOT NULL,
            validator           TEXT,
            role                TEXT,
            justification       TEXT,
            source_system       TEXT,
            source_module       TEXT,
            evidence_json       TEXT,
            metadata_json       TEXT
        )
    """)

    # ------------------------------------------------------------------
    # DPO FEEDBACK MEMORY
    # Reusable learning layer built from final DPO decisions. Agents can
    # retrieve similar past decisions without replacing rule checks or the
    # human validation step.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dpo_feedback_memory (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at              TEXT    NOT NULL DEFAULT (datetime('now')),
            memory_id               TEXT    UNIQUE,
            validation_id           TEXT,
            target_type             TEXT    NOT NULL,
            target_id               TEXT,
            target_label            TEXT,
            decision                TEXT,
            final_value             TEXT,
            source_system           TEXT,
            source_module           TEXT,
            context_signature       TEXT,
            agent_suggestion_json   TEXT,
            dpo_decision_json       TEXT,
            justification           TEXT,
            reusable                INTEGER DEFAULT 1,
            confidence              REAL    DEFAULT 0.75,
            tags_json               TEXT,
            metadata_json           TEXT
        )
    """)

    # ------------------------------------------------------------------
    # REAL-TIME SYNC SNAPSHOTS
    # Stores source/module fingerprints so the platform can detect changes
    # between QALITAS/GMAO pulls without re-processing everything blindly.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS realtime_snapshots (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at              TEXT    NOT NULL DEFAULT (datetime('now')),
            source_system           TEXT    NOT NULL,
            module                  TEXT    NOT NULL,
            module_label            TEXT,
            records_count           INTEGER DEFAULT 0,
            records_hash            TEXT,
            fields_hash             TEXT,
            personal_fields_count   INTEGER DEFAULT 0,
            sensitive_fields_count  INTEGER DEFAULT 0,
            physical_persons_count  INTEGER DEFAULT 0,
            snapshot_json           TEXT,
            UNIQUE(source_system, module)
        )
    """)

    # ------------------------------------------------------------------
    # REAL-TIME EVENTS
    # Audit trail of detected changes: new records, schema changes,
    # new personal fields, sync errors, etc.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS realtime_events (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at              TEXT    NOT NULL DEFAULT (datetime('now')),
            event_id                TEXT    UNIQUE,
            source_system           TEXT    NOT NULL,
            module                  TEXT,
            module_label            TEXT,
            severity                TEXT    DEFAULT 'info',
            event_type              TEXT    NOT NULL,
            title                   TEXT,
            message                 TEXT,
            old_count               INTEGER DEFAULT 0,
            new_count               INTEGER DEFAULT 0,
            delta_count             INTEGER DEFAULT 0,
            status                  TEXT    DEFAULT 'open',
            metadata_json           TEXT
        )
    """)

    # ------------------------------------------------------------------
    # GOVERNANCE SNAPSHOTS
    # Stores Agent D consolidated governance state over time for trends.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS governance_snapshots (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at              TEXT    NOT NULL DEFAULT (datetime('now')),
            id_traitement           TEXT,
            nom_traitement          TEXT,
            systeme                 TEXT,
            module                  TEXT,
            score_maturite_rgpd     INTEGER DEFAULT 0,
            niveau_maturite         TEXT,
            violations_count        INTEGER DEFAULT 0,
            risk_reviews_count      INTEGER DEFAULT 0,
            incident_reviews_count  INTEGER DEFAULT 0,
            dsars_open_count        INTEGER DEFAULT 0,
            actions_open_count      INTEGER DEFAULT 0,
            critical_alerts_count   INTEGER DEFAULT 0,
            snapshot_json           TEXT
        )
    """)

    # ------------------------------------------------------------------
    # DSAR MAILBOX SOURCES
    # Configured email inboxes that Agent C can use to read DSAR messages.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dsar_mailboxes (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at              TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at              TEXT    NOT NULL DEFAULT (datetime('now')),
            email_address           TEXT    NOT NULL UNIQUE,
            provider                TEXT    NOT NULL,   -- microsoft365 / gmail / imap
            label                   TEXT,
            is_active               INTEGER DEFAULT 1,
            access_status           TEXT    DEFAULT 'configured',
            last_sync_at            TEXT,
            sample_message          TEXT,
            metadata_json           TEXT
        )
    """)

    # ------------------------------------------------------------------
    # DSAR EXECUTIONS
    # Safe execution/audit trail for Q5 actions.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dsar_executions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            id_demande          TEXT    NOT NULL,
            type_droit          TEXT,
            executor            TEXT,
            mode_execution      TEXT,   -- safe_log / manual / approved
            decision            TEXT,   -- execute / blocked / review_required
            statut              TEXT,   -- planifie / journalise / bloque / cloture
            legal_basis_note    TEXT,
            targets_count       INTEGER DEFAULT 0,
            execution_json      TEXT
        )
    """)

    # ------------------------------------------------------------------
    # RISK REVIEWS (Q4)
    # Stores each Agent B risk/AIPD review snapshot with evidence.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS risk_reviews (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            review_id           TEXT    UNIQUE,
            id_traitement       TEXT,
            nom_traitement      TEXT,
            systeme             TEXT,
            module              TEXT,
            aipd_requise        INTEGER DEFAULT 0,
            aipd_decision       TEXT,
            nombre_risques      INTEGER DEFAULT 0,
            risques_critiques   INTEGER DEFAULT 0,
            risques_eleves      INTEGER DEFAULT 0,
            source_analysis_id  INTEGER,
            evidence_json       TEXT,
            output_json         TEXT
        )
    """)

    # ------------------------------------------------------------------
    # INCIDENT REVIEWS (Q6)
    # Keeps Agent B incident evidence separate from the Art.33 register.
    # ------------------------------------------------------------------
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS incident_reviews (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
            review_id           TEXT    UNIQUE,
            id_incident         TEXT,
            id_traitement       TEXT,
            nom_traitement      TEXT,
            systeme             TEXT,
            module              TEXT,
            qualification       TEXT,
            notifier_cnil       INTEGER DEFAULT 0,
            notifier_personnes  INTEGER DEFAULT 0,
            source_analysis_id  INTEGER,
            evidence_json       TEXT,
            output_json         TEXT
        )
    """)

    conn.commit()
    conn.close()
    print(f"[DB] Database initialized at: {ascii(str(DB_PATH))}")
