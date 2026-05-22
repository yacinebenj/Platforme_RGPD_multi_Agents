"""
patch_interface.py
Reads interface.html and injects the unstructured scan UI in 4 places.
Run from the project root: python patch_interface.py
"""
import sys, os

SRC  = "api/interface.html"
DEST = "api/interface.html"

with open(SRC, encoding="utf-8") as f:
    html = f.read()

# ─── GUARD ───────────────────────────────────────────────────────────────────
if "unscan-card" in html:
    print("[SKIP] Patch already applied.")
    sys.exit(0)

# ─── 1. CSS — insert before closing </style> ─────────────────────────────────
CSS = """
/* ─── UNSTRUCTURED SCAN ─── */
.unscan-card{background:var(--surface);border:1px solid var(--border);border-radius:var(--r-lg);margin-bottom:16px;overflow:hidden;border-top:3px solid var(--purple)}
.unscan-dropzone{border:2px dashed var(--border-2);border-radius:var(--r-lg);padding:32px 20px;text-align:center;cursor:pointer;transition:all .15s;background:var(--surface-2);margin-bottom:12px}
.unscan-dropzone:hover,.unscan-dropzone.drag-over{border-color:var(--purple);background:var(--purple-light)}
.unscan-dropzone input[type=file]{display:none}
.unscan-dropzone-label{font-size:13px;font-weight:600;color:var(--ink-2);margin-bottom:4px}
.unscan-dropzone-sub{font-size:11px;color:var(--ink-4)}
.unscan-finding{display:flex;align-items:flex-start;gap:10px;padding:9px 12px;border-radius:var(--r);margin-bottom:6px;border-left:3px solid;background:var(--surface)}
.unscan-finding.critique{border-color:var(--danger);background:var(--danger-light)}
.unscan-finding.elevee{border-color:var(--warning);background:var(--warning-light)}
.unscan-finding.moyenne{border-color:var(--accent);background:var(--accent-light)}
.unscan-finding.faible{border-color:var(--success);background:var(--success-light)}
.unscan-finding-label{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;min-width:110px;flex-shrink:0;padding-top:2px}
.unscan-finding-body{flex:1}
.unscan-finding-val{font-size:12px;font-weight:600;color:var(--ink);font-family:var(--mono)}
.unscan-finding-ctx{font-size:11px;color:var(--ink-3);margin-top:2px}
.unscan-finding-art{font-size:10px;color:var(--accent);margin-top:2px;font-style:italic}
.unscan-summary-bar{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px}
.unscan-stat{background:var(--surface-2);border:1px solid var(--border);border-radius:var(--r);padding:10px;text-align:center}
.unscan-stat-val{font-size:20px;font-weight:600;color:var(--ink);line-height:1}
.unscan-stat-lbl{font-size:10px;font-weight:600;color:var(--ink-4);text-transform:uppercase;letter-spacing:.05em;margin-top:3px}
.unscan-method-badge{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:600;background:var(--purple-dim);color:var(--purple);border:1px solid var(--purple-dim)}
"""
html = html.replace("</style>", CSS + "</style>", 1)

# ─── 2. HTML — unstructured card in Conformité section ───────────────────────
UNSCAN_CARD = """
    <!-- Q1 Unstructured Upload Card -->
    <div class="unscan-card" id="unscan_section">
      <div class="card-header">
        <div>
          <div class="card-title" style="color:var(--purple)">Q1 — Scanner un fichier non structuré</div>
          <div style="font-size:11px;color:var(--ink-3);margin-top:2px">PDF · Image · Audio — Détection PII par OCR / Whisper / NLP</div>
        </div>
        <span class="badge" style="background:var(--purple-dim);color:var(--purple);font-size:11px">Nouveau</span>
      </div>
      <div class="card-body">
        <div class="unscan-dropzone" id="unscan_dropzone"
             onclick="document.getElementById('unscan_file_input').click()"
             ondragover="event.preventDefault();this.classList.add('drag-over')"
             ondragleave="this.classList.remove('drag-over')"
             ondrop="handleUnscanDrop(event)">
          <input type="file" id="unscan_file_input"
                 accept=".pdf,.png,.jpg,.jpeg,.tiff,.bmp,.gif,.mp3,.wav,.m4a,.ogg"
                 onchange="handleUnscanFile(this.files[0])">
          <div style="font-size:32px;margin-bottom:8px;opacity:.5">📄</div>
          <div class="unscan-dropzone-label">Glisser un fichier ici ou cliquer pour sélectionner</div>
          <div class="unscan-dropzone-sub">PDF • PNG • JPG • TIFF • MP3 • WAV • M4A</div>
        </div>
        <div id="unscan_loading" style="display:none" class="loading-state">
          <div class="spinner"></div>
          <span id="unscan_loading_msg">Analyse en cours...</span>
        </div>
        <div id="unscan_results" style="display:none">
          <div class="unscan-summary-bar">
            <div class="unscan-stat"><div class="unscan-stat-val" id="unscan_file_type">—</div><div class="unscan-stat-lbl">Type</div></div>
            <div class="unscan-stat"><div class="unscan-stat-val" id="unscan_nb_findings" style="color:var(--danger)">0</div><div class="unscan-stat-lbl">PII trouvées</div></div>
            <div class="unscan-stat"><div class="unscan-stat-val" id="unscan_criticite" style="font-size:13px;padding-top:4px">—</div><div class="unscan-stat-lbl">Criticité</div></div>
            <div class="unscan-stat"><div class="unscan-stat-val" id="unscan_method" style="font-size:11px;padding-top:5px;color:var(--purple)">—</div><div class="unscan-stat-lbl">Extraction</div></div>
          </div>
          <div id="unscan_rgpd_alert" style="margin-bottom:12px"></div>
          <div id="unscan_findings_list"></div>
          <div id="unscan_empty" style="display:none" class="alert-bar success">Aucune donnée personnelle détectée dans ce fichier.</div>
        </div>
        <div id="unscan_error" style="display:none" class="alert-bar danger"></div>
      </div>
    </div>
"""

# Insert just before the manual form wrapper in the Conformité section
html = html.replace(
    '<div id="manual_form_wrapper" class="manual-wrapper">',
    UNSCAN_CARD + '\n    <div id="manual_form_wrapper" class="manual-wrapper">',
    1
)

# ─── 3. HTML — unstructured results card in QALITAS section ──────────────────
QALITAS_UNSTRUCT_CARD = """
      <!-- Q1 Unstructured scan results from batch -->
      <div id="qalitas_unstruct_card" style="display:none">
        <div class="card" style="border-top:3px solid var(--purple)">
          <div class="card-header">
            <div class="card-title" style="color:var(--purple)">Q1 — Fichiers non structurés — PDF · Images · Audio</div>
            <span class="unscan-method-badge" id="unstruct_method_badge">—</span>
          </div>
          <div class="card-body" id="qalitas_unstruct_content"></div>
        </div>
      </div>
"""
# Insert after qalitas_agent_results closing div
html = html.replace(
    '<div class="card-footer" style="background:var(--surface);border:1px solid var(--border);border-radius:var(--r-lg);padding:14px 20px;margin-bottom:16px;display:flex;gap:8px">',
    QALITAS_UNSTRUCT_CARD + '\n        <div class="card-footer" style="background:var(--surface);border:1px solid var(--border);border-radius:var(--r-lg);padding:14px 20px;margin-bottom:16px;display:flex;gap:8px">',
    1
)

# ─── 4. JS — inject functions before </script> ────────────────────────────────
JS = """
/* ─── UNSTRUCTURED SCAN ─── */
function escHtml(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

function handleUnscanDrop(e){
  e.preventDefault();
  document.getElementById('unscan_dropzone').classList.remove('drag-over');
  const file=e.dataTransfer.files[0];
  if(file)handleUnscanFile(file);
}

async function handleUnscanFile(file){
  if(!file)return;
  const loading=document.getElementById('unscan_loading');
  const results=document.getElementById('unscan_results');
  const error=document.getElementById('unscan_error');
  const msg=document.getElementById('unscan_loading_msg');
  results.style.display='none';error.style.display='none';loading.style.display='block';
  const ext=file.name.split('.').pop().toLowerCase();
  if(['mp3','wav','m4a','ogg','flac'].includes(ext)) msg.textContent='Transcription audio Whisper...';
  else if(['png','jpg','jpeg','tiff','bmp'].includes(ext)) msg.textContent='OCR Tesseract en cours...';
  else msg.textContent='Extraction PDF pdfplumber...';
  try{
    const fd=new FormData();fd.append('file',file);
    const res=await fetch('/scan/unstructured',{method:'POST',body:fd});
    const data=await res.json();
    loading.style.display='none';
    if(!res.ok){error.textContent='Erreur: '+(data.detail||JSON.stringify(data));error.style.display='block';return;}
    renderUnscanResults(data);
  }catch(e){loading.style.display='none';error.textContent='Erreur: '+e.message;error.style.display='block';}
}

const CRIT_COLORS={critique:'var(--danger)',elevee:'var(--warning)',moyenne:'var(--accent)',faible:'var(--success)'};
const PATTERN_LABELS={email:'Email',phone_intl:'Téléphone',phone_fr:'Téléphone FR',nss:'N° Séc. Sociale',cin_tn:'CIN Tunisien',gps_coords:'GPS',iban:'IBAN',ip_address:'IP',date_naissance:'Date naissance',nom_prenom:'Nom / Prénom',adresse:'Adresse',spacy_personne:'Personne (NER)'};

function renderUnscanResults(data){
  document.getElementById('unscan_results').style.display='block';
  const typeIcons={pdf:'📄',image:'🖼️',audio:'🎵',unknown:'❓'};
  document.getElementById('unscan_file_type').textContent=(typeIcons[data.file_type]||'❓')+' '+(data.file_type||'?');
  const nbEl=document.getElementById('unscan_nb_findings');
  nbEl.textContent=data.nb_findings||0;
  nbEl.style.color=data.nb_findings>0?'var(--danger)':'var(--success)';
  const crit=data.criticite_globale||'faible';
  const critEl=document.getElementById('unscan_criticite');
  critEl.textContent=crit.charAt(0).toUpperCase()+crit.slice(1);
  critEl.style.color=CRIT_COLORS[crit]||'var(--ink)';
  document.getElementById('unscan_method').textContent=data.extraction_method||'—';
  const alertEl=document.getElementById('unscan_rgpd_alert');
  if(data.rgpd_impact&&data.rgpd_impact.action_requise){
    alertEl.innerHTML=`<div class="alert-bar danger"><strong>Action RGPD requise</strong> — ${data.rgpd_impact.recommandation}</div>`;
  }else{
    const msg=data.error?`<div class="alert-bar warning">Avertissement : ${escHtml(data.error)}</div>`:`<div class="alert-bar success">${data.rgpd_impact?data.rgpd_impact.recommandation:'Aucune action requise.'}</div>`;
    alertEl.innerHTML=msg;
  }
  const listEl=document.getElementById('unscan_findings_list');
  const emptyEl=document.getElementById('unscan_empty');
  const findings=data.findings||[];
  if(!findings.length){listEl.innerHTML='';emptyEl.style.display='block';return;}
  emptyEl.style.display='none';
  listEl.innerHTML=`
    <div style="font-size:10px;font-weight:600;color:var(--ink-3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">${findings.length} donnée(s) personnelle(s) détectée(s)</div>
    ${findings.map(f=>`
      <div class="unscan-finding ${f.criticite||'faible'}">
        <div class="unscan-finding-label" style="color:${CRIT_COLORS[f.criticite]||'var(--ink-3)'}">${PATTERN_LABELS[f.pattern]||f.pattern}</div>
        <div class="unscan-finding-body">
          <div class="unscan-finding-val">${escHtml(f.extrait||'')}</div>
          ${f.contexte?`<div class="unscan-finding-ctx">«&nbsp;${escHtml(f.contexte)}&nbsp;»</div>`:''}
          ${f.article?`<div class="unscan-finding-art">${escHtml(f.article)}</div>`:''}
        </div>
        <span class="badge ${f.criticite==='critique'||f.criticite==='elevee'?'danger':f.criticite==='moyenne'?'info':'success'}" style="flex-shrink:0">${(f.type||'').charAt(0).toUpperCase()+(f.type||'').slice(1)}</span>
      </div>`).join('')}`;
}

function renderQalitasUnstructured(agentA){
  const unstruct=agentA&&agentA.q1_cartographie&&agentA.q1_cartographie.donnees_non_structurees;
  const card=document.getElementById('qalitas_unstruct_card');
  const content=document.getElementById('qalitas_unstruct_content');
  const badge=document.getElementById('unstruct_method_badge');
  if(!unstruct||unstruct.fichiers_scannes===0){if(card)card.style.display='none';return;}
  card.style.display='block';
  badge.textContent=unstruct.fichiers_scannes+' fichier(s) scanné(s)';
  const crit=unstruct.criticite_globale||'faible';
  let html=`<div class="unscan-summary-bar" style="grid-template-columns:repeat(3,1fr)">
    <div class="unscan-stat"><div class="unscan-stat-val">${unstruct.fichiers_scannes}</div><div class="unscan-stat-lbl">Fichiers scannés</div></div>
    <div class="unscan-stat"><div class="unscan-stat-val" style="color:${unstruct.fichiers_avec_donnees_personnelles>0?'var(--danger)':'var(--success)'}">${unstruct.fichiers_avec_donnees_personnelles}</div><div class="unscan-stat-lbl">Avec données perso.</div></div>
    <div class="unscan-stat"><div class="unscan-stat-val" style="font-size:13px;padding-top:4px;color:${CRIT_COLORS[crit]||'var(--ink)'}">${crit.charAt(0).toUpperCase()+crit.slice(1)}</div><div class="unscan-stat-lbl">Criticité globale</div></div>
  </div>`;
  if(unstruct.types_detectes&&unstruct.types_detectes.length){
    html+=`<div style="margin-bottom:12px"><div style="font-size:10px;font-weight:600;color:var(--ink-3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px">Types détectés</div><div class="classification-row">${unstruct.types_detectes.map(t=>`<span class="tag sensitive">${escHtml(t)}</span>`).join('')}</div></div>`;
  }
  const details=unstruct.details||[];
  if(details.length){
    html+=`<div style="font-size:10px;font-weight:600;color:var(--ink-3);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">Détail par fichier</div>`;
    details.forEach(d=>{
      const fc=CRIT_COLORS[d.criticite_globale]||'var(--ink-3)';
      html+=`<div style="padding:10px 14px;border-left:3px solid ${fc};background:var(--surface-2);border-radius:0 var(--r) var(--r) 0;margin-bottom:8px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
          <div style="font-size:12px;font-weight:600;color:var(--ink);font-family:var(--mono)">${escHtml(d.source_file||'—')}</div>
          <div style="display:flex;gap:6px;align-items:center">
            <span class="unscan-method-badge">${escHtml(d.extraction_method||'—')}</span>
            <span style="font-size:11px;font-weight:600;color:${fc}">${d.nb_findings||0} PII</span>
          </div>
        </div>
        ${(d.findings||[]).slice(0,3).map(f=>`<div style="font-size:11px;color:var(--ink-2);margin-top:3px">· <strong>${escHtml(PATTERN_LABELS[f.pattern]||f.pattern)}</strong> — <span style="font-family:var(--mono)">${escHtml(f.extrait||'')}</span></div>`).join('')}
        ${(d.findings||[]).length>3?`<div style="font-size:11px;color:var(--ink-3);margin-top:4px">+${d.findings.length-3} autre(s)...</div>`:''}
      </div>`;
    });
  }
  content.innerHTML=html;
}
"""
html = html.replace("</script>", JS + "\n</script>", 1)

# ─── 5. Wire renderQalitasUnstructured into qalitasAnalyse() ─────────────────
html = html.replace(
    "window.lastAgentDData=data.agent_d;",
    "window.lastAgentDData=data.agent_d;\n    if(data.agent_a)renderQalitasUnstructured(data.agent_a);",
    1
)

with open(DEST, "w", encoding="utf-8") as f:
    f.write(html)

print(f"[OK] interface.html patched ({len(html):,} chars)")
print("  Added: unscan CSS classes")
print("  Added: Q1 file upload card in Conformité section")
print("  Added: Q1 unstructured results card in QALITAS section")
print("  Added: JS functions (handleUnscanFile, renderUnscanResults, renderQalitasUnstructured)")
print("  Wired: qalitasAnalyse() now calls renderQalitasUnstructured()")