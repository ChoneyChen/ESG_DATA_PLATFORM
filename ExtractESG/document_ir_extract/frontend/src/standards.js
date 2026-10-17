import {docRequest} from "./api.js";

const packages = new Map();
export async function loadStandards() {
  const catalog = await docRequest("/api/pipeline/standards/catalog");
  if (!Array.isArray(catalog?.packages) || !catalog.diagnostics) {
    throw new Error("标准目录合同不匹配，请更新后端服务；旧记录保留");
  }
  const results = catalog.packages.map((entry) => {
    const key = `${entry.package_id}@${entry.package_version}#${entry.source_digest}`;
    const summary = {format_version: entry.format_version, manifest: {
      package_id: entry.package_id, package_version: entry.package_version,
      status: entry.status, core_source_datapoint_ids: entry.core_source_datapoint_ids || [],
      standard: {disclosure_requirement: entry.disclosure_requirement}},
      metrics: entry.metric_summaries || [], relations: []};
    return {key, catalog: entry, package: packages.get(key) || summary,
      detailsLoaded: packages.has(key), detailError: null};
  });
  // Diagnostic states stay distinct from a legitimately empty catalog.
  Object.assign(results, {diagnostics: catalog.diagnostics, planned: catalog.planned || [],
    historical: catalog.historical || [], registrationError: catalog.registration_error});
  return results;
}

export async function ensureStandardPackage(entry) {
  if (entry.detailsLoaded) return entry.package;
  if (entry.detailLoading) return entry.detailLoading;
  entry.detailLoading = (async () => {
    const standard = await docRequest(`/api/pipeline/standards/${encodeURIComponent(entry.catalog.package_id)}/${encodeURIComponent(entry.catalog.package_version)}`);
    if (standard?.manifest?.package_id !== entry.catalog.package_id ||
        standard?.manifest?.package_version !== entry.catalog.package_version ||
        standard?.compilation?.source_digest !== entry.catalog.source_digest) {
      throw new Error("标准包内容已变化，请刷新目录并重新预览选择");
    }
    const listed = entry.package.metrics.map(metric => [metric.metric_id, metric.source_datapoint_id]);
    const detailed = standard.metrics.map(metric => [metric.metric_id, metric.source_datapoint_id]);
    if (JSON.stringify(listed) !== JSON.stringify(detailed)) {
      throw new Error("标准目录与详细包的原编号不一致，请刷新目录");
    }
    packages.set(entry.key, standard);
    entry.package = standard; entry.detailsLoaded = true; entry.detailError = null;
    return standard;
  })();
  try { return await entry.detailLoading; }
  catch (error) { entry.detailError = error; throw error; }
  finally { entry.detailLoading = null; }
}

export function standardSelections(entries, selectedIds) {
  return entries.map(({catalog, package: standard}) => ({
    package_id: catalog.package_id || standard.manifest.package_id,
    package_version: catalog.package_version || standard.manifest.package_version,
    metric_ids: standard.metrics.filter((metric) => selectedIds.has(metric.metric_id)).map((metric) => metric.metric_id),
  })).filter((selection) => selection.metric_ids.length);
}

export const packageCoreMetricIds = (standard) => {
  const originalIds = new Set(standard?.manifest?.core_source_datapoint_ids || []);
  return (standard?.metrics || []).filter((metric) => originalIds.has(metric.source_datapoint_id)).map((metric) => metric.metric_id);
};

export async function loadPlatformMajorProfile() {
  // The subset is explicit. The endpoint also preserves the global 49-ID
  // selection and identifies pending packages instead of silently dropping them.
  return docRequest('/api/pipeline/extraction-profiles/platform-major?topics=E1,E2,E3');
}

export function profileMetricIds(entries, profile) {
  if (!profile?.selection_snapshot) throw new Error('主要条款配置缺少版本快照，请刷新后重试');
  const result = [];
  const originalIds = [];
  for (const selection of profile.session_standards || []) {
    const entry = entries.find(({catalog, package: standard}) =>
      (catalog.package_id || standard.manifest.package_id) === selection.package_id &&
      (catalog.package_version || standard.manifest.package_version) === selection.package_version);
    if (!entry) throw new Error(`主要条款要求的标准包版本不可用：${selection.package_id}@${selection.package_version}`);
    const available = new Map(entry.package.metrics.map((metric) => [metric.metric_id, metric]));
    const core = new Set(packageCoreMetricIds(entry.package));
    for (const id of selection.metric_ids || []) {
      if (!available.has(id) || !core.has(id)) throw new Error(`主要条款没有对应的包内核心条款：${id}`);
      result.push(id); originalIds.push(available.get(id).source_datapoint_id);
    }
  }
  const selectedOriginalIds = profile.source_datapoint_ids || [];
  if (result.length !== profile.output_count || new Set(result).size !== result.length ||
      originalIds.length !== selectedOriginalIds.length || originalIds.some((id, i) => id !== selectedOriginalIds[i])) {
    throw new Error('主要条款配置与原编号、标准包选择不一致');
  }
  return result;
}
