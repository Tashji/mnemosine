(function () {
  "use strict";

  const form = document.getElementById("setup-form");
  if (!form) {
    return;
  }

  const steps = Array.from(form.querySelectorAll(".wizard-step"));
  const progressButtons = Array.from(
    document.querySelectorAll(".wizard-progress-step")
  );
  const backButton = document.getElementById("wizard-back");
  const nextButton = document.getElementById("wizard-next");
  const submitButton = document.getElementById("wizard-submit");
  const errorBox = document.getElementById("setup-error");
  const reviewBox = document.getElementById("setup-review");
  const valuesNode = document.getElementById("setup-values");
  const formHidden = form.hidden;

  const rowTypes = {
    language: { container: "languages-rows", title: "Lingua" },
    parent: { container: "parents-rows", title: "Genitore o tutore" },
    sibling: { container: "siblings-rows", title: "Fratello o sorella" },
    person: { container: "people-rows", title: "Persona importante" },
    education: { container: "education-rows", title: "Formazione" },
    work: { container: "work-rows", title: "Lavoro" },
  };

  const defaults = {
    timezone: "Europe/Rome",
    birth_country: "Italia",
    citizenship: "Italia",
    gender: "non specificato",
    trait_introversion_extraversion: "5",
    trait_empathy_detachment: "5",
    trait_logic_impulsivity: "5",
    trait_resilience_sensitivity: "5",
  };

  let currentStep = 0;
  const rowCounters = {};

  function parseInitialValues() {
    if (!valuesNode) {
      return {};
    }
    try {
      const parsed = JSON.parse(valuesNode.dataset.values || "{}");
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (_error) {
      return {};
    }
  }

  const initialValues = parseInitialValues();
  const hasInitialValues = Object.keys(initialValues).length > 0;

  function setFieldValue(element, value) {
    if (element.type === "range") {
      element.value = value;
      updateRangeOutput(element);
      return;
    }
    if (element.tagName === "SELECT") {
      const option = Array.from(element.options).find(
        (item) => item.value === value
      );
      if (option) {
        element.value = value;
        return;
      }
      // I menu geografici accettano solo le voci previste: un valore
      // sconosciuto non deve diventare una nuova opzione.
      if (element.hasAttribute("data-strict")) {
        return;
      }
      // Valor preservado da un profilo o da un modulo precedente: resta
      // visibile invece di essere sostituito con la prima voce del menu.
      if (value !== undefined && value !== null && String(value) !== "") {
        element.add(new Option(String(value), String(value)));
        element.value = String(value);
      }
      return;
    }
    element.value = value;
  }

  function updateRangeOutput(element) {
    const output = document.querySelector(`output[for="${element.id}"]`);
    if (output) {
      output.textContent = element.value;
    }
  }

  function updateAllRanges() {
    form.querySelectorAll('input[type="range"][data-trait]').forEach((element) => {
      updateRangeOutput(element);
    });
  }

  function applyScalarValues(values, useDefaults) {
    form.querySelectorAll("input[name], select[name], textarea[name]").forEach(
      (element) => {
        const name = element.name;
        if (!name || name === "csrf") {
          return;
        }
        if (Object.prototype.hasOwnProperty.call(values, name)) {
          setFieldValue(element, values[name]);
        } else if (useDefaults && Object.prototype.hasOwnProperty.call(defaults, name)) {
          setFieldValue(element, defaults[name]);
        }
      }
    );
  }

  function rowIndexes(values, prefix) {
    const pattern = new RegExp(`^${prefix}_(\\d+)_`);
    const indexes = new Set();
    Object.keys(values).forEach((key) => {
      const match = key.match(pattern);
      if (match) {
        indexes.add(Number(match[1]));
      }
    });
    return Array.from(indexes).sort((left, right) => left - right);
  }

  function rowData(values, prefix, index) {
    const data = {};
    Object.keys(values).forEach((key) => {
      const match = key.match(new RegExp(`^${prefix}_${index}_(.+)$`));
      if (match) {
        data[match[1]] = values[key];
      }
    });
    return data;
  }

  function setRowField(row, name, value) {
    const element = row.querySelector(`[name="${name}"]`);
    if (element && value !== undefined && value !== null && value !== "") {
      setFieldValue(element, value);
    }
  }

  function addRow(type, data, forcedIndex) {
    const config = rowTypes[type];
    if (!config) {
      return null;
    }
    const template = document.getElementById(`${type}-row-template`);
    const container = document.getElementById(config.container);
    if (!template || !container) {
      return null;
    }
    const index = Number.isInteger(forcedIndex)
      ? forcedIndex
      : rowCounters[type] || 0;
    rowCounters[type] = Math.max(rowCounters[type] || 0, index + 1);

    const fragment = template.content.cloneNode(true);
    const row = fragment.querySelector(".setup-row");
    if (!row) {
      return null;
    }
    row.dataset.index = String(index);
    row.querySelectorAll("[name]").forEach((element) => {
      element.name = element.name.replaceAll("__INDEX__", String(index));
    });

    const rowTitle = row.querySelector(".setup-row-title");
    if (rowTitle) {
      rowTitle.textContent = config.title;
    }

    if (type === "language" && !data) {
      if (index === 0) {
        setRowField(row, `language_${index}_code`, "it");
        setRowField(row, `language_${index}_level`, "madrelingua");
      }
    } else if (type === "parent" && !data) {
      setRowField(
        row,
        `parent_${index}_relation`,
        index === 0 ? "padre" : index === 1 ? "madre" : "genitore"
      );
    } else if (type === "sibling" && !data) {
      setRowField(row, `sibling_${index}_relation`, "sorella");
    } else {
      row.querySelectorAll("[name]").forEach((element) => {
        const name = element.name;
        const key = name.replace(`${type}_${index}_`, "");
        if (data && Object.prototype.hasOwnProperty.call(data, key)) {
          setFieldValue(element, data[key]);
        }
      });
    }

    container.appendChild(fragment);
    return row;
  }

  function initializeRows(values) {
    Object.keys(rowTypes).forEach((type) => {
      const indexes = rowIndexes(values, type);
      if (indexes.length === 0) {
        if (type === "language") {
          addRow("language");
        } else if (type === "parent") {
          addRow("parent");
          addRow("parent");
        } else if (type === "sibling") {
          addRow("sibling");
        } else if (type === "person") {
          addRow("person");
        } else if (type === "education") {
          addRow("education");
        } else if (type === "work") {
          addRow("work");
        }
        return;
      }
      indexes.forEach((index) => {
        addRow(type, rowData(values, type, index), index);
      });
    });
  }

  function initGeoMenus() {
    form.querySelectorAll("[data-province-select]").forEach((province) => {
      const key = province.dataset.provinceSelect;
      const region = form.querySelector(`[data-region-select="${key}"]`);
      if (!region) {
        return;
      }

      function applyVisibility(resetOnEmptyRegion) {
        const selectedRegion = region.value;
        let provinceStillValid = true;
        province.querySelectorAll("optgroup").forEach((group) => {
          const visible = !selectedRegion || group.label === selectedRegion;
          group.hidden = !visible;
          group.disabled = !visible;
          group.querySelectorAll("option").forEach((option) => {
            option.hidden = !visible;
            option.disabled = !visible;
            if (option.selected && !visible) {
              provinceStillValid = false;
            }
          });
        });
        if (resetOnEmptyRegion && !selectedRegion) {
          province.value = "";
          return;
        }
        if (province.value && !provinceStillValid) {
          province.value = "";
        }
      }

      region.addEventListener("change", () => applyVisibility(true));
      province.addEventListener("change", () => {
        const option = province.selectedOptions[0];
        const provinceRegion = option ? option.dataset.region || "" : "";
        if (provinceRegion && region.value !== provinceRegion) {
          region.value = provinceRegion;
        }
        applyVisibility(false);
      });
      applyVisibility(false);
    });
  }

  function clearError() {
    if (errorBox) {
      errorBox.hidden = true;
      errorBox.textContent = "";
    }
    form.querySelectorAll(".has-error").forEach((element) => {
      element.classList.remove("has-error");
    });
  }

  function showError(message, element) {
    if (errorBox) {
      errorBox.textContent = message;
      errorBox.hidden = false;
    }
    if (element) {
      element.classList.add("has-error");
      element.focus();
    }
  }

  function fieldLabel(element) {
    if (element.id) {
      const explicit = document.querySelector(`label[for="${element.id}"]`);
      if (explicit) {
        return explicit.textContent.trim();
      }
    }
    const label = element.closest("label");
    const span = label ? label.querySelector("span") : null;
    if (span) {
      return span.textContent.trim();
    }
    return element.name || "Campo";
  }

  function validateStep(index) {
    const step = steps[index];
    if (!step) {
      return true;
    }
    const required = Array.from(step.querySelectorAll("[data-required]"));
    for (const element of required) {
      const value = String(element.value || "").trim();
      if (!value) {
        showError(`Compila il campo “${fieldLabel(element)}”.`, element);
        return false;
      }
      if (!element.checkValidity()) {
        showError(
          `Il campo “${fieldLabel(element)}” non è valido. Controlla il formato.`,
          element
        );
        return false;
      }
    }

    const birthDate = step.querySelector('[name="birth_date"]');
    if (birthDate && birthDate.value) {
      const selected = new Date(`${birthDate.value}T00:00:00`);
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      if (Number.isNaN(selected.getTime()) || selected > today) {
        showError("La data di nascita non può essere nel futuro.", birthDate);
        return false;
      }
    }

    const numbers = Array.from(
      step.querySelectorAll('input[type="number"][min], input[type="range"][min]')
    );
    for (const element of numbers) {
      if (element.value !== "" && !element.checkValidity()) {
        showError(
          `Il campo “${fieldLabel(element)}” è fuori dall’intervallo consentito.`,
          element
        );
        return false;
      }
    }

    clearError();
    return true;
  }

  function showStep(index, preserveError) {
    currentStep = Math.max(0, Math.min(index, steps.length - 1));
    steps.forEach((step, position) => {
      const active = position === currentStep;
      step.hidden = !active;
      step.classList.toggle("is-active", active);
    });
    progressButtons.forEach((button, position) => {
      const active = position === currentStep;
      button.classList.toggle("is-active", active);
      if (active) {
        button.setAttribute("aria-current", "step");
      } else {
        button.removeAttribute("aria-current");
      }
    });
    if (backButton) {
      backButton.disabled = currentStep === 0;
    }
    if (nextButton) {
      nextButton.hidden = currentStep === steps.length - 1;
    }
    if (submitButton) {
      submitButton.hidden = currentStep !== steps.length - 1;
    }
    if (reviewBox) {
      const reviewStep = reviewBox.closest(".wizard-step");
      if (reviewStep && steps.indexOf(reviewStep) === currentStep) {
        renderReview();
      }
    }
    if (!preserveError) {
      clearError();
    }
  }

  function displayValue(element) {
    if (element.type === "range") {
      return `${element.value}/10`;
    }
    if (element.tagName === "SELECT") {
      if (!element.value) {
        return "";
      }
      const option = element.options[element.selectedIndex];
      return option ? option.textContent.trim() : element.value;
    }
    return String(element.value || "").trim();
  }

  function renderReview() {
    if (!reviewBox) {
      return;
    }
    reviewBox.textContent = "";
    const groups = [
      { index: 0, title: "Identità e contesto" },
      { index: 1, title: "Carattere" },
      { index: 2, title: "Relazioni" },
      { index: 3, title: "Storia" },
    ];

    groups.forEach((group) => {
      const step = steps[group.index];
      if (!step) {
        return;
      }
      const section = document.createElement("section");
      section.className = "setup-review-group";
      const heading = document.createElement("h3");
      heading.textContent = group.title;
      section.appendChild(heading);

      const list = document.createElement("dl");
      list.className = "setup-review-list";
      let count = 0;
      step.querySelectorAll("input[name], select[name], textarea[name]").forEach(
        (element) => {
          if (
            element.type === "hidden" ||
            element.name === "csrf" ||
            element.disabled
          ) {
            return;
          }
          const value = displayValue(element);
          if (!value) {
            return;
          }
          const term = document.createElement("dt");
          term.textContent = fieldLabel(element);
          const description = document.createElement("dd");
          description.textContent = value;
          list.appendChild(term);
          list.appendChild(description);
          count += 1;
        }
      );

      if (count === 0) {
        const empty = document.createElement("p");
        empty.className = "setup-review-empty";
        empty.textContent = "Nessun dato inserito in questa sezione.";
        section.appendChild(empty);
      } else {
        section.appendChild(list);
      }
      reviewBox.appendChild(section);
    });
  }

  function nextStep() {
    if (validateStep(currentStep)) {
      showStep(currentStep + 1);
    }
  }

  function previousStep() {
    showStep(currentStep - 1);
  }

  function submitSetup(event) {
    for (let index = 0; index < steps.length; index += 1) {
      if (!validateStep(index)) {
        showStep(index, true);
        return;
      }
    }
    // The server is the source of truth; this only prevents obvious omissions.
    if (formHidden) {
      event.preventDefault();
    }
  }

  form.addEventListener("submit", submitSetup);
  if (nextButton) {
    nextButton.addEventListener("click", nextStep);
  }
  if (backButton) {
    backButton.addEventListener("click", previousStep);
  }

  progressButtons.forEach((button, index) => {
    button.addEventListener("click", () => {
      if (index > currentStep) {
        for (let position = currentStep; position < index; position += 1) {
          if (!validateStep(position)) {
            showStep(position, true);
            return;
          }
        }
      }
      showStep(index);
    });
  });

  form.querySelectorAll('input[type="range"][data-trait]').forEach((element) => {
    element.addEventListener("input", () => updateRangeOutput(element));
  });

  document.querySelectorAll("[data-add-row]").forEach((button) => {
    button.addEventListener("click", () => {
      const type = button.dataset.addRow;
      addRow(type);
      const container = document.getElementById(rowTypes[type].container);
      const lastRow = container ? container.lastElementChild : null;
      const firstInput = lastRow ? lastRow.querySelector("input, select, textarea") : null;
      if (firstInput) {
        firstInput.focus();
      }
    });
  });

  form.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-row]");
    if (!button) {
      return;
    }
    const row = button.closest(".setup-row");
    if (row) {
      row.remove();
      clearError();
    }
  });

  applyScalarValues(initialValues, !hasInitialValues);
  initializeRows(initialValues);
  updateAllRanges();
  initGeoMenus();
  showStep(0);
})();
