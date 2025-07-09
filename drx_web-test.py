<!DOCTYPE html>
<html>
<head>
  <title>Base Configurator Modal Test</title>
  <style>
    #base-configurator-modal {
      display: none;
      position: fixed; left: 0; top: 0; width: 100vw; height: 100vh;
      background: rgba(0,0,0,0.3); z-index: 1000;
    }
    .modal-content {
      background: #fff;
      width: 500px;
      margin: 10% auto;
      padding: 2em;
      border-radius: 8px;
    }
  </style>
</head>
<body>

<button id="base-configurator-btn">Base Configurator</button>
<div id="base-configurator-modal">
  <div class="modal-content">
    <button id="close-base-configurator">Close</button>
    <table id="base-configurator-table">
      <tbody></tbody>
    </table>
    <button id="add-base-configurator-row">Add Row</button>
    <button id="save-base-configurator">Save</button>
    <div id="base-configurator-msg"></div>
  </div>
</div>

<script>
document.addEventListener("DOMContentLoaded", function() {
  const configuratorBtn = document.getElementById('base-configurator-btn');
  const configuratorModal = document.getElementById('base-configurator-modal');
  const closeconfigurator = document.getElementById('close-base-configurator');
  const tableBody = document.querySelector('#base-configurator-table tbody');
  const addRowBtn = document.getElementById('add-base-configurator-row');
  const saveBtn = document.getElementById('save-base-configurator');
  const msgDiv = document.getElementById('base-configurator-msg');
  let dragSrcRow = null;

  if (!configuratorBtn || !configuratorModal || !closeconfigurator || !tableBody) {
    console.error("Base Configurator modal: One or more elements not found in the DOM.");
    return;
  }

  configuratorBtn.onclick = function() {
    configuratorModal.style.display = "block";
    loadBaseConfigurator();
  };
  closeconfigurator.onclick = function() {
    configuratorModal.style.display = "none";
    msgDiv.textContent = "";
  };
  window.addEventListener('click', function(event) {
    if (event.target === configuratorModal) {
      configuratorModal.style.display = "none";
      msgDiv.textContent = "";
    }
  });

  function makeRow(rowData = {base_no: ""}) {
    const tr = document.createElement("tr");
    tr.draggable = true;
    const tdBase = document.createElement("td");
    const inputBase = document.createElement("input");
    inputBase.type = "text";
    inputBase.value = rowData.base_no || "";
    tdBase.appendChild(inputBase);
    tr.appendChild(tdBase);

    // Drag handlers (minimal)
    tr.addEventListener('dragstart', function (e) {
      dragSrcRow = tr;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', '');
    });
    tr.addEventListener('dragover', function (e) {
      e.preventDefault();
    });
    tr.addEventListener('drop', function (e) {
      e.preventDefault();
      if (dragSrcRow && dragSrcRow !== tr) {
        tr.parentNode.insertBefore(dragSrcRow, tr);
      }
      dragSrcRow = null;
    });

    return tr;
  }

  function loadBaseConfigurator() {
    tableBody.innerHTML = "";
    for (let i = 0; i < 3; ++i) {
      tableBody.appendChild(makeRow({base_no: "Row " + (i+1)}));
    }
  }

  if (addRowBtn) {
    addRowBtn.onclick = function() {
      tableBody.appendChild(makeRow());
    };
  }

  if (saveBtn) {
    saveBtn.onclick = function() {
      msgDiv.textContent = "Saved!";
    }
  }
});
</script>
</body>
</html>