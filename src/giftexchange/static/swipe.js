// Swipe left (finger moving right to left) to go to the page named in
// <body data-swipe-left>. Pages set it where it makes sense, e.g. from a
// circle back to the home page. Without JavaScript nothing changes.
(() => {
  const target = document.body.dataset.swipeLeft;
  if (!target) return;
  let start = null;
  addEventListener("touchstart", (event) => {
    const touch = event.touches[0];
    start = event.touches.length === 1
      ? { x: touch.clientX, y: touch.clientY, time: Date.now() }
      : null;
  }, { passive: true });
  addEventListener("touchend", (event) => {
    if (!start) return;
    const touch = event.changedTouches[0];
    const dx = touch.clientX - start.x;
    const dy = touch.clientY - start.y;
    const quick = Date.now() - start.time < 600;
    start = null;
    // Far enough, mostly sideways and quick, so scrolling doesn't count.
    if (dx < -80 && Math.abs(dy) < Math.abs(dx) / 2 && quick) {
      location.href = target;
    }
  }, { passive: true });
})();
