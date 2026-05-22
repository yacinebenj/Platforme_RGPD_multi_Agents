# Présentation de Suivi Projet
## Plateforme d'Agents IA RGPD pour QALITAS et GMAO PRO

### Slide 1 — Titre
**Plateforme d'Agents IA RGPD**  
**Projet TIM Techniques industrielles et Management**

- Etat d'avancement du projet
- Situation actuelle
- Réalisations techniques
- Technologies utilisées
- Prochaines étapes

---

### Slide 2 — Objet du projet
**But du projet**

- Construire une plateforme RGPD opérationnelle et automatisée
- Couvrir les besoins RGPD sur QALITAS WEB
- Préparer l'extension vers GMAO PRO WEB
- Produire une logique de pilotage, de preuve et de suivi continu

**Ce que la plateforme doit faire**

- Cartographier les données personnelles
- Mesurer la conformité réglementaire
- Evaluer les risques
- Gérer les demandes DSAR
- Piloter la gouvernance DPO
- Alimenter l'amélioration continue

---

### Slide 3 — Architecture actuelle
**Architecture mise en place**

- Backend API central
- Orchestration multi-agents
- Connecteur QALITAS
- Base de données locale de traçabilité
- Interface web de démonstration et de pilotage

**Technologies utilisées**

- `FastAPI`
  Pourquoi : API rapide à développer, claire, adaptée aux endpoints métiers
- `Pydantic`
  Pourquoi : validation propre des entrées et structuration des requêtes
- `LangGraph`
  Pourquoi : orchestration simple du workflow multi-agents A → B → C → D
- `SQLite`
  Pourquoi : stockage local léger, simple à maintenir, suffisant pour cette phase
- `HTML / CSS / JavaScript`
  Pourquoi : interface légère, sans dépendance frontend lourde

---

### Slide 4 — Répartition fonctionnelle des agents
**Agent A — Q1 + Q2 + Q3**

- Cartographie des données
- Analyse de conformité
- Base légale et consentements

**Agent B — Q4 + Q6**

- Analyse des risques
- Déclenchement AIPD
- Gestion des incidents et violations

**Agent C — Q5**

- Gestion des droits des personnes
- Recherche transversale
- Constitution du dossier DSAR

**Agent D — Q7 + Q8**

- Gouvernance DPO
- Cockpit de pilotage
- Amélioration continue

---

### Slide 5 — Ce qui fonctionne aujourd'hui
**Socle technique déjà opérationnel**

- API principale active
- Workflow LangGraph actif
- Connecteur QALITAS déjà branché
- Plusieurs modules QALITAS déjà testés
- Interface de démonstration fonctionnelle
- Historique de plusieurs objets déjà sauvegardé en base

**Concrètement, le projet n'est plus au stade d'idée**

- Il exécute des analyses
- Il conserve les résultats
- Il fournit des vues de suivi
- Il produit déjà des sorties exploitables

---

### Slide 6 — Agent A : état actuel
**Ce qui a été réalisé**

- Détection des champs personnels et sensibles sur QALITAS
- Construction automatique d'un traitement RGPD à partir des données QALITAS
- Génération d'un registre RGPD de type Article 30
- Détection structurée des écarts de conformité
- Evaluation de la base légale

**Technologies utilisées**

- `Python`
  Pourquoi : logique métier claire et facilement maintenable
- `FastAPI + Pydantic`
  Pourquoi : exposer les analyses sous forme d'API propres
- `Groq`
  Pourquoi : aider à l'inférence et à certaines sorties de synthèse
- `FAISS`
  Pourquoi : support RAG pour enrichir certaines analyses et rapports

**Etat actuel**

- Agent A est l'agent le plus avancé sur la logique métier
- Il alimente déjà les autres agents
- Il reste encore à renforcer la cartographie complète des flux et la couverture GMAO PRO

---

### Slide 7 — Détection non structurée
**Travail réalisé**

- Reconstruction du module de scan non structuré
- Détection dans les fichiers PDF et images
- Identification de données telles que :
  - nom et prénom
  - email
  - téléphone
  - adresse
  - date de naissance
  - IBAN
  - numéro d'identité
  - informations médicales

**Technologies utilisées**

- `pdfplumber`
  Pourquoi : extraction de texte depuis les PDF
- `pytesseract`
  Pourquoi : OCR sur les images et documents scannés
- `Pillow`
  Pourquoi : lecture et prétraitement des images
- `Regex Python`
  Pourquoi : détection directe de motifs sensibles

**Etat actuel**

- Le scan non structuré fonctionne
- Les résultats sont enregistrés en base
- Cette brique renforce fortement Q1

---

### Slide 8 — Inventaire RGPD central
**Ce qui a été ajouté**

- Création d'un inventaire RGPD central en base
- Enregistrement des traitements analysés
- Enregistrement des champs détectés
- Lien entre données structurées et non structurées
- Début d'une logique de mémoire partagée entre agents

**Technologies utilisées**

- `SQLite`
  Pourquoi : centraliser l'historique et l'inventaire sans complexifier l'architecture
- `CRUD Python maison`
  Pourquoi : garder un code simple et lisible, sans ORM complexe

**Pourquoi c'est important**

- Cet inventaire devient la base commune du système
- Il permettra de mieux relier cartographie, risques, DSAR et gouvernance

---

### Slide 9 — Agent B : état actuel
**Ce qui a été réalisé**

- Analyse des risques RGPD
- Déclenchement logique des AIPD
- Qualification des incidents
- Décision de notification CNIL
- Sauvegarde des revues de risques
- Sauvegarde des revues d'incidents

**Technologies utilisées**

- `Python`
  Pourquoi : logique métier simple à formaliser
- `SQLite`
  Pourquoi : historiser les revues Q4 et Q6
- `FastAPI`
  Pourquoi : exposer les opérations d'analyse et d'historique

**Etat actuel**

- Agent B est déjà opérationnel
- Il ne se contente plus d'analyser : il garde aussi la trace de ses revues
- La partie la plus sensible restant à faire est l'exécution métier réelle dans les systèmes externes

---

### Slide 10 — Agent C : état actuel
**Ce qui a été réalisé**

- Qualification des demandes DSAR
- Calcul des délais réglementaires
- Dossier DSAR structuré
- Recherche transversale sur les données sauvegardées
- Préparation du paquet DSAR
- Export JSON / CSV
- Journalisation sécurisée de l'exécution

**Technologies utilisées**

- `FastAPI`
  Pourquoi : gestion simple des demandes DSAR via API
- `SQLite`
  Pourquoi : garder l'historique des demandes et des exécutions
- `JavaScript frontend`
  Pourquoi : rendre les résultats directement visibles et exportables

**Etat actuel**

- Agent C est déjà dans un état avancé
- Il sait rechercher, préparer et journaliser
- Il reste à connecter davantage d'actions réelles côté systèmes métiers

---

### Slide 11 — Agent D : état actuel
**Ce qui a été réalisé**

- Cockpit DPO
- Score de maturité RGPD
- Vue consolidée des alertes
- Plan d'actions prioritaires
- Recommandations d'amélioration
- Intégration de l'historique :
  - registres
  - actions
  - revues de risques
  - revues d'incidents
  - DSAR
  - AIPD
  - consentements

**Technologies utilisées**

- `Python`
  Pourquoi : logique de consolidation simple et maîtrisée
- `Groq`
  Pourquoi : génération du rapport DPO
- `Chart.js`
  Pourquoi : visualiser simplement les indicateurs dans le cockpit

**Etat actuel**

- Agent D ne montre plus seulement une synthèse du moment
- Il commence à exploiter l'historique global du projet

---

### Slide 12 — Interface et restitution
**Ce qui a été mis en place**

- Interface web de démonstration
- Vue analyse conformité
- Vue risques / incidents
- Vue DSAR
- Vue gouvernance DPO
- Vues historiques
- Exports PDF / Excel / JSON / CSV

**Technologies utilisées**

- `HTML / CSS / JavaScript`
  Pourquoi : interface légère et rapide à faire évoluer
- `Chart.js`
  Pourquoi : graphiques simples pour les indicateurs
- `jsPDF`
  Pourquoi : générer des documents PDF côté interface
- `jspdf-autotable`
  Pourquoi : tableaux propres dans les exports
- `XLSX`
  Pourquoi : export Excel des registres et résultats

---

### Slide 13 — Situation actuelle du projet
**Où en sommes-nous aujourd'hui**

- Le projet possède déjà un socle fonctionnel crédible
- Plusieurs exigences du cahier des charges sont partiellement ou largement couvertes
- L'approche multi-agents est réellement en place
- La traçabilité devient un point fort du projet

**Niveau de maturité**

- Le projet n'est plus un simple prototype
- Il est dans une phase de consolidation
- L'enjeu n'est plus seulement de faire des démonstrations
- L'enjeu est maintenant de compléter, stabiliser et structurer davantage

---

### Slide 14 — Scores et indicateurs calculés
**La plateforme calcule déjà plusieurs scores et niveaux**

**Côté conformité — Agent A**

- `score_brut`
  Ce que c'est : le cumul des gravités des violations détectées
  Ce qu'il fait dans le projet : il mesure le poids total des violations détectées
  Formule : `score_brut = somme des gravités des violations détectées`
- `score_normalise`
  Ce que c'est : la version ramenée sur 100 du score brut
  Ce qu'il fait dans le projet : il donne un pourcentage de non-conformité plus lisible
  Formule : `score_normalise = min((score_brut / score_max_theorique) × 100, 100)`
- `niveau_risque`
  Ce que c'est : le niveau de risque réglementaire associé au score
  Ce qu'il fait dans le projet : il classe le traitement en Faible / Moyen / Elevé / Critique
  Formule :
  - `Critique` si `score_normalise >= 40`
  - `Elevé` si `score_normalise >= 25`
  - `Moyen` si `score_normalise >= 10`
  - `Faible` sinon
- `nombre_violations`
  Ce que c'est : le total des règles non respectées
  Ce qu'il fait dans le projet : il mesure le volume global d'écarts
- `violations_critiques`
  Ce que c'est : le nombre de violations de gravité maximale
  Ce qu'il fait dans le projet : il aide à prioriser les actions urgentes
- `violations_elevees`
  Ce que c'est : le nombre de violations importantes mais non maximales
  Ce qu'il fait dans le projet : il aide à planifier les corrections à court terme

**Côté données non structurées**

- `criticite_globale`
  Ce que c'est : le niveau global de sensibilité d'un document scanné
  Ce qu'il fait dans le projet : il qualifie rapidement le niveau d'exposition du fichier
  Logique : niveau global déduit du type de données trouvées dans le document
- `nb_findings`
  Ce que c'est : le nombre total d'éléments détectés dans un fichier
  Ce qu'il fait dans le projet : il mesure la densité de données personnelles dans le document
  Formule : `nb_findings = nombre total d'éléments détectés dans le fichier`

**Côté risques et incidents — Agent B**

- `score` de chaque scénario de risque
  Ce que c'est : le score d'un risque individuel
  Ce qu'il fait dans le projet : il priorise les scénarios de risque
  Calcul : gravité × vraisemblance
- `nombre_risques`
  Ce que c'est : le nombre total de risques détectés
  Ce qu'il fait dans le projet : il montre la charge globale de risque du traitement
- `risques_critiques`
  Ce que c'est : le nombre de risques classés critiques
  Ce qu'il fait dans le projet : il déclenche les actions les plus urgentes
- `risques_eleves`
  Ce que c'est : le nombre de risques élevés
  Ce qu'il fait dans le projet : il complète la priorisation des mesures
- `gravite_incident`
  Ce que c'est : le niveau de gravité déclaré pour un incident
  Ce qu'il fait dans le projet : il influence la qualification de la violation
- `nombre_personnes_affectees`
  Ce que c'est : le nombre de personnes touchées par un incident
  Ce qu'il fait dans le projet : il aide à décider du niveau de notification

**Côté DSAR — Agent C**

- `jours_restants`
  Ce que c'est : le nombre de jours restants avant l'échéance DSAR
  Ce qu'il fait dans le projet : il suit le délai réglementaire restant
  Formule : `jours_restants = date_limite - date_du_jour`
- `statut_delai`
  Ce que c'est : le niveau d'urgence du délai DSAR
  Ce qu'il fait dans le projet : il signale si la demande est normale, urgente ou dépassée
  Logique :
  - dépassé si `< 0`
  - critique si `<= 5 jours`
  - urgent si `<= 10 jours`
- `targets_count`
  Ce que c'est : le nombre de cibles concernées dans l'exécution DSAR
  Ce qu'il fait dans le projet : il mesure l'ampleur de l'action à mener
  Formule : `targets_count = nombre de cibles impactées dans l'exécution DSAR`

**Côté gouvernance — Agent D**

- `score_maturite_rgpd`
  Ce que c'est : le score global de maturité RGPD du dispositif
  Ce qu'il fait dans le projet : il donne une lecture synthétique du niveau global de maîtrise
  Formule simplifiée :
  - base `100`
  - moins une pénalité liée au score de conformité
  - moins une pénalité liée aux risques critiques et élevés
  - moins une pénalité si AIPD requise non réalisée
  - moins une pénalité si notification CNIL requise
  - moins une pénalité si DSAR hors délai
- `niveau_maturite`
  Ce que c'est : la classe de maturité associée au score global
  Ce qu'il fait dans le projet : il permet une lecture managériale simple
  Logique :
  - `Initial` : `0–25`
  - `En développement` : `26–50`
  - `Défini` : `51–70`
  - `Géré` : `71–85`
  - `Optimisé` : `86–100`
- `indice_maturite_rgpd`
  Ce que c'est : un indice de maturité ajusté selon les faiblesses détectées
  Ce qu'il fait dans le projet : il affine la lecture du niveau réel de maturité
  Formule : `indice_maturite = score_maturite - pénalité liée au nombre de faiblesses`
- `tendance_maturite`
  Ce que c'est : l'interprétation globale de l'indice de maturité
  Ce qu'il fait dans le projet : il indique si la situation est bonne, moyenne ou à améliorer
  Logique :
  - critique si indice très bas
  - à améliorer si indice faible
  - en progression si indice moyen
  - bonne maturité si indice élevé

---

### Slide 15 — Limites et points à renforcer
**Limites actuelles**

- Couverture GMAO PRO encore incomplète
- Cartographie des flux encore partielle
- Certaines exécutions réelles restent prudentes ou simulées
- Détection encore perfectible sur certains cas métiers
- Le cockpit peut encore être enrichi en indicateurs et preuves

**Lecture projet**

- La base est bonne
- Mais le projet entre dans une phase critique où la qualité d'intégration devient essentielle

---

### Slide 16 — Prochaines étapes
**Priorités**

- Finaliser le backbone central RGPD
- Compléter la cartographie et la logique d'inventaire
- Démarrer plus proprement la partie GMAO PRO
- Renforcer les preuves opposables
- Consolider encore la gouvernance et le reporting

**A court terme**

- Stabilisation
- Complétude
- Démonstration plus forte

---

### Slide 17 — Conclusion
**Bilan**

- Le projet avance de façon concrète
- Les 4 agents existent et sont structurés
- Plusieurs briques importantes sont déjà opérationnelles
- La plateforme commence à produire de la valeur réelle en analyse, en preuve et en pilotage

**Message final**

- Nous sommes dans une phase charnière
- Les fondations techniques sont en place
- La priorité maintenant est de transformer cette base en solution plus complète, plus robuste et plus alignée avec l'ensemble du cahier des charges

---

## Version courte à dire à l'oral

**Ouverture**

"Aujourd'hui, le projet dispose déjà d'une architecture fonctionnelle avec une API centralisée, un workflow multi-agents, une première couverture QALITAS, des mécanismes de traçabilité et une interface de restitution. Nous avons dépassé le stade du simple prototype."

**Conclusion**

"La situation actuelle est positive : les fondations sont solides, plusieurs fonctionnalités sont déjà démontrables, et les prochains efforts vont porter surtout sur la consolidation, la complétude et l'extension vers GMAO PRO."
