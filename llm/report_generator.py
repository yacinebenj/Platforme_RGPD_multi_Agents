import os
from groq import Groq
from dotenv import load_dotenv
from llm.rag_builder import get_rag, search

load_dotenv("key.env")
client = Groq(api_key=os.getenv("GROQ_API_KEY"))
_rag = None

def get_rag_instance():
    global _rag
    if _rag is None:
        _rag = get_rag()
    return _rag


def build_queries(agent_d_output):
    queries = []
    synthese = agent_d_output.get("synthese", {})
    q7 = agent_d_output.get("q7_gouvernance", {})
    q8 = agent_d_output.get("q8_amelioration", {})

    alertes = q7.get("alertes_critiques", [])
    faiblesses = q8.get("faiblesses_identifiees", [])

    if "aipd" in str(alertes).lower() or "aipd" in str(faiblesses):
        queries.append("AIPD analyse impact protection donnees article 35")
    if "base_legale" in faiblesses:
        queries.append("base legale licite traitement article 6")
    if "securite" in faiblesses:
        queries.append("mesures securite techniques organisationnelles article 32")
    if "transferts" in faiblesses:
        queries.append("transfert donnees pays tiers garanties article 44 46")
    if "droits_personnes" in faiblesses:
        queries.append("droits personnes acces rectification effacement article 15 17")
    if "privacy_by_design" in faiblesses:
        queries.append("privacy by design protection donnees conception article 25")
    if not queries:
        queries.append("principes generaux protection donnees personnelles RGPD")

    return queries


def retrieve_legal_context(agent_d_output):
    index, chunks, model = get_rag_instance()
    queries = build_queries(agent_d_output)
    seen = set()
    context_chunks = []
    for query in queries:
        results = search(query, index, chunks, model, top_k=2)
        for r in results:
            text = r["text"][:200]
            if text not in seen:
                seen.add(text)
                context_chunks.append(r)
    return context_chunks


def generate_report(agent_a, agent_b, agent_c, agent_d_output):
    synthese = agent_d_output.get("synthese", {})
    q7 = agent_d_output.get("q7_gouvernance", {})
    q8 = agent_d_output.get("q8_amelioration", {})
    q1 = agent_a.get("q1_cartographie", {})
    q3 = agent_a.get("q3_base_legale", {})

    legal_chunks = retrieve_legal_context(agent_d_output)
    legal_context = ""
    for chunk in legal_chunks:
        legal_context += f"[{chunk['source']}]: {chunk['text'][:300]}\n\n"

    alertes = "\n".join(q7.get("alertes_critiques", []))
    faiblesses = ", ".join(q8.get("faiblesses_identifiees", []))
    priorites = q7.get("plan_actions_prioritaires", [])
    priorites_text = "\n".join([
        f"- [{p['niveau']}] {p['action']} ({p['delai']})"
        for p in priorites[:5]
    ])

    prompt = f"""[SYSTEM: You must respond exclusively in French. English is strictly forbidden.]

Tu es un Delegue a la Protection des Donnees (DPO) expert en RGPD et en droit tunisien de la protection des donnees (Loi 2004-63).

Reponds UNIQUEMENT ET EXCLUSIVEMENT en langue francaise. N'utilise aucun mot en anglais.

Redige un rapport DPO professionnel et structure en francais base sur l analyse suivante:

TRAITEMENT ANALYSE: {q1.get("nom_traitement", "Non specifie")} - Systeme: {q1.get("systeme", "Non specifie")}
SCORE DE MATURITE RGPD: {synthese.get("score_maturite_global", 0)}/100 - Niveau: {synthese.get("niveau_maturite", "Non defini")}
BASE LEGALE: {q3.get("base_legale", "Non definie")}
ALERTES CRITIQUES:
{alertes if alertes else "Aucune alerte critique"}
FAIBLESSES IDENTIFIEES: {faiblesses if faiblesses else "Aucune"}
PLAN D ACTIONS PRIORITAIRES:
{priorites_text if priorites_text else "Aucune priorite"}
TENDANCE: {synthese.get("tendance", "Non definie")}

ARTICLES JURIDIQUES PERTINENTS:
{legal_context}

Redige un rapport DPO structure avec:
1. Synthese executve (2-3 phrases)
2. Analyse de conformite (points cles)
3. Risques identifies et leur impact
4. Plan d actions recommande avec delais
5. Conclusion et prochaines etapes

Le rapport doit etre professionnel, cite les articles juridiques pertinents, et utilisable directement par un DPO ou un auditeur. Maximum 400 mots.
RAPPEL IMPORTANT: Redige uniquement en francais."""

    print("Generating DPO report with Groq (Llama 3.1 70B)...")
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1000,
        temperature=0.3
    )
    return response.choices[0].message.content

