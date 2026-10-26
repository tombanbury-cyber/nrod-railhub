async function deleteLayout(layoutId) {
  if (!confirm(`Delete layout ${layoutId} and all of its berths and signals? This cannot be undone.`)) return;
  try {
    await submitJson(`/api/layouts/${encodeURIComponent(layoutId)}`, "DELETE");
    window.location.reload();
  } catch (error) {
    setStatus("admin-status", `Failed to delete layout ${layoutId}: ${error.message}`, true);
  }
}
document.querySelectorAll("[data-delete-layout]").forEach((btn) => {
  btn.addEventListener("click", () => deleteLayout(btn.dataset.deleteLayout));
});
document.getElementById("open-import").addEventListener("click", () => openImportDialog(""));

async function importLayoutBackup(file) {
  let text;
  try {
    text = await file.text();
    JSON.parse(text);
  } catch (error) {
    setStatus("admin-status", `${file.name} is not valid JSON: ${error.message}`, true);
    return;
  }
  let replace = false;
  for (;;) {
    const response = await fetch(`/api/layouts/import?replace=${replace}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: text,
    });
    const body = await response.json().catch(() => ({}));
    if (response.ok) {
      window.location.reload();
      return;
    }
    const detail = typeof body.detail === "string" ? body.detail : response.statusText;
    if (response.status === 409 && !replace && /already exists/.test(detail) &&
        confirm(`${detail}\n\nOverwrite the existing layout and all of its objects?`)) {
      replace = true;
      continue;
    }
    setStatus("admin-status", `Import failed: ${detail}`, true);
    return;
  }
}
const importLayoutFile = document.getElementById("import-layout-file");
document.getElementById("import-layout-button").addEventListener("click", () => importLayoutFile.click());
importLayoutFile.addEventListener("change", () => {
  const file = importLayoutFile.files[0];
  importLayoutFile.value = "";
  if (file) importLayoutBackup(file);
});
