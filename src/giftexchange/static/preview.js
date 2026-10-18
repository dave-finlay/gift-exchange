// Size the email preview's frame to fit the email, so the whole email shows
// in the page with no scrolling inside the frame. Without JavaScript the
// frame keeps its fixed height and scrolls.
(() => {
  for (const frame of document.querySelectorAll("iframe.email-preview")) {
    const fit = () => {
      const page = frame.contentDocument?.documentElement;
      if (!page) return;
      // The frame's height includes its border.
      const border = frame.offsetHeight - frame.clientHeight;
      frame.style.height = `${page.scrollHeight + border}px`;
    };
    frame.addEventListener("load", fit);
    if (frame.contentDocument?.readyState === "complete") fit();
    // A narrower frame makes the email taller, and vice versa.
    addEventListener("resize", fit);
  }
})();
