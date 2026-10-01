"use strict";
document.querySelectorAll(".image-upload").forEach((form) => {
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const file = form.querySelector("input[type=file]").files[0];
    const status = form.querySelector(".upload-status");
    const button = form.querySelector("button");
    const progress = form.querySelector("progress");
    if (!file || file.size === 0 || file.size > Number(form.dataset.limit)) {
      status.textContent = "Choose a non-empty image within the upload limit.";
      return;
    }
    button.disabled = true;
    status.textContent = "Uploading…";
    const request = new XMLHttpRequest();
    request.open("POST", form.dataset.uploadUrl);
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.setRequestHeader("X-CSRF-Token", form.dataset.token);
    request.setRequestHeader("X-Upload-Filename", encodeURIComponent(file.name));
    request.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) progress.value = (e.loaded / e.total) * 100;
    });
    request.addEventListener("load", () => {
      if (request.status === 201) { window.location.reload(); return; }
      let message = "Upload failed. Check your session and storage limits, then retry.";
      try { message = JSON.parse(request.responseText).error || message; } catch (_) { /* HTML error/login response */ }
      status.textContent = message;
      button.disabled = false;
    });
    request.addEventListener("error", () => {
      status.textContent = "Connection lost. Refresh to check whether the file was saved before retrying.";
      button.disabled = false;
    });
    request.send(file);
  });
});
