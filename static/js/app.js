// Small page helpers. No inline JavaScript is used anywhere in the templates.
(function () {
  "use strict";

  // Forms with data-confirm ask before submitting (delete buttons).
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var question = form.getAttribute("data-confirm");
    if (question && !window.confirm(question)) {
      event.preventDefault();
      return;
    }
    // Disable the submit button(s) so a double click cannot send the form twice.
    window.setTimeout(function () {
      form.querySelectorAll("button[type=submit]").forEach(function (button) {
        button.disabled = true;
      });
    }, 0);
  });

  // Progress bars: width comes from data-width (0..100), no inline styles in templates.
  document.querySelectorAll(".progress-bar[data-width]").forEach(function (bar) {
    var width = Math.max(0, Math.min(100, parseFloat(bar.getAttribute("data-width")) || 0));
    bar.style.width = width + "%";
  });

  // Label colours come from data-color (validated as #RRGGBB on the server).
  document.querySelectorAll("[data-color]").forEach(function (element) {
    var color = element.getAttribute("data-color");
    if (/^#[0-9A-Fa-f]{6}$/.test(color)) {
      element.style.backgroundColor = color;
    }
  });
})();
