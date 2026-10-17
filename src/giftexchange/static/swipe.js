// Swipe left (finger moving right to left) to follow the URL in the nearest
// data-swipe-left attribute: a row's (e.g. deleting a person, which asks
// for confirmation first) or else the page's, on <body> (e.g. a circle
// back to the home page). Rows slide with the finger. Without JavaScript
// nothing changes: rows are still links, and every action has a button.
(() => {
  const DISTANCE = 80;
  let start = null;

  addEventListener("touchstart", (event) => {
    const element = event.target.closest("[data-swipe-left]");
    if (event.touches.length !== 1 || !element?.dataset.swipeLeft) {
      start = null;
      return;
    }
    const touch = event.touches[0];
    start = {
      element,
      row: element !== document.body,
      x: touch.clientX,
      y: touch.clientY,
      time: Date.now(),
      sliding: false,
    };
  }, { passive: true });

  addEventListener("touchmove", (event) => {
    if (!start?.row) return;
    const touch = event.touches[0];
    const dx = Math.min(0, touch.clientX - start.x);
    const dy = touch.clientY - start.y;
    if (!start.sliding && -dx > 10 && -dx > Math.abs(dy)) {
      start.sliding = true;
      start.element.classList.add("sliding");
    }
    if (start.sliding) start.element.style.transform = `translateX(${dx}px)`;
  }, { passive: true });

  addEventListener("touchend", (event) => {
    if (!start) return;
    const { element, row, x, y, time, sliding } = start;
    start = null;
    const touch = event.changedTouches[0];
    const dx = touch.clientX - x;
    const dy = touch.clientY - y;
    const sideways = dx < -DISTANCE && Math.abs(dy) < Math.abs(dx) / 2;
    // A row follows the finger; a page swipe must be quick, so slow
    // scrolling doesn't count.
    if (sideways && (row ? sliding : Date.now() - time < 600)) {
      location.href = element.dataset.swipeLeft;
    } else {
      reset(element);
    }
  }, { passive: true });

  addEventListener("touchcancel", () => {
    if (start) reset(start.element);
    start = null;
  });

  // Coming back to the page (e.g. Cancel or Back) shows rows in place.
  addEventListener("pageshow", () => {
    document.querySelectorAll("[data-swipe-left]").forEach(reset);
  });

  function reset(element) {
    element.classList.remove("sliding");
    element.style.transform = "";
  }
})();
