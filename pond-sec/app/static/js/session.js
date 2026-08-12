/* Live challenge clock.
   Kept in a file rather than inline so the Content-Security-Policy can forbid
   inline script outright — the header is worth nothing if the app needs
   'unsafe-inline' to work. */
(function () {
  // The panel only exists when a challenge on this page is running, so every
  // other page loads this file and does nothing.
  const source = document.querySelector(".live-panel");
  const clock = source && source.querySelector("#clock");
  if (!clock || !source) return;

  let elapsed = 0;
  let running = true;

  function paint() {
    const h = String(Math.floor(elapsed / 3600)).padStart(2, "0");
    const m = String(Math.floor((elapsed % 3600) / 60)).padStart(2, "0");
    const s = String(elapsed % 60).padStart(2, "0");
    clock.textContent = `${h}:${m}:${s}`;
  }

  // The server owns the clock; the browser only counts between polls, so a
  // reload or a second tab never shows a different time.
  async function sync() {
    try {
      const response = await fetch(source.dataset.timerUrl, {
        cache: "no-store",
        credentials: "same-origin",
      });
      if (!response.ok) return;
      const data = await response.json();
      elapsed = data.elapsed_seconds;
      running = data.status === "in_progress";
      paint();
    } catch (err) {
      /* keep counting locally until the next poll succeeds */
    }
  }

  sync();
  setInterval(function () { if (running) { elapsed += 1; paint(); } }, 1000);
  setInterval(sync, 30000);
})();
