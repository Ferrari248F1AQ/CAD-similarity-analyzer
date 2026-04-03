/**
 * CAD Similarity Analyzer - Frontend JavaScript
 * Supporta: Solid Edge, SolidWorks, Inventor, CATIA, FreeCAD, Fusion 360
 */

const APP_JS_BUILD = '2026-04-01-10:35';
console.log('📦 app.js build:', APP_JS_BUILD);

// ✨ Helper per parsing JSON robusto - evita "Unexpected end of JSON input"
async function safeJsonParse(response, defaultValue = null) {
    try {
        const text = await response.text();
        if (!text || text.trim() === '') {
            console.warn('⚠️ Empty response received');
            return defaultValue;
        }
        return JSON.parse(text);
    } catch (error) {
        console.error('❌ JSON parse error:', error);
        return defaultValue;
    }
}

// Fetch JSON con diagnostica robusta (gestisce body vuoti/non-JSON e HTTP error)
async function fetchJsonWithDiagnostics(url, options = {}) {
    const response = await fetch(url, options);
    const rawText = await response.text();

    if (!rawText || rawText.trim() === '') {
        return {
            ok: false,
            status: response.status,
            data: null,
            error: `Empty response from server (HTTP ${response.status})`
        };
    }

    try {
        const data = JSON.parse(rawText);
        if (!response.ok) {
            return {
                ok: false,
                status: response.status,
                data,
                error: data?.error || data?.message || `Request failed (HTTP ${response.status})`
            };
        }
        return { ok: true, status: response.status, data, error: null };
    } catch (error) {
        const compactBody = rawText.replace(/\s+/g, ' ').trim().slice(0, 200);
        return {
            ok: false,
            status: response.status,
            data: null,
            error: `Server returned non-JSON response (HTTP ${response.status}): ${compactBody || '[empty body]'}`
        };
    }
}

// Stato globale
let appState = {
    signatures: [],
    pairs: [],
    threshold: 0.70,
    currentDirectory: null,
    progressIntervalId: null,
    selectedCadType: 'auto',  // ✨ Tipo CAD selezionato
    matrixData: null,
    matrixLoaded: false,
    pairsRawScoreConfig: null,
    pairsSyncDirty: false,
    pairsForceRawRecompute: false
};
let _matrixReloadTimer = null;
let _matrixRequestSeq = 0;
let _matrixFetchController = null;

const DEFAULT_CRITICAL_SIMILARITY_THRESHOLD = 0.75;
const _paperModeThresholdFromServer = Number(window._paperModeThresholdFromServer);
const INITIAL_PAPER_MODE_THRESHOLD = Number.isFinite(_paperModeThresholdFromServer)
    ? Math.max(0.5, Math.min(0.99, _paperModeThresholdFromServer))
    : DEFAULT_CRITICAL_SIMILARITY_THRESHOLD;

// Stato per Paper Writing Mode (default sicuro)
// Il valore iniziale di 'enabled' viene dal server (iniettato nel template HTML)
let paperWritingState = {
    enabled: (typeof window._paperModeEnabledFromServer !== 'undefined') ? window._paperModeEnabledFromServer : false,
    threshold: INITIAL_PAPER_MODE_THRESHOLD,  // Soglia plagio (0..1), aggiornata dallo slider nel pannello Paper Mode
    hideCrossSessionPairs: false,
    pairLabels: {},
    currentLatex: '',
    currentStats: null
};

// Ultimo risultato di ottimizzazione (usato per apply/save manuale candidato o baseline)
let lastOptimizationResult = null;
let optimizationSessionFilterState = {
    available: [],
    selected: [],
    userCustomized: false,
    lastSummary: null
};

function getCriticalSimilarityThreshold() {
    const threshold = Number(paperWritingState?.threshold);
    if (Number.isFinite(threshold)) {
        return Math.max(0.5, Math.min(0.99, threshold));
    }
    return INITIAL_PAPER_MODE_THRESHOLD;
}

function getMediumSimilarityThreshold() {
    return Math.max(0.5, getCriticalSimilarityThreshold() - 0.10);
}

// ✨ Mapping CAD → estensioni supportate
const CAD_EXTENSIONS = {
    'SolidEdge': ['.par', '.psm', '.asm'],
    'SolidWorks': ['.sldprt', '.sldasm', '.slddrw'],
    'Inventor': ['.ipt', '.iam', '.idw'],
    'CATIA': ['.catpart', '.catproduct', '.catdrawing'],
    'FreeCAD': ['.fcstd'],
    'Fusion360': ['.f3d', '.f3z'],
    'auto': ['*']  // Tutte le estensioni
};

// ✨ PERSISTENZA: Chiave per localStorage
const STORAGE_KEY = 'cadSimilarityAnalyzer';

// Guard anti-doppio-click per setPairLabel
const _setPairLabelInFlight = new Set();

// ✨ Ottieni il tipo CAD selezionato
function getSelectedCadType() {
    const selected = document.querySelector('input[name="cadType"]:checked');
    return selected ? selected.value : 'auto';
}

// ✨ Aggiorna UI quando cambia il CAD
function onCadTypeChange() {
    appState.selectedCadType = getSelectedCadType();
    console.log('📦 Selected CAD:', appState.selectedCadType);

    // Salva preferenza
    saveToLocalStorage();

    // Aggiorna badge status
    updateCadStatusBadge();
}

// ✨ Aggiorna il badge di stato CAD
function updateCadStatusBadge() {
    const indicator = document.getElementById('status-indicator');
    const cadType = appState.selectedCadType;

    let badgeClass = 'bg-success';
    let cadName = cadType;

    if (cadType === 'auto') {
        badgeClass = 'bg-warning text-dark';
        cadName = 'Auto-Detect';
    }

    // Mantieni il badge COM se presente
    const comBadge = indicator.querySelector('.badge.bg-success, .badge.bg-warning');
    if (comBadge) {
        // Già inizializzato, non fare nulla
    }
}

// Salva stato in localStorage
function saveToLocalStorage() {
    const data = {
        lastDirectory: appState.currentDirectory,
        threshold: appState.threshold,
        selectedCadType: appState.selectedCadType,  // ✨ Salva anche il CAD
        paperHideCrossSessionPairs: !!paperWritingState.hideCrossSessionPairs
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
}

// Carica stato da localStorage
function loadFromLocalStorage() {
    try {
        const data = JSON.parse(localStorage.getItem(STORAGE_KEY));
        if (data) {
            if (data.lastDirectory) {
                document.getElementById('directoryPath').value = data.lastDirectory;
                appState.currentDirectory = data.lastDirectory;
            }
            if (data.threshold) {
                appState.threshold = data.threshold;
                document.getElementById('thresholdSlider').value = data.threshold * 100;
                document.getElementById('thresholdValue').textContent = Math.round(data.threshold * 100) + '%';
            }
            // ✨ Ripristina CAD selezionato
            if (data.selectedCadType) {
                appState.selectedCadType = data.selectedCadType;
                const radioBtn = document.getElementById('cad' + data.selectedCadType.replace('360', '360'));
                if (radioBtn) {
                    radioBtn.checked = true;
                }
            }
            if (typeof data.paperHideCrossSessionPairs === 'boolean') {
                paperWritingState.hideCrossSessionPairs = data.paperHideCrossSessionPairs;
            }
        }
    } catch (e) {
        console.error('Error loading localStorage:', e);
    }
}

// ✨ CONFIGURAZIONE APPLICAZIONE (letta da config.json via /api/config)
const DEFAULT_CRITERIA_EXCLUSION_POLICY = Object.freeze({
    enabled: true,
    exclude_if_unavailable: true,
    exclude_if_missing_or_non_finite: true,
    force_excluded: [],
    force_included: []
});

const DEFAULT_OPTIMIZER_TRAINING_POLICY = Object.freeze({
    strict_path_labeled_pairs_only: true
});

function sanitizeCriteriaNameList(values) {
    const out = [];
    if (!Array.isArray(values)) {
        return out;
    }
    values.forEach((item) => {
        const name = String(item || '').trim();
        if (!name || out.includes(name)) {
            return;
        }
        out.push(name);
    });
    return out;
}

function sanitizeCriteriaExclusionPolicy(policy) {
    const src = (policy && typeof policy === 'object') ? policy : {};
    const forceExcluded = sanitizeCriteriaNameList(src.force_excluded);
    const forceIncluded = sanitizeCriteriaNameList(src.force_included).filter(name => !forceExcluded.includes(name));
    return {
        enabled: src.enabled !== false,
        exclude_if_unavailable: src.exclude_if_unavailable !== false,
        exclude_if_missing_or_non_finite: src.exclude_if_missing_or_non_finite !== false,
        force_excluded: forceExcluded,
        force_included: forceIncluded
    };
}

function getCriteriaExclusionPolicy() {
    return sanitizeCriteriaExclusionPolicy(appConfig.criteria_exclusion_policy);
}

function sanitizeOptimizerTrainingPolicy(policy) {
    const src = (policy && typeof policy === 'object') ? policy : {};
    return {
        strict_path_labeled_pairs_only: src.strict_path_labeled_pairs_only !== false
    };
}

let appConfig = {
    show_sketch_parametric: true,  // default: visibile
    criteria_exclusion_policy: sanitizeCriteriaExclusionPolicy(DEFAULT_CRITERIA_EXCLUSION_POLICY),
    optimizer_training_policy: sanitizeOptimizerTrainingPolicy(DEFAULT_OPTIMIZER_TRAINING_POLICY)
};

async function loadAppConfig() {
    try {
        const res = await fetch('/api/config');
        const data = await res.json();
        if (data.success) {
            appConfig.show_sketch_parametric = data.show_sketch_parametric !== false;
            appConfig.criteria_exclusion_policy = sanitizeCriteriaExclusionPolicy(
                data.criteria_exclusion_policy || DEFAULT_CRITERIA_EXCLUSION_POLICY
            );
            appConfig.optimizer_training_policy = sanitizeOptimizerTrainingPolicy(
                data.optimizer_training_policy || DEFAULT_OPTIMIZER_TRAINING_POLICY
            );
        }
    } catch (e) {
        console.warn('⚠️ Could not load app config, using defaults:', e);
    }
}

// ✨ PESI MODIFICABILI - Carica i pesi globali dal backend
async function loadGlobalWeights() {
    try {
        // Carica i pesi persistenti dal backend
        let res = await fetch('/api/weights');
        if (!res.ok) {
            // Fallback backward-compatibility
            res = await fetch('/api/get_config_weights');
        }

        // ✨ Controlla se la risposta è vuota prima di fare il parsing
        const contentType = res.headers.get('content-type');
        if (!contentType || !contentType.includes('application/json')) {
            console.warn('⚠️ Invalid response content-type for weights:', contentType);
            throw new Error('Invalid response format');
        }

        const text = await res.text();
        if (!text || text.trim() === '') {
            console.warn('⚠️ Empty response from /api/get_config_weights');
            throw new Error('Empty response');
        }

        const data = JSON.parse(text);
        if (data.success && data.weights) {
            // ✅ Sovrascrivi currentWeights con TUTTI i pesi caricati (inclusi nuovi parametri fuzzy)
            // Separazione netta: pesi numerici (somma = 1.0) vs parametri fuzzy (non sono pesi!)
            const FUZZY_KEYS = new Set(['lcs_fuzzy_enabled','lcs_fuzzy_function','lcs_fuzzy_alpha','lcs_fuzzy_mix',
                                        'fuzzy_combination_enabled','fuzzy_combination_method',
                                        'fuzzy_combination_penalty','fuzzy_combination_boost']);
            Object.keys(data.weights).forEach(k => {
                currentWeights[k] = data.weights[k];
            });
            defaultWeights = { ...currentWeights };
            // Log della somma dei pesi reali (debug)
            const realWeightSum = Object.entries(currentWeights)
                .filter(([k,v]) => typeof v === 'number' && !FUZZY_KEYS.has(k) && !k.startsWith('_'))
                .reduce((s,[,v]) => s + v, 0);
            console.log(`✅ Weights loaded from backend: weight sum = ${(realWeightSum * 100).toFixed(1)}%`, currentWeights);
            initWeightsPanel();
            initLCSFuzzySelect();  // ✨ Inizializza select fuzzy
            initFuzzyCombination(); // ✨ Inizializza fuzzy combination
            return true;
        }
    } catch (e) {
        console.error('⚠️ Error loading global weights:', e);
        console.log('   Using default weights instead');
    }
    // Se fallisce, inizializza comunque il pannello con pesi di default
    initWeightsPanel();
    initLCSFuzzySelect();  // ✨ Inizializza select fuzzy
    initFuzzyCombination(); // ✨ Inizializza fuzzy combination
    return false;
}

// ✨ Salva i pesi globali sul backend
async function saveGlobalWeights() {
    try {
        // ✅ Prepara i dati da inviare: separa pesi numerici e parametri fuzzy
        const weightsToSave = {};

        // Copia tutti i valori
        Object.entries(currentWeights).forEach(([key, value]) => {
            weightsToSave[key] = value;
        });

        // Assicurati che i pesi numerici siano effettivamente numeri
        // MA preserva tipo originale per parametri fuzzy (boolean/string)
        Object.keys(weightsToSave).forEach(key => {
            if (key.startsWith('lcs_fuzzy_') || key.startsWith('fuzzy_combination_')) {
                return; // mantieni boolean, string, number così com'è
            }
            weightsToSave[key] = parseFloat(weightsToSave[key]) || 0;
        });

        const res = await fetch('/api/weights', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(weightsToSave)
        });

        // ✨ Controlla se la risposta è vuota
        const text = await res.text();
        if (!text || text.trim() === '') {
            throw new Error('Empty response from server');
        }

        const data = JSON.parse(text);
        if (data.success) {
            alert('✅ Global weights saved!\nThey will be used for all future analyses.');
            console.log('✅ Weights saved to backend:', data.weights);
            return true;
        } else {
            alert('❌ Error saving weights: ' + (data.error || 'unknown'));
        }
    } catch (e) {
        console.error('❌ Error in saveGlobalWeights:', e);
        alert('❌ Error saving weights: ' + e.message);
    }
    return false;
}

function syncCompareWeightsPanelFromState() {
    Object.entries(currentWeights || {}).forEach(([key, value]) => {
        if (key.startsWith('lcs_fuzzy_') || key.startsWith('fuzzy_combination_')) {
            return;
        }
        if (!Number.isFinite(value)) {
            return;
        }
        const compareSlider = document.getElementById('compare_weight_' + key);
        const compareValue = document.getElementById('compareWeightVal_' + key);
        const pct = Math.round(value * 100);
        if (compareSlider) {
            compareSlider.value = pct;
        }
        if (compareValue) {
            compareValue.textContent = pct + '%';
        }
    });
    updateCompareWeightsTotal();
}

function applyWeightsToUiState(nextWeights, options = {}) {
    if (!nextWeights || typeof nextWeights !== 'object') {
        return false;
    }
    const forceRawRecompute = !!options.forceRawRecompute;
    const syncPairs = options.syncPairs !== false;

    currentWeights = { ...currentWeights, ...nextWeights };
    initWeightsPanel();
    updateWeightsTotal();
    syncCompareWeightsPanelFromState();

    if (syncPairs) {
        handleWeightsChanged({ forceRawRecompute });
    }
    saveToLocalStorage();
    return true;
}

// Inizializzazione
document.addEventListener('DOMContentLoaded', async function() {
    // Carica stato salvato
    loadFromLocalStorage();

    // ✨ Inizializza listener per il selettore CAD
    document.querySelectorAll('input[name="cadType"]').forEach(radio => {
        radio.addEventListener('change', onCadTypeChange);
    });

    // ✨ Imposta il CAD type iniziale dallo stato
    appState.selectedCadType = getSelectedCadType();
    console.log('🚀 CAD Similarity Analyzer initialized. Selected CAD:', appState.selectedCadType);

    // ✨ Carica configurazione app (show_sketch_parametric, ecc.) PRIMA dei pesi
    await loadAppConfig();

    // ✨ Carica pesi globali dal backend
    await loadGlobalWeights();

    // Check status
    await checkStatus();
    updateThreshold();

    // Aggancio listener sullo slider della soglia per aggiornare il valore visualizzato e applicare il filtro
    const thrSlider = document.getElementById('thresholdSlider');
    if (thrSlider) {
        // Aggiorna il valore mostrato mentre l'utente muove il cursore
        thrSlider.addEventListener('input', (ev) => {
            const display = document.getElementById('thresholdValue');
            if (display) display.textContent = ev.target.value + '%';
        });

        // Al termine della modifica (change), applica subito il filtro: ricarica le pairs dal backend e ri-renderizza
        thrSlider.addEventListener('change', async (ev) => {
            try {
                updateThreshold(); // scrive in appState.threshold
                // Carica le coppie con la nuova soglia e ri-renderizza
                await loadPairs();
                updateStats();
                renderPairs();
            } catch (e) {
                console.error('Error applying updated threshold:', e);
            }
        });
    }

    // Prova a caricare automaticamente i risultati salvati
    let autoLoaded = false;
    if (appState.currentDirectory) {
        console.log('🔄 Attempting auto-load of results for:', appState.currentDirectory);
        autoLoaded = await loadSavedResultsSilent(appState.currentDirectory);
    }
    if (!autoLoaded) {
        console.log('🔄 No directory cache loaded, trying latest cached analysis...');
        await loadLatestResultsSilent();
    }

    // Aggiungi listener per caricare la matrix quando si apre il tab Matrix (LAZY LOADING)
    const matrixTabBtn = document.getElementById('matrix-tab');
    if (matrixTabBtn) {
        let matrixLoadedOnce = false;  // ✨ Track se già caricata
        matrixTabBtn.addEventListener('shown.bs.tab', async function () {
            try {
                // Carica solo la prima volta il tab viene aperto
                if (!matrixLoadedOnce) {
                    console.log('📊 Matrix tab opened for first time, loading...');
                    await loadMatrix();
                    matrixLoadedOnce = true;
                } else if (!appState.matrixLoaded) {
                    // Se è stata invalidata (directory cambiata), ricarica
                    await loadMatrix();
                }
            } catch (e) {
                console.error('Error loading matrix on tab show:', e);
            }
        });
    }

    const compareTabBtn = document.getElementById('compare-tab');
    if (compareTabBtn) {
        compareTabBtn.addEventListener('shown.bs.tab', function () {
            try {
                initCompareWeightsPanel();
                syncCompareWeightsPanelFromState();
            } catch (e) {
                console.error('Error syncing compare tab:', e);
            }
        });
    }

    // Lazy-load optimization coverage when opening optimization tab.
    const optimizeTabBtn = document.getElementById('optimize-tab');
    if (optimizeTabBtn) {
        optimizeTabBtn.addEventListener('shown.bs.tab', async function () {
            try {
                onOptimizationScopeChange();
                await refreshOptimizationDataset();
            } catch (e) {
                console.error('Error loading optimization tab:', e);
            }
        });
    }

    // Ensure scope-dependent controls are initialized.
    onOptimizationScopeChange();

    // ✨ Paper Writing Mode: inizializza dopo un breve delay per garantire che il DOM sia pronto
    setTimeout(function() {
        // Inizializza soglia plagio dallo slider HTML
        const paperSlider = document.getElementById('paperThresholdSlider');
        if (paperSlider) {
            paperWritingState.threshold = parseInt(paperSlider.value) / 100;
        }

        if (typeof initPaperWritingMode === 'function') {
            initPaperWritingMode();
        }
    }, 500);
});

// Carica signatures dal server (solo campi leggeri)
async function loadSignatures() {
    try {
        const res = await fetch('/api/signatures');
        const data = await safeJsonParse(res, []);
        appState.signatures = Array.isArray(data) ? data : [];
        return appState.signatures;
    } catch(e) {
        console.error('loadSignatures error:', e);
        return [];
    }
}

// Carica pairs dal server filtrate per soglia corrente
async function loadPairs() {
    try {
        if (appState.pairsSyncDirty && appState.pairs.length > 0 && appState.pairsForceRawRecompute) {
            const syncedPairs = await recalculateAllPairs({
                forceRawRecompute: appState.pairsForceRawRecompute,
                silent: true
            });
            if (Array.isArray(syncedPairs)) {
                return syncedPairs;
            }
        }

        const thr = appState.threshold || 0;
        const res = await fetch(`/api/pairs?threshold=${thr}&limit=10000&sync_global=0`);
        const data = await safeJsonParse(res, null);
        if (data && data.success) {
            const backendRawChanged = !!(data.sync_info && data.sync_info.raw_config_changed);
            appState.pairsRawScoreConfig = cloneRawScoreConfig(data.raw_score_config || {});
            appState.pairs = (data.pairs || []).map(pair => ({
                ...pair,
                _syncState: 'current',
                _rawScoreConfig: cloneRawScoreConfig(appState.pairsRawScoreConfig)
            }));
            const syncResult = syncPairsWithCurrentWeights();
            appState.pairsSyncDirty = syncResult.changedCount > 0 || syncResult.staleCount > 0;
            appState.pairsForceRawRecompute = syncResult.staleCount > 0;
            if (backendRawChanged) {
                appState.pairsForceRawRecompute = true;
                appState.pairsSyncDirty = true;
            }
            if (!appState.pairsSyncDirty) {
                appState.pairsForceRawRecompute = false;
            }
        }
        if (appState.pairsForceRawRecompute) {
            // Non bloccare l'avvio con un full recompute di tutte le coppie.
            // Aggiorna solo le coppie visibili in modo lazy; il full recompute resta manuale.
            refreshVisiblePairScores();
        }
        return appState.pairs;
    } catch(e) {
        console.error('loadPairs error:', e);
        return [];
    }
}

// Carica risultati in modo silenzioso (senza alert)
async function loadSavedResultsSilent(directory) {
    if (!directory) return false;

    try {
        const apiResp = await fetchJsonWithDiagnostics('/api/load_results', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ directory: directory })
        });

        if (!apiResp.ok || !apiResp.data) {
            console.warn('loadSavedResultsSilent failed:', apiResp.error);
            return false;
        }

        const result = apiResp.data;

        if (result.success && result.cached && result.summary) {
            console.log('✅ Results loaded server-side:', result.summary);
            appState.currentDirectory = directory;
            saveToLocalStorage();

            // Dataset changed: matrix must be rebuilt.
            markMatrixDirty({ clearData: true, reloadIfVisible: false });

            // Carica dati leggeri via API dedicate (no 22MB di JSON)
            await Promise.all([loadSignatures(), loadPairs()]);

            updateStats();
            renderFilesTable();
            populateCompareSelectors();

            // Carica labels (forza sempre reload, resetta cache locale)
            try {
                paperWritingState.pairLabels = {};
                await loadPairLabels();
            } catch(e) {
                console.warn('loadPairLabels in silent load failed:', e);
            }

            // Resetta LaTeX cache: il prossimo export sarà sempre fresco
            paperWritingState.currentLatex = '';

            renderPairs();
            markMatrixDirty({ clearData: false, reloadIfVisible: true, debounceMs: 120 });
            return true;
        }
    } catch (error) {
        console.error('Error auto-loading results:', error);
    }
    return false;
}

// Carica in modo silenzioso l'ultimo risultato disponibile in cache
async function loadLatestResultsSilent() {
    try {
        const apiResp = await fetchJsonWithDiagnostics('/api/load_latest_results');
        if (!apiResp.ok || !apiResp.data) {
            console.warn('loadLatestResultsSilent failed:', apiResp.error);
            return false;
        }

        const result = apiResp.data;
        if (!result || !result.success || !result.cached || !result.summary) {
            return false;
        }

        const recoveredDir = result.summary.directory || '';
        if (recoveredDir) {
            appState.currentDirectory = recoveredDir;
            const input = document.getElementById('directoryPath');
            if (input) input.value = recoveredDir;
            saveToLocalStorage();
        }

        markMatrixDirty({ clearData: true, reloadIfVisible: false });

        await Promise.all([loadSignatures(), loadPairs()]);
        updateStats();
        renderFilesTable();
        populateCompareSelectors();

        try {
            paperWritingState.pairLabels = {};
            await loadPairLabels();
        } catch (e) {
            console.warn('loadPairLabels in latest silent load failed:', e);
        }

        paperWritingState.currentLatex = '';
        renderPairs();
        markMatrixDirty({ clearData: false, reloadIfVisible: true, debounceMs: 120 });
        console.log('✅ Latest cached analysis recovered:', result.summary);
        return true;
    } catch (error) {
        console.error('Error loading latest cached results:', error);
    }
    return false;
}

// Gestione Status Bar
function showStatusBar(message, step) {
    const statusBar = document.getElementById('globalStatusBar');
    document.getElementById('statusMessage').textContent = message;
    if (step) {
        document.getElementById('statusDetails').textContent = step;
    }
    statusBar.classList.remove('d-none');
    statusBar.classList.remove('success', 'error');
    document.body.classList.add('status-bar-visible');
}

function hideStatusBar() {
    const statusBar = document.getElementById('globalStatusBar');
    statusBar.classList.add('d-none');
    document.body.classList.remove('status-bar-visible');
    if (appState.progressIntervalId) {
        clearInterval(appState.progressIntervalId);
        appState.progressIntervalId = null;
    }
}

function closeStatusBar() {
    hideStatusBar();
}

function updateStatusProgress(percentage, message, details) {
    const progressBar = document.getElementById('statusProgressBar');
    const progressText = document.getElementById('progressText');
    const statusMessage = document.getElementById('statusMessage');
    const statusDetails = document.getElementById('statusDetails');

    progressBar.style.width = percentage + '%';
    progressText.textContent = percentage + '%';

    if (message) {
        statusMessage.textContent = message;
    }
    if (details) {
        // Formatta il testo dei dettagli per una migliore leggibilità
        let formattedDetails = details;
        if (details.includes('/')) {
            // Se contiene un conteggio (es: "2/10")
            formattedDetails = `📊 ${details}`;
        }
        statusDetails.textContent = formattedDetails;
    }
}

function setStatusSuccess() {
    const statusBar = document.getElementById('globalStatusBar');
    statusBar.classList.remove('error');
    statusBar.classList.add('success');
    updateStatusProgress(100, '✓ Analysis completed!', '');

    // Auto-hide dopo 3 secondi
    setTimeout(() => {
        hideStatusBar();
    }, 3000);
}

function setStatusError(message) {
    const statusBar = document.getElementById('globalStatusBar');
    statusBar.classList.remove('success');
    statusBar.classList.add('error');
    document.getElementById('statusMessage').textContent = '✗ ' + message;
}

// Polling dello stato di progresso
function startProgressPolling() {
    if (appState.progressIntervalId) {
        clearInterval(appState.progressIntervalId);
    }

    appState.progressIntervalId = setInterval(async () => {
        try {
            const response = await fetch('/api/progress');
            const progress = await response.json();

            if (!progress.active && progress.status === 'idle') {
                clearInterval(appState.progressIntervalId);
                appState.progressIntervalId = null;
                return;
            }

            // Formatta i dettagli: mostra il numero progressivo se disponibile
            let details = '';
            if (progress.processed_files > 0 || progress.total_files > 0) {
                details = `${progress.processed_files}/${progress.total_files}`;
            }

            // Aggiorna la status bar con il messaggio corrente
            updateStatusProgress(progress.percentage, progress.current_step, details);

            // Se completato con successo
            if (progress.status === 'complete') {
                clearInterval(appState.progressIntervalId);
                appState.progressIntervalId = null;
                setStatusSuccess();

                // Ricarica i dati dopo un breve delay
                setTimeout(async () => {
                    markMatrixDirty({ clearData: true, reloadIfVisible: false });
                    await Promise.all([
                        loadSignatures(),
                        loadPairs()
                    ]);
                    updateStats();
                    populateCompareSelectors();

                    // ✨ Carica SEMPRE le labels (fix race condition paper mode)
                    try { await loadPairLabels(); } catch(e) {}
                    renderPairs();
                    markMatrixDirty({ clearData: false, reloadIfVisible: true, debounceMs: 120 });

                    // ✨ PERSISTENZA: Salva la directory corrente nel localStorage
                    saveToLocalStorage();
                }, 500);
            }
            // Se errore
            else if (progress.status === 'error') {
                clearInterval(appState.progressIntervalId);
                appState.progressIntervalId = null;
                setStatusError(progress.error_message || 'Error during analysis');
            }
        } catch (error) {
            console.error('Error polling progress:', error);
        }
    }, 500);  // Poll ogni 500ms
}

// Controlla stato del sistema
async function checkStatus() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();

        const indicator = document.getElementById('status-indicator');
        if (data.com_available) {
            indicator.innerHTML = '<span class="badge bg-success"><i class="bi bi-check-circle"></i> COM Active</span>';
        } else {
            indicator.innerHTML = '<span class="badge bg-warning"><i class="bi bi-exclamation-triangle"></i> COM Not Available</span>';
        }

        if (data.current_directory) {
            document.getElementById('directoryPath').value = data.current_directory;
            appState.currentDirectory = data.current_directory;
            saveToLocalStorage();
        }

        // ✨ Carica info sui CAD disponibili
        await loadCadInfo();

    } catch (error) {
        console.error('Error checking status:', error);
    }
}

// ✨ Carica info sui CAD disponibili e aggiorna UI
async function loadCadInfo() {
    try {
        const response = await fetch('/api/cad_info');
        const data = await response.json();

        if (data.cad_types) {
            // Aggiorna i bottoni radio con tooltip informativi
            // TUTTI i CAD sono selezionabili, ma mostriamo un indicatore per quelli verificati
            Object.entries(data.cad_types).forEach(([cadName, info]) => {
                const radioId = 'cad' + cadName;
                const radio = document.getElementById(radioId);
                const label = document.querySelector(`label[for="${radioId}"]`);

                if (radio && label) {
                    // Tutti i CAD sono sempre abilitati
                    radio.disabled = false;
                    label.classList.remove('disabled');
                    label.style.opacity = '1';

                    // Mostra tooltip con info e stato di verifica
                    const verifiedText = info.verified ? '✓ Verified' : 'X Not verified';
                    label.title = `${info.name} (${info.extensions.join(', ')}) - ${verifiedText}`;

                    // Aggiungi classe CSS se verificato (bordo verde)
                    if (info.verified) {
                        label.classList.add('cad-verified');
                    } else {
                        label.classList.remove('cad-verified');
                    }
                }
            });
        }

        console.log('📦 Available CAD types:', data.cad_types);

    } catch (error) {
        console.error('Error loading CAD info:', error);
    }
}

// Carica risultati salvati da cache
async function loadSavedResults(directory) {
    if (!directory || directory.trim() === '') {
        alert('First enter a path in the "Folder path" field');
        return false;
    }

    try {
        const apiResp = await fetchJsonWithDiagnostics('/api/load_results', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ directory: directory })
        });

        if (!apiResp.ok) {
            alert('❌ ' + (apiResp.error || 'Request failed while loading cached results'));
            return false;
        }

        const result = apiResp.data;
        if (!result || typeof result !== 'object') {
            alert('❌ Invalid JSON payload from server');
            return false;
        }

        if (result.success && result.cached && result.summary) {
            console.log('✅ Results loaded from cache:', result.summary);
            appState.currentDirectory = directory;
            saveToLocalStorage();
            markMatrixDirty({ clearData: true, reloadIfVisible: false });

            // Carica dati leggeri via API dedicate
            await Promise.all([loadSignatures(), loadPairs()]);

            updateStats();
            renderFilesTable();
            populateCompareSelectors();

            // Carica labels (forza sempre il reload dopo un Load Saved Results)
            try {
                paperWritingState.pairLabels = {}; // reset per forzare reload
                await loadPairLabels();
            } catch(e) {
                console.warn('loadPairLabels in loadSavedResults failed:', e);
            }

            renderPairs();
            markMatrixDirty({ clearData: false, reloadIfVisible: true, debounceMs: 120 });

            // Aggiorna statistiche paper writing e resetta LaTeX cache
            paperWritingState.currentLatex = '';
            if (typeof showPlagiarismStats === 'function' && paperWritingState.enabled) {
                try { showPlagiarismStats(); } catch(e) {}
            }

            const s = result.summary;
            const visiblePairs = getRenderablePairsWithIndex().length;
            alert(`✅ Results loaded!\n${s.file_count} files, ${s.pairs_count} total pairs\n(showing ${visiblePairs} above ${(appState.threshold*100).toFixed(0)}% threshold)`);
            return true;

        } else if (result.success && !result.cached) {
            alert('⚠️ No saved results for this directory.\nClick "Analyze" to analyze it.');
            return false;
        } else {
            alert('❌ Error: ' + (result.error || 'Unknown error'));
            return false;
        }
    } catch (error) {
        console.error('Error loading results:', error);
        alert('❌ Error loading: ' + error.message);
        return false;
    }
}

// Aggiorna valore soglia
function updateThreshold() {
    const slider = document.getElementById('thresholdSlider');
    const display = document.getElementById('thresholdValue');
    appState.threshold = slider.value / 100;
    display.textContent = slider.value + '%';
}

// Analizza directory
async function analyzeDirectory() {
    const directory = document.getElementById('directoryPath').value.trim();
    if (!directory) {
        alert('Enter a valid path');
        return;
    }
    const skipLeaf = document.getElementById('skipLeafCheckbox')?.checked || false;
    const cadType = getSelectedCadType();  // ✨ Tipo CAD selezionato

    const btn = document.getElementById('btnAnalyze');
    btn.disabled = true;
    btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Analyzing...';

    try {
        // Mostra la status bar
        const cadLabel = cadType === 'auto' ? 'all CAD types' : cadType;
        showStatusBar(`Starting analysis (${cadLabel})...`, 'Preparation...');
        startProgressPolling();
        markMatrixDirty({ clearData: true, reloadIfVisible: false });

        const response = await fetch('/api/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                directory: directory,
                skip_same_leaf: skipLeaf,
                cad_type: cadType  // ✨ Passa il tipo CAD al backend
            })
        });

        const result = await response.json();

        if (result.error) {
            setStatusError(result.error);
            return;
        }

        appState.currentDirectory = directory;
        saveToLocalStorage();
        // Il polling gestirà il caricamento dei dati quando completato

    } catch (error) {
        console.error('Error analyzing:', error);
        setStatusError(error.message || 'Error during analysis');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="bi bi-search"></i> Analyze';
    }
}

// Elimina tutta la cache
async function purgeCache() {
    if (!confirm('Are you sure you want to delete all cache data?')) return;

    try {
        const response = await fetch('/api/purge', { method: 'POST' });
        const data = await safeJsonParse(response, { success: false });
        if (data && data.success) {
            alert('✅ Cache fully cleared.');
            // Reset stato
            appState.signatures = [];
            appState.pairs = [];
            markMatrixDirty({ clearData: true, reloadIfVisible: false });
            renderFilesTable();
            renderPairs();
            updateStats();
        } else {
            alert('❌ Error: ' + (data.error || 'Unknown'));
        }
    } catch (error) {
        console.error('Error purging cache:', error);
        alert('❌ Error: ' + error.message);
    }
}

function isMatrixTabVisible() {
    const pane = document.getElementById('matrix');
    return !!(pane && pane.classList.contains('show') && pane.classList.contains('active'));
}

function scheduleMatrixReload(delayMs = 300) {
    if (_matrixReloadTimer) {
        clearTimeout(_matrixReloadTimer);
        _matrixReloadTimer = null;
    }
    const delay = Number.isFinite(delayMs) ? Math.max(0, delayMs) : 300;
    _matrixReloadTimer = setTimeout(() => {
        _matrixReloadTimer = null;
        loadMatrix();
    }, delay);
}

function markMatrixDirty(options = {}) {
    const clearData = options.clearData !== false;
    const reloadIfVisible = options.reloadIfVisible === true;
    const debounceMs = Number.isFinite(options.debounceMs) ? Math.max(0, options.debounceMs) : 300;

    appState.matrixLoaded = false;
    if (clearData) {
        appState.matrixData = null;
        if (isMatrixTabVisible()) {
            renderMatrixTable();
        }
    }

    if (reloadIfVisible && isMatrixTabVisible()) {
        scheduleMatrixReload(debounceMs);
    }
}

// Aggiorna la visualizzazione della matrice (dimensione celle, font, percentuali)
function updateMatrixDisplay() {
    const container = document.getElementById('matrixContainer');
    const table = container.querySelector('table.matrix-table');
    if (!table) return; // niente da aggiornare

    // Leggi le opzioni UI
    const cellSize = document.getElementById('matrixCellSize')?.value || 'medium';
    const fontSize = document.getElementById('matrixFontSize')?.value || 'small';
    const showPercent = document.getElementById('matrixShowPercent')?.checked !== false;

    // Rimuovi classi precedenti e applica quelle nuove
    table.classList.remove('cell-compact','cell-small','cell-medium','cell-large');
    table.classList.remove('font-x-small','font-small','font-medium','font-large');

    table.classList.add('cell-' + (cellSize === 'compact' ? 'compact' : cellSize));
    table.classList.add('font-' + (fontSize === 'x-small' ? 'x-small' : fontSize));

    // Aggiorna il contenuto delle celle in base a showPercent
    const highThreshold = getCriticalSimilarityThreshold();
    const mediumThreshold = getMediumSimilarityThreshold();
    table.querySelectorAll('td.matrix-value').forEach(td => {
        const raw = td.dataset.raw;
        if (raw === 'null' || raw === 'None' || raw === '') {
            td.innerHTML = '<span class="matrix-cell self">—</span>';
            return;
        }
        const val = parseFloat(raw);
        if (Number.isNaN(val)) {
            td.innerHTML = '<span class="matrix-cell">N/A</span>';
            return;
        }
        const pct = Math.round(val * 100);
        const cls = val >= highThreshold ? 'high' : val >= mediumThreshold ? 'medium' : 'low';
        const sameFolder = td.dataset.sameFolder === 'true';
        let cellClass = 'matrix-cell ' + cls;
        if (sameFolder) cellClass += ' same-folder';
        if (!showPercent) {
            td.innerHTML = `<span class="${cellClass}"></span>`;
        } else {
            td.innerHTML = `<span class="${cellClass}">${pct}%</span>`;
        }
    });
}

// Carica la matrice dal backend e la memorizza in appState.matrixData
async function loadMatrix() {
    const container = document.getElementById('matrixContainer');
    if (!container) return;
    const requestSeq = ++_matrixRequestSeq;
    if (_matrixFetchController) {
        try { _matrixFetchController.abort(); } catch (e) { /* ignore */ }
    }
    const controller = new AbortController();
    _matrixFetchController = controller;
    const timeoutId = setTimeout(() => {
        try { controller.abort(); } catch (e) { /* ignore */ }
    }, 120000);

    try {
        container.innerHTML = '<div class="text-center p-4"><i class="bi bi-hourglass-split spinner"></i> Generating matrix...</div>';
        const res = await fetch('/api/matrix', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ weights: { ...currentWeights } }),
            signal: controller.signal
        });
        const data = await safeJsonParse(res, null);
        if (requestSeq !== _matrixRequestSeq) {
            return;
        }
        console.log('🔁 /api/matrix response received:', !!data, data && data.files ? data.files.length + ' files' : 'no files');
        if (!data || !data.matrix) {
            container.innerHTML = '<p class="text-muted">No matrix available. Run an analysis first.</p>';
            return;
        }

        appState.matrixData = data; // cache
        appState.matrixLoaded = true;
        renderMatrixTable();
    } catch (e) {
        if (e && e.name === 'AbortError') {
            if (requestSeq === _matrixRequestSeq) {
                container.innerHTML = '<p class="text-warning">Matrix generation interrupted or timed out. Retry from the Matrix tab.</p>';
            }
            return;
        }
        console.error('Error loading matrix:', e);
        container.innerHTML = `<p class="text-danger">Error loading matrix: ${e.message || e}</p>`;
    } finally {
        clearTimeout(timeoutId);
        if (_matrixFetchController === controller) {
            _matrixFetchController = null;
        }
    }
}

// Renderizza la matrice nella UI usando appState.matrixData CON LIMITE per performance
function renderMatrixTable() {
    const container = document.getElementById('matrixContainer');
    if (!container) return;
    const data = appState.matrixData;
    if (!data || !data.files || !data.matrix) {
        container.innerHTML = '<p class="text-muted">No matrix available. Run an analysis first.</p>';
        return;
    }

    // ✨ PERFORMANCE FIX: Se matrice è troppo grande, mostra solo le prime 20 colonne e 20 righe
    let files = data.files;
    let rows = data.matrix;
    const MAX_MATRIX_SIZE = 20;
    let isTruncated = false;

    if (files && files.length > MAX_MATRIX_SIZE) {
        files = files.slice(0, MAX_MATRIX_SIZE);
        isTruncated = true;
    }
    if (rows && rows.length > MAX_MATRIX_SIZE) {
        rows = rows.slice(0, MAX_MATRIX_SIZE);
        isTruncated = true;
    }

    if (!files || files.length === 0) {
        container.innerHTML = '<p class="text-muted">No files in matrix.</p>';
        return;
    }

    let html = '<table class="matrix-table table table-bordered table-sm">';

    // Header: empty corner + file names
    html += '<thead><tr><th></th>' + files.map(f => `<th title="${f.path}"><small>${escapeHtml(f.name)}</small></th>`).join('') + '</tr></thead>';

    html += '<tbody>';
    for (const row of rows) {
        html += `<tr><th scope="row"><small>${escapeHtml(row.file)}</small></th>`;
        const rowPath = String(row.path || '');
        const rowParent = rowPath ? rowPath.replace(/[\\/]+/g, '/').replace(/\/+$/, '').split('/').slice(0, -1).join('/').toLowerCase() : '';
        for (const col of files) {
            const raw = row.similarities ? row.similarities[col.path] : null;
            const rawStr = (raw === null || raw === undefined) ? 'null' : String(raw);
            const colPath = String(col.path || '');
            const colParent = colPath ? colPath.replace(/[\\/]+/g, '/').replace(/\/+$/, '').split('/').slice(0, -1).join('/').toLowerCase() : '';
            const sameFolder = (rowParent && colParent && rowParent === colParent) ? 'true' : 'false';
            html += `<td class="matrix-value" data-raw="${rawStr}" data-same-folder="${sameFolder}"></td>`;
        }
        html += '</tr>';
    }
    html += '</tbody></table>';

    if (isTruncated) {
        html += `<div class="alert alert-info mt-2"><small><i class="bi bi-info-circle"></i> Matrix shown: ${files.length}×${rows.length} (max ${MAX_MATRIX_SIZE}×${MAX_MATRIX_SIZE} for performance). Full matrix available in "Show raw JSON".</small></div>`;
    }

    container.innerHTML = html;
    updateMatrixDisplay();
}

// Utility: escape simple text for HTML
function escapeHtml(txt) {
    if (!txt) return '';
    return String(txt).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

// Carica le firme (usa la funzione definita sopra)
// loadSignatures() è già definita a riga ~281

// Carica le coppie simili (usa la funzione definita sopra)
// loadPairs() è già definita a riga ~294

// Filtra coppie per soglia
async function filterPairs() {
    updateThreshold();
    await loadPairs();
    // Aggiorna le statistiche e ri-renderizza le coppie per riflettere la nuova soglia
    updateStats();
    renderPairs();
}

// Aggiorna statistiche
function updateStats(filesCount = null, pairsCount = null) {
    const panel = document.getElementById('statsPanel');
    const fc = filesCount !== null ? filesCount : appState.signatures.length;
    const totalPairs = Array.isArray(appState.pairs) ? appState.pairs.length : 0;
    const visiblePairs = getRenderablePairsWithIndex().length;
    const pc = pairsCount !== null ? pairsCount : visiblePairs;
    const hiddenCross = Math.max(0, totalPairs - visiblePairs);
    const showHiddenCross = isCrossSessionPairsFilterActive();

    panel.innerHTML = `
        <div class="mb-2">
            <i class="bi bi-file-earmark"></i> <strong>${fc}</strong> files analyzed
        </div>
        <div class="mb-2">
            <i class="bi bi-link-45deg"></i> <strong>${pc}</strong> similar pairs
        </div>
        ${showHiddenCross ? `
        <div class="mb-2">
            <i class="bi bi-funnel"></i> Hidden cross-session pairs: <strong>${hiddenCross}</strong>
        </div>
        ` : ''}
        <div class="mb-2">
            <i class="bi bi-percent"></i> Threshold: <strong>${(appState.threshold * 100).toFixed(0)}%</strong>
        </div>
    `;
}

// Renderizza tabella file
function renderFilesTable() {
    const tbody = document.getElementById('filesTableBody');

    if (!appState.signatures || appState.signatures.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" class="text-muted text-center">No files analyzed</td></tr>';
        return;
    }

    tbody.innerHTML = appState.signatures.map((sig, idx) => {
        const folder = sig.filepath ? sig.filepath.split('\\').slice(-2, -1)[0] : 'X';
        // ✨ Determina tipo CAD dall'estensione o dal campo cad_type
        const cadType = sig.cad_type || detectCadTypeFromPath(sig.filepath);
        const cadBadge = getCadBadge(cadType);

        return `
            <tr onclick="showFileDetailByIndex(${idx})" style="cursor: pointer;">
                <td><i class="bi bi-file-earmark"></i> ${sig.filename}</td>
                <td>${cadBadge}</td>
                <td><small class="text-muted">${folder}</small></td>
                <td>${sig.author || '<span class="text-muted">-</span>'}</td>
                <td><span class="badge bg-primary">${sig.feature_count}</span></td>
                <td><span class="badge bg-info">${sig.total_2d_geometry_count || 0}</span></td>
                <td><span class="badge bg-secondary">${sig.total_2d_constraint_count || 0}</span></td>
                <td>
                    <button class="btn btn-sm btn-outline-primary" onclick="event.stopPropagation(); showFileDetailByIndex(${idx})" title="View details">
                        <i class="bi bi-eye"></i>
                    </button>
                </td>
            </tr>
        `;
    }).join('');
}

// ✨ Rileva tipo CAD dal path del file
function detectCadTypeFromPath(filepath) {
    if (!filepath) return 'Unknown';
    const ext = filepath.toLowerCase().split('.').pop();
    const extMap = {
        'par': 'SolidEdge', 'psm': 'SolidEdge', 'asm': 'SolidEdge',
        'sldprt': 'SolidWorks', 'sldasm': 'SolidWorks', 'slddrw': 'SolidWorks',
        'ipt': 'Inventor', 'iam': 'Inventor', 'idw': 'Inventor',
        'catpart': 'CATIA', 'catproduct': 'CATIA', 'catdrawing': 'CATIA',
        'fcstd': 'FreeCAD',
        'f3d': 'Fusion360', 'f3z': 'Fusion360'
    };
    return extMap[ext] || 'Unknown';
}

// ✨ Genera badge HTML per tipo CAD
function getCadBadge(cadType) {
    const badges = {
        'SolidEdge': '<span class="cad-type-badge solid-edge">SE</span>',
        'SolidWorks': '<span class="cad-type-badge solidworks">SW</span>',
        'Inventor': '<span class="cad-type-badge inventor">INV</span>',
        'CATIA': '<span class="cad-type-badge catia">CAT</span>',
        'FreeCAD': '<span class="cad-type-badge freecad">FC</span>',
        'Fusion360': '<span class="cad-type-badge fusion360">F360</span>',
        'Unknown': '<span class="cad-type-badge auto">X</span>'
    };
    return badges[cadType] || badges['Unknown'];
}

// ✨ NUOVA FUNZIONE: Mostra dettagli file per indice
function showFileDetailByIndex(idx) {
    if (idx < 0 || idx >= appState.signatures.length) {
        alert('File not found');
        return;
    }
    const sig = appState.signatures[idx];
    showFileDetailFromSignature(sig);
}

// Mostra dettagli da signature
function showFileDetailFromSignature(sig) {
    if (!sig) {
        alert('File data not available');
        return;
    }

    // DEBUG: Log i dati ricevuti
    console.log('🔍 DEBUG: File data received:');
    console.log('   filepath:', sig.filepath);
    console.log('   sketches_count:', sig.sketches_count);
    console.log('   sketches_data:', sig.sketches_data);

    const modal = new bootstrap.Modal(document.getElementById('fileDetailModal'));
    document.getElementById('fileDetailTitle').textContent = sig.filename;

    // Costruisci contenuto
    let sketchesHtml = '';
    if (sig.sketches_data && sig.sketches_data.length > 0) {
        sketchesHtml = sig.sketches_data.map((sk, skIdx) => {
            // Elenco dettagliato geometrie
            let geometrieHtml = '<div class="mt-2"><small class="text-muted"><strong>Geometric entities:</strong></small>';
            if (sk.geometry_detailed && sk.geometry_detailed.length > 0) {
                geometrieHtml += '<ul class="list-unstyled small mb-2">';
                sk.geometry_detailed.forEach((geom, gIdx) => {
                    geometrieHtml += `<li><code>${geom.id}</code> - ${geom.type}</li>`;
                });
                geometrieHtml += '</ul>';
            } else {
                geometrieHtml += '<p class="text-muted">-</p>';
            }
            geometrieHtml += '</div>';

            // Elenco dettagliato vincoli
            let vincoliHtml = '<div class="mt-2"><small class="text-muted"><strong>Constraints:</strong></small>';
            if (sk.constraint_detailed && sk.constraint_detailed.length > 0) {
                vincoliHtml += '<ul class="list-unstyled small mb-2">';
                sk.constraint_detailed.forEach((constraint, cIdx) => {
                    let valueStr = constraint.value !== null && constraint.value !== undefined ? ` = ${Number(constraint.value).toFixed(2)}` : '';
                    let categoria = constraint.categoria ? `<span class="badge ${constraint.tipo === 'geometrico' ? 'bg-info' : constraint.tipo === 'dimensionale' ? 'bg-warning' : 'bg-secondary'} me-2">${constraint.categoria}</span>` : '';
                    let descrizione = constraint.descrizione ? ` - <em>${constraint.descrizione}</em>` : '';
                    vincoliHtml += `<li>${categoria}<code>${constraint.id}</code>${descrizione}${valueStr}</li>`;
                });
                vincoliHtml += '</ul>';
            } else {
                vincoliHtml += '<p class="text-muted">No constraints</p>';
            }
            vincoliHtml += '</div>';

            return `
            <div class="sketch-card">
                <strong>📋 ${sk.name || 'Sketch'}</strong>
                <div class="row mt-2">
                    <div class="col-6">
                        <small class="text-muted">2D Geometries: ${sk.geometry_count || 0}</small>
                        <div class="geometry-list">
                            ${Object.entries(sk.geometry_types || {}).map(([type, count]) => 
                                `<span class="geom-badge">${type}: ${count}</span>`
                            ).join('')}
                        </div>
                        ${geometrieHtml}
                    </div>
                    <div class="col-6">
                        <small class="text-muted">Constraints: ${sk.constraint_count || 0}</small>
                        <div class="constraint-list">
                            ${Object.entries(sk.constraint_types || {}).map(([type, count]) => 
                                `<span class="constraint-badge">${type}: ${count}</span>`
                            ).join('')}
                        </div>
                        ${vincoliHtml}
                    </div>
                </div>
            </div>
            `;
        }).join('');
    } else {
        sketchesHtml = '<p class="text-muted">No sketch data available</p>';
    }

    document.getElementById('fileDetailBody').innerHTML = `
        <div class="detail-section">
            <h6><i class="bi bi-info-circle"></i> General Information</h6>
            <div class="row">
                <div class="col-md-6">
                    <p><strong>File:</strong> ${sig.filename}</p>
                    <p><strong>Author:</strong> ${sig.author || '-'}</p>
                    <p><strong>Last saved by:</strong> ${sig.last_author || '-'}</p>
                </div>
                <div class="col-md-6">
                    <p><strong>Company:</strong> ${sig.company || '-'}</p>
                    <p><strong>Template:</strong> ${sig.template || '-'}</p>
                    <p><strong>Hash:</strong> <code>${sig.file_hash || '-'}</code></p>
                </div>
            </div>
        </div>

        <div class="detail-section">
            <h6><i class="bi bi-box"></i> 3D Features (${sig.feature_count})</h6>
            <div class="mb-2">
                ${Object.entries(sig.feature_types || {}).map(([type, count]) => 
                    `<span class="feature-badge">${type}: ${count}</span>`
                ).join(' ')}
            </div>
            <p><strong>Sequence:</strong></p>
            <div class="feature-sequence">
                ${(sig.feature_sequence || []).map((feat, i) => 
                    `<span class="sequence-item">${i+1}. ${feat}</span>`
                ).join('')}
            </div>
        </div>

        <div class="detail-section">
            <h6><i class="bi bi-pencil-square"></i> 2D Sketches (${sig.sketches_count || 0})</h6>
            <div class="row mb-2">
                <div class="col-md-4">
                    <strong>Total 2D Geometries:</strong> ${sig.total_2d_geometry_count || 0}
                </div>
                <div class="col-md-4">
                    <strong>Total 2D Constraints:</strong> ${sig.total_2d_constraint_count || 0}
                </div>
                <div class="col-md-4">
                    <strong>Ratio C/G:</strong> ${(sig.constraint_to_geometry_ratio || 0).toFixed(2)}
                </div>
            </div>
            ${sketchesHtml}
        </div>

        <div class="detail-section">
            <h6><i class="bi bi-palette"></i> Modeling Style</h6>
            <div class="row">
                <div class="col-md-3">
                    <p><strong>Extrusion ratio:</strong> ${((sig.extrusion_ratio || 0) * 100).toFixed(1)}%</p>
                </div>
                <div class="col-md-3">
                    <p><strong>Cutout ratio:</strong> ${((sig.cutout_ratio || 0) * 100).toFixed(1)}%</p>
                </div>
                <div class="col-md-3">
                    <p><strong>Hole ratio:</strong> ${((sig.hole_ratio || 0) * 100).toFixed(1)}%</p>
                </div>
                <div class="col-md-3">
                    <p><strong>Round/Chamfer:</strong> ${((sig.round_chamfer_ratio || 0) * 100).toFixed(1)}%</p>
                </div>
            </div>
            <p><strong>Naming style:</strong> ${sig.naming_style || 'unknown'}</p>
        </div>
    `;

    modal.show();
}

// ✨ HELPER: ottiene la similarità coerente con i pesi correnti partendo dai dettagli grezzi
function calculateSimilarityFromDetails(details) {
    if (!details || typeof details !== 'object') {
        return null;
    }

    const recombined = combineScoresClient(details, currentWeights);
    return typeof recombined.overall === 'number' ? recombined.overall : null;
}

// ✨ VIRTUALIZATION STATE - Mostra solo coppie visibili (fix lag)
let _renderPairsDebounceTimer = null;
let _lastRenderedPairsCount = 0;
const PAIRS_PER_PAGE = 50;  // Mostra 50 coppie per volta
let _pairsCurrentPage = 0;
let _visiblePairRefreshInFlight = false;

function isCrossSessionPairsFilterActive() {
    return !!(paperWritingState?.enabled && paperWritingState?.hideCrossSessionPairs);
}

function isCrossSessionPair(pair) {
    if (!pair || typeof pair !== 'object') {
        return false;
    }
    const session1 = normalizePairLabelSession(inferSessionNameFromFilePath(pair.path1 || ''));
    const session2 = normalizePairLabelSession(inferSessionNameFromFilePath(pair.path2 || ''));
    return !!(session1 && session2 && session1 !== session2);
}

function getRenderablePairsWithIndex() {
    const pairs = Array.isArray(appState.pairs) ? appState.pairs : [];
    if (!isCrossSessionPairsFilterActive()) {
        return pairs.map((pair, idx) => ({ pair, idx }));
    }
    return pairs
        .map((pair, idx) => ({ pair, idx }))
        .filter(item => !isCrossSessionPair(item.pair));
}

// Renderizza coppie simili CON VIRTUALIZZAZIONE e debounce
function renderPairs(pageNum = _pairsCurrentPage) {
    const container = document.getElementById('pairsContainer');
    const filteredPairs = getRenderablePairsWithIndex();
    const isCrossFilterActive = isCrossSessionPairsFilterActive();

    console.log('🔍 renderPairs() called:', {
        'appState.pairs.length': appState.pairs.length,
        'filteredPairs.length': filteredPairs.length,
        'appState.threshold': appState.threshold,
        'paperWritingEnabled': paperWritingState.enabled,
        'hideCrossSessionPairs': isCrossFilterActive,
        'page': pageNum
    });

    if (filteredPairs.length === 0) {
        const emptyMsg = isCrossFilterActive
            ? 'No pairs available after hiding cross-session pairs.'
            : `No pairs found with similarity ≥ ${(appState.threshold * 100).toFixed(0)}%`;
        container.innerHTML = `
            <div class="alert alert-info">
                <i class="bi bi-info-circle"></i> ${emptyMsg}
            </div>
        `;
        return;
    }

    const totalPages = Math.max(1, Math.ceil(filteredPairs.length / PAIRS_PER_PAGE));
    const safePageNum = Math.min(Math.max(pageNum, 0), totalPages - 1);
    _pairsCurrentPage = safePageNum;

    const startIdx = safePageNum * PAIRS_PER_PAGE;
    const endIdx = Math.min(startIdx + PAIRS_PER_PAGE, filteredPairs.length);
    const pairsToShow = filteredPairs.slice(startIdx, endIdx);
    const staleCount = filteredPairs.filter(item => item?.pair?._syncState === 'stale').length;

    let html = '';

    if (staleCount > 0) {
        html += `
            <div class="pairs-sync-note" title="Some scores are waiting for a full recomputation with the current raw-score settings.">
                scores updating
            </div>
        `;
    }

    // Pagination controls
    if (totalPages > 1) {
        html += '<div class="pagination-controls mb-2"><small>';
        html += `Page ${safePageNum + 1} of ${totalPages} (${filteredPairs.length} visible pairs) | `;
        if (safePageNum > 0) {
            html += `<a href="#" onclick="renderPairs(${safePageNum - 1}); return false;">← Prev</a> | `;
        }
        if (safePageNum < totalPages - 1) {
            html += `<a href="#" onclick="renderPairs(${safePageNum + 1}); return false;">Next →</a>`;
        }
        html += '</small></div>';
    }

    html += pairsToShow.map((entry) => {
        const pair = entry.pair;
        const idx = entry.idx;
        // Clamp similarity a [0, 1] per sicurezza (dati vecchi in cache potrebbero avere valori > 1)
        const displaySimilarity = Math.max(0, Math.min(1, pair.similarity || 0));
        const escapedPath1 = (pair.path1 || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        const escapedPath2 = (pair.path2 || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        const highThreshold = getCriticalSimilarityThreshold();
        const mediumThreshold = getMediumSimilarityThreshold();

        const simClass = displaySimilarity >= highThreshold ? 'high-similarity' :
                        displaySimilarity >= mediumThreshold ? 'medium-similarity' : '';
        const badgeClass = displaySimilarity >= highThreshold ? 'similarity-high' :
                          displaySimilarity >= mediumThreshold ? 'similarity-medium' : 'similarity-low';
        const syncIndicator = pair?._syncState === 'stale'
            ? '<span class="pair-sync-indicator" title="Score pending full recomputation"></span>'
            : '';

        const pairSession = inferPairSessionName(pair.path1 || '', pair.path2 || '');
        const pairKey = getPairLabelKey(pair.file1, pair.file2, pair.path1 || '', pair.path2 || '', pairSession);
        const paperWritingHtml = getPaperWritingButtonsHtml(pair.file1, pair.file2, displaySimilarity, pair.path1 || '', pair.path2 || '', pairSession);
        const escapedPairKey = escapeHtml(pairKey);

        return `
            <div class="card pair-card ${simClass}" data-pair-key="${escapedPairKey}">
                <div class="card-body">
                    <div class="row align-items-center" onclick="showPairDetailByPaths('${escapedPath1}', '${escapedPath2}', ${idx})" style="cursor: pointer;">
                        <div class="col-md-4">
                            <h6 class="mb-1"><i class="bi bi-file-earmark"></i> ${pair.file1}</h6>
                            <small class="text-muted">
                                <i class="bi bi-folder"></i> ${pair.folder1}
                                <span class="badge bg-light text-dark ms-2">${pair.features1} feat</span>
                            </small>
                        </div>
                        <div class="col-md-2 text-center">
                            <div class="similarity-badge-wrap">
                                <span class="similarity-badge ${badgeClass}">
                                    ${(displaySimilarity * 100).toFixed(1)}%
                                </span>
                                ${syncIndicator}
                            </div>
                        </div>
                        <div class="col-md-4">
                            <h6 class="mb-1"><i class="bi bi-file-earmark"></i> ${pair.file2}</h6>
                            <small class="text-muted">
                                <i class="bi bi-folder"></i> ${pair.folder2}
                                <span class="badge bg-light text-dark ms-2">${pair.features2} feat</span>
                            </small>
                        </div>
                        <div class="col-md-2 text-end">
                            <button class="btn btn-sm btn-outline-primary" onclick="event.stopPropagation(); showPairDetailByPaths('${escapedPath1}', '${escapedPath2}', ${idx})">
                                <i class="bi bi-search"></i> Analyze
                            </button>
                        </div>
                    </div>
                    ${paperWritingHtml}
                </div>
            </div>
        `;
    }).join('');

    container.innerHTML = html;

    if (pairsToShow.some(item => item?.pair?._syncState === 'stale')) {
        refreshVisiblePairScores();
    }
}

function showPairDetailByPaths(path1 = '', path2 = '', fallbackIdx = -1) {
    const hasPaths = !!(path1 && path2);
    if (hasPaths && appState.pairs && appState.pairs.length > 0) {
        const idxByPath = findPairIndexByPaths(path1, path2);
        if (idxByPath !== -1) {
            showPairDetail(idxByPath);
            return;
        }
        console.warn('showPairDetailByPaths: pair not found by path', { path1, path2, fallbackIdx });
        alert('Error: pair not found');
        return;
    }
    const idx = Number(fallbackIdx);
    if (Number.isInteger(idx) && idx >= 0 && idx < (appState.pairs || []).length) {
        showPairDetail(idx);
        return;
    }
    console.warn('showPairDetailByPaths: pair not found', { path1, path2, fallbackIdx });
    alert('Error: pair not found');
}

// Mostra dettaglio coppia
function showPairDetail(idx) {
    try {
        const pair = appState.pairs[idx];
        if (!pair) {
            console.error('❌ Pair not found:', idx);
            alert('Error: pair not found');
            return;
        }

        console.log('🔍 showPairDetail called for pair:', pair);

        // Passa al tab confronto
        const tab = new bootstrap.Tab(document.getElementById('compare-tab'));
        tab.show();

        // Attendi che il tab sia visibile, poi imposta i valori e confronta
        setTimeout(() => {
            const sel1 = document.getElementById('compareFile1');
            const sel2 = document.getElementById('compareFile2');

            if (!sel1 || !sel2) {
                console.error('❌ Selectors not found!');
                alert('Error during loading: selectors not found');
                return;
            }

            // Imposta i selettori (path1 e path2 sono filepath completi)
            sel1.value = pair.path1;
            sel2.value = pair.path2;

            console.log('📋 Selectors set:', {
                'sel1.value': sel1.value,
                'sel2.value': sel2.value,
                'path1': pair.path1,
                'path2': pair.path2
            });

            // Verifica se i valori sono stati impostati correttamente
            if (!sel1.value || !sel2.value) {
                console.error('❌ Values not set in selectors!');
                console.log('Available values in sel1:', Array.from(sel1.options).map(o => o.value));
                alert('Error: unable to select files. Reload results.');
                return;
            }

            // Esegui il confronto automaticamente
            compareFiles();
        }, 100);
    } catch (error) {
        console.error('❌ Error in showPairDetail:', error);
        alert('Error during loading:\n' + error.message);
    }
}

// Popola selettori confronto
function populateCompareSelectors() {
    const sel1 = document.getElementById('compareFile1');
    const sel2 = document.getElementById('compareFile2');

    const options = appState.signatures.map(sig => {
        const folder = sig.filepath ? sig.filepath.split('\\').slice(-2, -1)[0] : 'X';
        return `<option value="${sig.filepath}">[${folder}] ${sig.filename}</option>`;
    }).join('');

    sel1.innerHTML = '<option value="">Select first file...</option>' + options;
    sel2.innerHTML = '<option value="">Select second file...</option>' + options;
}

// ✨ PESI MODIFICABILI - Default (sincronizzati con config.json)
// NOTA: i pesi vengono SOVRASCRITTI dal server all'avvio (vedi initApp → loadWeightsFromServer)
// I valori qui sotto sono solo un fallback se il server non risponde.
// I pesi numerici devono sommare a 1.0 (100%). I parametri lcs_fuzzy_* e fuzzy_combination_*
// NON sono pesi — sono parametri di configurazione e NON vengono inclusi nel totale.
let currentWeights = {
    'author_match': 0.02,
    'feature_count_similarity': 0.05,
    'feature_type_similarity': 0.11,
    'style_similarity': 0.04,
    'bigram_similarity': 0.02,
    'trigram_similarity': 0.03,
    'lcs_similarity': 0.25,
    'feature_names_similarity': 0.02,
    'geometry_2d_similarity': 0.26,
    'constraint_2d_similarity': 0.11,
    'constraint_ratio_similarity': 0.09,
    'sketch_parametric_similarity': 0.00,
    'lcs_fuzzy_enabled': true,
    'lcs_fuzzy_function': 'exponential',
    'lcs_fuzzy_alpha': 2.0,
    'lcs_fuzzy_mix': 0.7,
    'fuzzy_combination_enabled': false,
    'fuzzy_combination_method': 'gaussian',
    'fuzzy_combination_penalty': 0.3,
    'fuzzy_combination_boost': 0.15
};

let defaultWeights = { ...currentWeights };
const RAW_SCORER_VERSION = 4;

function toFiniteNumber(value, fallback = 0) {
    const num = Number(value);
    return Number.isFinite(num) ? num : fallback;
}

function clamp01(value) {
    return Math.max(0, Math.min(1, toFiniteNumber(value, 0)));
}

function cloneRawScoreConfig(config = {}) {
    return {
        scorer_version: toFiniteNumber(config.scorer_version, RAW_SCORER_VERSION),
        lcs_fuzzy_enabled: config.lcs_fuzzy_enabled !== false,
        lcs_fuzzy_function: String(config.lcs_fuzzy_function || 'exponential'),
        lcs_fuzzy_alpha: toFiniteNumber(config.lcs_fuzzy_alpha, 2.0),
        lcs_fuzzy_mix: toFiniteNumber(config.lcs_fuzzy_mix, 0.7)
    };
}

function extractRawScoreConfigFromWeights(weights = currentWeights) {
    return cloneRawScoreConfig({
        scorer_version: RAW_SCORER_VERSION,
        lcs_fuzzy_enabled: weights?.lcs_fuzzy_enabled,
        lcs_fuzzy_function: weights?.lcs_fuzzy_function,
        lcs_fuzzy_alpha: weights?.lcs_fuzzy_alpha,
        lcs_fuzzy_mix: weights?.lcs_fuzzy_mix
    });
}

function rawScoreConfigsEqual(a, b) {
    const lhs = cloneRawScoreConfig(a || {});
    const rhs = cloneRawScoreConfig(b || {});
    return lhs.scorer_version === rhs.scorer_version &&
        lhs.lcs_fuzzy_enabled === rhs.lcs_fuzzy_enabled &&
        lhs.lcs_fuzzy_function === rhs.lcs_fuzzy_function &&
        Math.abs(lhs.lcs_fuzzy_alpha - rhs.lcs_fuzzy_alpha) < 1e-9 &&
        Math.abs(lhs.lcs_fuzzy_mix - rhs.lcs_fuzzy_mix) < 1e-9;
}

function sortPairsBySimilarity() {
    appState.pairs.sort((a, b) => (Number(b.similarity) || 0) - (Number(a.similarity) || 0));
}

function getPairRawScoreConfig(pair) {
    return cloneRawScoreConfig((pair && pair._rawScoreConfig) || appState.pairsRawScoreConfig || {});
}

function buildScoringWeightsSnapshot(weights = {}) {
    const snapshot = {};
    Object.entries(weights || {}).forEach(([key, value]) => {
        if (String(key).startsWith('_')) {
            return;
        }
        if (Number.isFinite(value)) {
            snapshot[key] = Number(value);
            return;
        }
        if (key.startsWith('lcs_fuzzy_') || key.startsWith('fuzzy_combination_')) {
            if (typeof value === 'boolean' || typeof value === 'string') {
                snapshot[key] = value;
            }
        }
    });
    return snapshot;
}

function scoringWeightsEquivalent(weightsA, weightsB) {
    const a = buildScoringWeightsSnapshot(weightsA || {});
    const b = buildScoringWeightsSnapshot(weightsB || {});
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const key of keys) {
        if (!(key in a) || !(key in b)) {
            return false;
        }
        const va = a[key];
        const vb = b[key];
        if (typeof va === 'number' && typeof vb === 'number') {
            if (Math.abs(va - vb) > 1e-9) {
                return false;
            }
        } else if (va !== vb) {
            return false;
        }
    }
    return true;
}

function normalizePathForMatch(path) {
    if (!path) {
        return '';
    }
    return String(path)
        .replace(/\|/g, ':')
        .replace(/\//g, '\\')
        .trim()
        .toLowerCase();
}

function buildPairLookupKey(path1, path2) {
    const a = normalizePathForMatch(path1);
    const b = normalizePathForMatch(path2);
    if (!a || !b) {
        return '';
    }
    return a <= b ? `${a}||${b}` : `${b}||${a}`;
}

function findPairIndexByPaths(path1, path2) {
    const expectedKey = buildPairLookupKey(path1, path2);
    if (!expectedKey || !Array.isArray(appState.pairs) || appState.pairs.length === 0) {
        return -1;
    }
    return appState.pairs.findIndex(pair => buildPairLookupKey(pair?.path1 || '', pair?.path2 || '') === expectedKey);
}

function findPairIndex(file1, file2, options = {}) {
    const pathA = options.path1 || file1?.filepath || '';
    const pathB = options.path2 || file2?.filepath || '';
    if (pathA && pathB) {
        const idxByPath = findPairIndexByPaths(pathA, pathB);
        if (idxByPath !== -1) {
            return idxByPath;
        }
        // Con path espliciti non fare fallback sui filename: in sessioni diverse possono essere duplicati.
        return -1;
    }

    return appState.pairs.findIndex(pair =>
        (pair?.file1 === file1?.filename && pair?.file2 === file2?.filename) ||
        (pair?.file1 === file2?.filename && pair?.file2 === file1?.filename)
    );
}

function combineScoresClient(rawScores, weights = currentWeights) {
    const scores = { ...(rawScores || {}) };
    const useFuzzy = !!weights?.fuzzy_combination_enabled;
    const fuzzyMethod = weights?.fuzzy_combination_method || 'gaussian';
    const fuzzyPenalty = toFiniteNumber(weights?.fuzzy_combination_penalty, 0.3);
    const fuzzyBoost = toFiniteNumber(weights?.fuzzy_combination_boost, 0.15);

    const numericWeights = {};
    Object.entries(weights || {}).forEach(([key, value]) => {
        if (!key.startsWith('lcs_fuzzy_') &&
            !key.startsWith('fuzzy_combination_') &&
            !key.startsWith('_') &&
            Number.isFinite(value)) {
            numericWeights[key] = Number(value);
        }
    });

    const amScore = clamp01(scores.author_match);
    if (Object.prototype.hasOwnProperty.call(numericWeights, 'author_match')) {
        numericWeights.author_match = numericWeights.author_match * amScore;
    }

    const unavailable = new Set(
        Array.isArray(scores._unavailable_criteria) ? scores._unavailable_criteria.map(String) : []
    );
    const exclusionPolicy = getCriteriaExclusionPolicy();
    let excludeIfUnavailable = exclusionPolicy.exclude_if_unavailable !== false;
    let excludeIfMissingOrNonFinite = exclusionPolicy.exclude_if_missing_or_non_finite !== false;
    let forceExcluded = new Set((exclusionPolicy.force_excluded || []).map(String));
    let forceIncluded = new Set((exclusionPolicy.force_included || []).map(String));
    forceIncluded = new Set(Array.from(forceIncluded).filter(key => !forceExcluded.has(key)));

    // Modalita' legacy: comportamento storico immutato.
    if (exclusionPolicy.enabled === false) {
        excludeIfUnavailable = true;
        excludeIfMissingOrNonFinite = true;
        forceExcluded = new Set();
        forceIncluded = new Set();
    }

    const activeCriteria = [];
    const excludedCriteria = [];

    Object.entries(numericWeights).forEach(([key, weight]) => {
        if (!(weight > 0)) {
            excludedCriteria.push(key);
            return;
        }
        if (forceExcluded.has(key)) {
            excludedCriteria.push(key);
            return;
        }
        const forcedIncluded = forceIncluded.has(key);
        if (unavailable.has(key) && excludeIfUnavailable && !forcedIncluded) {
            excludedCriteria.push(key);
            return;
        }

        let scoreValue = Number(scores[key]);
        if (!Number.isFinite(scoreValue)) {
            if (excludeIfMissingOrNonFinite && !forcedIncluded) {
                excludedCriteria.push(key);
                return;
            }
            // Se la policy non esclude i missing/non-finite, includi il criterio con score 0.
            scoreValue = 0;
        }

        const normalizedScore = clamp01(scoreValue);
        scores[key] = normalizedScore;
        activeCriteria.push([key, Number(weight), normalizedScore]);
    });

    scores._active_criteria = activeCriteria.map(([key]) => key);
    scores._excluded_criteria = [...new Set(excludedCriteria)];

    if (!useFuzzy) {
        const totalWeight = activeCriteria.reduce((sum, [, weight]) => sum + weight, 0);
        if (totalWeight <= 0) {
            scores.overall = 0.0;
        } else {
            const linearSimilarity = activeCriteria.reduce(
                (sum, [, weight, scoreValue]) => sum + scoreValue * (weight / totalWeight),
                0
            );
            scores.overall = clamp01(linearSimilarity);
        }
        return scores;
    }

    if (activeCriteria.length === 0) {
        scores.overall = 0.0;
        return scores;
    }

    const weightsList = activeCriteria.map(([, weight]) => weight);
    const scoreList = activeCriteria.map(([, , scoreValue]) => scoreValue);
    const totalWeight = weightsList.reduce((sum, weight) => sum + weight, 0);

    if (totalWeight <= 0) {
        scores.overall = 0.0;
        return scores;
    }

    const linearSimilarity = activeCriteria.reduce(
        (sum, [, weight, scoreValue]) => sum + (scoreValue * weight),
        0
    ) / totalWeight;

    const normalizedWeights = weightsList.map(weight => weight / totalWeight);
    const meanScore = scoreList.reduce(
        (sum, scoreValue, idx) => sum + scoreValue * normalizedWeights[idx],
        0
    );
    const weightedVariance = scoreList.reduce(
        (sum, scoreValue, idx) => sum + (((scoreValue - meanScore) ** 2) * normalizedWeights[idx]),
        0
    );
    const weightedStd = Math.sqrt(weightedVariance);

    let coherence;
    if (fuzzyMethod === 'triangular') {
        coherence = Math.max(0.0, 1.0 - (weightedStd / 0.35));
    } else if (fuzzyMethod === 'gaussian') {
        coherence = Math.exp(-4.0 * (weightedStd ** 2));
    } else {
        coherence = 1.0 / (1.0 + 8.0 * (weightedStd ** 2));
    }

    const boostComponent = fuzzyBoost * coherence;
    const penaltyComponent = fuzzyPenalty * (1.0 - coherence);
    const fuzzyFactor = 1.0 + boostComponent - penaltyComponent;

    scores.overall = clamp01(linearSimilarity * fuzzyFactor);
    scores._fuzzy_method = fuzzyMethod;
    scores._fuzzy_weighted_std = weightedStd;
    scores._fuzzy_weighted_mean = meanScore;
    scores._fuzzy_coherence = coherence;
    scores._fuzzy_factor = fuzzyFactor;
    scores._fuzzy_boost = boostComponent;
    scores._fuzzy_penalty = penaltyComponent;
    scores._fuzzy_linear = linearSimilarity;

    return scores;
}

function syncPairsWithCurrentWeights() {
    const currentRawConfig = extractRawScoreConfigFromWeights(currentWeights);
    let staleCount = 0;
    let changedCount = 0;

    (appState.pairs || []).forEach(pair => {
        if (!pair || typeof pair !== 'object' || !pair.details || typeof pair.details !== 'object') {
            if (pair) pair._syncState = 'stale';
            staleCount += 1;
            return;
        }

        if (!rawScoreConfigsEqual(getPairRawScoreConfig(pair), currentRawConfig)) {
            pair._syncState = 'stale';
            staleCount += 1;
            return;
        }

        const prevSimilarity = toFiniteNumber(pair.similarity, 0);
        const recombined = combineScoresClient(pair.details, currentWeights);
        const nextSimilarity = toFiniteNumber(recombined.overall, prevSimilarity);
        if (Math.abs(prevSimilarity - nextSimilarity) > 1e-9) {
            changedCount += 1;
        }

        pair.similarity = nextSimilarity;
        pair.details = recombined;
        pair._rawScoreConfig = cloneRawScoreConfig(currentRawConfig);
        pair._syncState = 'current';
    });

    sortPairsBySimilarity();
    return { staleCount, changedCount };
}

function handleWeightsChanged(options = {}) {
    const forceRawRecompute = !!options.forceRawRecompute;
    const deepRecompute = !!options.deepRecompute;
    appState.pairsSyncDirty = true;
    appState.pairsForceRawRecompute = appState.pairsForceRawRecompute || forceRawRecompute;
    markMatrixDirty({ clearData: false, reloadIfVisible: true, debounceMs: 450 });

    if (appState.pairs && appState.pairs.length > 0) {
        syncPairsWithCurrentWeights();
        updateStats();
        renderPairs(_pairsCurrentPage);
    }

    autoRecalculateComparison({ forceRawRecompute });
    if (forceRawRecompute) {
        refreshVisiblePairScores();
        scheduleRecalculation({ forceRawRecompute: true });
    } else if (deepRecompute) {
        scheduleRecalculation({ forceRawRecompute: false });
    }
}

async function refreshVisiblePairScores() {
    if (_visiblePairRefreshInFlight || !appState.pairs || appState.pairs.length === 0) {
        return;
    }

    const renderablePairs = getRenderablePairsWithIndex();
    const startIdx = _pairsCurrentPage * PAIRS_PER_PAGE;
    const endIdx = Math.min(startIdx + PAIRS_PER_PAGE, renderablePairs.length);
    const stalePairs = renderablePairs
        .slice(startIdx, endIdx)
        .map(item => item.pair)
        .filter(pair => pair && pair._syncState === 'stale' && pair.path1 && pair.path2);

    if (stalePairs.length === 0) {
        return;
    }

    const pairIndexByKey = new Map();
    (appState.pairs || []).forEach((pair, idx) => {
        const key = buildPairLookupKey(pair?.path1 || '', pair?.path2 || '');
        if (key && !pairIndexByKey.has(key)) {
            pairIndexByKey.set(key, idx);
        }
    });

    _visiblePairRefreshInFlight = true;
    try {
        const response = await fetch('/api/compare_batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                weights: { ...currentWeights },
                items: stalePairs.map(pair => ({
                    path1: pair.path1.replace(/:/g, '|'),
                    path2: pair.path2.replace(/:/g, '|')
                }))
            })
        });

        if (!response.ok) {
            return;
        }

        const result = await safeJsonParse(response, null);
        if (!result || !result.success || !Array.isArray(result.results)) {
            return;
        }

        const rawCfg = cloneRawScoreConfig(result.raw_score_config || extractRawScoreConfigFromWeights(currentWeights));
        let updatedCount = 0;

        result.results.forEach(item => {
            if (!item || item.error) {
                return;
            }
            const pairKey = buildPairLookupKey(item.path1, item.path2);
            const pairIndex = pairKey ? pairIndexByKey.get(pairKey) : -1;
            if (!Number.isInteger(pairIndex) || pairIndex < 0) {
                return;
            }

            appState.pairs[pairIndex].similarity = toFiniteNumber(item.similarity, appState.pairs[pairIndex].similarity);
            appState.pairs[pairIndex].details = item.details || appState.pairs[pairIndex].details;
            appState.pairs[pairIndex]._rawScoreConfig = rawCfg;
            appState.pairs[pairIndex]._syncState = 'current';
            updatedCount += 1;
        });

        if (updatedCount > 0) {
            const staleLeft = appState.pairs.some(pair => pair?._syncState === 'stale');
            appState.pairsSyncDirty = staleLeft;
            if (!staleLeft) {
                appState.pairsForceRawRecompute = false;
            }
            sortPairsBySimilarity();
            renderPairs(_pairsCurrentPage);
            updateStats();
        }
    } catch (error) {
        console.warn('Visible pair refresh failed:', error);
    } finally {
        _visiblePairRefreshInFlight = false;
    }
}

// Descrizioni criteri (in inglese)
const criteriaDescriptions = {
    'author_match': 'Author Match',
    'feature_count_similarity': 'Feature Count Match',
    'feature_type_similarity': 'Feature Types Distribution',
    'style_similarity': 'Modeling Style',
    'bigram_similarity': 'Consecutive Pairs (2 feat.)',
    'trigram_similarity': 'Consecutive Triples (3 feat.)',
    'lcs_similarity': 'Longest Common Subsequence (LCS)',
    'feature_names_similarity': 'Custom Feature Names',  // ✨ Aggiunto
    'geometry_2d_similarity': '2D Sketch Geometries',
    'constraint_2d_similarity': '2D Sketch Constraints',
    'constraint_ratio_similarity': 'Constraints/Geometry Ratio',
    'sketch_parametric_similarity': 'Sketch Topology (COM)'
};

// ✨ Descrizioni dettagliate per tooltip informativi
const criteriaDetailedInfo = {
    'author_match': {
        'title': 'Author Match',
        'description': 'Compares the author name in file metadata.',
        'example': 'If both files have "Mario Rossi" as author → 100%',
        'impact': 'Low weight (2%) because easily falsifiable',
        'recommendation': 'Useful only as a clue'
    },
    'feature_count_similarity': {
        'title': 'Feature Count Match',
        'description': 'Monotonic normalized similarity on total 3D feature counts.',
        'example': 'File A: 6 feat, File B: 5 feat → 83.3%',
        'impact': 'Low-medium weight (5%)',
        'recommendation': 'Filters out obvious false positives'
    },
    'feature_type_similarity': {
        'title': 'Feature Types Distribution',
        'description': 'Distribution of feature types (cosine similarity).',
        'example': 'Same mix of Protrusion/Cutout/Hole → high similarity',
        'impact': 'Medium weight (11%)',
        'recommendation': 'Captures "compositional style"'
    },
    'style_similarity': {
        'title': 'Modeling Style',
        'description': 'Style ratios (extrusion/cutout/hole/round).',
        'example': 'Both with 60% protrusion, 30% cutout → high similarity',
        'impact': 'Low weight (4%)',
        'recommendation': 'Designer\'s fingerprint'
    },
    'bigram_similarity': {
        'title': 'Consecutive Pairs (2 features)',
        'description': 'Consecutive pairs (e.g. "Protrusion→Cutout").',
        'example': '[Prot→Cut, Cut→Hole] common → high similarity',
        'impact': 'Low weight (2%)',
        'recommendation': 'Captures local workflow patterns'
    },
    'trigram_similarity': {
        'title': 'Consecutive Triples (3 features)',
        'description': 'Consecutive triples (e.g. "Prot→Cut→Hole").',
        'example': '[Prot→Cut→Hole] identical → 100%',
        'impact': 'Low weight (3%)',
        'recommendation': 'Captures unique "signatures"'
    },
    'lcs_similarity': {
        'title': 'Longest Common Subsequence (LCS)',
        'description': 'Longest common subsequence among features (even non-consecutive).',
        'example': '[A,B,C,D,E] vs [A,X,C,D,E] → LCS=[A,C,D,E] (4/5 = 80%)',
        'impact': 'High weight (25%)',
        'recommendation': 'Most important criterion for detecting operation order'
    },
    'feature_names_similarity': {
        'title': 'Custom Feature Names',
        'description': 'Evaluated only on user-renamed/custom names; software default names are excluded.',
        'example': 'Both renamed to "Base_Principale" → high similarity',
        'impact': 'Low weight (2%)',
        'recommendation': 'Useful when students systematically rename meaningful features'
    },
    'geometry_2d_similarity': {
        'title': '2D Sketch Geometries',
        'description': 'Types of entities in sketches (lines, arcs, circles...).',
        'example': '10 lines, 3 circles identical → 100%',
        'impact': 'Very high weight (26%)',
        'recommendation': 'Captures sketch complexity'
    },
    'constraint_2d_similarity': {
        'title': '2D Sketch Constraints',
        'description': 'Types of sketch constraints (tangency, parallel, dimensions...).',
        'example': '5 tangencies, 3 parallels identical → 100%',
        'impact': 'Medium weight (11%)',
        'recommendation': 'Reveals "design thinking"'
    },
    'constraint_ratio_similarity': {
        'title': 'Constraints/Geometry Ratio',
        'description': 'Ratio of constraints to geometries in sketches.',
        'example': '20 constraints / 15 geom = 1.33 similar → high similarity',
        'impact': 'Low-medium weight (9%)',
        'recommendation': 'Indicator of "constraint style"'
    },
    'sketch_parametric_similarity': {
        'title': 'Sketch Topology (COM)',
        'description': 'Compares sketches as graphs of 2D primitives and connections reconstructed from COM geometry coordinates.',
        'example': 'Two sketches with the same loops and junction structure → high similarity even with different absolute size',
        'impact': 'Disabled by default (0%)',
        'recommendation': 'Useful when topology matters more than global placement or orientation'
    }
};

// Ultimo risultato confronto (per ricalcolo con nuovi pesi)
let lastCompareResult = null;
let _latestCompareRequestSeq = 0;

// Aggiorna un peso nella sidebar
function updateWeight(key, value) {
    currentWeights[key] = value / 100;
    document.getElementById('weightVal_' + key).textContent = value + '%';
    updateWeightsTotal();

    // SYNC: Aggiorna anche lo slider corrispondente nel pannello comparison (se esiste)
    const compareSlider = document.getElementById('compare_weight_' + key);
    const compareValue = document.getElementById('compareWeightVal_' + key);
    if (compareSlider) {
        compareSlider.value = value;
    }
    if (compareValue) {
        compareValue.textContent = value + '%';
    }
    updateCompareWeightsTotal();

    // AUTO-RECALC: riallinea lista + comparison e persiste in background
    handleWeightsChanged();
}

// Aggiorna totale pesi nella sidebar
function updateWeightsTotal() {
    // ✅ Filtra solo i pesi numerici (esclude parametri fuzzy LCS e fuzzy combination)
    const numericWeights = Object.entries(currentWeights)
        .filter(([key, value]) => Number.isFinite(value) && !key.startsWith('lcs_fuzzy_') && !key.startsWith('fuzzy_combination_'))
        .map(([key, value]) => value);

    const total = numericWeights.reduce((a, b) => a + b, 0);
    const totalPercent = Math.round(total * 100);

    const display = document.getElementById('weightsTotalDisplay');
    const valueSpan = document.getElementById('weightsTotalValue');

    if (display && valueSpan) {
        valueSpan.textContent = totalPercent;

        // Colore badge: verde se ~100%, rosso altrimenti
        const isCorrect = Math.abs(total - 1) < 0.02; // tolleranza ±2%

        if (isCorrect) {
            display.style.color = '#28a745'; // verde
            display.innerHTML = `<i class="bi bi-check-circle-fill"></i> Total: <span id="weightsTotalValue">${totalPercent}</span>%`;
        } else {
            display.style.color = '#dc3545'; // rosso
            display.innerHTML = `<i class="bi bi-exclamation-triangle-fill"></i> Total: <span id="weightsTotalValue">${totalPercent}</span>% <small>(should be ~100%)</small>`;
        }
    }
}

// Inizializza pannello pesi
function initWeightsPanel() {
    const container = document.getElementById('weightsContainer');
    if (!container) return;

    let html = '';
    Object.entries(currentWeights).forEach(([key, value]) => {
        // ✅ FILTRA I PARAMETRI FUZZY - NON DEVONO APPARIRE COME CURSORI!
        if (key.startsWith('lcs_fuzzy_') || key.startsWith('fuzzy_combination_')) {
            return;
        }
        if (!Number.isFinite(value)) {
            return;
        }
        // ✅ FILTRA sketch_parametric se nascosto da config
        if (key === 'sketch_parametric_similarity' && !appConfig.show_sketch_parametric) {
            return;
        }

        const desc = criteriaDescriptions[key] || key;
        const info = criteriaDetailedInfo[key] || {};

        html += `
            <div class="col-12 weight-slider-container">
                <div class="d-flex align-items-center mb-1">
                    <label class="small mb-0 flex-grow-1">${desc}</label>
                    <button class="btn btn-sm btn-outline-info btn-info-weight ms-2 popover-btn" 
                            type="button"
                            data-key="${key}">
                        <i class="bi bi-info-circle"></i>
                    </button>
                </div>
                <div class="d-flex align-items-center">
                    <input type="range" class="form-range form-range-sm me-2" min="0" max="30" value="${value * 100}" 
                           id="weight_${key}" onchange="updateWeight('${key}', this.value)">
                    <span class="weight-value" id="weightVal_${key}">${Math.round(value * 100)}%</span>
                </div>
            </div>
        `;
    });
    container.innerHTML = html;
    updateWeightsTotal();

    // ✨ NUOVO: Aggiungi sezione Fuzzy Combination DOPO gli slider
    const fuzzySection = document.getElementById('fuzzyCombinationSection');
    if (fuzzySection) {
        const fuzzyEnabled = currentWeights['fuzzy_combination_enabled'] || false;
        const fuzzyPenalty = currentWeights['fuzzy_combination_penalty'] || 0.3;

        fuzzySection.innerHTML = `
            <div class="card border-warning mb-3">
                <div class="card-header bg-warning bg-opacity-10">
                    <h6 class="mb-0">
                        <i class="bi bi-sliders"></i> Fuzzy Combination
                        <button class="btn btn-sm btn-outline-info float-end" type="button" 
                                data-bs-toggle="popover" 
                                data-bs-placement="left"
                                data-bs-html="true"
                                data-bs-title="Fuzzy Combination"
                                data-bs-content="<p><strong>Penalizes models with inconsistent criteria</strong></p><p><em>Complete plagiarism example:</em> all criteria ~90% -> minimal penalty -> high similarity</p><p><em>Partial plagiarism example:</em> half criteria 100%, half 10% -> strong penalty -> reduced similarity</p><p><em>Recommendation:</em> enable for academic plagiarism detection (penalty 0.3-0.5)</p>">
                            <i class="bi bi-info-circle"></i>
                        </button>
                    </h6>
                </div>
                <div class="card-body">
                    <div class="form-check form-switch mb-3">
                        <input class="form-check-input" type="checkbox" id="fuzzyCombinationEnabled" 
                               ${fuzzyEnabled ? 'checked' : ''} 
                               onchange="toggleFuzzyCombination(this.checked)">
                        <label class="form-check-label" for="fuzzyCombinationEnabled">
                            <strong>Enable Fuzzy Combination</strong>
                            <br><small class="text-muted">Penalizes models with highly inconsistent criteria (e.g. some at 100%, others at 10%)</small>
                        </label>
                    </div>
                    
                    <div id="fuzzyPenaltyContainer" style="display: ${fuzzyEnabled ? 'block' : 'none'}">
                        <label class="form-label small">
                            Penalty (inconsistency): <strong id="fuzzyPenaltyValue">${(fuzzyPenalty * 100).toFixed(0)}%</strong>
                        </label>
                        <input type="range" class="form-range" min="0" max="100" value="${fuzzyPenalty * 100}" 
                               id="fuzzyPenaltySlider" oninput="updateFuzzyPenalty(this.value)">
                        <div class="d-flex justify-content-between">
                            <small class="text-muted">0% = Disabled</small>
                            <small class="text-muted">50% = Moderate</small>
                            <small class="text-muted">100% = Severe</small>
                        </div>
                        <div class="alert alert-info alert-sm mt-2" id="fuzzyPenaltyDesc">
                            ${getFuzzyPenaltyDescription(fuzzyPenalty)}
                        </div>
                    </div>
                </div>
            </div>
        `;

        // Inizializza popover
        const popoverBtn = fuzzySection.querySelector('[data-bs-toggle="popover"]');
        if (popoverBtn) {
            new bootstrap.Popover(popoverBtn, {
                container: 'body',
                sanitize: false
            });
        }
    }

    // ✨ Inizializza i popover Bootstrap per ogni pulsante
    document.querySelectorAll('.popover-btn').forEach(btn => {
        const key = btn.getAttribute('data-key');
        const info = criteriaDetailedInfo[key] || {};

        // Costruisci contenuto popover (senza problemi di escape!)
        const content = `
            <div class="text-start">
                <p class="mb-2"><strong>${info.description || 'No description available.'}</strong></p>
                <p class="mb-2"><em>Esempio:</em> ${info.example || '-'}</p>
                <p class="mb-2"><em>Impatto:</em> ${info.impact || '-'}</p>
                <p class="mb-0"><em>Consiglio:</em> ${info.recommendation || '-'}</p>
            </div>
        `;

        new bootstrap.Popover(btn, {
            trigger: 'click',
            placement: 'left',
            html: true,
            title: info.title || criteriaDescriptions[key] || key,
            content: content,
            container: 'body',
            sanitize: false
        });
    });
}

// ✨ Aggiorna funzione fuzzy LCS (semplice)
function updateLCSFuzzyFunction(value) {
    const desc = document.getElementById('lcsFuzzyDesc');

    if (value === 'none') {
        currentWeights['lcs_fuzzy_enabled'] = false;
        if (desc) desc.textContent = 'All features are weighted equally';
    } else {
        currentWeights['lcs_fuzzy_enabled'] = true;
        currentWeights['lcs_fuzzy_function'] = value;
        currentWeights['lcs_fuzzy_alpha'] = 2.0;
        currentWeights['lcs_fuzzy_mix'] = 0.7;

        if (desc) {
            if (value === 'linear') {
                desc.textContent = 'Early features weigh slightly more (constant decay)';
            } else if (value === 'exponential') {
                desc.textContent = 'Early features weigh much more (recommended for plagiarism detection)';
            } else if (value === 'logarithmic') {
                desc.textContent = 'Gradual transition between early and late features';
            }
        }
    }
    // NB: Cambiare LCS fuzzy richiede ricalcolo completo (raw_scores cambiano).
    handleWeightsChanged({ forceRawRecompute: true });
}

// ✨ Funzioni per fuzzy combination (Coherence-based: boost + penalty)
function getFuzzyPenaltyDescription(penalty) {
    if (penalty <= 0) return 'No fuzzy effect (pure linear combination)';
    if (penalty < 0.2) return 'Very mild coherence check';
    if (penalty < 0.4) return 'Moderate coherence check (recommended)';
    if (penalty < 0.6) return 'Strong coherence check';
    if (penalty < 0.8) return 'Very strong coherence check';
    return 'Maximum coherence enforcement';
}

function toggleFuzzyCombination(enabled) {
    currentWeights['fuzzy_combination_enabled'] = enabled;
    const control = document.getElementById('fuzzyPenaltyControl');
    if (control) {
        control.style.display = enabled ? 'block' : 'none';
    }
    initFuzzyCombination();
    handleWeightsChanged();
}

function updateFuzzyMethod(method) {
    currentWeights['fuzzy_combination_method'] = method;
    handleWeightsChanged();
}

function updateFuzzyBoost(value) {
    const boost = parseFloat(value) / 100.0;
    currentWeights['fuzzy_combination_boost'] = boost;

    const display = document.getElementById('fuzzyBoostDisplay');
    if (display) {
        display.textContent = Math.round(boost * 100) + '%';
    }
    updateFuzzyDescription();
    handleWeightsChanged();
}

function updateFuzzyPenalty(value) {
    const penalty = parseFloat(value) / 100.0;
    currentWeights['fuzzy_combination_penalty'] = penalty;

    const display = document.getElementById('fuzzyPenaltyDisplay');
    if (display) {
        display.textContent = Math.round(penalty * 100) + '%';
    }
    updateFuzzyDescription();
    handleWeightsChanged();
}

function updateFuzzyDescription() {
    const boost = currentWeights['fuzzy_combination_boost'] || 0;
    const penalty = currentWeights['fuzzy_combination_penalty'] || 0;
    const desc = document.getElementById('fuzzyPenaltyDesc');
    if (!desc) return;

    if (boost === 0 && penalty === 0) {
        desc.textContent = 'No fuzzy effect (pure linear combination)';
    } else if (boost > 0 && penalty === 0) {
        desc.textContent = 'Only rewarding coherent criteria (no penalty for discordant)';
    } else if (boost === 0 && penalty > 0) {
        desc.textContent = 'Only penalizing discordant criteria (no boost for coherent)';
    } else {
        desc.textContent = `Boost +${Math.round(boost*100)}% for coherent, Penalty -${Math.round(penalty*100)}% for discordant`;
    }
}

function initFuzzyCombination() {
    const enabled = currentWeights['fuzzy_combination_enabled'] || false;
    const method = currentWeights['fuzzy_combination_method'] || 'gaussian';
    const penalty = currentWeights['fuzzy_combination_penalty'] || 0.3;
    const boost = currentWeights['fuzzy_combination_boost'] || 0.15;

    const toggle = document.getElementById('fuzzyCombinationToggle');
    const methodSelect = document.getElementById('fuzzyMethodSelect');
    const penaltySlider = document.getElementById('fuzzyPenaltySlider');
    const boostSlider = document.getElementById('fuzzyBoostSlider');
    const control = document.getElementById('fuzzyPenaltyControl');

    if (toggle) toggle.checked = enabled;
    if (methodSelect) methodSelect.value = method;
    if (penaltySlider) penaltySlider.value = penalty * 100;
    if (boostSlider) boostSlider.value = boost * 100;
    if (control) control.style.display = enabled ? 'block' : 'none';

    // Update displays without triggering recalculation
    const penaltyDisplay = document.getElementById('fuzzyPenaltyDisplay');
    if (penaltyDisplay) penaltyDisplay.textContent = Math.round(penalty * 100) + '%';
    const boostDisplay = document.getElementById('fuzzyBoostDisplay');
    if (boostDisplay) boostDisplay.textContent = Math.round(boost * 100) + '%';
    updateFuzzyDescription();
}

// Inizializza select fuzzy dai pesi caricati
function initLCSFuzzySelect() {
    const enabled = currentWeights['lcs_fuzzy_enabled'] !== false;
    const func = currentWeights['lcs_fuzzy_function'] || 'exponential';
    const select = document.getElementById('lcsFuzzyFunction');

    if (select) {
        if (!enabled) {
            select.value = 'none';
        } else {
            select.value = func;
        }
        updateLCSFuzzyFunction(select.value);
    }
}

// Reset pesi default
function resetWeights() {
    // Ricarica i pesi di default dal backend (config.json)
    currentWeights = { ...defaultWeights };
    initWeightsPanel();
    initLCSFuzzySelect();
    alert('⚠️ Weights reset to defaults.\nClick "Save Global Weights" to use them for future analyses.');
}

// Inizializza pannello pesi nel tab Confronto (container separato)
function initCompareWeightsPanel() {
    const container = document.getElementById('compareWeightsContainer');
    if (!container) return;

    let html = '';
    Object.entries(currentWeights).forEach(([key, value]) => {
        // ✅ FILTRA I PARAMETRI FUZZY E VALORI NON NUMERICI
        if (key.startsWith('lcs_fuzzy_') || key.startsWith('fuzzy_combination_')) {
            return;
        }
        if (!Number.isFinite(value)) {
            return;
        }

        const desc = criteriaDescriptions[key] || key;

        html += `
            <div class="col-12 weight-slider-container">
                <label class="small">${desc}</label>
                <div class="d-flex align-items-center">
                    <input type="range" class="form-range form-range-sm me-2" min="0" max="30" value="${value * 100}" 
                           id="compare_weight_${key}" onchange="updateCompareWeight('${key}', this.value)">
                    <span class="weight-value" id="compareWeightVal_${key}">${Math.round(value * 100)}%</span>
                </div>
            </div>
        `;
    });

    container.innerHTML = html;
    updateCompareWeightsTotal();
}

// Aggiorna un peso nel confronto
function updateCompareWeight(key, value) {
    const numericValue = parseFloat(value);
    if (!Number.isFinite(numericValue)) {
        return;
    }
    currentWeights[key] = numericValue / 100;
    document.getElementById('compareWeightVal_' + key).textContent = value + '%';
    updateCompareWeightsTotal();

    // SYNC: Aggiorna anche lo slider corrispondente nel pannello globale
    const globalSlider = document.getElementById('weight_' + key);
    const globalValue = document.getElementById('weightVal_' + key);
    if (globalSlider) {
        globalSlider.value = value;
    }
    if (globalValue) {
        globalValue.textContent = value + '%';
    }
    updateWeightsTotal();

    // AUTO-RECALC: riallinea lista + comparison e persiste in background
    handleWeightsChanged();
}

// Aggiorna totale pesi nel confronto
function updateCompareWeightsTotal() {
    const numericWeights = Object.entries(currentWeights)
        .filter(([key, value]) => Number.isFinite(value) && !key.startsWith('lcs_fuzzy_') && !key.startsWith('fuzzy_combination_'))
        .map(([key, value]) => value);

    const total = numericWeights.reduce((a, b) => a + b, 0);
    const totalPercent = Math.round(total * 100);

    const display = document.getElementById('compareWeightsTotalDisplay');
    if (display) {
        const isCorrect = Math.abs(total - 1) < 0.02;
        if (isCorrect) {
            display.innerHTML = `<i class="bi bi-check-circle-fill text-success"></i> Total: ${totalPercent}%`;
        } else {
            display.innerHTML = `<i class="bi bi-exclamation-triangle-fill text-danger"></i> Total: ${totalPercent}% <small>(should be ~100%)</small>`;
        }
    }
}

// Apply modified weights and recompute the active comparison
async function applyWeightsAndCompare() {
    if (!lastCompareResult) {
        alert('Run a comparison first.');
        return;
    }

    // Keep global panel in sync with the current compare sliders.
    initWeightsPanel();
    updateWeightsTotal();

    await autoRecalculateComparison({ forceRawRecompute: false });
    alert('✅ Weights applied. Comparison and pair score are aligned with current UI weights.');
}

// Confronta due file
async function compareFiles() {
    const path1 = document.getElementById('compareFile1').value;
    const path2 = document.getElementById('compareFile2').value;

    if (!path1 || !path2) {
        alert('Select both files.');
        return;
    }

    if (path1 === path2) {
        alert('Select two different files.');
        return;
    }

    try {
        const requestSeq = ++_latestCompareRequestSeq;
        // Invia anche i parametri fuzzy non numerici (bool/string), necessari per raw-score LCS.
        const weightsToSend = { ...currentWeights };

        const response = await fetch('/api/compare', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                path1: path1.replace(/:/g, '|'),
                path2: path2.replace(/:/g, '|'),
                weights: weightsToSend  // ✨ Passa i pesi attuali al backend
            })
        });

        if (!response.ok) {
            const payload = await response.text();
            console.error('Error from /api/compare:', response.status, payload);
            alert(`Comparison error (${response.status}):\n\n${payload.substring(0, 200)}`);
            return;
        }

        const result = await safeJsonParse(response, null);
        if (!result) {
            alert('❌ Error parsing comparison result');
            return;
        }
        if (requestSeq !== _latestCompareRequestSeq) {
            console.log('ℹ️ Discarding stale /api/compare response');
            return;
        }


        // Initialize the sidebar weight panel if needed.
        initWeightsPanel();

        // Save for future fast recomputation (/api/recombine).
        lastCompareResult = {
            file1Data: result.file1,
            file2Data: result.file2,
            details: result.similarity,
            rawScores: result.raw_scores || null,
            weightsUsed: { ...weightsToSend },
            requestedPath1: path1,
            requestedPath2: path2
        };

        // Render with the current weight snapshot.
        renderCompareResultNew(result.file1, result.file2, result.similarity, {
            weightsUsed: weightsToSend,
            requestedPath1: path1,
            requestedPath2: path2
        });

    } catch (error) {
        console.error('Comparison error:', error);
        alert('Error during comparison.');
    }
}

// Apri file direttamente nel CAD associato (o Solid Edge via COM quando possibile)
async function openFileInCad(filepath) {
    if (!filepath) {
        alert('File path is not available.');
        return;
    }
    try {
        const response = await fetch('/api/open_in_cad', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filepath: filepath.replace(/:/g, '|') })
        });
        const data = await safeJsonParse(response, { success: false });
        if (!data || !data.success) {
            const msg = (data && (data.error || data.message)) || 'Error opening file in associated CAD.';
            alert(`❌ ${msg}`);
            return;
        }
        // Niente alert di successo per non essere invasivi.
        console.log(`✅ File aperto nel CAD (${data.method}):`, data.filepath);
    } catch (error) {
        console.error('openFileInCad error:', error);
        alert(`❌ Error opening file in CAD: ${error.message}`);
    }
}

// ✨ NUOVA FUNZIONE: Render confronto affiancato con schede dettagliate
function renderCompareResultNew(file1, file2, similarity, options = {}) {
    // ✅ USA IL VALORE 'overall' CALCOLATO DAL BACKEND (include fuzzy logic!)
    const overallSimilarity = similarity.overall || 0;
    const weightsUsed = (options && options.weightsUsed && typeof options.weightsUsed === 'object')
        ? options.weightsUsed
        : (lastCompareResult?.weightsUsed || currentWeights);
    const weightsMatchCurrent = scoringWeightsEquivalent(weightsUsed, currentWeights);
    const activeCriteriaSet = new Set(
        Array.isArray(similarity?._active_criteria) ? similarity._active_criteria.map(String) : []
    );
    const excludedCriteriaSet = new Set(
        Array.isArray(similarity?._excluded_criteria) ? similarity._excluded_criteria.map(String) : []
    );
    const criteriaKeys = Object.entries(weightsUsed || {})
        .filter(([key, value]) =>
            Number.isFinite(value) &&
            !key.startsWith('lcs_fuzzy_') &&
            !key.startsWith('fuzzy_combination_') &&
            !key.startsWith('_') &&
            (key !== 'sketch_parametric_similarity' || appConfig.show_sketch_parametric)
        )
        .map(([key]) => key);

    console.log('🔍 DEBUG Similarity:', {
        'overallSimilarity': overallSimilarity,
        'similarity.overall': similarity.overall,
        'currentWeights': currentWeights,
        'weightsUsed': weightsUsed,
        'weightsMatchCurrent': weightsMatchCurrent
    });

    // ✨ SINCRONIZZAZIONE: Aggiorna il valore della coppia nella lista "Similar Pairs"
    const pairIndex = findPairIndex(file1, file2, {
        path1: options.requestedPath1 || file1?.filepath || '',
        path2: options.requestedPath2 || file2?.filepath || ''
    });
    if (pairIndex !== -1) {
        if (weightsMatchCurrent) {
            appState.pairs[pairIndex].similarity = overallSimilarity;
            appState.pairs[pairIndex].details = similarity;
            appState.pairs[pairIndex]._rawScoreConfig = extractRawScoreConfigFromWeights(weightsUsed);
            appState.pairs[pairIndex]._syncState = 'current';
            console.log(`✅ Pair similarity updated in Similar Pairs: ${(overallSimilarity * 100).toFixed(1)}%`);
            renderPairs(_pairsCurrentPage);
        } else {
            // Compare result was computed with a stale weight snapshot.
            appState.pairs[pairIndex]._syncState = 'stale';
            appState.pairsSyncDirty = true;
            renderPairs(_pairsCurrentPage);
            console.log('ℹ️ Compare result not injected into Similar Pairs (weights snapshot mismatch).');
        }
    } else {
        console.warn('⚠️ Unable to sync Similar Pairs: pair not found by path', {
            requestedPath1: options.requestedPath1 || '',
            requestedPath2: options.requestedPath2 || '',
            file1Path: file1?.filepath || '',
            file2Path: file2?.filepath || ''
        });
    }

    // Show similarity result
    const simContainer = document.getElementById('compareSimilarityResult');
    const highThreshold = getCriticalSimilarityThreshold();
    const mediumThreshold = getMediumSimilarityThreshold();
    const badgeClass = overallSimilarity >= highThreshold ? 'high' : overallSimilarity >= mediumThreshold ? 'medium' : 'low';

    // Paper Writing Mode labeling buttons for comparison view
    // Escape paths for use in onclick
    const escapedPath1 = (file1.filepath || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const escapedPath2 = (file2.filepath || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");

    const compareSession = inferPairSessionName(file1.filepath || '', file2.filepath || '');
    const compareLabel = getPairLabel(file1.filename, file2.filename, file1.filepath || '', file2.filepath || '', compareSession);
    const labelingHtml = paperWritingState.enabled ? `
        <div class="card mb-3 border-warning">
            <div class="card-header bg-warning text-dark">
                <i class="bi bi-tag"></i> Plagiarism Label
            </div>
            <div class="card-body">
                <div class="d-flex align-items-center gap-3">
                    <span class="label-badge-container" id="compareCurrentLabel">${getLabelBadgeHtml(compareLabel)}</span>
                    <div class="btn-group">
                        <button class="btn ${compareLabel === 'CONFIRMED_PLAGIARISM' ? 'btn-danger' : 'btn-outline-danger'}" 
                                onclick="setPairLabelFromCompare('${file1.filename}', '${file2.filename}', 'CONFIRMED_PLAGIARISM', ${overallSimilarity}, '${escapedPath1}', '${escapedPath2}')">
                            <i class="bi bi-exclamation-triangle"></i> Confirmed Plagiarism
                        </button>
                        <button class="btn ${compareLabel === 'NOT_PLAGIARISM' ? 'btn-success' : 'btn-outline-success'}" 
                                onclick="setPairLabelFromCompare('${file1.filename}', '${file2.filename}', 'NOT_PLAGIARISM', ${overallSimilarity}, '${escapedPath1}', '${escapedPath2}')">
                            <i class="bi bi-check"></i> Not Plagiarism
                        </button>
                        <button class="btn ${compareLabel === 'UNDECIDED' ? 'btn-secondary' : 'btn-outline-secondary'}" 
                                onclick="setPairLabelFromCompare('${file1.filename}', '${file2.filename}', 'UNDECIDED', ${overallSimilarity}, '${escapedPath1}', '${escapedPath2}')">
                            <i class="bi bi-question"></i> Uncertain
                        </button>
                    </div>
                </div>
            </div>
        </div>
    ` : '';

    simContainer.innerHTML = `
        <div class="similarity-result-card ${badgeClass}">
            <div class="similarity-value">${(overallSimilarity * 100).toFixed(1)}%</div>
            <div class="similarity-label">Overall Similarity</div>
        </div>
        ${weightsMatchCurrent ? '' : `
        <div class="alert alert-warning py-2 mt-2 mb-2">
            This result was computed with an older weight snapshot.
            Click <strong>Compare</strong> to align it with current UI weights.
        </div>`}
        ${labelingHtml}
        <div class="card mb-3">
            <div class="card-header"><i class="bi bi-speedometer2"></i> Criteria Breakdown</div>
            <div class="card-body">
                ${criteriaKeys.map((key) => {
                    const value = similarity?.[key];
                    const desc = criteriaDescriptions[key] || key;
                    const hasScore = Number.isFinite(value);
                    const pct = hasScore ? (Number(value) * 100).toFixed(0) : 'N/A';
                    const isActive = activeCriteriaSet.has(key) && !excludedCriteriaSet.has(key);
                    const barColor = !isActive ? 'bg-secondary' : (value >= highThreshold ? 'bg-danger' : value >= 0.5 ? 'bg-warning' : 'bg-success');
                    // Mostra i pesi effettivamente usati nel calcolo di questo risultato.
                    const weight = (weightsUsed && typeof weightsUsed[key] === 'number') ? weightsUsed[key] : 0;
                    const weightLabel = `×${Math.round(weight * 100)}%`;
                    const statusLabel = isActive ? '' : '<span class="ms-1 text-muted">(excluded)</span>';
                    return `
                        <div class="criteria-detail-row">
                            <span class="criteria-name">${desc}</span>
                            <div class="criteria-bar-container">
                                <div class="criteria-bar-fill ${barColor}" style="width: ${hasScore ? pct : 0}%"></div>
                            </div>
                            <span class="criteria-value">${pct}%</span>
                            <span class="criteria-weight">${weightLabel}${statusLabel}</span>
                        </div>
                    `;
                }).join('')}
            </div>
        </div>
    `;

    // Show side-by-side panel
    document.getElementById('compareDetailedResult').style.display = 'flex';

    // Titles
    const folder1 = file1.filepath ? file1.filepath.split('\\').slice(-2, -1)[0] : '';
    const folder2 = file2.filepath ? file2.filepath.split('\\').slice(-2, -1)[0] : '';
    document.getElementById('compareFile1Title').innerHTML = `<i class="bi bi-file-earmark"></i> ${file1.filename} <small class="text-light">[${folder1}]</small>`;
    document.getElementById('compareFile2Title').innerHTML = `<i class="bi bi-file-earmark"></i> ${file2.filename} <small class="text-light">[${folder2}]</small>`;

    // Generate HTML for file card
    function generateFileCard(f) {
        const filepathEscaped = (f.filepath || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
        let sketchesHtml = '';
        if (f.sketches_data && f.sketches_data.length > 0) {
            sketchesHtml = f.sketches_data.map((sk) => `
                <div class="sketch-card">
                    <strong>📋 ${sk.name || 'Sketch'}</strong>
                    <div class="row mt-2">
                        <div class="col-6">
                            <small>Geometries: <strong>${sk.geometry_count || 0}</strong></small>
                            <div class="geometry-list">
                                ${Object.entries(sk.geometry_types || {}).map(([t, c]) => 
                                    `<span class="geom-badge">${t}: ${c}</span>`
                                ).join('')}
                            </div>
                        </div>
                        <div class="col-6">
                            <small>Constraints: <strong>${sk.constraint_count || 0}</strong></small>
                            <div class="constraint-list">
                                ${Object.entries(sk.constraint_types || {}).map(([t, c]) => 
                                    `<span class="constraint-badge">${t}: ${c}</span>`
                                ).join('')}
                            </div>
                        </div>
                    </div>
                </div>
            `).join('');
        } else {
            sketchesHtml = '<p class="text-muted">No sketch data available</p>';
        }

        return `
            <div class="compare-section">
                <div class="compare-section-title"><i class="bi bi-person"></i> Metadata</div>
                <p><strong>Author:</strong> ${f.author || '-'}</p>
                <p><strong>Last saved by:</strong> ${f.last_author || '-'}</p>
                <p><strong>Template:</strong> ${f.template || '-'}</p>
                <p class="mb-2"><strong>Path:</strong> <code class="small">${escapeHtml(f.filepath || '-')}</code></p>
                <button class="btn btn-sm btn-outline-primary"
                        onclick="openFileInCad('${filepathEscaped}')"
                        title="Open this file directly in CAD">
                    <i class="bi bi-box-arrow-up-right"></i> Open In CAD
                </button>
            </div>
            
            <div class="compare-section">
                <div class="compare-section-title"><i class="bi bi-box"></i> 3D Features</div>
                <p><strong>Total:</strong> <span class="badge bg-primary">${f.feature_count}</span></p>
                <div class="mb-2">
                    ${Object.entries(f.feature_types || {}).map(([t, c]) => 
                        `<span class="feature-badge">${t}: ${c}</span>`
                    ).join(' ')}
                </div>
                <p><strong>Sequence:</strong></p>
                <div class="feature-sequence">
                    ${(f.feature_sequence || []).slice(0, 15).map((feat, i) => 
                        `<span class="sequence-item">${i+1}. ${feat}</span>`
                    ).join('')}
                    ${(f.feature_sequence || []).length > 15 ? '<span class="text-muted">...</span>' : ''}
                </div>
            </div>
            
            <div class="compare-section">
                <div class="compare-section-title"><i class="bi bi-pencil"></i> 2D Sketches</div>
                <div class="row mb-2">
                    <div class="col-4"><strong>Sketches:</strong> ${f.sketches_count || 0}</div>
                    <div class="col-4"><strong>2D Geom:</strong> ${f.total_2d_geometry_count || 0}</div>
                    <div class="col-4"><strong>Constraints:</strong> ${f.total_2d_constraint_count || 0}</div>
                </div>
                ${sketchesHtml}
            </div>
            
            <div class="compare-section">
                <div class="compare-section-title"><i class="bi bi-palette"></i> Modeling Style</div>
                <p><strong>Extrusion ratio:</strong> ${((f.extrusion_ratio || 0) * 100).toFixed(0)}%</p>
                <p><strong>Cutout ratio:</strong> ${((f.cutout_ratio || 0) * 100).toFixed(0)}%</p>
                <p><strong>Hole ratio:</strong> ${((f.hole_ratio || 0) * 100).toFixed(0)}%</p>
                <p><strong>Naming style:</strong> ${f.naming_style || '-'}</p>
            </div>
        `;
    }

    // Populate the two columns
    document.getElementById('compareFile1Details').innerHTML = generateFileCard(file1);
    document.getElementById('compareFile2Details').innerHTML = generateFileCard(file2);
}

// Set label from comparison view (updates UI in comparison view)
async function setPairLabelFromCompare(file1, file2, label, similarity, path1 = '', path2 = '') {
    const sessionName = inferPairSessionName(path1, path2);
    await setPairLabel(file1, file2, label, similarity, path1, path2, sessionName);

    // Update the label badge in comparison view
    const badgeContainer = document.getElementById('compareCurrentLabel');
    if (badgeContainer) {
        badgeContainer.innerHTML = getLabelBadgeHtml(label);
    }

    // Update button states
    const buttons = document.querySelectorAll('#compareSimilarityResult .btn-group .btn');
    buttons.forEach(btn => {
        const btnLabel = btn.textContent.includes('Confirmed') ? 'CONFIRMED_PLAGIARISM' :
                        btn.textContent.includes('Not') ? 'NOT_PLAGIARISM' : 'UNDECIDED';
        btn.classList.remove('btn-danger', 'btn-success', 'btn-secondary');
        btn.classList.remove('btn-outline-danger', 'btn-outline-success', 'btn-outline-secondary');

        if (btnLabel === label) {
            if (label === 'CONFIRMED_PLAGIARISM') btn.classList.add('btn-danger');
            else if (label === 'NOT_PLAGIARISM') btn.classList.add('btn-success');
            else btn.classList.add('btn-secondary');
        } else {
            if (btnLabel === 'CONFIRMED_PLAGIARISM') btn.classList.add('btn-outline-danger');
            else if (btnLabel === 'NOT_PLAGIARISM') btn.classList.add('btn-outline-success');
            else btn.classList.add('btn-outline-secondary');
        }
    });
}

// Genera HTML per il badge dell'etichetta
function getLabelBadgeHtml(label) {
    if (!label) return '';

    const badges = {
        'CONFIRMED_PLAGIARISM': '<span class="badge bg-danger"><i class="bi bi-exclamation-triangle-fill"></i> Confirmed</span>',
        'NOT_PLAGIARISM': '<span class="badge bg-success"><i class="bi bi-check-circle-fill"></i> Not Plagiarism</span>',
        'UNDECIDED': '<span class="badge bg-secondary"><i class="bi bi-question-circle-fill"></i> Uncertain</span>'
    };

    return badges[label] || '';
}

// Aggiorna lo stato dei bottoni di etichettatura
function updateLabelButtonStates(container, currentLabel) {
    const buttons = container.querySelectorAll('.label-btn');
    buttons.forEach(btn => {
        const btnLabel = btn.dataset.label;
        if (btnLabel === currentLabel) {
            btn.classList.add('active');
            btn.classList.remove('btn-outline-danger', 'btn-outline-success', 'btn-outline-secondary');
            if (btnLabel === 'CONFIRMED_PLAGIARISM') btn.classList.add('btn-danger');
            else if (btnLabel === 'NOT_PLAGIARISM') btn.classList.add('btn-success');
            else btn.classList.add('btn-secondary');
        } else {
            btn.classList.remove('active', 'btn-danger', 'btn-success', 'btn-secondary');
            if (btnLabel === 'CONFIRMED_PLAGIARISM') btn.classList.add('btn-outline-danger');
            else if (btnLabel === 'NOT_PLAGIARISM') btn.classList.add('btn-outline-success');
            else btn.classList.add('btn-outline-secondary');
        }
    });
}

// Generate HTML for Paper Writing Mode labeling buttons (OPTIMIZED: only if enabled)
function getPaperWritingButtonsHtml(file1, file2, similarity, path1 = '', path2 = '', session = '') {
    // ✨ PERFORMANCE: If paper writing is disabled, return empty string immediately (no DOM creation)
    if (!paperWritingState || !paperWritingState.enabled) return '';

    const sessionName = session || inferPairSessionName(path1, path2);
    const key = getPairLabelKey(file1, file2, path1, path2, sessionName);
    const currentLabel = getPairLabel(file1, file2, path1, path2, sessionName);

    const confirmedClass = currentLabel === 'CONFIRMED_PLAGIARISM' ? 'btn-danger active' : 'btn-outline-danger';
    const notClass = currentLabel === 'NOT_PLAGIARISM' ? 'btn-success active' : 'btn-outline-success';
    const uncertainClass = currentLabel === 'UNDECIDED' ? 'btn-secondary active' : 'btn-outline-secondary';

    // Escape paths for use in onclick (replace backslashes and quotes)
    const escapedPath1 = (path1 || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
    const escapedPath2 = (path2 || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");

    const escapedSession = sessionName.replace(/'/g, "\\'");
    const escapedKey = escapeHtml(key);

    return `
        <div class="mt-2 paper-writing-controls" data-pair-key="${escapedKey}">
            <div class="d-flex align-items-center gap-2">
                <span class="label-badge-container">${getLabelBadgeHtml(currentLabel)}</span>
                <div class="btn-group">
                    <button class="btn btn-sm ${confirmedClass} label-btn" data-label="CONFIRMED_PLAGIARISM"
                            onclick="event.stopPropagation(); setPairLabel('${file1}', '${file2}', 'CONFIRMED_PLAGIARISM', ${similarity}, '${escapedPath1}', '${escapedPath2}', '${escapedSession}')"
                            title="Mark as confirmed plagiarism">
                        <i class="bi bi-exclamation-triangle"></i> C
                    </button>
                    <button class="btn btn-sm ${notClass} label-btn" data-label="NOT_PLAGIARISM"
                            onclick="event.stopPropagation(); setPairLabel('${file1}', '${file2}', 'NOT_PLAGIARISM', ${similarity}, '${escapedPath1}', '${escapedPath2}', '${escapedSession}')"
                            title="Mark as NOT plagiarism">
                        <i class="bi bi-check"></i> N
                    </button>
                    <button class="btn btn-sm ${uncertainClass} label-btn" data-label="UNDECIDED"
                            onclick="event.stopPropagation(); setPairLabel('${file1}', '${file2}', 'UNDECIDED', ${similarity}, '${escapedPath1}', '${escapedPath2}', '${escapedSession}')"
                            title="Mark as uncertain">
                        <i class="bi bi-question"></i> U
                    </button>
                </div>
            </div>
        </div>
    `;
}

// Aggiorna la soglia di plagio dallo slider Paper Mode
function updatePaperThreshold(value) {
    const pct = parseInt(value);
    paperWritingState.threshold = pct / 100;
    const label = document.getElementById('paperThresholdValue');
    if (label) label.textContent = pct + '%';
}

// Export LaTeX Table
async function exportLatexTable() {
    const directory = document.getElementById('directoryPath').value.trim();
    const exportBtn = document.getElementById('btnExportLatex');
    const prevBtnHtml = exportBtn ? exportBtn.innerHTML : '';

    if (!directory) {
        alert('Enter a dataset path first.');
        return;
    }

    try {
        if (exportBtn) {
            exportBtn.disabled = true;
            exportBtn.innerHTML = '<i class="bi bi-hourglass-split spinner"></i> Export in progress...';
        }
        showStatusBar('Generating LaTeX table...', 'Large datasets can require a few minutes.');

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 10 * 60 * 1000);
        const response = await fetch('/api/paper_writing/export_latex', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                root: directory,
                threshold: paperWritingState.threshold  // Soglia plagio dedicata (0..1)
            }),
            signal: controller.signal
        });
        clearTimeout(timeoutId);

        const data = await safeJsonParse(response, null);

        if (data.success) {
            paperWritingState.currentLatex = data.latex;

            // Mostra nel modal
            document.getElementById('latexCodeContent').textContent = data.latex;
            const modal = new bootstrap.Modal(document.getElementById('latexExportModal'));
            modal.show();
        } else {
            alert('Error: ' + (data.error || 'Unknown error'));
            if (data.traceback) console.error(data.traceback);
        }
    } catch (error) {
        console.error('Error exporting LaTeX:', error);
        if (error && error.name === 'AbortError') {
            alert('Export timeout (>10 minutes). Retry after loading saved results or with a smaller dataset root.');
        } else {
            alert('Error: ' + error.message);
        }
    } finally {
        hideStatusBar();
        if (exportBtn) {
            exportBtn.disabled = false;
            exportBtn.innerHTML = prevBtnHtml || '<i class="bi bi-file-earmark-code"></i> Export LaTeX Table';
        }
    }
}

// Copy LaTeX to clipboard
function copyLatexToClipboard() {
    const latex = paperWritingState.currentLatex;
    navigator.clipboard.writeText(latex).then(() => {
        alert('✅ LaTeX code copied to clipboard!');
    }).catch(err => {
        console.error('Copy failed:', err);
        // Fallback
        const textarea = document.createElement('textarea');
        textarea.value = latex;
        document.body.appendChild(textarea);
        textarea.select();
        document.execCommand('copy');
        document.body.removeChild(textarea);
        alert('✅ LaTeX code copied!');
    });
}

// Download LaTeX as .tex file
function downloadLatexFile() {
    const latex = paperWritingState.currentLatex;
    const blob = new Blob([latex], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'cad_similarity_table.tex';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// Show plagiarism statistics
async function showPlagiarismStats() {
    try {
        const response = await fetch('/api/paper_writing/labels_detail');
        const data = await safeJsonParse(response, null);

        if (!data || !data.success) {
            alert('❌ Error loading statistics: ' + ((data && data.error) || 'Unknown error'));
            return;
        }

        const sessions = data.sessions || [];
        const total = data.total || 0;

        // Totali globali
        const totalConfirmed = sessions.reduce((s, x) => s + x.confirmed_count, 0);
        const totalNot = sessions.reduce((s, x) => s + x.not_plagiarism_count, 0);
        const totalUndecided = sessions.reduce((s, x) => s + x.undecided_count, 0);

        let html = `
            <div class="row mb-3 text-center">
                <div class="col-4">
                    <h3 class="text-danger">${totalConfirmed}</h3>
                    <small class="text-muted">Confirmed Plagiarism</small>
                </div>
                <div class="col-4">
                    <h3 class="text-success">${totalNot}</h3>
                    <small class="text-muted">Not Plagiarism</small>
                </div>
                <div class="col-4">
                    <h3 class="text-secondary">${totalUndecided}</h3>
                    <small class="text-muted">Uncertain</small>
                </div>
            </div>
            <hr>
        `;

        if (sessions.length === 0) {
            html += '<p class="text-muted text-center">No labels yet</p>';
        } else {
            sessions.forEach(sess => {
                html += `
                    <div class="card mb-3 border-secondary">
                        <div class="card-header d-flex justify-content-between align-items-center py-2">
                            <strong><i class="bi bi-folder"></i> ${sess.session}</strong>
                            <span class="badge bg-secondary">${sess.total} labels</span>
                        </div>
                        <div class="card-body p-2">
                            <div class="row text-center mb-2">
                                <div class="col-4">
                                    <span class="badge bg-danger">${sess.confirmed_count} Confirmed</span>
                                </div>
                                <div class="col-4">
                                    <span class="badge bg-success">${sess.not_plagiarism_count} Not</span>
                                </div>
                                <div class="col-4">
                                    <span class="badge bg-secondary">${sess.undecided_count} Uncertain</span>
                                </div>
                            </div>`;

                // Confirmed pairs
                if (sess.confirmed.length > 0) {
                    html += `<div class="mb-1"><small class="text-danger fw-bold"><i class="bi bi-exclamation-triangle-fill"></i> Confirmed Plagiarism:</small><ul class="list-unstyled ms-2 mb-0">`;
                    sess.confirmed.forEach(item => {
                        const sim = item.notes ? item.notes.replace('similarity: ', '') : '';
                        html += `<li class="small"><code>${item.file_a}</code> ↔ <code>${item.file_b}</code> <span class="text-muted">${sim}</span></li>`;
                    });
                    html += `</ul></div>`;
                }

                // Not plagiarism pairs
                if (sess.not_plagiarism.length > 0) {
                    html += `<div class="mb-1"><small class="text-success fw-bold"><i class="bi bi-check-circle-fill"></i> Not Plagiarism:</small><ul class="list-unstyled ms-2 mb-0">`;
                    sess.not_plagiarism.forEach(item => {
                        const sim = item.notes ? item.notes.replace('similarity: ', '') : '';
                        html += `<li class="small"><code>${item.file_a}</code> ↔ <code>${item.file_b}</code> <span class="text-muted">${sim}</span></li>`;
                    });
                    html += `</ul></div>`;
                }

                // Undecided pairs
                if (sess.undecided.length > 0) {
                    html += `<div class="mb-1"><small class="text-secondary fw-bold"><i class="bi bi-question-circle-fill"></i> Uncertain:</small><ul class="list-unstyled ms-2 mb-0">`;
                    sess.undecided.forEach(item => {
                        const sim = item.notes ? item.notes.replace('similarity: ', '') : '';
                        html += `<li class="small"><code>${item.file_a}</code> ↔ <code>${item.file_b}</code> <span class="text-muted">${sim}</span></li>`;
                    });
                    html += `</ul></div>`;
                }

                html += `</div></div>`;
            });
        }

        document.getElementById('plagiarismStatsBody').innerHTML = html;
        const modal = new bootstrap.Modal(document.getElementById('plagiarismStatsModal'));
        modal.show();

    } catch (error) {
        console.error('Error loading stats:', error);
        alert('❌ Error: ' + error.message);
    }
}

// (renderPairs è già definita sopra con supporto Paper Writing Mode integrato)

// Helper: show server-update warning once per browser session.
function showServerUpdateWarningOnce() {
    try {
        const key = 'paperStatsServerUpdateWarningShown';
        if (sessionStorage.getItem(key)) return;
        sessionStorage.setItem(key, '1');
        alert('⚠️ Restart the server to apply updates, then retry.');
    } catch (e) {
        console.warn('⚠️ Restart the server to apply updates, then retry.');
    }
}

// ✨ Ricalcola automaticamente la similarità quando i pesi cambiano
// Usa /api/recombine (istantaneo) se raw_scores sono cached, altrimenti /api/compare
async function autoRecalculateComparison(options = {}) {
    if (!lastCompareResult) {
        return;
    }

    try {
        const weightsToSend = { ...currentWeights };
        const forceRawRecompute = !!options.forceRawRecompute;

        // ── Percorso veloce: /api/recombine (solo aritmetica, no ricalcolo dati) ──
        // Verifica che rawScores sia un oggetto con almeno una chiave ({} non è utile per il ricalcolo)
        const rs = lastCompareResult.rawScores;
        const hasValidRawScores = rs && typeof rs === 'object' && !Array.isArray(rs) && Object.keys(rs).length > 0;
        if (!forceRawRecompute && hasValidRawScores) {
            try {
                const response = await fetch('/api/recombine', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        raw_scores: lastCompareResult.rawScores,
                        weights: weightsToSend
                    })
                });

                if (response && response.ok) {
                    const result = await safeJsonParse(response, null);
                    if (result && result.success && result.similarity) {
                        lastCompareResult.details = result.similarity;
                        lastCompareResult.weightsUsed = { ...weightsToSend };
                        renderCompareResultNew(
                            lastCompareResult.file1Data,
                            lastCompareResult.file2Data,
                            result.similarity,
                            {
                                weightsUsed: weightsToSend,
                                requestedPath1: lastCompareResult.requestedPath1 || lastCompareResult.file1Data?.filepath || '',
                                requestedPath2: lastCompareResult.requestedPath2 || lastCompareResult.file2Data?.filepath || ''
                            }
                        );
                        console.log('✅ Instant recompute via /api/recombine');
                        return;
                    }
                }
                console.warn('⚠️ /api/recombine failed or returned an invalid payload, fallback to /api/compare');
            } catch (err) {
                console.warn('⚠️ /api/recombine error:', err);
            }
         }

        // ── Percorso lento (fallback): /api/compare (ricalcola tutto) ──
        const path1 = lastCompareResult.file1Data.filepath;
        const path2 = lastCompareResult.file2Data.filepath;

        if (!path1 || !path2) {
            console.warn('⚠️ Cannot recompute: missing file paths');
            return;
        }

        const response = await fetch('/api/compare', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                path1: path1.replace(/:/g, '|'),
                path2: path2.replace(/:/g, '|'),
                weights: weightsToSend
            })
        });

        if (!response.ok) {
            console.error('Error from /api/compare during recompute');
            return;
        }

        const result = await safeJsonParse(response, null);
        if (!result) {
            console.error('Error parsing recompute response');
            return;
        }

        lastCompareResult = {
            file1Data: result.file1,
            file2Data: result.file2,
            details: result.similarity,
            rawScores: result.raw_scores || null,
            weightsUsed: { ...weightsToSend },
            requestedPath1: path1,
            requestedPath2: path2
        };

        renderCompareResultNew(result.file1, result.file2, result.similarity, {
            weightsUsed: weightsToSend,
            requestedPath1: path1,
            requestedPath2: path2
        });
        if (forceRawRecompute) {
            console.log('✅ Comparison recomputed via /api/compare (forced raw recompute)');
        } else {
            console.log('✅ Comparison recomputed via /api/compare (fallback)');
        }

    } catch (error) {
        console.error('Error during recompute:', error);
    }
}

// Debounce helper for optional full recompute requests.
let _recalcDebounceTimer = null;
let _recalcForceRawPending = false;
function scheduleRecalculation(options = {}) {
    if (options.forceRawRecompute) {
        _recalcForceRawPending = true;
    }
    if (_recalcDebounceTimer) clearTimeout(_recalcDebounceTimer);
    _recalcDebounceTimer = setTimeout(() => {
        _recalcDebounceTimer = null;
        const forceRawRecompute = _recalcForceRawPending;
        _recalcForceRawPending = false;
        // Recompute only if pairs are available (analysis already executed).
        if (appState.pairs && appState.pairs.length > 0) {
            recalculateAllPairs({ forceRawRecompute });
        }
    }, 1500);
}

// Ricalcola TUTTE le coppie con i pesi/fuzzy correnti via /api/recombine_all
// Istantaneo: usa i raw_scores già estratti, no re-analisi dei file CAD
async function recalculateAllPairs(options = {}) {
    if (!appState.pairs || appState.pairs.length === 0) {
        // Non mostrare alert se chiamata automaticamente dal debounce
        return [];
    }
    const forceRawRecompute = !!options.forceRawRecompute;
    const silent = !!options.silent;

    const btn = document.querySelector('[onclick="recalculateAllPairs()"]');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="bi bi-hourglass-split spinner"></i> Recalculating...';
    }

    try {
        const response = await fetch('/api/recombine_all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                weights: currentWeights,
                threshold: appState.threshold,
                recompute_raw: forceRawRecompute,
                auto_recompute_raw: false,
                persist: false,
                limit: 10000
            })
        });

        const result = await safeJsonParse(response, null);
        if (!result || !result.success) {
            if (!silent) {
                alert('❌ Recalculation failed: ' + ((result && result.error) || 'unknown'));
            }
            return null;
        }

        // Aggiorna le coppie nel frontend
        const currentRawConfig = cloneRawScoreConfig(result.raw_score_config || extractRawScoreConfigFromWeights(currentWeights));
        appState.pairsRawScoreConfig = currentRawConfig;
        appState.pairs = (result.pairs || []).map(pair => ({
            ...pair,
            _syncState: 'current',
            _rawScoreConfig: cloneRawScoreConfig(currentRawConfig)
        }));
        appState.pairsSyncDirty = false;
        appState.pairsForceRawRecompute = false;
        sortPairsBySimilarity();

        // Re-render
        renderPairs(_pairsCurrentPage);
        updateStats();

        // Se c'è un confronto attivo nella tab Comparison, ricalcola anche quello
        if (lastCompareResult) {
            await autoRecalculateComparison({ forceRawRecompute });
        }

        console.log(`✅ ${result.updated}/${result.total} pairs recalculated with current weights`);
        return appState.pairs;

    } catch (error) {
        console.error('Error recalculating pairs:', error);
        if (!silent) {
            alert('❌ Error: ' + error.message);
        }
        return null;
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-arrow-repeat"></i> Apply Weights (recalculate)';
        }
    }
}

// Alias per backward compatibility
function recalculatePairsSimilarities() {
    recalculateAllPairs();
}

// Helper per Paper Writing Mode: key, lettura e aggiornamento etichette
function normalizePairLabelFile(name) {
    const raw = (name || '').toString().split(/[\\/]/).pop();
    return raw.replace(/_/g, ' ').replace(/\s+/g, ' ').trim().toLowerCase();
}

function normalizePairLabelSession(session) {
    return (session || '').toString().replace(/_/g, ' ').replace(/\s+/g, ' ').trim().toUpperCase();
}

function normalizePairLabelPath(pathValue) {
    const raw = (pathValue || '').toString().trim();
    if (!raw) return '';
    return raw.replace(/\//g, '\\').replace(/\\+/g, '\\').toLowerCase();
}

function getPairLabelLookupKeys(file1, file2, path1 = '', path2 = '', session = '') {
    const keys = [];
    const fa = normalizePairLabelFile(file1);
    const fb = normalizePairLabelFile(file2);
    if (!fa || !fb) return keys;

    const files = [fa, fb].sort();
    const p1 = normalizePairLabelPath(path1);
    const p2 = normalizePairLabelPath(path2);
    if (p1 && p2) {
        const paths = [p1, p2].sort();
        keys.push(`PATH|${paths[0]}|${paths[1]}`);
    }

    const ns = normalizePairLabelSession(session);
    if (ns) {
        keys.push(`SESSION|${ns}|${files[0]}|${files[1]}`);
    }

    // Always keep FILE fallback aligned with backend lookup map.
    keys.push(`FILE|${files[0]}|${files[1]}`);

    const legacyA = (file1 || '').toString().split(/[\\/]/).pop();
    const legacyB = (file2 || '').toString().split(/[\\/]/).pop();
    if (legacyA && legacyB) {
        keys.push([legacyA, legacyB].sort().join('|'));
    }

    return Array.from(new Set(keys));
}

function getPairLabelKey(file1, file2, path1 = '', path2 = '', session = '') {
    const keys = getPairLabelLookupKeys(file1, file2, path1, path2, session);
    return keys.length > 0 ? keys[0] : '';
}

function getPairLabel(file1, file2, path1 = '', path2 = '', session = '') {
    try {
        const labels = (paperWritingState && paperWritingState.pairLabels) || {};
        const keys = getPairLabelLookupKeys(file1, file2, path1, path2, session);
        for (const key of keys) {
            if (key && Object.prototype.hasOwnProperty.call(labels, key)) {
                return labels[key];
            }
        }
        return null;
    } catch (e) {
        console.warn('getPairLabel error', e);
        return null;
    }
}

// ✨ Throttle loadPairLabels per evitare fetch spam
let _loadPairLabelsInFlight = false;
async function loadPairLabels(session = '') {
    // Se è già in corso un caricamento, non farne un altro (throttle)
    if (_loadPairLabelsInFlight) {
        console.log('⏳ loadPairLabels already in flight, skipping...');
        return paperWritingState.pairLabels || {};
    }

    _loadPairLabelsInFlight = true;
    try {
        const query = new URLSearchParams();
        if (session) {
            query.set('session', session);
        }
        query.set('_ts', Date.now().toString());
        const url = '/api/paper_writing/get_pair_labels' + (query.toString() ? `?${query.toString()}` : '');
        const res = await fetch(url, { cache: 'no-store' });
        const data = await safeJsonParse(res, { success: false, labels: {} });
        if (data && data.success) {
            // data.labels è una mappa lookup_key -> label
            paperWritingState = paperWritingState || {};
            paperWritingState.pairLabels = data.labels || {};
            console.log('✅ Pair labels loaded:', Object.keys(paperWritingState.pairLabels).length);
            return paperWritingState.pairLabels;
        }
        return {};
    } catch (err) {
        console.warn('Error in loadPairLabels:', err);
        return {};
    } finally {
        _loadPairLabelsInFlight = false;
    }
}

async function setPairLabel(file1, file2, label, similarity = 0, path1 = '', path2 = '', session = '') {
    // Guard anti-doppio-click: blocca se la stessa coppia è già in salvataggio
    const guardKey = getPairLabelKey(file1, file2, path1, path2, session);
    if (_setPairLabelInFlight.has(guardKey)) {
        console.warn('setPairLabel: already in flight for', guardKey);
        return { success: false, error: 'already in flight' };
    }
    _setPairLabelInFlight.add(guardKey);

    try {
        const payload = {
            session: session || '',
            file1: file1,
            file2: file2,
            label: label,
            similarity: similarity,
            path1: path1,
            path2: path2
        };

        const res = await fetch('/api/paper_writing/set_pair_label', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await safeJsonParse(res, { success: false });
        if (data && data.success) {
            // Aggiorna cache locale
            paperWritingState = paperWritingState || {};
            paperWritingState.pairLabels = paperWritingState.pairLabels || {};
            for (const key of getPairLabelLookupKeys(file1, file2, path1, path2, session)) {
                paperWritingState.pairLabels[key] = label;
            }

            // Aggiorna SOLO il DOM della card interessata (NO renderPairs completo)
            // evita il "click-through" su DOM ricostruito durante il click
            const card = document.querySelector(`.pair-card[data-pair-key="${CSS.escape(guardKey)}"]`);
            if (card) {
                const badgeSpan = card.querySelector('.label-badge-container');
                if (badgeSpan) badgeSpan.innerHTML = getLabelBadgeHtml(label);
                const btnContainer = card.querySelector('.paper-writing-controls');
                if (btnContainer) updateLabelButtonStates(btnContainer, label);
            }

            // Aggiorna anche il badge nel modal di confronto (se aperto)
            const badgeContainer = document.getElementById('compareCurrentLabel');
            if (badgeContainer) badgeContainer.innerHTML = getLabelBadgeHtml(label);

            return data;
        } else {
            console.warn('setPairLabel failed', data);
            return data;
        }
    } catch (err) {
        console.error('Error in setPairLabel:', err);
        return { success: false, error: err.message };
    } finally {
        _setPairLabelInFlight.delete(guardKey);
    }
}

async function initPaperWritingMode() {
    console.log('📝 initPaperWritingMode() called, current state:', paperWritingState.enabled);

    try {
        const res = await fetch('/api/paper_writing_mode');
        const data = await safeJsonParse(res, null);

        if (data && data.success) {
            // Aggiorna con i valori dal backend
            paperWritingState.enabled = !!data.enabled;
            const apiThreshold = Number(data?.config?.default_similarity_threshold);
            if (Number.isFinite(apiThreshold)) {
                paperWritingState.threshold = Math.max(0.5, Math.min(0.99, apiThreshold));
            }

            const toggleContainer = document.getElementById('paperWritingToggleContainer');
            const toggleCheckbox = document.getElementById('paperWritingModeToggle');
            const panel = document.getElementById('paperWritingPanel');
            const paperSlider = document.getElementById('paperThresholdSlider');
            const paperValue = document.getElementById('paperThresholdValue');

            if (paperSlider) {
                paperSlider.value = String(Math.round(paperWritingState.threshold * 100));
            }
            if (paperValue) {
                paperValue.textContent = Math.round(paperWritingState.threshold * 100) + '%';
            }

            // Show/hide toggle in navbar
            if (toggleContainer) {
                toggleContainer.style.display = data.show_toggle ? 'block' : 'none';
            }

            // Sync checkbox
            if (toggleCheckbox) {
                toggleCheckbox.checked = paperWritingState.enabled;
            }

            // Show/hide paper panel
            if (panel) {
                if (paperWritingState.enabled) {
                    panel.classList.remove('d-none');
                } else {
                    panel.classList.add('d-none');
                }
            }
            syncPaperCrossSessionFilterControl();

            console.log('📝 Paper Writing Mode from API:', { enabled: paperWritingState.enabled, show_toggle: data.show_toggle });
        } else {
            console.warn('📝 API returned no data, using server-injected value:', paperWritingState.enabled);
        }
    } catch (err) {
        console.warn('📝 initPaperWritingMode fetch failed, using server-injected value:', paperWritingState.enabled, err);
    }
    syncPaperCrossSessionFilterControl();

    // Se abilitata (da API o da valore server), carica le etichette
    if (paperWritingState.enabled) {
        try {
            // Ricarica le labels solo se non sono già state caricate dal silent load
            const alreadyHasLabels = Object.keys(paperWritingState.pairLabels || {}).length > 0;
            if (!alreadyHasLabels) {
                await loadPairLabels();
            } else {
                console.log('📝 Labels already loaded (' + Object.keys(paperWritingState.pairLabels).length + ' entries), skipping reload');
            }
        } catch (e) {
            console.warn('📝 loadPairLabels failed:', e);
        }
        try {
            // Ri-renderizza sempre le coppie per mostrare i bottoni paper mode
            renderPairs();
        } catch (e) {
            console.warn('📝 renderPairs after paper mode init failed:', e);
        }
    } else if (appState.pairs && appState.pairs.length > 0) {
        // Paper mode disabilitato ma ci sono coppie: re-render per nascondere eventuali bottoni
        try { renderPairs(); } catch (e) { /* ignore */ }
    }

    console.log('📝 initPaperWritingMode() completed, enabled:', paperWritingState.enabled);
}

// Listener wrapper per rimuovere/aggiungere correttamente
function _paperModeToggleListener(event) {
    const enabled = !!event.target.checked;
    togglePaperWritingMode(enabled);
}

async function togglePaperWritingMode(enabled) {
    try {
        // Aggiorna subito UI per feedback
        const panel = document.getElementById('paperWritingPanel');
        const toggleCheckbox = document.getElementById('paperWritingModeToggle');
        if (panel) {
            if (enabled) panel.classList.remove('d-none'); else panel.classList.add('d-none');
        }
        if (toggleCheckbox) toggleCheckbox.disabled = true;

        const res = await fetch('/api/paper_writing_mode', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: !!enabled })
        });

        const data = await safeJsonParse(res, { success: false });

        if (data && data.success) {
            paperWritingState.enabled = !!enabled;

            if (paperWritingState.enabled) {
                // Carica etichette appena abiliti la modalità
                await loadPairLabels();
            } else {
                // Se disabiliti, svuota la cache lato client
                paperWritingState.pairLabels = {};
            }
            syncPaperCrossSessionFilterControl();
            updateStats();
            _pairsCurrentPage = 0;
            saveToLocalStorage();
            // Aggiorna le coppie per mostrare/nascondere bottoni paper mode
            try { renderPairs(); } catch (e) { /* ignore */ }
        } else {
            // rollback UI se fallito
            if (panel) {
                if (!enabled) panel.classList.remove('d-none'); else panel.classList.add('d-none');
            }
            if (toggleCheckbox) toggleCheckbox.checked = !enabled;
            alert('Error while enabling/disabling Paper Writing Mode.');
        }
    } catch (err) {
        console.error('togglePaperWritingMode error:', err);
        alert('Server communication error.');
    } finally {
        const toggleCheckbox = document.getElementById('paperWritingModeToggle');
        if (toggleCheckbox) toggleCheckbox.disabled = false;
    }
}

// Clear Paper Stats (UI handler)
async function clearPaperStats() {
    // Ask explicit confirmation before deleting all labels.
    const confirmed = confirm(
        '⚠️ WARNING: this operation will delete ALL plagiarism labels (Confirmed, Not Plagiarism, Undecided) from the database.\n\nAre you sure you want to continue?'
    );
    if (!confirmed) return;

    try {
        const btn = document.getElementById('btnClearPaperStats');
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<i class="bi bi-hourglass-split spinner"></i> Clearing...';
        }

        const res = await fetch('/api/paper_writing/clear_stats', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ all: true })
        });

        const data = await safeJsonParse(res, { success: false });
        if (data && data.success) {
            // Svuota cache locale labels
            paperWritingState.pairLabels = {};
            paperWritingState.currentStats = { generated_at: null, threshold: null, sessions: [] };

            // Aggiorna la UI delle coppie (rimuove i badge)
            try { renderPairs(); } catch(e) {}

            // Chiudi il modal stats se aperto
            try {
                const modalEl = document.getElementById('plagiarismStatsModal');
                const bsModal = bootstrap.Modal.getInstance(modalEl);
                if (bsModal) bsModal.hide();
            } catch (e) {}

            alert(`✅ Labels deleted: ${data.deleted} entries removed.`);
        } else {
            alert('Error: ' + (data.error || 'Unknown error'));
            console.error('clearPaperStats failed', data);
        }
    } catch (err) {
        console.error('clearPaperStats exception', err);
        alert('Server communication error.');
    } finally {
        const btn = document.getElementById('btnClearPaperStats');
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-trash"></i> Clear Paper Stats';
        }
    }
}

function inferCurrentSessionName() {
    const sessions = new Map();
    (appState.signatures || []).forEach(sig => {
        const rawSession = inferSessionNameFromFilePath(sig.filepath || '');
        if (rawSession) {
            sessions.set(rawSession.toLowerCase(), rawSession);
        }
    });
    if (sessions.size === 1) {
        return Array.from(sessions.values())[0];
    }
    if (sessions.size > 1) {
        return '';
    }

    const rawDir = (appState.currentDirectory || document.getElementById('directoryPath')?.value || '').trim();
    if (!rawDir) return '';
    const parts = rawDir.replace(/[\\/]+$/, '').split(/[\\/]/).filter(Boolean);
    if (!parts.length) return '';
    const last = parts[parts.length - 1];
    if ((last.toUpperCase() === 'MECCANICI' || last.toUpperCase() === 'NON MECCANICI') && parts.length > 1) {
        const candidate = parts[parts.length - 2];
        return /^(Exam|Exemption|Simulation)\b/i.test(candidate) ? candidate : '';
    }
    return /^(Exam|Exemption|Simulation)\b/i.test(last) ? last : '';
}

function inferSessionNameFromFilePath(filepath = '') {
    const rawPath = (filepath || '').trim();
    if (!rawPath) return '';
    const parts = rawPath.replace(/[\\/]+$/, '').split(/[\\/]/).filter(Boolean);
    if (!parts.length) return '';
    for (let i = 0; i < parts.length - 1; i++) {
        const next = (parts[i + 1] || '').toUpperCase();
        if (next === 'MECCANICI' || next === 'NON MECCANICI') {
            return parts[i];
        }
    }
    return '';
}

function inferPairSessionName(path1 = '', path2 = '') {
    const session1 = inferSessionNameFromFilePath(path1);
    const session2 = inferSessionNameFromFilePath(path2);
    if (session1 && session2) {
        return session1 === session2 ? session1 : '';
    }
    return session1 || session2 || inferCurrentSessionName();
}

function syncPaperCrossSessionFilterControl() {
    const toggle = document.getElementById('paperHideCrossSessionSwitch');
    if (!toggle) return;
    toggle.checked = !!paperWritingState.hideCrossSessionPairs;
    toggle.disabled = !paperWritingState.enabled;
}

function toggleHideCrossSessionPairs(enabled) {
    paperWritingState.hideCrossSessionPairs = !!enabled;
    syncPaperCrossSessionFilterControl();
    _pairsCurrentPage = 0;
    updateStats();
    renderPairs(0);
    saveToLocalStorage();
}

function normalizeOptimizationSessionName(session = '') {
    return normalizePairLabelSession(session);
}

function uniqueOptimizationSessions(values = []) {
    const out = [];
    const seen = new Set();
    (values || []).forEach(value => {
        const ns = normalizeOptimizationSessionName(value);
        if (!ns || seen.has(ns)) return;
        seen.add(ns);
        out.push(ns);
    });
    out.sort((a, b) => a.localeCompare(b));
    return out;
}

function getOptimizationSelectedSessions() {
    return uniqueOptimizationSessions(optimizationSessionFilterState.selected || []);
}

function getOptimizationSessionFilterMode() {
    return optimizationSessionFilterState.userCustomized ? 'custom' : 'all';
}

function syncOptimizationSessionFilterState(summary = {}) {
    const filesBySession = summary.files_by_session || {};
    const labeledBySession = summary.sessions || {};
    const available = uniqueOptimizationSessions([
        ...Object.keys(filesBySession || {}),
        ...Object.keys(labeledBySession || {})
    ]);
    const availableSet = new Set(available);

    if (!optimizationSessionFilterState.userCustomized) {
        const serverMode = (summary.selected_sessions_mode || 'all').toString().toLowerCase();
        const serverPreferred = serverMode === 'custom'
            ? uniqueOptimizationSessions(summary.selected_sessions_effective || summary.selected_sessions_requested || [])
            : [];
        const selected = serverPreferred.length > 0 ? serverPreferred : available;
        optimizationSessionFilterState.selected = selected.filter(name => availableSet.has(name));
    } else {
        optimizationSessionFilterState.selected = getOptimizationSelectedSessions().filter(name => availableSet.has(name));
    }

    optimizationSessionFilterState.available = available;
    optimizationSessionFilterState.lastSummary = summary;
}

function onOptimizationSessionCheckboxChange(event) {
    const input = event?.target;
    if (!input) return;
    const session = normalizeOptimizationSessionName(input.value || input.getAttribute('data-session') || '');
    if (!session) return;

    const selected = new Set(getOptimizationSelectedSessions());
    if (input.checked) {
        selected.add(session);
    } else {
        selected.delete(session);
    }
    optimizationSessionFilterState.selected = uniqueOptimizationSessions(Array.from(selected));
    optimizationSessionFilterState.userCustomized = true;
    renderOptimizationSessionSelector(optimizationSessionFilterState.lastSummary || {});
}

function renderOptimizationSessionSelector(summary = {}) {
    const statusEl = document.getElementById('optimizationSessionSelectionStatus');
    const container = document.getElementById('optimizationSessionSelector');
    const btnAll = document.getElementById('btnOptimizationSelectAllSessions');
    const btnNone = document.getElementById('btnOptimizationSelectNoSessions');
    if (!statusEl || !container) return;

    const available = uniqueOptimizationSessions(optimizationSessionFilterState.available || []);
    const selected = getOptimizationSelectedSessions();
    const selectedSet = new Set(selected);
    const scope = document.getElementById('optimizationScope')?.value || 'all';
    const disableSelector = scope === 'current';
    const filesBySession = summary.files_by_session || {};
    const labeledBySession = summary.sessions || {};

    if (btnAll) {
        btnAll.disabled = disableSelector || available.length === 0;
    }
    if (btnNone) {
        btnNone.disabled = disableSelector || available.length === 0;
    }

    if (!available.length) {
        statusEl.innerHTML = 'No sessions available from current analysis.';
        container.innerHTML = '<span class="text-muted">Run analysis and refresh dataset to list sessions.</span>';
        return;
    }

    statusEl.innerHTML = `
        <strong>${selected.length}</strong> / <strong>${available.length}</strong> sessions selected
        ${disableSelector ? '<span class="text-muted"> (disabled while scope is "Current session only")</span>' : ''}
    `;

    const rows = available.map((session, idx) => {
        const checked = selectedSet.has(session) ? 'checked' : '';
        const disabled = disableSelector ? 'disabled' : '';
        const filesCount = Number(filesBySession[session] || 0);
        const sessionStats = labeledBySession[session] || {};
        const posCount = Number(sessionStats.positive || 0);
        const negCount = Number(sessionStats.negative || 0);
        return `
            <div class="form-check mb-1">
                <input class="form-check-input optimization-session-checkbox"
                       type="checkbox"
                       id="optimizationSession_${idx}"
                       value="${escapeHtml(session)}"
                       ${checked}
                       ${disabled}>
                <label class="form-check-label" for="optimizationSession_${idx}">
                    <code>${escapeHtml(session)}</code>
                    <span class="text-muted">files ${filesCount}, pos ${posCount}, neg ${negCount}</span>
                </label>
            </div>
        `;
    }).join('');

    container.innerHTML = rows;
    container.querySelectorAll('.optimization-session-checkbox').forEach(el => {
        el.addEventListener('change', onOptimizationSessionCheckboxChange);
    });
}

function optimizationSelectAllSessions() {
    optimizationSessionFilterState.selected = uniqueOptimizationSessions(optimizationSessionFilterState.available || []);
    optimizationSessionFilterState.userCustomized = true;
    renderOptimizationSessionSelector(optimizationSessionFilterState.lastSummary || {});
}

function optimizationSelectNoSessions() {
    optimizationSessionFilterState.selected = [];
    optimizationSessionFilterState.userCustomized = true;
    renderOptimizationSessionSelector(optimizationSessionFilterState.lastSummary || {});
}

function onOptimizationScopeChange() {
    const scopeEl = document.getElementById('optimizationScope');
    const rowEl = document.getElementById('optimizationCurrentSessionRow');
    const inputEl = document.getElementById('optimizationCurrentSession');
    if (!scopeEl || !rowEl || !inputEl) return;

    const scope = scopeEl.value || 'all';
    if (scope === 'current') {
        rowEl.classList.remove('d-none');
        if (!inputEl.value.trim()) {
            inputEl.value = inferCurrentSessionName();
        }
        inputEl.placeholder = inputEl.value.trim() ? inputEl.value.trim() : 'Exam 22-01-2025';
    } else {
        rowEl.classList.add('d-none');
    }
    renderOptimizationSessionSelector(optimizationSessionFilterState.lastSummary || {});
}

function renderOptimizationDatasetSummary(payload) {
    const summaryEl = document.getElementById('optimizationDatasetSummary');
    if (!summaryEl) return;

    if (!payload || !payload.success) {
        summaryEl.innerHTML = `<span class="text-danger">Error loading training summary.</span>`;
        return;
    }

    const s = payload.summary || {};
    syncOptimizationSessionFilterState(s);
    renderOptimizationSessionSelector(s);
    const ignored = s.ignored || {};
    const crossIncluded = s.cross_session_included || 0;
    const strictMode = s.strict_path_labeled_pairs_only !== false;
    const migration = s.migration || {};
    const sessions = s.sessions || {};
    const requestedSession = s.current_session_requested || '';
    const resolvedSession = s.current_session_resolved || '';
    const selectedMode = (s.selected_sessions_mode || 'all').toString().toLowerCase();
    const selectedEffective = uniqueOptimizationSessions(s.selected_sessions_effective || []);
    const selectedScopeInfo = selectedMode === 'custom'
        ? `<div><strong>Session filter:</strong> custom (${selectedEffective.length} selected)</div>`
        : (selectedMode === 'current'
            ? '<div><strong>Session filter:</strong> current session only</div>'
            : '<div><strong>Session filter:</strong> all sessions</div>');
    const sessionRows = Object.entries(sessions)
        .map(([name, v]) => {
            const p = v.positive || 0;
            const n = v.negative || 0;
            return `<tr><td>${escapeHtml(name)}</td><td>${p}</td><td>${n}</td></tr>`;
        })
        .join('');

    const sessionTable = sessionRows
        ? `<div class="table-responsive mt-2">
                <table class="table table-sm table-bordered mb-0">
                    <thead><tr><th>Session</th><th>Pos</th><th>Neg</th></tr></thead>
                    <tbody>${sessionRows}</tbody>
               </table>
           </div>`
        : '<div class="text-muted mt-2">No session-level labeled pairs available.</div>';
    const currentScopeInfo = s.scope === 'current'
        ? `<div><strong>Current session:</strong> ${resolvedSession
            ? `<code>${escapeHtml(resolvedSession)}</code>`
            : '<span class="text-warning">not resolved</span>'}</div>
           ${requestedSession && requestedSession !== resolvedSession
            ? `<div><strong>Requested:</strong> <code>${escapeHtml(requestedSession)}</code></div>`
            : ''}`
        : '';

    summaryEl.innerHTML = `
        <div><strong>Ready:</strong> ${payload.ready ? '<span class="text-success">Yes</span>' : '<span class="text-warning">No</span>'}</div>
        <div><strong>SciPy:</strong> ${payload.has_scipy ? 'available' : 'missing'}</div>
        <div><strong>Author criterion:</strong> ${payload.ignore_author_forced ? 'excluded (forced)' : 'configurable'}</div>
        <div><strong>Labeled pairs mode:</strong> ${strictMode ? 'strict path-only' : 'with recovery/inference'}</div>
        ${currentScopeInfo}
        ${selectedScopeInfo}
        <div><strong>Samples:</strong> ${s.samples_total || 0} (pos ${s.positive || 0}, neg ${s.negative || 0})</div>
        <div><strong>Legacy path migration:</strong> migrated ${migration.migrated || 0}, already path-aware ${migration.already_with_paths || 0}, ambiguous ${migration.ambiguous || 0}, unresolved ${migration.unresolved || 0}${migration.saved ? ', saved' : ''}</div>
        <div><strong>Ignored:</strong> undecided ${ignored.undecided || 0}, outside scope ${ignored.outside_scope || 0}, missing sig ${ignored.missing_signature || 0}, missing path ${ignored.missing_path || 0}, pair not found ${ignored.label_pair_not_found || 0}, cross-session ${ignored.cross_session_pair || 0}, unresolved session ${ignored.unresolved_session || 0}, ambiguous session ${ignored.ambiguous_session || 0}</div>
        <div><strong>Cross-session used:</strong> ${crossIncluded}</div>
        <div><strong>Recovered:</strong> legacy session labels ${ignored.recovered_session || 0}</div>
        <div><strong>Message:</strong> ${escapeHtml(payload.message || 'Dataset ready.')}</div>
        ${sessionTable}
    `;
}

async function refreshOptimizationDataset(options = {}) {
    const touchResults = options.touchResults !== false;
    const btn = document.getElementById('btnOptimizationRefresh');
    const resultsBox = document.getElementById('optimizationResults');
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<i class="bi bi-hourglass-split"></i> Loading...';
    }
    if (touchResults && resultsBox) {
        resultsBox.innerHTML = `
            <div class="text-muted">
                <span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>
                Reading labeled dataset coverage...
            </div>
        `;
    }
    try {
        const scope = document.getElementById('optimizationScope')?.value || 'all';
        const currentSession = (document.getElementById('optimizationCurrentSession')?.value || inferCurrentSessionName() || '').trim();
        const ignoreAuthor = document.getElementById('optimizationIgnoreAuthor')?.checked !== false;
        const selectedSessionsMode = getOptimizationSessionFilterMode();
        const selectedSessions = getOptimizationSelectedSessions();
        const strictPathLabeledOnly = appConfig?.optimizer_training_policy?.strict_path_labeled_pairs_only !== false;
        const query = new URLSearchParams({
            scope: scope,
            current_session: currentSession,
            ignore_author: ignoreAuthor ? '1' : '0',
            strict_path_labeled_pairs_only: strictPathLabeledOnly ? '1' : '0',
            selected_sessions_mode: selectedSessionsMode
        });
        if (selectedSessionsMode === 'custom') {
            selectedSessions.forEach(session => query.append('selected_session', session));
        }
        query.set('_ts', Date.now().toString());
        const response = await fetch(`/api/paper_writing/weights_optimization_dataset?${query.toString()}`, { cache: 'no-store' });
        const data = await safeJsonParse(response, { success: false });
        renderOptimizationDatasetSummary(data);
        if (touchResults && resultsBox && data && data.success) {
            resultsBox.innerHTML = `<span class="text-muted">Dataset summary updated. Ready: ${data.ready ? 'yes' : 'no'}.</span>`;
        }
        return data;
    } catch (e) {
        console.error('refreshOptimizationDataset error:', e);
        renderOptimizationDatasetSummary({ success: false });
        if (touchResults && resultsBox) {
            resultsBox.innerHTML = `<span class="text-danger">Dataset refresh failed: ${e.message}</span>`;
        }
        return { success: false, error: e.message };
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-arrow-repeat"></i> Refresh Dataset';
        }
    }
}

function renderOptimizationResults(result) {
    const box = document.getElementById('optimizationResults');
    if (!box) return;
    unlockOptimizationControls();

    if (!result || !result.success) {
        lastOptimizationResult = null;
        box.innerHTML = `<span class="text-danger">${result?.error || 'Optimization failed.'}</span>`;
        return;
    }
    lastOptimizationResult = result;

    const before = result.evaluation_before || {};
    const after = result.evaluation_after || {};
    const improvement = result.improvement || {};
    const hasCandidateEval = !!(result.evaluation_candidate && typeof result.evaluation_candidate === 'object');
    const candidateEval = hasCandidateEval ? result.evaluation_candidate : null;
    const improvementCandidate = (result.improvement_candidate && typeof result.improvement_candidate === 'object')
        ? result.improvement_candidate
        : null;
    const guardrail = result.guardrail || { accepted: true };
    const selectedSolution = String(result.selected_solution || (guardrail.accepted ? 'candidate' : 'baseline')).toLowerCase();
    const manualAppliedSolution = String(result.manual_applied_solution || '').toLowerCase();
    const hasUiAppliedSolution = (manualAppliedSolution === 'candidate' || manualAppliedSolution === 'baseline');
    const uiAppliedSolution = hasUiAppliedSolution ? manualAppliedSolution : '';
    const uiOverride = hasUiAppliedSolution && uiAppliedSolution !== selectedSolution;
    const solverMessage = String(result.optimization?.message || '').trim();
    const optimizerSeed = Number(result.optimizer_seed);
    const optimizerSeedInfo = Number.isFinite(optimizerSeed)
        ? `${optimizerSeed}${result.optimizer_seed_source ? ` (${escapeHtml(String(result.optimizer_seed_source))})` : ''}`
        : '';
    const restartParts = [];
    if (Number.isFinite(Number(result.hybrid_restarts))) restartParts.push(`hybrid ${Number(result.hybrid_restarts)}`);
    if (Number.isFinite(Number(result.genetic_restarts))) restartParts.push(`genetic ${Number(result.genetic_restarts)}`);
    if (Number.isFinite(Number(result.lbfgsb_restarts))) restartParts.push(`lbfgsb ${Number(result.lbfgsb_restarts)}`);
    const restartInfo = restartParts.join(', ');
    const utility = Number(result.optimization?.utility);
    const hasUtility = Number.isFinite(utility);
    const hasCandidateChanges = Array.isArray(result.weight_changes_candidate) && result.weight_changes_candidate.length > 0;
    const allChanges = (result.weight_changes || []).map(item => {
        const d = Number(item.delta || 0);
        const sign = d >= 0 ? '+' : '';
        return `<tr>
            <td>${item.name}</td>
            <td>${(Number(item.old || 0) * 100).toFixed(2)}%</td>
            <td>${(Number(item.new || 0) * 100).toFixed(2)}%</td>
            <td>${sign}${(d * 100).toFixed(2)}%</td>
        </tr>`;
    }).join('');
    const candidateChanges = (result.weight_changes_candidate || []).map(item => {
        const d = Number(item.delta || 0);
        const sign = d >= 0 ? '+' : '';
        return `<tr>
            <td>${item.name}</td>
            <td>${(Number(item.old || 0) * 100).toFixed(2)}%</td>
            <td>${(Number(item.new || 0) * 100).toFixed(2)}%</td>
            <td>${sign}${(d * 100).toFixed(2)}%</td>
        </tr>`;
    }).join('');
    const candidateSeparationDelta = Number(
        improvementCandidate?.separation_delta ?? (
            hasCandidateEval
                ? (Number(candidateEval?.separation || 0) - Number(before.separation || 0))
                : NaN
        )
    );
    const candidateMarginDelta = Number(
        improvementCandidate?.margin_delta ?? (
            hasCandidateEval
                ? (Number(candidateEval?.margin || 0) - Number(before.margin || 0))
                : NaN
        )
    );
    const showCandidateAsMain = hasCandidateEval || hasCandidateChanges;
    const displayAfter = showCandidateAsMain && hasCandidateEval ? candidateEval : after;
    const displaySepDelta = showCandidateAsMain ? candidateSeparationDelta : Number(improvement.separation_delta || 0);
    const displayMarginDelta = showCandidateAsMain ? candidateMarginDelta : Number(improvement.margin_delta || 0);
    const displayChanges = showCandidateAsMain && hasCandidateChanges ? candidateChanges : allChanges;
    const displayAfterLabel = showCandidateAsMain ? 'Candidate' : 'After';
    const displayViolBefore = before.violations || {};
    const displayViolAfter = showCandidateAsMain ? (guardrail.after_candidate || (candidateEval?.violations || {})) : (after.violations || {});
    const beforePosCount = Number(before?.positive?.count ?? result.training_summary?.positive ?? 0);
    const beforeNegCount = Number(before?.negative?.count ?? result.training_summary?.negative ?? 0);
    const afterPosCount = Number(displayAfter?.positive?.count ?? after?.positive?.count ?? beforePosCount);
    const afterNegCount = Number(displayAfter?.negative?.count ?? after?.negative?.count ?? beforeNegCount);
    const thresholdPct = Number.isFinite(Number(result.threshold))
        ? (Number(result.threshold) * 100).toFixed(0)
        : (getCriticalSimilarityThreshold() * 100).toFixed(0);
    const migration = result.training_summary?.migration || {};
    const uiStatusHtml = hasUiAppliedSolution
        ? (uiAppliedSolution === 'candidate'
            ? '<span class="text-success">candidate applied (UI only)</span>'
            : '<span class="text-secondary">baseline restored (candidate discarded)</span>')
        : '<span class="text-muted">unchanged (choose action below)</span>';
    const candidateActions = result.candidate_weights ? `
        <button class="btn btn-sm btn-warning" onclick="applyOptimizationSolution('candidate')">
            <i class="bi bi-lightning-charge"></i> Apply Candidate in UI
        </button>
        <button class="btn btn-sm btn-success" onclick="saveOptimizationSolution('candidate')">
            <i class="bi bi-save"></i> Apply + Save Candidate
        </button>` : '';
    const baselineAction = result.baseline_weights ? `
        <button class="btn btn-sm btn-outline-secondary" onclick="applyOptimizationSolution('baseline')">
            <i class="bi bi-x-circle"></i> Discard Candidate
        </button>` : '';

    box.innerHTML = `
        <div><strong>Solver:</strong> ${result.optimization?.success ? '<span class="text-success">converged</span>' : '<span class="text-warning">partial</span>'}
            (${result.optimization?.iterations || 0} iter, obj=${Number(result.optimization?.objective || 0).toFixed(6)}${hasUtility ? `, utility=${utility.toFixed(6)}` : ''})</div>
        ${solverMessage ? `<div><strong>Solver note:</strong> ${escapeHtml(solverMessage)}</div>` : ''}
        ${optimizerSeedInfo ? `<div><strong>Optimizer seed:</strong> ${optimizerSeedInfo}</div>` : ''}
        ${restartInfo ? `<div><strong>Restarts:</strong> ${restartInfo}</div>` : ''}
        <div><strong>Guardrail:</strong> ${guardrail.accepted ? '<span class="text-success">passed</span>' : `<span class="text-warning">failed</span> (${escapeHtml(guardrail.reason || 'candidate would be rejected')})`}</div>
        <div><strong>Author criterion:</strong> ${result.ignore_author_forced ? 'excluded (forced)' : (result.ignore_author ? 'excluded' : 'included')}</div>
        <div><strong>Start weights:</strong> ${result.start_source === 'ui' ? 'current UI' : 'saved global'}</div>
        <div><strong>Training set:</strong> pos ${beforePosCount}, neg ${beforeNegCount} (threshold ${thresholdPct}%)</div>
        <div><strong>Legacy path migration:</strong> migrated ${migration.migrated || 0}, ambiguous ${migration.ambiguous || 0}, unresolved ${migration.unresolved || 0}${migration.saved ? ', saved' : ''}</div>
        <div><strong>Solver proposal:</strong> ${selectedSolution === 'candidate' ? '<span class="text-success">candidate</span>' : '<span class="text-secondary">baseline</span>'}</div>
        <div><strong>UI weights:</strong> ${uiStatusHtml}${uiOverride ? ' <span class="badge bg-warning text-dark">manual override</span>' : ''}</div>
        <div><strong>Separation:</strong> ${(Number(before.separation || 0) * 100).toFixed(2)}% → ${(Number(displayAfter?.separation || 0) * 100).toFixed(2)}%
            (delta ${(displaySepDelta * 100).toFixed(2)}%)</div>
        <div><strong>Margin:</strong> ${(Number(before.margin || 0) * 100).toFixed(2)}% → ${(Number(displayAfter?.margin || 0) * 100).toFixed(2)}%
            (delta ${(displayMarginDelta * 100).toFixed(2)}%)</div>
        <div><strong>Violations:</strong> pos ${(displayViolBefore?.positive_below_threshold ?? before.violations?.positive_below_threshold ?? 0)}/${beforePosCount}→${displayViolAfter?.positive_below_threshold ?? 'n/a'}/${afterPosCount},
            neg ${(displayViolBefore?.negative_above_threshold ?? before.violations?.negative_above_threshold ?? 0)}/${beforeNegCount}→${displayViolAfter?.negative_above_threshold ?? 'n/a'}/${afterNegCount}</div>
        <div class="table-responsive mt-2">
            <table class="table table-sm table-bordered mb-0">
                <thead><tr><th>Weight</th><th>Before</th><th>${displayAfterLabel}</th><th>Delta</th></tr></thead>
                <tbody>${displayChanges}</tbody>
            </table>
        </div>
        <div class="mt-2 text-muted">
            ${result.saved_global_weights
                ? 'Candidate saved globally.'
                : (result.manual_saved_solution === 'candidate'
                    ? 'Candidate manually saved globally.'
                    : 'Global weights unchanged.')}
        </div>
        <div class="d-flex flex-wrap gap-2 mt-2">
            ${candidateActions}
            ${baselineAction}
        </div>
        ${(!result.candidate_weights && !result.baseline_weights) ? `<div class="small text-warning mt-2">Apply/Save unavailable: backend response missing solution weights.</div>` : ''}
    `;
}

function unlockOptimizationControls() {
    const runBtn = document.getElementById('btnOptimizationRun');
    const refreshBtn = document.getElementById('btnOptimizationRefresh');
    if (runBtn) {
        runBtn.disabled = false;
        runBtn.innerHTML = '<i class="bi bi-activity"></i> Optimize Iteratively';
    }
    if (refreshBtn) {
        refreshBtn.disabled = false;
    }
}

function _getOptimizationSolutionWeights(solution = 'selected') {
    const result = lastOptimizationResult;
    if (!result || !result.success) {
        return null;
    }
    if (solution === 'candidate') {
        if (result.candidate_weights) {
            return result.candidate_weights;
        }
        if (result.numeric_weights_candidate && typeof result.numeric_weights_candidate === 'object') {
            return { ...currentWeights, ...result.numeric_weights_candidate };
        }
        return null;
    }
    if (solution === 'baseline') {
        if (result.baseline_weights) {
            return result.baseline_weights;
        }
        if (result.numeric_weights_baseline && typeof result.numeric_weights_baseline === 'object') {
            return { ...currentWeights, ...result.numeric_weights_baseline };
        }
        return null;
    }
    return result.optimized_weights || null;
}

function applyOptimizationSolution(solution = 'selected', showAlert = true) {
    try {
        const weights = _getOptimizationSolutionWeights(solution);
        if (!weights) {
            if (showAlert) {
                alert('No optimization solution available to apply.');
            }
            return false;
        }
        const appliedOk = applyWeightsToUiState(weights, { forceRawRecompute: false, syncPairs: true });
        if (!appliedOk) {
            if (showAlert) {
                alert('Unable to apply optimization weights.');
            }
            return false;
        }
        if (lastOptimizationResult && lastOptimizationResult.success) {
            const normalized = (solution === 'candidate' || solution === 'baseline')
                ? solution
                : String(lastOptimizationResult.selected_solution || 'candidate').toLowerCase();
            lastOptimizationResult.manual_applied_solution = normalized;
            renderOptimizationResults(lastOptimizationResult);
        }
        if (showAlert) {
            const appliedLabel = solution === 'candidate' ? 'candidate' : (solution === 'baseline' ? 'baseline' : 'selected');
            alert(`Applied ${appliedLabel} weights in UI (not saved globally).`);
        }
        return true;
    } finally {
        // Defensive unlock: avoids stale disabled state after manual actions (e.g. "Discard Candidate").
        unlockOptimizationControls();
    }
}

async function saveOptimizationSolution(solution = 'selected') {
    const okApply = applyOptimizationSolution(solution, false);
    if (!okApply) {
        return false;
    }
    const okSave = await saveGlobalWeights();
    if (okSave && lastOptimizationResult) {
        lastOptimizationResult.saved_global_weights = true;
        lastOptimizationResult.manual_saved_solution = solution;
        renderOptimizationResults(lastOptimizationResult);
    }
    return okSave;
}

async function runIterativeOptimization(saveGlobal = false) {
    const runBtn = document.getElementById('btnOptimizationRun');
    const refreshBtn = document.getElementById('btnOptimizationRefresh');
    const resultsBox = document.getElementById('optimizationResults');
    const startedAt = Date.now();
    let timer = null;
    let poller = null;
    let pollInFlight = false;

    const phaseLabel = (phase) => {
        const map = {
            idle: 'Idle',
            preparing: 'Preparing input',
            loading_labels: 'Loading manual labels',
            building_dataset: 'Building session-aware dataset',
            solver_running: 'Running solver',
            evaluating: 'Evaluating optimized weights',
            complete: 'Completed',
            error: 'Error'
        };
        return map[phase] || phase || 'Running';
    };

    const renderProgress = (progress) => {
        const live = document.getElementById('optimizationLiveStatus');
        if (!live) return;
        const ds = progress?.dataset || {};
        const solver = progress?.solver || {};
        const elapsed = Number(progress?.elapsed_sec || Math.floor((Date.now() - startedAt) / 1000));
        const iter = Number(solver.iteration || 0);
        const maxiter = Number(solver.maxiter || 0);
        const hasDataset = Number(ds.samples_total || 0) > 0;
        const labelsProcessed = Number(ds.labels_processed || 0);
        const labelsTotal = Number(ds.labels_total || 0);
        const hasBuildProgress = labelsTotal > 0;
        const rawFromPairs = Number(ds.raw_from_pair_index || 0);
        const rawFromCache = Number(ds.raw_from_optimizer_cache || 0);
        const rawComputed = Number(ds.raw_computed || 0);
        const pairsProcessed = Number(ds.pairs_processed || 0);
        const pairsTotal = Number(ds.pairs_total || 0);
        const skippedCrossPairs = Number(ds.skipped_cross_session_pairs || 0);
        const hasPairIndexProgress = pairsTotal > 0;
        live.innerHTML = `
            <div><strong>Phase:</strong> ${phaseLabel(progress?.phase)}</div>
            <div><strong>Status:</strong> ${progress?.message || 'Working...'}</div>
            ${hasDataset ? `<div><strong>Dataset:</strong> ${ds.samples_total} samples (pos ${ds.positive || 0}, neg ${ds.negative || 0}, sessions ${ds.sessions_count || 0})</div>` : ''}
                    ${hasPairIndexProgress ? `<div><strong>Pair Index:</strong> ${pairsProcessed}/${pairsTotal} (cross-session found ${skippedCrossPairs})</div>` : ''}
            ${hasBuildProgress ? `<div><strong>Dataset Build:</strong> labels ${labelsProcessed}/${labelsTotal}, raw pair-index ${rawFromPairs}, cache ${rawFromCache}, computed ${rawComputed}</div>` : ''}
            ${(iter > 0 || maxiter > 0) ? `<div><strong>Solver:</strong> iteration ${iter}/${maxiter || '?'}</div>` : ''}
            <div><strong>Elapsed:</strong> ${elapsed}s</div>
        `;
    };

    if (runBtn) {
        runBtn.disabled = true;
        runBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Optimizing...';
    }
    if (refreshBtn) {
        refreshBtn.disabled = true;
    }
    if (resultsBox) {
        resultsBox.innerHTML = `
            <div class="d-flex align-items-center text-primary mb-2">
                <span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>
                Running iterative optimization on labeled pairs...
            </div>
            <div class="small text-muted" id="optimizationLiveStatus">
                Initializing solver...
            </div>
        `;
        poller = setInterval(async () => {
            if (pollInFlight) return;
            pollInFlight = true;
            try {
                const pRes = await fetch('/api/paper_writing/weights_optimization_progress');
                const pData = await safeJsonParse(pRes, { success: false });
                if (pData && pData.success && pData.progress) {
                    renderProgress(pData.progress);
                }
            } catch (e) {
                const live = document.getElementById('optimizationLiveStatus');
                if (live) {
                    const sec = Math.floor((Date.now() - startedAt) / 1000);
                    live.textContent = `Optimization in progress... elapsed ${sec}s`;
                }
            } finally {
                pollInFlight = false;
            }
        }, 1200);
        timer = setInterval(() => {
            const live = document.getElementById('optimizationLiveStatus');
            if (!live) return;
            if (!live.innerHTML || live.innerHTML.indexOf('Phase:') === -1) {
                const sec = Math.floor((Date.now() - startedAt) / 1000);
                live.textContent = `Optimization in progress... elapsed ${sec}s`;
            }
        }, 1000);
    }
    try {
        const scope = document.getElementById('optimizationScope')?.value || 'all';
        const currentSession = (document.getElementById('optimizationCurrentSession')?.value || inferCurrentSessionName() || '').trim();
        const selectedSessionsMode = getOptimizationSessionFilterMode();
        const selectedSessions = getOptimizationSelectedSessions();
        const ignoreAuthor = document.getElementById('optimizationIgnoreAuthor')?.checked !== false;
        const startFromUi = document.getElementById('optimizationStartFromUi')?.checked === true;
        const defaultOptimizationThreshold = getCriticalSimilarityThreshold();
        const threshold = parseFloat(document.getElementById('optimizationThreshold')?.value || String(defaultOptimizationThreshold));
        const maxiter = parseInt(document.getElementById('optimizationMaxIter')?.value || '120', 10);
        const optimizerMethod = (document.getElementById('optimizationMethod')?.value || 'hybrid').toLowerCase();

        const payload = {
            scope: scope,
            current_session: currentSession,
            selected_sessions_mode: selectedSessionsMode,
            selected_sessions: selectedSessions,
            ignore_author: ignoreAuthor,
            start_from_ui: startFromUi,
            strict_path_labeled_pairs_only: appConfig?.optimizer_training_policy?.strict_path_labeled_pairs_only !== false,
            threshold: Number.isFinite(threshold) ? threshold : defaultOptimizationThreshold,
            maxiter: Number.isFinite(maxiter) ? maxiter : 120,
            optimizer_method: optimizerMethod,
            save_global_weights: !!saveGlobal
        };
        if (startFromUi) {
            payload.start_weights = { ...currentWeights };
        }

        const response = await fetch('/api/paper_writing/weights_optimization_iterative', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await safeJsonParse(response, { success: false });

        renderOptimizationResults(result);

        // Refresh coverage as labels/scope constraints may have changed availability.
        try {
            await refreshOptimizationDataset({ touchResults: false });
        } catch (refreshError) {
            console.warn('Optimization completed but dataset refresh failed:', refreshError);
        }
        return result;
    } catch (e) {
        console.error('runIterativeOptimization error:', e);
        renderOptimizationResults({ success: false, error: e.message });
        return { success: false, error: e.message };
    } finally {
        if (timer) {
            clearInterval(timer);
        }
        if (poller) {
            clearInterval(poller);
        }
        unlockOptimizationControls();
    }
}
