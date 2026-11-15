// Live countdown for waitlist seat holds: <span class="countdown" data-seconds="..."></span>
document.querySelectorAll(".countdown").forEach(function (el) {
  var left = parseInt(el.dataset.seconds, 10);
  function tick() {
    if (left <= 0) { el.textContent = "expired"; document.querySelectorAll("button.claim").forEach(function (b) { b.disabled = true; }); return; }
    var m = Math.floor(left / 60), s = left % 60;
    el.textContent = m + ":" + String(s).padStart(2, "0");
    left -= 1; setTimeout(tick, 1000);
  }
  tick();
});
// Add a ticket category row in the event form
var add = document.getElementById("add-cat");
if (add) add.addEventListener("click", function () {
  var tpl = document.getElementById("cat-row").content.cloneNode(true);
  document.getElementById("cat-rows").appendChild(tpl);
});
