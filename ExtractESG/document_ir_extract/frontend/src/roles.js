/* Secrets live in this page's memory and are never added to localStorage. */
const secrets = new Map();
export const getSecret = (name) => secrets.get(name) || null;
export function setSecret(name, value) {
  value ? secrets.set(name, value.trim()) : secrets.delete(name);
}
export function roleConfig(provider = "local_qwen", model = null, allowed = false, allowOcrApi = false) {
  return {
    semantic_brain: {provider, model: provider === "qiniu_vlm" ? model || null : null},
    specialist: {provider: "local_nuextract", model: null},
    cloud_authorization: {allowed: Boolean(allowed), allow_ocr_api: Boolean(allowOcrApi)},
  };
}
export function initializeSecrets() {
  for (const [id, name] of [["settings-qiniu-key", "qiniu_api_key"], ["settings-ocr-token", "ocr_token"]]) {
    const input = document.getElementById(id);
    input?.addEventListener("input", () => setSecret(name, input.value));
  }
  document.getElementById("settings-clear-secrets")?.addEventListener("click", () => {
    secrets.clear();
    for (const id of ["settings-qiniu-key", "settings-ocr-token"]) document.getElementById(id).value = "";
    document.getElementById("settings-secret-status").textContent = "本页临时凭据已清空。后端环境配置仍可由后端使用。";
  });
}
