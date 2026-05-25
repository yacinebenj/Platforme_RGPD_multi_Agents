# RGPD Multi-Agent — Plateforme de mise en conformité RGPD / Loi 2004-63

Plateforme multi-agent pour l'automatisation de la conformité RGPD (Règlement Général sur la Protection des Données)
et de la Loi Tunisienne 2004-63. Destinée aux DPO et responsables conformité pour cartographier, analyser et piloter
les traitements de données personnelles au sein d'un système d'information multi-source.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Interface Web (HTML/JS)               │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP / SSE
┌────────────────────────▼────────────────────────────────┐
│                    API FastAPI (api/main.py)              │
└────┬──────┬──────┬──────┬──────┬──────┬──────┬──────────┘
     │      │      │      │      │      │      │
┌────▼──┐┌──▼───┐┌──▼───┐┌──▼───┐┌──▼──┐┌──▼───┐┌──▼────┐
│Agent A││Agent B││Agent C││Agent D││ ML  ││ Moteur││Orches-│
│Carto &││Risques││ DSAR ││Gouvern││Clas- ││Règles ││trateur│
│Conform││      ││      ││ance   ││sif.  ││RGPD   ││       │
└───┬───┘└──┬────┘└──┬───┘└──┬───┘└──┬───┘└──┬────┘└──┬───┘
    │       │       │       │       │       │       │
┌───▼───────▼───────▼───────▼───────▼───────▼───────▼───┐
│              Connecteurs source                        │
│  ┌─────────┐  ┌─────────┐  ┌──────────────────┐       │
│  │ QALITAS │  │  GMAO   │  │ Email (IMAP/M365) │       │
│  └─────────┘  └─────────┘  └──────────────────┘       │
└───────────────────────────────────────────────────────┘
```

## Agents

| Agent | Rôle |
|---|---|
| **Agent A** — Data Mapper & Compliance | Cartographie les données personnelles, évalue la conformité (Q1–Q3), détecte les champs dans les enregistrements sources |
| **Agent B** — Risk Assessor | Analyse les risques, évalue la gravité, propose des traitements correctifs |
| **Agent C** — DSAR Manager | Gère les demandes d'exercice des droits (accès, rectification, effacement, opposition, portabilité) |
| **Agent D** — Governance | Produit des rapports de gouvernance, suit les indicateurs, génère les synthèses DPO |

## Fonctionnalités

- **Cartographie des traitements** — Analyse multi-source (QALITAS, GMAO) des données personnelles
- **Détection données non structurées** — Scan PDF (pdfplumber), images (Tesseract OCR), audio (Whisper)
- **Moteur de règles RGPD** — Évalue la conformité selon la Loi 2004-63 et le RGPD
- **Assistant DPO (RAG + LLM)** — Réponses contextuelles sur le périmètre de la plateforme
- **Gestion des DSAR** — Pipeline complet de réception, classification et réponse aux demandes de droits
- **Gestion des incidents** — Notification 72h, évaluation de gravité, plan d'actions correctives
- **AIPD** — Analyse d'Impact sur la Protection des Données intégrée
- **Registre des traitements** — Génération automatique du registre RGPD / Loi 2004-63
- **Preuves documentaires** — Attachement, validation et cycle de vie des preuves de conformité
- **SSE Temps réel** — Surveillance continue des sources avec événements push

## Prérequis

- Python 3.11+
- Pip / venv

## Installation

```bash
# 1. Cloner le dépôt
git clone <votre-repo>
cd rgpd-multi-agent

# 2. Créer l'environnement virtuel
python -m venv venv
venv\Scripts\activate   # Windows
# source venv/bin/activate  # Linux/Mac

# 3. Installer les dépendances
pip install fastapi uvicorn python-dotenv pydantic pdfplumber pillow
pip install groq openai sentence-transformers torch transformers
# Optionnel (détection avancée) :
pip install pytesseract openai-whisper spacy
python -m spacy download fr_core_news_sm

# 4. Configurer les accès
cp .env_exemple key.env
# Éditer key.env avec vos identifiants QALITAS / GMAO / GROQ
```

## Configuration

Copier `.env_exemple` vers `key.env` et renseigner :

| Variable | Description |
|---|---|
| `GROQ_API_KEY` | Clé API Groq (LLM pour l'inférence Agent A) |
| `QALITAS_URL` / `USER` / `PASSWORD` | Identifiants QALITAS WEB |
| `QALITAS_COMPANY` / `GROUP` / `SITE` | Contexte de connexion QALITAS |
| `GMAO_URL` / `USER` / `PASSWORD` | Identifiants GMAO PRO WEB |
| `GMAO_COMPANY` / `SITE` | Contexte de connexion GMAO |
| `*_TIMEOUT` | Timeouts (login, fetch, retry) en secondes |

## Lancement

```bash
uvicorn api.main:app --reload --port 8000
```

Ouvrir `http://localhost:8000` dans un navigateur pour l'interface web.

## Structure du projet

```
├── agents/          # Agents RGPD (A, B, C, D) + détection non structurée
├── api/             # API FastAPI + interface web
├── database/        # Couche persistance (SQLite, CRUD, seed)
├── gmao/            # Connecteur GMAO PRO WEB
├── integrations/    # Connecteurs email (IMAP, M365)
├── llm/             # RAG, assistant DPO, génération rapports
├── ml/              # Classifieurs (DSAR, champ, intention)
├── orchestrator/    # Orchestrateur du pipeline multi-agent
├── qalitas/         # Connecteur QALITAS WEB
├── realtime/        # Surveillance temps réel
├── rules/           # Moteur de règles RGPD / Loi 2004-63
├── security/        # Contrôle d'accès basé sur les rôles
├── tools/           # Utilitaires (découverte d'endpoints)
└── uploads/         # Textes de loi de référence (RGPD, Loi 2004-63)
```
