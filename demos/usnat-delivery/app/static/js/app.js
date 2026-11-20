// Small, dependency-free UI enhancements (no CDN, no build step).

(function () {
  "use strict";

  var THEME_KEY = "msdocs-search-theme";

  function applyTheme(theme) {
    document.documentElement.setAttribute("data-bs-theme", theme);
    var toggle = document.getElementById("theme-toggle");
    if (toggle) {
      toggle.textContent = theme === "dark" ? "\u2600\uFE0F Light" : "\uD83C\uDF19 Dark";
    }
  }

  function initTheme() {
    var saved = localStorage.getItem(THEME_KEY);
    var preferred = saved || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    applyTheme(preferred);

    var toggle = document.getElementById("theme-toggle");
    if (toggle) {
      toggle.addEventListener("click", function () {
        var current = document.documentElement.getAttribute("data-bs-theme");
        var next = current === "dark" ? "light" : "dark";
        localStorage.setItem(THEME_KEY, next);
        applyTheme(next);
      });
    }
  }

  function initSearchShortcut() {
    var input = document.getElementById("search-input");
    if (!input) return;
    document.addEventListener("keydown", function (e) {
      if (e.key === "/" && document.activeElement !== input) {
        e.preventDefault();
        input.focus();
        input.select();
      }
    });
  }

  function initSubmitSpinner() {
    var form = document.getElementById("search-form");
    var button = document.getElementById("search-button");
    if (!form || !button) return;
    form.addEventListener("submit", function () {
      if (!inputHasValue(form)) return;
      button.disabled = true;
      button.innerHTML = '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>Searching...';
    });
  }

  function inputHasValue(form) {
    var input = form.querySelector('input[name="q"]');
    return input && input.value.trim().length > 0;
  }

  document.addEventListener("DOMContentLoaded", function () {
    initTheme();
    initSearchShortcut();
    initSubmitSpinner();
  });
})();
