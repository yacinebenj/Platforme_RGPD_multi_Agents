# Discours — Démonstration Technique

---

## Partie 1 : Introduction

Bonjour, je m'appelle Yacine Ben Jemaa. Je vais vous présenter la plateforme d'agents IA pour la conformité RGPD que j'ai développée pour TIM, appliquée aux logiciels QALITAS WEB et GMAO PRO WEB.

La plateforme est construite avec FastAPI pour le backend, SQLite pour le stockage, et une interface web de pilotage. Elle orchestre 4 agents d'IA spécialisés : Agent A pour la cartographie et la conformité, Agent B pour les risques et incidents, Agent C pour la gestion des droits DSAR, et Agent D pour la gouvernance DPO.

---

## Partie 2 : Démarrage de l'application

L'application se lance avec une seule commande Uvicorn. Le serveur démarre sur le port 8000 avec l'API FastAPI.

---

## Partie 3 : Vue d'ensemble — Interface Web

Voici l'interface web. Après authentification, on arrive sur le tableau de bord principal.

Ce tableau de bord centralise les indicateurs clés : le score de conformité global, le niveau de vigilance, le nombre de violations, et le score de maturité RGPD. C'est le cockpit du DPO.

---

## Partie 4 : Agent A — Cartographie et Conformité

Commençons par Agent A — le cœur de la plateforme. Je lance une analyse du module clients de QALITAS.

Agent A est le fichier le plus important — environ 3800 lignes. L'agent commence par scanner les champs de l'API avec des expressions régulières pour détecter les données personnelles : emails, téléphones, CIN, IBAN, adresses IP. C'est le niveau 0 de l'intelligence — déterministe, rapide. Il vérifie aussi les cartes bancaires avec l'algorithme de Luhn.

Après la détection, l'agent utilise le RAG — Retrieval-Augmented Generation. Le RGPD et la Loi Tunisienne 2004-63 sont découpés en chunks de 400 mots, chacun transformé en un vecteur de 384 dimensions par le modèle paraphrase-multilingual-MiniLM-L12-v2. Ces vecteurs sont stockés dans un index FAISS.

Quand une violation est trouvée, l'article correspondant est recherché sémantiquement dans les textes de loi. La distance L2 entre le vecteur de la requête et les vecteurs des chunks détermine les articles les plus pertinents.

Chaque violation reçoit ainsi une citation légale réelle — pas de l'hallucination. Cela rend la plateforme auditable.

Voici les résultats. L'interface montre chaque violation avec sa gravité, le score de conformité normalisé sur 100, et les citations du RGPD ou de la Loi Tunisienne récupérées par RAG.

---

## Partie 5 : Agent B — Risques et Incidents

L'Agent B analyse les risques selon 8 scénarios : accès non autorisé, fuite de données, sous-traitants non maîtrisés, etc. Chaque scénario a une gravité et une vraisemblance. Le score est égal à la gravité multipliée par la vraisemblance.

Si le traitement n'a pas de mesures de sécurité, le scénario R01 est déclenché avec un score de 9. Si les données sont sensibles sans protection, R05 s'ajoute avec 6.

Le risque résiduel est calculé en soustrayant les points de mitigation : chiffrement, contrôles d'accès, durée de conservation définie.

Je déclare un incident : un laptop non chiffré contenant les fiches de paie de 200 employés a été perdu.

Quand je ne remplis que la description, l'Agent B utilise Groq LLM pour inférer la gravité, le nombre de personnes affectées, et la nature des données. C'est le niveau 2.

Le score incident est égal à la gravité multipliée par 2, plus le bonus personnes, plus le bonus sensibles, moins la pénalité chiffrement. Ici : 5 fois 2 plus 3 plus 3 moins 0 égale 16. Niveau élevé — notification CNIL obligatoire sous 72 heures.

---

## Partie 6 : Agent C — Gestion des Droits DSAR

L'Agent C gère les demandes d'exercice des droits. Je colle un email reçu.

Le message est envoyé à Groq LLM qui extrait le nom du demandeur, le type de droit, le système concerné. Ensuite, la fonction de finalisation du type DSAR valide avec 3 couches : heuristique par mots-clés, TF-IDF plus régression logistique, et Groq.

La décision finale est un arbre de règles déterministe : si identité non vérifiée, le statut est en attente. Si 3 demandes ou plus en 30 jours, la demande est abusive. Si obligation légale de conservation, c'est une exception légale. Sinon, la demande est valide.

L'Agent C recherche aussi les données de la personne dans l'inventaire QALITAS et GMAO via une recherche transversale.

---

## Partie 7 : Agent D — Gouvernance et Maturité

L'Agent D est le méta-agent. Il consomme les sorties des agents A, B et C pour produire un score de maturité unique de 0 à 100.

Le score commence à 100 et perd des points : les violations critiques enlèvent jusqu'à 9 points, l'absence de base légale confirmée enlève 6 points, une AIPD requise mais non réalisée enlève 6 points. Le score est plancher à 15.

5 niveaux de maturité : Initial de 0 à 25, En développement de 26 à 50, Défini de 51 à 70, Géré de 71 à 85, Optimisé de 86 à 100. L'objectif est d'atteindre au moins le niveau Géré.

L'Agent D génère aussi un plan d'actions prioritaire et une comparaison de tendance avec les snapshots historiques.

---

## Partie 8 : Boucle de Feedback DPO — Mémoire

Un aspect clé de la plateforme : la mémoire DPO. Chaque fois que le DPO valide ou corrige une décision, cette validation est stockée en base.

La prochaine fois qu'un champ similaire est analysé, l'agent vérifie d'abord si le DPO a déjà tranché. Si oui, il utilise directement cette décision plutôt que de repartir de zéro. C'est l'apprentissage continu.

---

## Partie 9 : Connecteurs Externes

La plateforme se connecte aux API REST de QALITAS et GMAO PRO. Chaque module a ses endpoints avec des fallbacks — si un endpoint échoue, on essaie le suivant.

Le connecteur gère l'authentification avec extraction du token CSRF, la gestion de session, et la reconnexion automatique en cas d'expiration.

---

## Partie 10 : Conclusion

Pour conclure, la plateforme couvre l'ensemble du cycle RGPD : cartographie automatique des données, analyse de conformité avec 47 règles, évaluation des risques avec mitigation, gestion des incidents avec notification CNIL, traitement des droits DSAR avec recherche transversale, et gouvernance DPO avec score de maturité.

L'architecture en 3 niveaux d'intelligence — règles déterministes au niveau 0, TF-IDF plus machine learning au niveau 0.5, RAG et Groq LLM aux niveaux 1 et 2 — permet un équilibre entre rapidité, explicabilité et précision.

Merci de votre attention.
