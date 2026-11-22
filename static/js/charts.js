// Draws the burndown and velocity charts from JSON embedded with Django's
// json_script filter (safe: the data is never inserted as HTML).
(function () {
  "use strict";

  function readJson(id) {
    var element = document.getElementById(id);
    return element ? JSON.parse(element.textContent) : null;
  }

  var burndown = readJson("burndown-data");
  if (burndown && window.Chart) {
    new window.Chart(document.getElementById("burndown-chart"), {
      type: "line",
      data: {
        labels: burndown.labels,
        datasets: [
          { label: "Remaining (actual)", data: burndown.remaining, borderColor: "#0d6efd", tension: 0, spanGaps: false },
          { label: "Ideal", data: burndown.ideal, borderColor: "#adb5bd", borderDash: [6, 4], pointRadius: 0 },
        ],
      },
      options: {
        maintainAspectRatio: false,
        scales: { y: { beginAtZero: true, title: { display: true, text: "Story points" } } },
      },
    });
  }

  var velocity = readJson("velocity-data");
  if (velocity && window.Chart) {
    new window.Chart(document.getElementById("velocity-chart"), {
      type: "bar",
      data: {
        labels: velocity.labels,
        datasets: [
          { label: "Committed", data: velocity.committed, backgroundColor: "#ced4da" },
          { label: "Completed", data: velocity.completed, backgroundColor: "#198754" },
        ],
      },
      options: { maintainAspectRatio: false, scales: { y: { beginAtZero: true } } },
    });
  }
})();
