# Discours complet — Démonstration Technique
## Plateforme d'Agents IA pour la Conformité RGPD

---

## Partie 1 : Introduction

Bonjour, je m'appelle Yacine Ben Jemaa. Je vais vous présenter la plateforme d'agents IA pour la conformité RGPD que j'ai développée pour TIM, appliquée aux logiciels QALITAS WEB et GMAO PRO WEB.

La plateforme est construite avec FastAPI pour le backend, SQLite pour le stockage, et une interface web de pilotage. Elle orchestre 4 agents d'IA spécialisés : Agent A pour la cartographie et la conformité, Agent B pour les risques et incidents, Agent C pour la gestion des droits DSAR, et Agent D pour la gouvernance DPO.

L'architecture repose sur 3 niveaux d'intelligence progressive : le niveau 0 avec un moteur de règles déterministe et des regex, le niveau 0.5 avec du machine learning TF-IDF et régression logistique, le niveau 1 avec du RAG sémantique via FAISS, et le niveau 2 avec le LLM Groq pour les cas ambigus.

---

## Partie 2 : Démarrage de l'application

L'application se lance avec une seule commande Uvicorn. Le serveur démarre sur le port 8000 avec l'API FastAPI.

---

## Partie 3 : Vue d'ensemble — Interface Web

Voici l'interface web. Après authentification, on arrive sur le tableau de bord principal.

Ce tableau de bord centralise les indicateurs clés : le score de conformité global, le niveau de vigilance, le nombre de violations, et le score de maturité RGPD. C'est le cockpit du DPO.

---

## Partie 4 : Agent A — Cartographie et Conformité

Commençons par Agent A — le cœur de la plateforme, environ 3800 lignes. Je lance une analyse du module clients de QALITAS.

L'Agent A fonctionne avec 3 niveaux d'intelligence.

**Niveau 0 — le moteur de règles et les regex.** Il scanne d'abord les champs de l'API avec des expressions régulières pour détecter les données personnelles : emails, téléphones, CIN, IBAN, adresses IP, cartes bancaires avec l'algorithme de Luhn. Ensuite, 47 règles de conformité évaluent le traitement. Ces règles sont organisées en 10 catégories de A à J dans la knowledge base : principes généraux, bases légales, transparence, droits des personnes, sécurité, AIPD, sous-traitants, transferts internationaux, DPO, et responsabilité. Chaque règle a une gravité de 1 à 3. Si le traitement n'a pas de base légale, la règle RGPD-05 se déclenche avec une gravité 3. Si les mesures de sécurité sont absentes, la règle RGPD-13 se déclenche avec une gravité 3. Le score de conformité normalisé est calculé sur 115 points maximum.

**Niveau 0.5 — le Machine Learning.** Pour chaque champ détecté dans l'API, on utilise un classifieur TF-IDF combiné à une régression logistique. Ce modèle est entraîné sur une cinquantaine de noms de champs. Par exemple, Email est classifié comme donnée de contact, Salaire comme donnée financière, CIN comme identité officielle. Ce classifieur est purement indicatif — il donne une recommandation, mais la décision finale appartient au moteur de règles. L'avantage c'est que c'est explicable : on peut montrer que le mot Email active les coefficients du modèle vers la catégorie contact avec tel poids. C'est déterministe, rapide — 5 millisecondes — et ça ne coûte rien.

**Niveau 1 — le RAG.** Après la détection des violations, l'Agent utilise le Retrieval-Augmented Generation. Le RGPD et la Loi Tunisienne 2004-63 sont découpés en chunks de 400 mots avec un chevauchement de 50 mots pour ne pas couper les articles importants. Chaque chunk est transformé en un vecteur de 384 dimensions par le modèle paraphrase-multilingual-MiniLM-L12-v2, qui supporte le français et l'anglais. Ces vecteurs sont stockés dans un index FAISS. Quand une violation est trouvée — par exemple absence de chiffrement des données bancaires — l'article correspondant est recherché sémantiquement. La distance L2 entre le vecteur de la requête et les vecteurs stockés détermine les articles les plus pertinents. Chaque violation reçoit ainsi une citation légale réelle, pas de l'hallucination. Cela rend la plateforme auditable.

**Niveau 2 — le LLM Groq.** Si l'utilisateur écrit une description en langage naturel comme Traitement des fiches de paie des employés via QALITAS, l'Agent envoie cette description à Groq qui retourne un JSON structuré avec la base légale inférée, la finalité, les mesures de sécurité recommandées, et la liste des données collectées. Ces champs sont fusionnés avec les données du formulaire avant l'évaluation par les règles.

Voici les résultats. L'interface montre chaque violation avec sa gravité, le score de conformité normalisé sur 100, et les citations du RGPD ou de la Loi Tunisienne récupérées par RAG au niveau 1.

---

## Partie 5 : Agent B — Risques et Incidents

Passons à l'Agent B, le gestionnaire de risques et incidents, environ 690 lignes.

**Niveau 0 — le moteur de risques.** L'Agent B analyse les risques selon 8 scénarios prédéfinis, numérotés R01 à R08. Chaque scénario a une condition, une gravité et une vraisemblance. Par exemple, le scénario R01 pour accès non autorisé se déclenche si le traitement n'a pas de mesures de sécurité, avec une gravité de 3 sur 5 et une vraisemblance de 3 sur 5. Le score est le produit : 3 fois 3 égale 9, ce qui est critique. Le scénario R05 pour données sensibles sans protection se déclenche si le champ données sensibles est vrai sans garanties spécifiques.

Le risque résiduel est calculé en soustrayant des points de mitigation. Par exemple, si le traitement a des mesures de sécurité, on gagne 2 points de mitigation. S'il a du chiffrement, encore 2 points. Si la durée de conservation est définie, 1 point. Mais si les données sont sensibles, on perd 1 point de mitigation. Le score résiduel est le score initial moins la mitigation, avec un minimum de zéro.

**Niveau 0.5 — le Machine Learning.** L'Agent B n'utilise pas de ML. Tout est déterministe : les calculs de scores sont purement arithmétiques. C'est un choix de conception parce que les incidents doivent être reproductibles et auditable — on ne peut pas avoir une IA qui décide un jour qu'un incident est grave et le lendemain qu'il ne l'est pas avec la même entrée.

**Niveau 2 — le LLM Groq pour l'inférence d'incidents.** Une fonctionnalité que j'ai récemment ajoutée : si l'utilisateur écrit une description en langage naturel comme un laptop non chiffré contenant les fiches de paie de 200 employés a été perdu, sans remplir les champs structurés, l'Agent B envoie cette description à Groq qui retourne un JSON avec la gravité inférée, le nombre de personnes affectées, si des données sensibles sont impliquées et si les données étaient chiffrées. Ces champs sont ensuite utilisés par le moteur de règles pour calculer le score incident.

**Le score incident** est calculé ainsi : la gravité multipliée par 2, plus un bonus de 3 points si plus de 100 personnes sont affectées, plus 3 points si des données sensibles sont impliquées, plus 1 point si des trouvailles non structurées existent, moins 3 points si les données étaient chiffrées. Dans notre exemple : gravité 5 fois 2 égale 10, plus 3 pour 200 personnes, plus 3 pour les données sensibles, moins 0 pour non chiffré, égale 16. Niveau élevé — notification CNIL obligatoire sous 72 heures.

---

## Partie 6 : Agent C — Gestion des Droits DSAR

L'Agent C gère les demandes d'exercice des droits des personnes, environ 630 lignes.

**Niveau 0 — l'arbre de qualification.** La décision finale est un arbre de règles déterministe. On vérifie d'abord si le type de droit est valide parmi les 7 droits RGPD : accès, rectification, effacement, limitation, portabilité, opposition, décision automatisée. Si le type n'est pas reconnu, la demande est invalide. Ensuite, on vérifie si l'identité du demandeur est vérifiée — si non, statut en attente de vérification. Si la personne a fait 3 demandes ou plus dans les 30 derniers jours, c'est abusif. Si le droit est la portabilité mais que la base légale n'est ni consentement ni contrat, c'est non applicable. Si le droit est l'effacement mais qu'il y a une obligation légale de conservation, c'est une exception légale. Sinon, la demande est valide.

**Niveau 0.5 — le Machine Learning.** Pour l'extraction du type de droit à partir d'un email, on utilise 3 couches. D'abord, une heuristique par mots-clés avec scoring de position : si le message contient copie, on score accès ; si supprimer, on score effacement. Ensuite, un classifieur TF-IDF plus régression logistique entraîné sur des exemples de messages pour les 7 classes de droits. Enfin, on compare les deux résultats. Si l'heuristique trouve un type avec un score élevé, on l'utilise. Sinon, on utilise le ML. Si le ML a une confiance faible, on utilise Groq.

**Niveau 2 — le LLM Groq pour l'extraction.** Quand l'utilisateur colle un email reçu par le DPO, on envoie le texte à Groq avec un prompt qui demande d'extraire le nom du demandeur, le type de droit, le système concerné, les données concernées, et un résumé. Groq retourne un JSON structuré qui pré-remplit le formulaire. Le DPO peut ensuite valider ou corriger avant de soumettre.

**La recherche transversale.** Une fois la demande qualifiée, l'Agent C lance une recherche dans tout l'inventaire QALITAS et GMAO pour trouver les données de la personne concernée. Il utilise les endpoints découverts — comme GetEmployees, GetCustomers — et cherche par nom, email ou identifiant. Les résultats sont consolidés dans un paquet DSAR prêt à être exécuté.

---

## Partie 7 : Agent D — Gouvernance et Maturité

L'Agent D est le méta-agent, environ 800 lignes. Il consomme les sorties des agents A, B et C pour produire un score de maturité unique de 0 à 100.

**Niveau 0 — le calcul de maturité.** Le score commence à 100 et applique des pénalités progressives. Le score de conformité d'Agent A est multiplié par 0,45 et soustrait. Les violations critiques enlèvent jusqu'à 9 points. Les violations élevées enlèvent jusqu'à 6 points. Les risques critiques enlèvent jusqu'à 9 points. Des pénalités structurelles s'appliquent : absence de base légale confirmée enlève 6 points, absence de durée de conservation définie enlève 5 points, absence de mesures de sécurité enlève 5 points, absence de processus droits enlève 5 points, AIPD requise mais non réalisée enlève 6 points, notification CNIL requise enlève 5 points, DSAR en retard enlève 5 points. Le score est plancher à 15.

Cinq niveaux de maturité. Initial de 0 à 25, en rouge. En développement de 26 à 50, en orange. Défini de 51 à 70, en jaune. Géré de 71 à 85, en vert. Optimisé de 86 à 100. L'objectif est d'atteindre au moins le niveau Géré.

**Niveau 2 — le LLM Groq pour le rapport DPO.** L'Agent D utilise Groq pour générer un rapport DPO narratif. Il envoie les scores, les violations principales, les risques identifiés, et les tendances historiques à Groq qui rédige un rapport en français avec un résumé exécutif, une analyse détaillée, et des recommandations. Si Groq n'est pas disponible, un template de rapport statique est utilisé en fallback.

**Le plan d'actions prioritaire.** L'Agent D identifie les faiblesses les plus critiques et génère un plan d'actions avec des échéances. Par exemple : réaliser l'AIPD obligatoire dans les 30 jours, mettre en place le chiffrement des données sensibles dans les 15 jours, formaliser le contrat de sous-traitance.

**La comparaison de tendance.** L'Agent D compare le score actuel avec les snapshots historiques stockés en base. Si la tendance est à la hausse, c'est bon signe. Si elle est à la baisse, une alerte est déclenchée.

---

## Partie 8 : Boucle de Feedback DPO — Mémoire

Un aspect clé de la plateforme : la mémoire DPO. Chaque fois que le DPO valide ou corrige une décision — que ce soit la classification d'un champ, le type de droit DSAR, ou la décision AIPD — cette validation est stockée en base dans la table dpo_feedback_memory avec le contexte complet, la décision, et la justification.

La prochaine fois qu'un champ similaire est analysé par le classifieur de niveau 0.5, ou qu'un traitement similaire est évalué pour l'AIPD, l'Agent vérifie d'abord si le DPO a déjà tranché sur un cas similaire. Il utilise une recherche par similarité dans la mémoire. Si un précédent existe avec un score de correspondance élevé, l'Agent utilise directement cette décision plutôt que de repartir de zéro. C'est l'apprentissage continu, sans nécessité de ré-entraîner les modèles.

---

## Partie 9 : Connecteurs Externes

La plateforme se connecte aux API REST de QALITAS et GMAO PRO. Pour QALITAS, 10 endpoints JSON fonctionnels ont été découverts et implémentés : Account pour l'authentification, Customer pour les clients, Employee pour les employés, Equipment pour les équipements, Actions pour les actions. Pour GMAO PRO, 6 endpoints sont implémentés.

Chaque module a plusieurs endpoints avec des fallbacks — si un endpoint échoue avec une erreur 404, on essaie le suivant. Par exemple, pour récupérer les sites, on essaie d'abord Account sur GetSiteByCompany avec l'identifiant de l'entreprise, puis GetSiteByCompany sans préfixe, puis Account sur GetSites.

Le connecteur gère l'authentification avec extraction automatisée du token CSRF depuis le HTML de login, la gestion de session avec cookies, et la reconnexion automatique en cas d'expiration de session. Chaque appel API est loggé dans la base pour traçabilité.

---

## Partie 10 : Conclusion

Pour conclure, la plateforme couvre l'ensemble du cycle RGPD. Cartographie automatique des données depuis QALITAS et GMAO. Analyse de conformité avec 47 règles couvrant le RGPD et la Loi Tunisienne. Évaluation des risques avec 8 scénarios et calcul de risque résiduel avec mitigation. Gestion des incidents avec score automatisé et décision de notification CNIL sous 72 heures. Traitement des droits DSAR avec qualification par arbre de règles et recherche transversale dans tout l'inventaire. Gouvernance DPO avec score de maturité, plan d'actions, et comparaison de tendance.

L'architecture en 3 niveaux d'intelligence permet un équilibre optimal. Le niveau 0 déterministe garantit la reproductibilité et l'auditabilité. Le niveau 0.5 avec TF-IDF et régression logistique offre de l'explicabilité et de la rapidité. Le niveau 1 avec RAG FAISS fournit des citations légales réelles sans hallucination. Le niveau 2 avec Groq LLM gère les cas ambigus et l'extraction en langage naturel. Les 4 agents communiquent via un orchestrateur central, et la boucle de feedback DPO permet un apprentissage continu sans ré-entraînement.

Merci de votre attention.
