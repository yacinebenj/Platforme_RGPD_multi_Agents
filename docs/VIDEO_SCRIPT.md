# Script de Démonstration Technique — Plateforme d'Agents IA RGPD

**Durée estimée : 12-15 minutes**

---

## Partie 1 : Introduction (1 min)

**Ce que vous montrez à l'écran :** Terminal + navigateur sur l'URL `http://localhost:8000/`

**Ce que vous dites :**

> "Bonjour, je m'appelle Yacine Ben Jemaa. Je vais vous présenter la plateforme d'agents IA pour la conformité RGPD que j'ai développée pour TIM, appliquée aux logiciels QALITAS WEB et GMAO PRO WEB."
>
> "La plateforme est construite avec FastAPI pour le backend, SQLite pour le stockage, et une interface web de pilotage. Elle orchestre 4 agents d'IA spécialisés : Agent A pour la cartographie et la conformité, Agent B pour les risques et incidents, Agent C pour la gestion des droits DSAR, et Agent D pour la gouvernance DPO."

---

## Partie 2 : Démarrage de l'application (30 sec)

**Ce que vous montrez à l'écran :** Terminal avec la commande pour lancer le serveur

```
python -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

**Ce que vous dites :**

> "L'application se lance avec une seule commande Uvicorn. Le serveur démarre sur le port 8000 avec l'API FastAPI."

---

## Partie 3 : Vue d'ensemble — Interface Web (1 min 30)

**Ce que vous montrez à l'écran :** Navigateur ouvert sur `http://localhost:8000/` — la page de login

**Action :** Connectez-vous avec un compte DPO

**Ce que vous dites :**

> "Voici l'interface web. Après authentification, on arrive au tableau de bord principal."

**Ce que vous montrez :** Le tableau de bord avec les KPIs

> "Ce tableau de bord centralise les indicateurs clés : le score de conformité global, le niveau de vigilance, le nombre de violations, et le score de maturité RGPD. C'est le cockpit du DPO."

---

## Partie 4 : Agent A — Cartographie et Conformité (3 min)

### 4.1 Analyse d'un traitement QALITAS (1 min 30)

**Ce que vous montrez à l'écran :** Interface → Section "Analyses disponibles" → Cliquez sur "Analyser QALITAS"

**Action :** Ouvrez le code `agents/agent_a.py` à côté

**Ce que vous dites :**

> "Commençons par Agent A — le cœur de la plateforme. Je lance une analyse du module 'clients' de QALITAS."
>
> "Regardons le code rapidement. Agent A est le fichier le plus important — environ 3800 lignes."

**Montrez les lignes 170-210 (patterns regex) :**

> "L'agent commence par scanner les champs de l'API avec des expressions régulières pour détecter les données personnelles : emails, téléphones, CIN, IBAN, adresses IP. C'est le niveau 0 de l'intelligence — déterministe, rapide."

**Montrez la ligne 1496 (détection Luhn) :**

> "Il vérifie aussi les cartes bancaires avec l'algorithme de Luhn."

### 4.2 RAG — Citations juridiques (1 min)

**Montrez `llm/rag_builder.py` lignes 100-148 :**

> "Après la détection, l'agent utilise le RAG — Retrieval-Augmented Generation. Le RGPD et la Loi Tunisienne 2004-63 sont découpés en chunks de 400 mots, chacun transformé en un vecteur de 384 dimensions par le modèle 'paraphrase-multilingual-MiniLM-L12-v2'. Ces vecteurs sont stockés dans un index FAISS."

**Montrez `llm/rag_builder.py` lignes 184-234 (`search` function) :**

> "Quand une violation est trouvée, l'article correspondant est recherché sémantiquement dans les textes de loi. La distance L2 (Euclidienne) entre le vecteur de la requête et les vecteurs des chunks détermine les articles les plus pertinents."

**Montrez `agents/agent_a.py` lignes 116-137 (`enrich_violations_with_rag`) :**

> "Chaque violation reçoit ainsi une citation légale réelle — pas de l'hallucination. Cela rend la plateforme auditabled."

### 4.3 Résultats de l'analyse (30 sec)

**Montrez les violations affichées dans l'interface :**

> "Voici les résultats. L'interface montre chaque violation avec sa gravité, le score de conformité normalisé sur 100, et les citations du RGPD ou de la Loi Tunisienne récupérées par RAG."

---

## Partie 5 : Agent B — Risques et Incidents (2 min 30)

### 5.1 Analyse des risques Q4 (1 min)

**Montrez `agents/agent_b.py` lignes 4-83 (SCENARIOS_RISQUES) :**

> "L'Agent B analyse les risques selon 8 scénarios : accès non autorisé, fuite de données, sous-traitants non maîtrisés, etc. Chaque scénario a une gravité et une vraisemblance. Le score = gravité × vraisemblance."

**Montrez `agents/agent_b.py` lignes 483-508 (`evaluer_risques`) :**

> "Si le traitement n'a pas de mesures de sécurité, le scénario R01 est déclenché avec un score de 9. Si les données sont sensibles sans protection, R05 s'ajoute avec 6."

**Montrez `agents/agent_b.py` lignes 363-408 (`_compute_residual_risk`) :**

> "Le risque résiduel est calculé en soustrayant les points de mitigation : chiffrement (+2), contrôles d'accès (+2), durée de conservation définie (+1)."

### 5.2 Déclaration d'incident Q6 (1 min)

**Montrez le formulaire d'incident dans l'interface :**

> "Je déclare un incident : 'Un laptop non chiffré contenant les fiches de paie de 200 employés a été perdu'."

**Montrez `agents/agent_b.py` la fonction `infer_incident_from_description` (nouvelle) :**

> "Quand je ne remplis que la description, l'Agent B utilise Groq LLM pour inférer la gravité, le nombre de personnes affectées, et la nature des données. C'est le niveau 2."

**Montrez `agents/agent_b.py` lignes 452-481 (`_assess_incident_risk`) :**

> "Le score incident = gravité × 2 + bonus personnes + bonus sensibles − pénalité chiffrement. Ici : 5×2 + 3 + 3 − 0 = 16 → niveau élevé → notification CNIL obligatoire sous 72h."

---

## Partie 6 : Agent C — Gestion des Droits DSAR (2 min)

**Montrez l'interface DSAR — textarea pour coller un message :**

> "L'Agent C gère les demandes d'exercice des droits. Je colle cet email reçu :"

**Montrez `api/main.py` lignes 3476-3499 (prompt Groq pour extraction) :**

> "Le message est envoyé à Groq LLM qui extrait : le nom du demandeur, le type de droit, le système concerné. Puis `_finalize_dsar_type` valide avec 3 couches : heuristique par mots-clés, TF-IDF + régression logistique, et Groq."

**Montrez `agents/agent_c.py` lignes 448-471 (`qualifier_demande`) :**

> "La décision finale est un arbre de règles déterministe : si identité non vérifiée → 'en attente', si 3+ demandes en 30 jours → 'abusive', si obligation légale de conservation → 'exception légale'. Sinon → 'valide'."

**Montrez la recherche transversale (Agent C) :**

> "L'Agent C recherche aussi les données de la personne dans l'inventaire QALITAS et GMAO via une recherche transversale."

---

## Partie 7 : Agent D — Gouvernance et Maturité (1 min 30)

**Montrez `agents/agent_d.py` lignes 453-515 (`calculer_score_maturite`) :**

> "L'Agent D est le méta-agent. Il consomme les sorties de A, B et C pour produire un score de maturité unique de 0 à 100."

**Montrez le tableau de bord Agent D dans l'interface :**

> "Le score commence à 100 et perd des points : les violations critiques enlèvent jusqu'à 9 points, l'absence de base légale confirmée enlève 6 points, une AIPD requise mais non réalisée enlève 6 points. Le score est plancher à 15."

> "5 niveaux de maturité : Initial (0-25) → En développement (26-50) → Défini (51-70) → Géré (71-85) → Optimisé (86-100). L'objectif est d'atteindre au moins 'Géré'."

**Montrez le plan d'actions prioritaire :**

> "L'Agent D génère aussi un plan d'actions prioritaire et une comparaison de tendance avec les snapshots historiques."

---

## Partie 8 : Boucle de Feedback DPO — Mémoire (1 min)

**Montrez `database/crud.py` (recherche de fonction `find_similar_dpo_memory`) :**

> "Un aspect clé de la plateforme : la mémoire DPO. Chaque fois que le DPO valide ou corrige une décision, cette validation est stockée en base."

**Montrez `ml/field_classifier.py` (recherche de `_memory_override`) :**

> "La prochaine fois qu'un champ similaire est analysé, l'agent vérifie d'abord si le DPO a déjà tranché. Si oui, il utilise directement cette décision plutôt que de repartir de zéro. C'est l'apprentissage continu."

---

## Partie 9 : Connecteurs Externes (1 min)

**Montrez `qalitas/connector.py` lignes 33-45 (MODULE_ENDPOINTS) :**

> "La plateforme se connecte aux API REST de QALITAS et GMAO PRO. Chaque module a ses endpoints avec des fallbacks — si `/Customer/GetEnabledCustomers` échoue, on essaie `/Customer/GetCustomers`."

**Montrez `integrations/source_connector_base.py` lignes 78-137 (`fetch_records_from_endpoints`) :**

> "Le connecteur gère l'authentification avec extraction du token CSRF, la gestion de session, et la reconnexion automatique en cas d'expiration."

---

## Partie 10 : Conclusion (30 sec)

**Ce que vous montrez :** Retour au tableau de bord global

**Ce que vous dites :**

> "Pour conclure, la plateforme couvre l'ensemble du cycle RGPD : cartographie automatique des données, analyse de conformité avec 47 règles, évaluation des risques avec mitigation, gestion des incidents avec notification CNIL, traitement des droits DSAR avec recherche transversale, et gouvernance DPO avec score de maturité."
>
> "L'architecture en 3 niveaux d'intelligence — règles déterministes (niveau 0), TF-IDF + ML (niveau 0.5), RAG + Groq LLM (niveaux 1 et 2) — permet un équilibre entre rapidité, explicabilité et précision."
>
> "Merci de votre attention."

---

## Points d'attention techniques pour la vidéo

1. **Ayez l'IDE ouvert** avec les fichiers clés prêts à être montrés
2. **Préparez un traitement QALITAS déjà analysé** pour gagner du temps
3. **Ayez un incident et un DSAR pré-remplis** dans le formulaire
4. **Si Groq ne répond pas**, les niveaux 0 et 0.5 continuent de fonctionner — montrez cette résilience
5. **Parlez lentement** — la démo technique est dense
6. **Montrez le code + l'interface en parallèle** (split screen) quand vous expliquez un agent
