/* The reader's saved theme, applied before the page is drawn.
 *
 * Loaded in <head>, ahead of the stylesheets, and on purpose the only script
 * there. charts.js used to do this from the end of <body>, so a reader who
 * had picked the light theme on a dark system got a dark page first and a
 * flash to light once the scripts arrived. It cannot be an inline script: the
 * page's policy allows only the site's own files.
 *
 * Only the two values the toggle writes are taken; anything else in storage is
 * left to the system's preference.
 */
(function () {
  try {
    var saved = localStorage.getItem('vb-theme');
    if (saved === 'light' || saved === 'dark') {
      document.documentElement.setAttribute('data-theme', saved);
    }
  } catch (e) {}
})();
