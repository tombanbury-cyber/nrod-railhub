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
