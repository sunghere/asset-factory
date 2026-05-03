/* Tiny fetch + EventSource wrapper. Exposes window.api.

   One-shot legacy localStorage migration:
     assetFactoryApiKey  → af_api_key
   Runs at bundle evaluation time (before any request()). If af_api_key is
   already set, we never overwrite it; the legacy key is just removed to
   keep devtools tidy. Missing/unsupported localStorage is ignored. */
(function migrateApiKey() {
  try {
    const ls = window.localStorage;
    if (!ls) return;
    const legacy = ls.getItem('assetFactoryApiKey');
    if (legacy == null) return;
    const current = ls.getItem('af_api_key');
    if (!current && legacy) ls.setItem('af_api_key', legacy);
    ls.removeItem('assetFactoryApiKey');
  } catch {
    // storage disabled (private mode / quota) — nothing to migrate.
  }
})();

class ApiError extends Error {
  constructor(status, statusText, body) {
    super(`${status} ${statusText}: ${typeof body === 'string' ? body : JSON.stringify(body)}`);
    this.name = 'ApiError';
    this.status = status;
    this.statusText = statusText;
    this.body = body;
  }
}

async function request(method, path, { body, headers, signal } = {}) {
  const opts = {
    method,
    headers: { Accept: 'application/json', ...(headers || {}) },
    signal,
  };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  // Best-effort API key header. Server only enforces on writes; safe to pass on reads.
  const apiKey = window.localStorage?.getItem('af_api_key');
  if (apiKey) opts.headers['X-API-Key'] = apiKey;

  const res = await fetch(path, opts);
  const text = await res.text();
  let parsed = text;
  try { parsed = text ? JSON.parse(text) : null; } catch { /* keep as text */ }

  if (!res.ok) throw new ApiError(res.status, res.statusText, parsed);
  return parsed;
}

const api = {
  ApiError,

  // System / health
  health: () => request('GET', '/api/health'),
  /** @deprecated Use comfyuiHealth() — A1111 backend is being phased out. */
  healthSd: () => request('GET', '/api/health/sd'),
  // ComfyUI primary backend (PLAN_comfyui_catalog.md §3.1)
  comfyuiHealth: () => request('GET', '/api/comfyui/health'),
  comfyuiCatalog: () => request('GET', '/api/comfyui/catalog'),
  comfyuiQueue: () => request('GET', '/api/comfyui/queue'),
  gcStatus: () => request('GET', '/api/system/gc/status'),

  // Catalog (legacy A1111 — deprecated, kept for backwards compat)
  /** @deprecated Use comfyuiCatalog().checkpoints — A1111 endpoint will be removed. */
  models: () => request('GET', '/api/sd/catalog/models'),
  /** @deprecated Use comfyuiCatalog().loras — A1111 endpoint will be removed. */
  loras: () => request('GET', '/api/sd/catalog/loras'),
  catalogUsage: () => request('GET', '/api/sd/catalog/usage'),
  catalogUsageBatches: ({ model, lora, limit = 20 } = {}) => {
    const qs = new URLSearchParams();
    if (model) qs.set('model', model);
    if (lora) qs.set('lora', lora);
    qs.set('limit', String(limit));
    return request('GET', `/api/sd/catalog/usage/batches?${qs.toString()}`);
  },

  // Projects
  listProjects: () => request('GET', '/api/projects'),
  getProjectSpec: (id) => request('GET', `/api/projects/${encodeURIComponent(id)}/spec`),

  // Batches
  listBatches: ({ since, limit, status, project } = {}) => {
    const qs = new URLSearchParams();
    if (since) qs.set('since', since);
    if (limit) qs.set('limit', String(limit));
    if (status) qs.set('status', status);
    if (project) qs.set('project', project);
    const q = qs.toString();
    return request('GET', `/api/batches${q ? '?' + q : ''}`);
  },
  getBatchDetail: (batchId) =>
    request('GET', `/api/batches/${encodeURIComponent(batchId)}`),
  listBatchCandidates: (batchId) =>
    request('GET', `/api/batches/${encodeURIComponent(batchId)}/candidates`),
  listBatchTasks: (batchId) =>
    request('GET', `/api/batches/${encodeURIComponent(batchId)}/tasks`),
  retryFailedTasks: (batchId) =>
    request('POST', `/api/batches/${encodeURIComponent(batchId)}/retry-failed`),
  cancelBatch: (batchId) =>
    request('POST', `/api/batches/${encodeURIComponent(batchId)}/cancel`),
  deleteBatch: (batchId, { force = false } = {}) => {
    const qs = force ? '?force=true' : '';
    return request('DELETE', `/api/batches/${encodeURIComponent(batchId)}${qs}`);
  },
  rejectCandidate: (batchId, candidateId) =>
    request('POST', `/api/batches/${encodeURIComponent(batchId)}/candidates/${candidateId}/reject`),
  unrejectCandidate: (batchId, candidateId) =>
    request('POST', `/api/batches/${encodeURIComponent(batchId)}/candidates/${candidateId}/unreject`),

  // Cherry-pick queue
  cherryPickQueue: ({ since, limit } = {}) => {
    const qs = new URLSearchParams();
    if (since) qs.set('since', since);
    if (limit) qs.set('limit', String(limit));
    const q = qs.toString();
    return request('GET', `/api/cherry-pick/queue${q ? '?' + q : ''}`);
  },

  // Approve / undo
  approveFromCandidate: (payload) =>
    request('POST', '/api/assets/approve-from-candidate', { body: payload }),
  undoApprove: (assetId) =>
    request('POST', `/api/assets/${encodeURIComponent(assetId)}/undo-approve`),

  // Assets
  listAssets: (params = {}) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v != null && v !== '') qs.set(k, v);
    const q = qs.toString();
    return request('GET', `/api/assets${q ? '?' + q : ''}`);
  },
  assetSummary: (project) => {
    const qs = project ? `?project=${encodeURIComponent(project)}` : '';
    return request('GET', `/api/assets/summary${qs}`);
  },
  getAssetDetail: (id) =>
    request('GET', `/api/assets/${encodeURIComponent(id)}/detail`),
  getAssetHistory: (id) =>
    request('GET', `/api/assets/${encodeURIComponent(id)}/history`),
  getAssetCandidates: (id, jobId) => {
    const q = jobId ? `?job_id=${encodeURIComponent(jobId)}` : '';
    return request('GET', `/api/assets/${encodeURIComponent(id)}/candidates${q}`);
  },
  selectAssetCandidate: (id, body) =>
    request('POST', `/api/assets/${encodeURIComponent(id)}/select-candidate`, { body }),
  restoreAssetHistory: (id, version) =>
    request('POST', `/api/assets/${encodeURIComponent(id)}/restore-history`, { body: { version } }),
  regenerateAsset: (id) =>
    request('POST', `/api/assets/${encodeURIComponent(id)}/regenerate`),
  validateAsset: (id) =>
    request('POST', `/api/validate/${encodeURIComponent(id)}`),
  validateAll: (project) => {
    const q = project ? `?project=${encodeURIComponent(project)}` : '';
    return request('POST', `/api/validate/all${q}`);
  },
  revalidateFailed: (project) => {
    const q = project ? `?project=${encodeURIComponent(project)}` : '';
    return request('POST', `/api/batch/revalidate-failed${q}`);
  },
  regenerateFailed: (project) => {
    const q = project ? `?project=${encodeURIComponent(project)}` : '';
    return request('POST', `/api/batch/regenerate-failed${q}`);
  },
  patchAssetStatus: (id, status) =>
    request('PATCH', `/api/assets/${encodeURIComponent(id)}`, { body: { status } }),

  // Jobs
  recentJobs: (limit = 20) =>
    request('GET', `/api/jobs/recent?limit=${limit}`),
  getJob: (id) => request('GET', `/api/jobs/${encodeURIComponent(id)}`),

  // Design batch (workflow 곱집합 enqueue) — DesignBatchRequest payload.
  // spec: { asset_key, project, category, workflow_category, workflow_variants[],
  //         workflow_params_overrides[], prompts[], common, seeds_per_combo, ... }
  createDesignBatch: (spec) =>
    request('POST', '/api/batches', { body: spec }),

  // 단일/N 회 ComfyUI 워크플로우 생성 — WorkflowGenerateRequest payload.
  // spec: { project, asset_key, category, workflow_category, workflow_variant,
  //         prompt, subject, prompt_mode, candidates_total, workflow_params, ... }
  workflowsGenerate: (spec) =>
    request('POST', '/api/workflows/generate', { body: spec }),

  // Export
  runExport: (body) => request('POST', '/api/export', { body }),
  getManifest: ({ project, category, since } = {}) => {
    const params = new URLSearchParams();
    if (project) params.set('project', project);
    if (category) params.set('category', category);
    if (since) params.set('since', since);
    const q = params.toString();
    return request('GET', `/api/export/manifest${q ? `?${q}` : ''}`);
  },

  // System
  runGc: () => request('POST', '/api/system/gc/run'),
  systemDb: () => request('GET', '/api/system/db'),
  systemWorker: () => request('GET', '/api/system/worker'),
  systemLogs: ({ level, limit } = {}) => {
    const qs = new URLSearchParams();
    if (level) qs.set('level', level);
    if (limit) qs.set('limit', String(limit));
    const q = qs.toString();
    return request('GET', `/api/system/logs/recent${q ? '?' + q : ''}`);
  },

  // Convenience builders
  imageUrl: (asset) => asset?.image_url || (asset?.id ? `/api/assets/${asset.id}/image` : null),
  candidateImageUrl: (candidate, size) => {
    if (!candidate) return null;
    const base = candidate.image_url
      || (candidate.id ? `/api/asset-candidates/${candidate.id}/image` : null);
    if (!base) return null;
    if (!size) return base;
    const sep = base.includes('?') ? '&' : '?';
    return `${base}${sep}size=${size}`;
  },

  // SSE
  events(onEvent, onError) {
    const es = new EventSource('/api/events');
    es.onmessage = (e) => {
      try { onEvent(JSON.parse(e.data)); } catch (err) { /* ignore parse errs */ }
    };
    if (onError) es.onerror = onError;
    return es;
  },
};

window.api = api;
window.ApiError = ApiError;
