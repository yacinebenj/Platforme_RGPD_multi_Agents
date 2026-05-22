# Roadmap de Completion du Cahier des Charges

Ce document transforme le cahier des charges RGPD en plan d'implementation concret, aligne sur l'architecture actuelle du projet.

## 1. Repartition des agents

- `Agent A` : `RGPD-Q1 + RGPD-Q2 + RGPD-Q3`
- `Agent B` : `RGPD-Q4 + RGPD-Q6`
- `Agent C` : `RGPD-Q5`
- `Agent D` : `RGPD-Q7 + RGPD-Q8`

## 2. Etat actuel du socle technique

### Orchestration
- Orchestrateur LangGraph lineaire dans [workflow.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/orchestrator/workflow.py)
- Flux actuel : `A -> B -> C -> D`

### API / UI
- API FastAPI dans [main.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/api/main.py)
- Interface unique dans [interface.html](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/api/interface.html)

### IA / LLM / recherche
- Groq pour l'inference et la generation
- FAISS + embeddings via [rag_builder.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/llm/rag_builder.py)

### Persistance
- SQLite dans [db.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/database/db.py)
- CRUD dans [crud.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/database/crud.py)

### Connecteurs
- QALITAS dans [connector.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/qalitas/connector.py)
- GMAO PRO : absent ou non structure comme connecteur dedie

## 3. Diagnostic par bloc du cahier

### Q1 - Cartographie intelligente des donnees
Etat : `partiel`

Deja present :
- detection de champs personnels/sensibles QALITAS
- cartographie simple par traitement
- support unstructured partiel dans l'UI

Manque critique :
- modele canonique des actifs et flux de donnees
- support GMAO PRO natif
- cartographie dynamique `Donnee <-> Processus <-> Module <-> Utilisateur <-> Outil`
- registre Article 30 complet et exportable
- detection robuste des pieces jointes, PDF, images, audio
- alertes sur nouveaux champs, donnees orphelines, sur-conservation

### Q2 - Analyse de conformite
Etat : `avance mais incomplet`

Deja present :
- moteur de regles
- scoring
- priorisation des ecarts
- integration RAG juridique

Manque critique :
- mapping explicite vers RGPD + ISO 27001 + ISO 9001 + ISO 45001
- analyse continue sur inventaire vivant
- scoring par site, processus, systeme
- declenchement metier plus structure vers actions QALITAS

### Q3 - Bases legales & consentements
Etat : `partiel`

Deja present :
- attribution simple de base legale
- endpoints de consentement dans l'API

Manque critique :
- registre de preuves de consentement opposables
- horodatage, version, finalite, retrait, historique
- controle d'incoherence finalite/base legale/traitement
- blocage reel des traitements non conformes

### Q4 - Risques & AIPD
Etat : `partiel`

Deja present :
- evaluation des risques
- declenchement logique AIPD

Manque critique :
- matrice de risque persistante
- scenarios de risques types par systeme/module
- dossier AIPD complet CNIL-ready base sur inventaire reel
- boucle de suivi des mesures residuelles

### Q5 - DSAR
Etat : `moyen`

Deja present :
- qualification de demande
- generation de reponse

Manque critique :
- recherche transversale reelle dans tous les jeux de donnees
- recherche unstructured
- export d'acces / portabilite exploitable
- workflow delai 30 jours avec alertes et prolongations

### Q6 - Violations & incidents
Etat : `partiel`

Deja present :
- qualification incident
- logique de notification
- generation de dossier CNIL

Manque critique :
- detection proactive multi-sources
- journal des evenements horodate
- typologie confidentiality / integrity / availability
- chainage automatique vers Q4/Q5/Q7/Q8

### Q7 - Gouvernance DPO
Etat : `bon socle`

Deja present :
- consolidation multi-agents
- score de maturite
- rapport DPO

Manque critique :
- cockpit temps reel multisites
- preuves centralisees
- filtres processus/systeme/site
- alignement explicite ISO 27701

### Q8 - Amelioration continue & veille
Etat : `embryonnaire`

Deja present :
- quelques sorties de recommandations et veille statique

Manque critique :
- base de connaissance de retour d'experience
- tendances, recurrence, ROI, maturite dans le temps
- veille reglementaire automatique et impact analysis
- boucle PDCA RGPD reliee aux actions

## 4. Priorite strategique

La priorite suivante doit etre :

## Epic 1 - Completer Q1 comme colonne vertebrale du systeme

Pourquoi :
- Q1 alimente directement Q2, Q3, Q4, Q5, Q6, Q7 et Q8
- sans inventaire/flux solide, le reste reste partiellement theorique
- c'est aussi la condition pour integrer GMAO PRO proprement

## 5. Plan d'implementation par epic

## Epic 1 - Q1 Backbone + GMAO PRO

### Objectif
Construire un modele vivant des traitements, donnees, flux, pieces jointes et preuves pour QALITAS et GMAO PRO.

### Fichiers a creer
- [inventory_models.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/database/inventory_models.py)
- [inventory_service.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/database/inventory_service.py)
- [gmao_connector.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/gmao/gmao_connector.py)
- [unstructured_scanner.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/agents/unstructured_scanner.py)
- [flow_mapper.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/agents/flow_mapper.py)
- [register_exporter.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/llm/register_exporter.py)

### Fichiers a modifier
- [agent_a.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/agents/agent_a.py)
- [main.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/api/main.py)
- [db.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/database/db.py)
- [crud.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/database/crud.py)
- [interface.html](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/api/interface.html)

### Sous-lots

#### 1.1 Modele canonique d'inventaire
Tables/objets a introduire :
- `systems`
- `modules`
- `processes`
- `data_assets`
- `data_fields`
- `data_subject_types`
- `data_flows`
- `attachments_inventory`
- `retention_rules`
- `security_controls`
- `evidence_items`

#### 1.2 Connecteur GMAO PRO
Minimum viable connector :
- statut
- modules
- fetch structured data
- normalisation vers le modele canonique

#### 1.3 Scan unstructured
Support cible :
- PDF
- images
- metadata pieces jointes
- audio/video en mode phase 1 : metadata + placeholders

#### 1.4 Cartographie dynamique des flux
Produire :
- collecte
- usage
- stockage
- partage
- archivage/suppression

#### 1.5 Registre Article 30 dynamique
Sorties :
- registre par traitement
- registre consolide par systeme
- export PDF / Excel / JSON

#### 1.6 Alertes Q1
Detecter :
- nouveaux champs non declares
- donnees sans responsable
- donnees sensibles sans mesures renforcees
- conservation depassee
- donnees sans finalite justifiee

### Owner principal
- `Agent A`

## Epic 2 - Q5 DSAR transverse reel

### Pourquoi juste apres Q1
Une vraie gestion DSAR depend d'une recherche transverse structuree + non structuree.

### Fichiers a creer
- [dsar_search.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/agents/dsar_search.py)
- [dsar_export.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/llm/dsar_export.py)

### Fichiers a modifier
- [agent_c.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/agents/agent_c.py)
- [main.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/api/main.py)
- [crud.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/database/crud.py)
- [interface.html](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/api/interface.html)

### Capacites a livrer
- recherche exhaustive sur QALITAS + GMAO PRO
- recherche dans pieces jointes inventoriees
- workflow legal 30 jours
- alertes `J+15`, `J+25`
- gestion des refus motives et exceptions
- export acces / portabilite

### Owner principal
- `Agent C`

## Epic 3 - Q4 + Q6 operationnels et relies

### Fichiers a creer
- [risk_library.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/agents/risk_library.py)
- [incident_engine.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/agents/incident_engine.py)
- [aipd_builder.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/llm/aipd_builder.py)

### Fichiers a modifier
- [agent_b.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/agents/agent_b.py)
- [main.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/api/main.py)
- [db.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/database/db.py)
- [crud.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/database/crud.py)

### Capacites a livrer
- librairie de scenarios de risques par type de traitement
- dossiers AIPD complets et persistants
- journal d'incident horodate
- logique CIA : confidentialite / integrite / disponibilite
- reevaluation automatique du risque residuel
- chainage incident -> AIPD -> actions -> cockpit

### Owner principal
- `Agent B`

## Epic 4 - Q2 + Q3 de niveau cahier

### Fichiers a creer
- [compliance_mapping.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/rules/compliance_mapping.py)
- [consent_ledger.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/database/consent_ledger.py)

### Fichiers a modifier
- [agent_a.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/agents/agent_a.py)
- [rule_engine.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/rules/rule_engine.py)
- [crud.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/database/crud.py)
- [main.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/api/main.py)

### Capacites a livrer
- mapping explicite RGPD + CNIL/EDPB + ISO
- score par site, processus, systeme
- registre opposable des bases legales
- preuves de consentement opposables
- versioning, retrait, historique
- incoherences finalite/base legale/traitement

### Owner principal
- `Agent A`

## Epic 5 - Q7 cockpit DPO temps reel

### Fichiers a creer
- [dashboard_service.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/database/dashboard_service.py)
- [evidence_center.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/database/evidence_center.py)

### Fichiers a modifier
- [agent_d.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/agents/agent_d.py)
- [interface.html](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/api/interface.html)
- [main.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/api/main.py)

### Capacites a livrer
- cockpit multisites
- KPIs consolides temps reel
- top risques / DSAR / incidents / actions
- filtres par systeme, module, site, processus
- coffre de preuves RGPD

### Owner principal
- `Agent D`

## Epic 6 - Q8 veille + PDCA RGPD

### Fichiers a creer
- [regulatory_watch.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/agents/regulatory_watch.py)
- [continuous_improvement.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/agents/continuous_improvement.py)

### Fichiers a modifier
- [agent_d.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/agents/agent_d.py)
- [main.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/api/main.py)
- [crud.py](/C:/Users/yassi/OneDrive/Документы/rgpd-multi-agent/database/crud.py)

### Capacites a livrer
- base de retour d'experience
- tendances incidents / DSAR / ecarts
- indice de maturite dans le temps
- veille CNIL / EDPB / jurisprudence / IA Act / NIS2
- analyse d'impact sur traitements existants
- boucle PDCA avec creation automatique d'actions

### Owner principal
- `Agent D`

## 6. Ordre de realisation recommande

1. `Epic 1` - Q1 Backbone + GMAO PRO
2. `Epic 2` - Q5 DSAR transverse reel
3. `Epic 3` - Q4 + Q6 operationnels relies
4. `Epic 4` - Q2 + Q3 niveau cahier
5. `Epic 5` - Q7 cockpit DPO temps reel
6. `Epic 6` - Q8 veille + PDCA RGPD

## 7. Sprints concrets recommandes

## Sprint 1
- creer le modele canonique d'inventaire
- ajouter les tables SQLite associees
- brancher `Agent A` sur ce modele

## Sprint 2
- ajouter connecteur GMAO PRO
- normaliser ses sorties
- stocker les actifs et flux

## Sprint 3
- scanner les pieces jointes et construire l'inventaire unstructured
- exposer la cartographie dynamique dans l'API et l'UI

## Sprint 4
- rendre `Agent C` reellement transverse via l'inventaire
- produire exports acces / portabilite

## Sprint 5
- renforcer `Agent B` avec matrice de risques persistante et journal des incidents

## Sprint 6
- cockpit `Agent D`
- veille `Q8`
- PDCA et creation automatique d'actions

## 8. Definition of Done par agent

### Agent A
- Q1 cartographie structurée + non structurée
- registre Article 30 dynamique
- Q2 score de conformite multi-systemes
- Q3 base legale / consentement avec preuves

### Agent B
- Q4 AIPD complete et traçable
- Q6 gestion de violation avec dossier, delais, decisions

### Agent C
- recherche transverse exhaustive
- workflow DSAR complet avec delais, decisions, exports

### Agent D
- cockpit DPO consolidé
- preuves opposables centralisées
- veille et amelioration continue avec tendance et impact

## 9. Frameworks et briques techniques deja en place

- `FastAPI` : API backend
- `Pydantic` : schemas de requetes/reponses
- `LangGraph` : orchestration multi-agents
- `SQLite` : persistance locale
- `Groq` : inference/generation LLM
- `FAISS` : recherche vectorielle RAG
- `HTML/CSS/JavaScript` : interface actuelle
- `jsPDF` + `jspdf-autotable` : exports PDF front-end

## 10. Frameworks / briques a ajouter selon les epics

- `pandas` : export Excel / CSV, tableaux consolides
- `openpyxl` : generation Excel RGPD
- `pdfplumber` : extraction PDF plus robuste
- `pytesseract` : OCR images/PDF scannes
- `Pillow` : pre-traitement image OCR
- `python-docx` : lecture pieces jointes DOCX si necessaire
- `networkx` ou structure maison : representation de flux de donnees
- `plotly` ou rendu JS dedie : cartographie et cockpit dynamiques
- `httpx` si futur connecteur GMAO PRO/API externes plus propres

## 11. Decision immediate

La prochaine implementation a lancer doit etre :

## `Epic 1 - Q1 Backbone + GMAO PRO`

Premier sous-lot a coder :
- schema d'inventaire canonique
- tables SQLite associees
- service de normalisation QALITAS
- squelette connecteur GMAO PRO

Apres cela, le reste du cahier devient beaucoup plus faisable proprement.
