(function () {
  var KEY = "mplr_trainee_name";

  function init() {
    var input = document.getElementById("trainee-name");
    if (!input) return;

    var saved = "";
    try {
      saved = localStorage.getItem(KEY) || "";
    } catch (e) {}
    input.value = saved;

    input.addEventListener("input", function () {
      try {
        localStorage.setItem(KEY, input.value);
      } catch (e) {}
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  window.getTraineeName = function () {
    try {
      return (localStorage.getItem(KEY) || "").trim();
    } catch (e) {
      return "";
    }
  };
})();
