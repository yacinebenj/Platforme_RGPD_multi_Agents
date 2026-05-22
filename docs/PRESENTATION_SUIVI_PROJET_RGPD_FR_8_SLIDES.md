# Presentation de Suivi Projet
## Version courte - 8 slides

### Slide 1 - Titre
**Plateforme d'Agents IA RGPD pour QALITAS et GMAO PRO**  
**Point d'avancement detaille du projet**

- Etat reel du projet
- Realisations fonctionnelles
- Technologies utilisees et leur role
- Ajouts recents et prochaine phase

---

### Slide 2 - Objectif et architecture generale
**Objectif du projet**

- Construire une plateforme RGPD operationnelle, automatisee et tracable
- Assister un DPO dans la cartographie, la conformite, les risques, les DSAR, la gouvernance et l'amelioration continue
- Commencer par QALITAS WEB et preparer l'extension vers GMAO PRO WEB

**Architecture fonctionnelle**

- Une API centrale pour recevoir les traitements, incidents, DSAR et extractions
- 4 agents specialises :
  - Agent A : Q1 + Q2 + Q3
  - Agent B : Q4 + Q6
  - Agent C : Q5
  - Agent D : Q7 + Q8
- Une base SQLite pour la tracabilite, l'historique et les preuves
- Une interface web orientee DPO pour exploiter les resultats

**Technologies principales**

- `FastAPI`
  - Ce que c'est : un framework Python pour construire des API web
  - Ce qu'il fait dans le projet : il expose toutes les routes d'analyse, d'historique, de synchronisation et de pilotage
- `Pydantic`
  - Ce que c'est : une bibliotheque de validation de donnees
  - Ce qu'elle fait dans le projet : elle structure les traitements, incidents, DSAR et requetes de generation
- `LangGraph`
  - Ce que c'est : un framework d'orchestration multi-agents
  - Ce qu'il fait dans le projet : il gere les workflows complets A -> B -> C -> D sur certains parcours globaux
- `SQLite`
  - Ce que c'est : une base de donnees legere embarquee
  - Ce qu'elle fait dans le projet : elle stocke les analyses, registres, incidents, DSAR, consentements, inventaires et snapshots de gouvernance

---

### Slide 3 - Etat actuel du projet
**Situation actuelle**

- Le projet n'est plus un simple prototype visuel
- La plateforme execute de vraies analyses sur des donnees QALITAS
- Les agents produisent des resultats metier, des historiques et des preuves
- L'interface a ete reorganisee pour etre plus lisible pour un futur DPO
- Le projet est entre dans une phase de consolidation et de validation manuelle

**Ce qui fonctionne deja**

- Workflow multi-agents
- Connecteur QALITAS sur plusieurs modules
- Analyse RGPD sur traitements, incidents et DSAR
- Sauvegarde automatique des analyses
- Tableaux de bord et historiques

**Lecture globale**

- Le socle technique est solide
- Les fonctionnalites majeures existent
- L'enjeu principal devient maintenant la qualite des resultats et la stabilite fonctionnelle

---

### Slide 4 - Avancement metier par agent
**Agent A - Cartographie, conformite, base legale**

- Cartographie RGPD des traitements
- Detection des donnees personnelles dans QALITAS
- Analyse de conformite et calcul des violations
- Analyse de la base legale et du consentement
- Ajouts recents :
  - cartographie des flux de donnees
  - alertes Q1
  - inventaire RGPD central
  - filtrage des personnes physiques dans les enregistrements QALITAS

**Agent B - Risques et incidents**

- Analyse des risques
- AIPD / DPIA
- Qualification des incidents et violations
- Justification de notification CNIL
- Ajouts recents :
  - risque residuel
  - structures AIPD et CNIL plus explicites
  - historiques Q4 et Q6

**Agent C - DSAR**

- Qualification des demandes
- Calcul des delais
- Recherche transversale
- Construction du paquet DSAR
- Journalisation et execution locale dans la plateforme
- Ajouts recents :
  - execution rectification / effacement dans les donnees gerees par la plateforme
  - boite mail DSAR configurable
  - extraction DSAR depuis un message texte ou email

**Agent D - Gouvernance et amelioration continue**

- Cockpit DPO
- Score et niveau de maturite
- Recommandations d'amelioration
- Veille reglementaire
- Ajouts recents :
  - snapshots de gouvernance
  - tendances dans le temps
  - detection de faiblesses recurrentes

---

### Slide 5 - Realisations techniques importantes
**Travaux marquants deja realises**

- Reconstruction du scan non structure
- Sauvegarde des preuves de scan en base
- Creation d'un inventaire RGPD central partage entre agents
- Historisation des risques, incidents, DSAR et gouvernance
- Amelioration de l'UX pour passer d'une logique "outils" a une logique "espace DPO"

**Ajouts recents importants**

- `records_details` sur QALITAS
  - La plateforme ne se limite plus a lister des champs
  - Elle affiche quels enregistrements contiennent quelles donnees detectees
- Filtre `personne_physique`
  - La detection QALITAS a ete durcie pour mieux separer personnes physiques et entreprises
  - Cela reduit les faux positifs sur les modules clients / fournisseurs
- `inventory_flows` et `inventory_alerts`
  - Les flux Q1 et les alertes Q1 sont maintenant sauvegardes et reutilisables
- `governance_snapshots`
  - La gouvernance n'est plus seulement instantanee
  - La plateforme garde une memoire des etats successifs
- `dsar_mailboxes`
  - Agent C peut maintenant gerer des boites mail DSAR autorisees
  - Une extraction peut etre lancee depuis une boite configuree

**Technologies utilisees**

- `pdfplumber`
  - Ce que c'est : une bibliotheque d'extraction texte PDF
  - Ce qu'elle fait dans le projet : elle lit les documents PDF pour detecter des donnees personnelles
- `pytesseract`
  - Ce que c'est : un moteur OCR utilise depuis Python
  - Ce qu'il fait dans le projet : il extrait du texte depuis des images ou scans
- `Pillow`
  - Ce que c'est : une bibliotheque de traitement d'images
  - Ce qu'elle fait dans le projet : elle prepare les images avant OCR
- `Groq`
  - Ce que c'est : un service d'inference LLM
  - Ce qu'il fait dans le projet : il aide a la generation de rapports, de dossiers et a certaines analyses intelligentes
- `FAISS`
  - Ce que c'est : une bibliotheque de recherche vectorielle
  - Ce qu'elle fait dans le projet : elle supporte la partie RAG de la plateforme
- `requests`
  - Ce que c'est : une bibliotheque Python pour les appels HTTP
  - Ce qu'elle fait dans le projet : elle est utilisee pour les connecteurs externes comme QALITAS et le squelette Microsoft 365
- `imaplib`
  - Ce que c'est : la bibliotheque standard Python pour IMAP
  - Ce qu'elle fait dans le projet : elle permet de lire une boite mail Gmail / IMAP dans le prototype DSAR

---

### Slide 6 - Interface et restitution des resultats
**Ce qui est visible dans l'interface**

- Cockpit DPO
- Traitements
- Incidents et risques
- DSAR
- Gouvernance
- Historiques et registres

**Ajouts interface recents**

- Theme sombre pour une lecture plus confortable
- Navigation reorganisee pour un futur usage DPO
- Agent B ne se lance plus "de nulle part" sans contexte
- Ajout d'un affichage plus humain des champs QALITAS
- Ajout des tendances dans la gouvernance
- Ajout d'une zone de configuration de boites mail DSAR

**Restitution metier**

- L'utilisateur peut voir :
  - les violations
  - les recommandations
  - les scores
  - les dossiers AIPD / CNIL
  - les paquets DSAR
  - les historiques de decisions
  - les snapshots de gouvernance

**Technologies utilisees**

- `HTML / CSS / JavaScript`
  - Ce que c'est : les technologies standards de l'interface web
  - Ce qu'elles font dans le projet : elles permettent la saisie, la visualisation et l'exploitation des resultats
- `Chart.js`
  - Ce que c'est : une bibliotheque de graphiques
  - Ce qu'elle fait dans le projet : elle affiche les KPIs de conformite, risques et maturite
- `jsPDF` + `jspdf-autotable`
  - Ce que c'est : des bibliotheques d'export PDF
  - Ce qu'elles font dans le projet : elles permettent l'export de dossiers, registres et rapports
- `XLSX`
  - Ce que c'est : une bibliotheque d'export Excel
  - Ce qu'elle fait dans le projet : elle genere des fichiers Excel de synthese et de registre

---

### Slide 7 - Scores et indicateurs calcules
**Conformite**

- `score_brut`
  - Ce que c'est : somme des gravites des violations detectees
  - Ce qu'il fait dans le projet : base de calcul de la non-conformite
- Formule : `score_brut = somme des gravites des violations detectees`

- `score_normalise`
  - Ce que c'est : score ramene sur 100
  - Ce qu'il fait dans le projet : comparaison simple entre traitements
- Formule : `score_normalise = min((score_brut / score_max_theorique) x 100, 100)`

- `niveau_risque`
  - Ce que c'est : niveau de risque reglementaire associe
  - Ce qu'il fait dans le projet : classe le traitement en Faible / Moyen / Eleve / Critique

**Donnees non structurees**

- `criticite_globale`
  - Ce que c'est : niveau global de sensibilite d'un document
  - Ce qu'il fait dans le projet : aide a qualifier rapidement le fichier

- `nb_findings`
  - Ce que c'est : nombre d'elements detectes dans un fichier
  - Ce qu'il fait dans le projet : mesure la densite d'informations personnelles

**Risques et incidents**

- `score_risque`
  - Ce que c'est : score d'un scenario de risque
  - Ce qu'il fait dans le projet : priorise les scenarios de risque
- Formule : `score_risque = gravite x vraisemblance`

- `score_residuel`
  - Ce que c'est : score du risque apres prise en compte de mesures de reduction
  - Ce qu'il fait dans le projet : permet de juger si le risque reste acceptable ou non

- `niveau_incident`
  - Ce que c'est : niveau d'impact global d'un incident
  - Ce qu'il fait dans le projet : aide a justifier les obligations de notification

**DSAR et gouvernance**

- `jours_restants`
  - Ce que c'est : nombre de jours restants avant l'echeance DSAR
  - Ce qu'il fait dans le projet : pilote l'urgence
- Formule : `jours_restants = date_limite - date_du_jour`

- `targets_count`
  - Ce que c'est : nombre de cibles impactees par une action DSAR
  - Ce qu'il fait dans le projet : mesure l'ampleur d'une rectification ou d'un effacement

- `score_maturite_rgpd`
  - Ce que c'est : score global de maturite RGPD
  - Ce qu'il fait dans le projet : donne une vision synthetique du niveau global de maitrise
- Logique :
  - base `100`
  - penalites liees a la conformite
  - penalites liees aux risques
  - penalite AIPD non realisee
  - penalite notification CNIL requise
  - penalite DSAR hors delai

- `niveau_maturite`
  - Ce que c'est : classe associee au score global
  - Ce qu'il fait dans le projet : facilite la lecture manageriale

- `indice_maturite_rgpd`
  - Ce que c'est : indice ajuste selon les faiblesses identifiees
  - Ce qu'il fait dans le projet : affine l'evaluation globale

- `tendance_maturite`
  - Ce que c'est : interpretation globale de l'indice de maturite
  - Ce qu'il fait dans le projet : indique si la situation se degrade, progresse ou se stabilise

- `tendances`
  - Ce que c'est : comparaison entre l'etat actuel et les snapshots precedents
  - Ce qu'il fait dans le projet : permet au DPO de voir l'evolution des violations, des actions ouvertes, des DSAR ouvertes et de la maturite

---

### Slide 8 - Limites actuelles et prochaine phase
**Points encore a renforcer**

- GMAO PRO reste encore moins avance que QALITAS
- La lecture temps reel des boites mail Microsoft 365 depend encore de la configuration Graph
- La lecture Gmail / IMAP est en prototype et necessite des parametres IMAP valides
- Certaines classifications restent perfectibles sur les cas metier ambigus
- La phase de validation manuelle est maintenant indispensable

**Priorite immediate**

- Tester manuellement :
  - les violations
  - les recommandations
  - les classifications de donnees
  - les decisions AIPD / CNIL
  - les traitements DSAR
  - les tendances de gouvernance

**Conclusion**

- La plateforme dispose d'une base fonctionnelle reelle
- Les principales briques du cahier des charges existent deja
- La prochaine phase vise surtout la correction, la fiabilisation et la stabilite des resultats

---

## Version courte a dire a l'oral

"La plateforme dispose aujourd'hui d'une base fonctionnelle solide : API centralisee, agents specialises, connecteur QALITAS, inventaire RGPD, historique, DSAR, risques, gouvernance et amelioration continue. Les derniers ajouts ont surtout renforce la qualite metier : filtrage des personnes physiques, snapshots de gouvernance, tendances dans le temps et integration d'une premiere logique de boites mail DSAR. La prochaine phase est principalement une phase de validation manuelle et de correction des resultats."
