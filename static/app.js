// Asset Factory Frontend

const el = (id) => document.getElementById(id);

const state = {
  apiKey: localStorage.getItem("assetFactoryApiKey") || "",
  selectedAssetId: null,
};

function apiHeaders(withAuth = false) {
  const headers = { "Content-Type": "application/json" };
  if (withAuth && state.apiKey) {
    headers["x-api-key"] = state.apiKey;
  }
  return headers;
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const message = await response.text();
    throw new Error(`${response.status} ${message}`);
  }
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response.text();
}

function setApiKey() {
  state.apiKey = el("api-key").value.trim();
  localStorage.setItem("assetFactoryApiKey", state.apiKey);
}

async function checkSdHealth() {
  const statusEl = el("sd-status");
  statusEl.textContent = "확인 중...";
  try {
    const data = await request("/api/health/sd");
    statusEl.textContent = `정상 (모델 ${data.model_count}개)`;
  } catch (error) {
    statusEl.textContent = `실패: ${error.message}`;
  }
}

async function submitGenerateForm(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);
  const payload = {
    project: formData.get("project"),
    asset_key: formData.get("asset_key"),
    category: formData.get("category") || "character",
    prompt: formData.get("prompt"),
  };

  const resultEl = el("generate-result");
  resultEl.textContent = "등록 중...";
  try {
    const data = await request("/api/generate", {
      method: "POST",
      headers: apiHeaders(true),
      body: JSON.stringify(payload),
    });
    resultEl.textContent = `작업 등록 완료: ${data.job_id}`;
    el("job-id-input").value = data.job_id;
  } catch (error) {
    resultEl.textContent = `실패: ${error.message}`;
  }
}

async function loadSpecs() {
  const select = el("spec-select");
  select.innerHTML = "";
  try {
    const projects = await request("/api/projects");
    if (projects.length === 0) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "spec 없음";
      select.appendChild(option);
      return;
    }
    projects.forEach((project) => {
      const option = document.createElement("option");
      option.value = project.id;
      option.textContent = project.id;
      select.appendChild(option);
    });
  } catch (error) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = `로드 실패: ${error.message}`;
    select.appendChild(option);
  }
}

async function generateBatch() {
  const specId = el("spec-select").value;
  const resultEl = el("batch-result");
  if (!specId) {
    resultEl.textContent = "유효한 spec을 선택하세요.";
    return;
  }
  resultEl.textContent = "배치 등록 중...";
  try {
    const data = await request("/api/generate/batch", {
      method: "POST",
      headers: apiHeaders(true),
      body: JSON.stringify({ spec_id: specId }),
    });
    resultEl.textContent = `배치 등록 완료: ${data.job_id} (task ${data.task_count})`;
    el("job-id-input").value = data.job_id;
  } catch (error) {
    resultEl.textContent = `실패: ${error.message}`;
  }
}

async function runScan() {
  const resultEl = el("scan-result");
  const project = el("scan-project").value.trim();
  const rootPath = el("scan-root").value.trim();
  if (!project || !rootPath) {
    resultEl.textContent = "project와 scan root path를 입력하세요.";
    return;
  }
  resultEl.textContent = "스캔 중...";
  try {
    const data = await request("/api/projects/scan", {
      method: "POST",
      headers: apiHeaders(true),
      body: JSON.stringify({ project, root_path: rootPath }),
    });
    resultEl.textContent = `스캔 완료: ${data.scanned_count}개`;
    await refreshAssets();
    await refreshSummary();
  } catch (error) {
    resultEl.textContent = `실패: ${error.message}`;
  }
}

async function runExport() {
  const resultEl = el("export-result");
  const project = el("export-project").value.trim();
  const outputDir = el("export-dir").value.trim();
  const saveManifest = el("save-manifest").checked;

  resultEl.textContent = "내보내기 중...";
  try {
    const payload = { save_manifest: saveManifest };
    if (project) payload.project = project;
    if (outputDir) payload.output_dir = outputDir;
    const data = await request("/api/export", {
      method: "POST",
      headers: apiHeaders(true),
      body: JSON.stringify(payload),
    });
    resultEl.textContent = `완료: ${data.exported_count}개, 경로=${data.output_dir}${data.manifest_path ? `, manifest=${data.manifest_path}` : ""}`;
    await refreshSummary();
  } catch (error) {
    resultEl.textContent = `실패: ${error.message}`;
  }
}

function candidateImageUrl(project, assetKey, jobId, slotIndex) {
  const q = new URLSearchParams({
    project,
    asset_key: assetKey,
    job_id: jobId,
    slot_index: String(slotIndex),
  });
  return `/api/asset-candidates/image?${q.toString()}`;
}

function cardTemplate(asset) {
  const card = document.createElement("article");
  card.className = "asset-card";
  if (state.selectedAssetId === asset.id) {
    card.classList.add("selected");
  }
  card.dataset.assetId = asset.id;
  const imageUrl = `/api/assets/${asset.id}/image`;
  card.innerHTML = `
    <img src="${imageUrl}" alt="${asset.asset_key}" loading="lazy">
    <h3>${asset.asset_key}</h3>
    <div class="asset-meta">
      <div>project: ${asset.project}</div>
      <div>category: ${asset.category}</div>
      <div>status: ${asset.status}</div>
      <div>validation: ${asset.validation_status} (${asset.color_count} colors)</div>
      <div>size: ${asset.width}x${asset.height}</div>
    </div>
    <div class="asset-actions">
      <button type="button" data-action="approve">승인</button>
      <button type="button" data-action="reject">리젝</button>
      <button type="button" data-action="validate">검증</button>
      <button type="button" data-action="regen">재생성</button>
    </div>
    <small>${asset.validation_message || ""}</small>
  `;

  card.addEventListener("click", (e) => {
    if (e.target.closest("button")) return;
    selectAsset(asset.id);
  });

  card.querySelector('[data-action="approve"]').addEventListener("click", (e) => {
    e.stopPropagation();
    patchAssetStatus(asset.id, "approved");
  });
  card.querySelector('[data-action="reject"]').addEventListener("click", (e) => {
    e.stopPropagation();
    patchAssetStatus(asset.id, "rejected");
  });
  card.querySelector('[data-action="validate"]').addEventListener("click", (e) => {
    e.stopPropagation();
    revalidateAsset(asset.id);
  });
  card.querySelector('[data-action="regen"]').addEventListener("click", (e) => {
    e.stopPropagation();
    regenerateAsset(asset.id);
  });

  return card;
}

async function selectAsset(assetId) {
  state.selectedAssetId = assetId;
  el("detail-panel").classList.remove("hidden");
  await loadDetail(assetId);
  document.querySelectorAll(".asset-card").forEach((c) => {
    c.classList.toggle("selected", c.dataset.assetId === assetId);
  });
}

function closeDetail() {
  state.selectedAssetId = null;
  el("detail-panel").classList.add("hidden");
  document.querySelectorAll(".asset-card").forEach((c) => c.classList.remove("selected"));
}

async function loadDetail(assetId) {
  const metaEl = el("detail-meta");
  const histEl = el("detail-history");
  const candEl = el("detail-candidates");
  const hintEl = el("detail-candidates-hint");
  metaEl.textContent = "로딩 중...";
  histEl.textContent = "";
  candEl.innerHTML = "";
  try {
    const asset = await request(`/api/assets/${assetId}/detail`);
    const view = { ...asset };
    metaEl.textContent = JSON.stringify(view, null, 2);

    const history = await request(`/api/assets/${assetId}/history`);
    histEl.innerHTML =
      history.length === 0
        ? "이력 없음"
        : history
            .map(
              (h) =>
                `<div>v${h.version} · ${h.created_at || ""} · ${h.validation_status || ""} · ${h.image_path || ""}</div>`
            )
            .join("");

    const jobId = asset.job_id || "";
    const q = jobId ? `?job_id=${encodeURIComponent(jobId)}` : "";
    const cands = await request(`/api/assets/${assetId}/candidates${q}`);
    if (cands.length === 0) {
      hintEl.textContent = "이 에셋에 등록된 후보 슬롯이 없습니다 (단일 생성 또는 다른 job).";
      return;
    }
    const useJob = jobId || cands[0].job_id;
    const slots = cands.filter((c) => c.job_id === useJob);
    hintEl.textContent = `job ${useJob} · 슬롯 ${slots.length}개 — 적용할 슬롯을 고릅니다.`;
    slots.forEach((c) => {
      const wrap = document.createElement("div");
      wrap.className = "candidate-thumb";
      const img = document.createElement("img");
      img.src = candidateImageUrl(asset.project, asset.asset_key, c.job_id, c.slot_index);
      img.alt = `slot ${c.slot_index}`;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = `슬롯 ${c.slot_index} 적용`;
      btn.addEventListener("click", async () => {
        try {
          await request(`/api/assets/${assetId}/select-candidate`, {
            method: "POST",
            headers: apiHeaders(true),
            body: JSON.stringify({ job_id: c.job_id, slot_index: c.slot_index }),
          });
          await refreshAssets();
          await loadDetail(assetId);
        } catch (error) {
          alert(`후보 적용 실패: ${error.message}`);
        }
      });
      wrap.appendChild(img);
      wrap.appendChild(document.createElement("br"));
      wrap.appendChild(btn);
      candEl.appendChild(wrap);
    });
  } catch (error) {
    metaEl.textContent = `상세 로드 실패: ${error.message}`;
  }
}

async function batchRevalidateFailed() {
  const project = el("filter-project").value.trim();
  const q = project ? `?project=${encodeURIComponent(project)}` : "";
  try {
    const data = await request(`/api/batch/revalidate-failed${q}`, {
      method: "POST",
      headers: apiHeaders(true),
    });
    alert(`재검증 ${data.revalidated}건, 여전히 FAIL ${data.still_fail}건`);
    await refreshAssets();
    await refreshSummary();
  } catch (error) {
    alert(`일괄 재검증 실패: ${error.message}`);
  }
}

async function batchRegenerateFailed() {
  const project = el("filter-project").value.trim();
  const q = project ? `?project=${encodeURIComponent(project)}` : "";
  try {
    const data = await request(`/api/batch/regenerate-failed${q}`, {
      method: "POST",
      headers: apiHeaders(true),
    });
    alert(`재생성 작업 ${data.queued_jobs}건 등록됨`);
    if (data.job_ids && data.job_ids[0]) {
      el("job-id-input").value = data.job_ids[0];
    }
    await refreshRecentJobs();
  } catch (error) {
    alert(`일괄 재생성 실패: ${error.message}`);
  }
}

async function refreshAssets() {
  const grid = el("assets-grid");
  grid.textContent = "로딩 중...";
  const params = new URLSearchParams();
  const project = el("filter-project").value.trim();
  const status = el("filter-status").value;
  const category = el("filter-category").value;
  const validation = el("filter-validation").value;
  if (project) params.set("project", project);
  if (status) params.set("status", status);
  if (category) params.set("category", category);
  if (validation) params.set("validation_status", validation);

  try {
    const assets = await request(`/api/assets?${params.toString()}`);
    grid.innerHTML = "";
    if (assets.length === 0) {
      grid.textContent = "표시할 에셋이 없습니다.";
      closeDetail();
      return;
    }
    assets.forEach((asset) => grid.appendChild(cardTemplate(asset)));
    if (state.selectedAssetId && assets.some((a) => a.id === state.selectedAssetId)) {
      await loadDetail(state.selectedAssetId);
    } else if (state.selectedAssetId) {
      closeDetail();
    }
  } catch (error) {
    grid.textContent = `조회 실패: ${error.message}`;
  }
}

async function patchAssetStatus(assetId, status) {
  try {
    await request(`/api/assets/${assetId}`, {
      method: "PATCH",
      headers: apiHeaders(true),
      body: JSON.stringify({ status }),
    });
    await refreshAssets();
    await refreshSummary();
    if (state.selectedAssetId === assetId) {
      await loadDetail(assetId);
    }
  } catch (error) {
    alert(`상태 변경 실패: ${error.message}`);
  }
}

async function revalidateAsset(assetId) {
  try {
    const data = await request(`/api/validate/${assetId}`, {
      method: "POST",
      headers: apiHeaders(true),
    });
    alert(`검증 결과: ${data.passed ? "PASS" : "FAIL"} - ${data.message}`);
    await refreshAssets();
    await refreshSummary();
    if (state.selectedAssetId === assetId) {
      await loadDetail(assetId);
    }
  } catch (error) {
    alert(`검증 실패: ${error.message}`);
  }
}

async function regenerateAsset(assetId) {
  try {
    const data = await request(`/api/assets/${assetId}/regenerate`, {
      method: "POST",
      headers: apiHeaders(true),
    });
    el("job-id-input").value = data.job_id;
    alert(`재생성 작업 등록: ${data.job_id}`);
  } catch (error) {
    alert(`재생성 실패: ${error.message}`);
  }
}

function summaryCard(label, value) {
  return `<div class="summary-card"><span>${label}</span><b>${value}</b></div>`;
}

async function refreshSummary() {
  const cardsEl = el("summary-cards");
  const listEl = el("summary-by-category");
  const project = el("filter-project").value.trim();
  const params = new URLSearchParams();
  if (project) params.set("project", project);

  cardsEl.textContent = "로딩 중...";
  listEl.textContent = "";

  try {
    const data = await request(`/api/assets/summary?${params.toString()}`);
    const byStatus = data.by_status || {};
    const byValidation = data.by_validation || {};
    const total = data.total || 0;
    const approved = byStatus.approved || 0;
    const validationFail = byValidation.fail || 0;
    const approvalRate = total > 0 ? ((approved / total) * 100).toFixed(1) : "0.0";
    const failRate = total > 0 ? ((validationFail / total) * 100).toFixed(1) : "0.0";

    cardsEl.innerHTML = [
      summaryCard("총 에셋", total),
      summaryCard("승인", approved),
      summaryCard("리젝", byStatus.rejected || 0),
      summaryCard("대기", byStatus.pending || 0),
      summaryCard("검증 PASS", byValidation.pass || 0),
      summaryCard("검증 FAIL", byValidation.fail || 0),
      summaryCard("승인률", `${approvalRate}%`),
      summaryCard("실패율", `${failRate}%`),
    ].join("");

    const categories = Object.entries(data.by_category || {});
    listEl.textContent =
      categories.length === 0
        ? "카테고리 집계 없음"
        : categories.map(([key, value]) => `${key}: ${value}`).join(" | ");
  } catch (error) {
    cardsEl.textContent = `요약 조회 실패: ${error.message}`;
  }
}

async function refreshRecentJobs() {
  const jobsEl = el("recent-jobs");
  jobsEl.textContent = "로딩 중...";
  try {
    const jobs = await request("/api/jobs/recent?limit=8");
    if (jobs.length === 0) {
      jobsEl.textContent = "최근 작업 없음";
      return;
    }
    jobsEl.innerHTML = jobs
      .map(
        (job) =>
          `<div>${job.created_at} | ${job.job_type} | ${job.status} | done ${job.completed_count}/${job.total_count} | fail ${job.failed_count}</div>`
      )
      .join("");
  } catch (error) {
    jobsEl.textContent = `작업 조회 실패: ${error.message}`;
  }
}

async function checkJob() {
  const jobId = el("job-id-input").value.trim();
  if (!jobId) return;
  try {
    const data = await request(`/api/jobs/${jobId}`);
    el("job-result").textContent = JSON.stringify(data, null, 2);
  } catch (error) {
    el("job-result").textContent = `조회 실패: ${error.message}`;
  }
}

function startSse() {
  const events = new EventSource("/api/events");
  events.onmessage = async () => {
    await refreshAssets();
    await refreshSummary();
    await refreshRecentJobs();
    await checkJob();
    if (state.selectedAssetId) {
      await loadDetail(state.selectedAssetId);
    }
  };
  events.onerror = () => {
    events.close();
    setTimeout(startSse, 3000);
  };
}

function init() {
  el("api-key").value = state.apiKey;
  el("save-api-key").addEventListener("click", setApiKey);
  el("check-sd").addEventListener("click", checkSdHealth);
  el("generate-form").addEventListener("submit", submitGenerateForm);
  el("refresh-specs").addEventListener("click", loadSpecs);
  el("generate-batch").addEventListener("click", generateBatch);
  el("run-scan").addEventListener("click", runScan);
  el("run-export").addEventListener("click", runExport);
  el("refresh-summary").addEventListener("click", refreshSummary);
  el("refresh-jobs").addEventListener("click", refreshRecentJobs);
  el("refresh-assets").addEventListener("click", refreshAssets);
  el("check-job").addEventListener("click", checkJob);
  el("close-detail").addEventListener("click", closeDetail);
  el("batch-revalidate-fail").addEventListener("click", batchRevalidateFailed);
  el("batch-regenerate-fail").addEventListener("click", batchRegenerateFailed);
  ["filter-category", "filter-validation", "filter-status"].forEach((id) => {
    el(id).addEventListener("change", () => {
      refreshAssets();
    });
  });
  loadSpecs();
  refreshAssets();
  refreshSummary();
  refreshRecentJobs();
  startSse();
}

window.addEventListener("DOMContentLoaded", init);
