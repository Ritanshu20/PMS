// Kanban drag-and-drop. The server is the authority: every drop is sent to
// POST /projects/<id>/tasks/<number>/move/ and the card snaps back if the
// server says no (workflow rule, open blocker, version conflict, role).
(function () {
  "use strict";

  var board = document.getElementById("board");
  if (!board) {
    return;
  }
  var alertBox = document.getElementById("board-alert");
  var csrfToken = document.querySelector("meta[name=csrf-token]").getAttribute("content");
  var dragged = null;
  var origin = null; // where the card came from, to snap back on error

  function showError(message) {
    alertBox.textContent = message; // textContent: never interpret server text as HTML
    alertBox.classList.remove("d-none");
  }

  function hideError() {
    alertBox.classList.add("d-none");
    alertBox.textContent = "";
  }

  function cardsIn(column) {
    return Array.prototype.slice.call(column.querySelectorAll(".task-card"));
  }

  function refreshColumn(column) {
    var count = cardsIn(column).length;
    var badge = column.parentElement.querySelector(".column-count");
    if (badge) {
      badge.textContent = count;
    }
    var empty = column.querySelector(".empty-column");
    if (empty) {
      empty.classList.toggle("d-none", count > 0);
    }
  }

  function cardAfterPointer(column, y) {
    var candidates = cardsIn(column).filter(function (card) {
      return card !== dragged;
    });
    for (var i = 0; i < candidates.length; i++) {
      var box = candidates[i].getBoundingClientRect();
      if (y < box.top + box.height / 2) {
        return candidates[i];
      }
    }
    return null;
  }

  function snapBack(card, from) {
    var droppedInto = card.parentElement;
    from.column.insertBefore(card, from.next);
    refreshColumn(from.column);
    refreshColumn(droppedInto);
  }

  function sendMove(card, column) {
    var payload = {
      status: column.getAttribute("data-status"),
      position: cardsIn(column).indexOf(card),
      version: parseInt(card.getAttribute("data-version"), 10),
    };
    return fetch(card.getAttribute("data-move-url"), {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken },
      credentials: "same-origin",
      body: JSON.stringify(payload),
    }).then(function (response) {
      return response.json().then(function (data) {
        return { ok: response.ok, data: data };
      });
    });
  }

  board.addEventListener("dragstart", function (event) {
    var card = event.target.closest(".task-card[draggable=true]");
    if (!card) {
      return;
    }
    dragged = card;
    origin = { column: card.parentElement, next: card.nextElementSibling };
    card.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.getAttribute("data-number"));
  });

  board.addEventListener("dragend", function () {
    if (dragged) {
      dragged.classList.remove("dragging");
    }
    board.querySelectorAll(".drag-over").forEach(function (column) {
      column.classList.remove("drag-over");
    });
  });

  board.addEventListener("dragover", function (event) {
    var column = event.target.closest(".board-column-body");
    if (!column || !dragged) {
      return;
    }
    event.preventDefault();
    column.classList.add("drag-over");
    var after = cardAfterPointer(column, event.clientY);
    column.insertBefore(dragged, after);
  });

  board.addEventListener("dragleave", function (event) {
    var column = event.target.closest(".board-column-body");
    if (column && !column.contains(event.relatedTarget)) {
      column.classList.remove("drag-over");
    }
  });

  board.addEventListener("drop", function (event) {
    var column = event.target.closest(".board-column-body");
    if (!column || !dragged) {
      return;
    }
    event.preventDefault();
    var card = dragged;
    var from = origin; // keep our own copy: another drag may start before the reply
    hideError();
    refreshColumn(column);
    refreshColumn(from.column);
    sendMove(card, column)
      .then(function (result) {
        if (result.ok) {
          card.setAttribute("data-version", result.data.version);
        } else {
          snapBack(card, from);
          showError(result.data.detail || "The move was rejected.");
        }
      })
      .catch(function () {
        snapBack(card, from);
        showError("The move could not be saved. Reload the page and try again.");
      });
  });
})();
