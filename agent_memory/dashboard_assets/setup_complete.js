(function () {
  "use strict";

  const stateNode = document.getElementById("finalize-job");
  if (!stateNode) {
    return;
  }

  const progress = document.getElementById("finalize-progress");
  const progressBar = document.getElementById("finalize-progress-bar");
  const progressTrack = document.getElementById("finalize-progress-track");
  const progressState = document.getElementById("finalize-progress-state");
  const progressSummary = document.getElementById("finalize-progress-summary");
  const progressPercent = document.getElementById("finalize-progress-percent");
  const progressDetail = document.getElementById("finalize-progress-detail");
  const stepNodes = new Map();
  document.querySelectorAll("[data-finalize-step]").forEach((node) => {
    stepNodes.set(node.dataset.finalizeStep, node);
  });
  const submitButtons = Array.from(
    document.querySelectorAll("[data-finalize-form] button[type='submit']")
  );
  const endpoint = stateNode.dataset.statusUrl || "/setup/finalize/status";
  let running = stateNode.dataset.running === "1";
  let timer = null;

  function setText(node, value) {
    if (node) {
      node.textContent = value;
    }
  }

  function formatElapsed(value) {
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds < 0) {
      return "";
    }
    return `${seconds.toLocaleString("it-IT", {
      maximumFractionDigits: 1,
    })} s`;
  }

  function buttonLabel(action, state) {
    if (action === "biography") {
      if (state === "success") {
        return "Rigenera biografia";
      }
      if (state === "running") {
        return "Generazione in corso…";
      }
      return "Genera biografia";
    }
    if (state === "success") {
      return "Ricostruisci indice";
    }
    if (state === "running") {
      return "Costruzione in corso…";
    }
    return "Costruisci indice";
  }

  function setButtonsDisabled(disabled) {
    submitButtons.forEach((button) => {
      button.disabled = disabled;
    });
  }

  function applyStep(step, busy) {
    const card = stepNodes.get(step.key);
    if (!card) {
      return;
    }
    card.classList.remove(
      "is-pending",
      "is-running",
      "is-success",
      "is-error"
    );
    card.classList.add(`is-${step.state}`);
    const text = card.querySelector(".setup-complete-text");
    setText(text, step.text || "");
    const button = card.querySelector("button[type='submit']");
    if (button) {
      button.textContent = buttonLabel(step.key, step.state);
      button.disabled = busy;
    }
  }

  function applyJob(job) {
    running = job.state === "running";
    stateNode.dataset.running = running ? "1" : "0";
    if (progress) {
      progress.hidden = false;
      progress.classList.remove(
        "is-pending",
        "is-running",
        "is-success",
        "is-error"
      );
      progress.classList.add(
        running ? "is-running" : job.ok ? "is-success" : "is-error"
      );
      progress.setAttribute("aria-busy", running ? "true" : "false");
    }

    const elapsed = formatElapsed(job.elapsed);
    if (running) {
      setText(progressState, "In corso");
      setText(progressSummary, elapsed ? `In corso da ${elapsed}.` : "In corso.");
      setText(
        progressDetail,
        "La prima costruzione dell’indice può richiedere qualche minuto. La pagina si aggiorna automaticamente."
      );
    } else if (job.ok) {
      setText(progressState, "Completato");
      setText(
        progressSummary,
        elapsed ? `Completamento terminato in ${elapsed}.` : "Completamento terminato."
      );
      setText(progressDetail, "Puoi riprovare o aprire la dashboard.");
    } else {
      setText(progressState, "Terminato con errori");
      setText(
        progressSummary,
        elapsed
          ? `Completamento terminato con errori in ${elapsed}.`
          : "Completamento terminato con errori."
      );
      setText(
        progressDetail,
        "Il profilo resta valido; riprova l’operazione non riuscita."
      );
    }

    const value = Math.max(0, Math.min(100, Number(job.progress) || 0));
    setText(progressPercent, `${value}%`);
    if (progressBar) {
      progressBar.style.width = `${value}%`;
    }
    if (progressTrack) {
      progressTrack.setAttribute("aria-valuenow", String(value));
    }

    (job.steps || []).forEach((step) => applyStep(step, running));
    setButtonsDisabled(running);
  }

  function schedule(delay) {
    if (timer !== null) {
      return;
    }
    timer = window.setTimeout(() => {
      timer = null;
      poll();
    }, delay);
  }

  async function poll() {
    try {
      const response = await fetch(endpoint, {
        cache: "no-store",
        credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      if (response.status === 401 || response.status === 409) {
        window.location.reload();
        return;
      }
      const contentType = response.headers.get("content-type") || "";
      if (!contentType.includes("application/json")) {
        if (running) {
          window.location.reload();
        }
        return;
      }
      if (!response.ok) {
        throw new Error(`Stato non disponibile (${response.status}).`);
      }
      const payload = await response.json();
      if (!payload.job) {
        if (running) {
          window.location.reload();
        }
        return;
      }
      applyJob(payload.job);
      if (payload.job.state === "running") {
        schedule(1000);
      }
    } catch (error) {
      if (!running) {
        return;
      }
      setText(
        progressDetail,
        "Aggiornamento non disponibile; nuovo tentativo in corso…"
      );
      schedule(2000);
    }
  }

  if (running) {
    setButtonsDisabled(true);
    poll();
  }
})();
