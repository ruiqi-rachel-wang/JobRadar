(function () {
  const resizer = document.getElementById('sidebar-resizer');
  const catWrap = document.querySelector('.cat-wrap');
  if (!resizer || !catWrap) return;

  // Restore saved height
  const saved = localStorage.getItem('jd_cat_h');
  if (saved) { catWrap.style.maxHeight = saved + 'px'; catWrap.style.height = saved + 'px'; }

  let startY, startH;

  resizer.addEventListener('mousedown', function (e) {
    e.preventDefault();
    startY = e.clientY;
    startH = catWrap.getBoundingClientRect().height;
    resizer.classList.add('is-dragging');
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';

    function onMove(e) {
      const newH = Math.max(32, Math.min(360, startH + e.clientY - startY));
      catWrap.style.maxHeight = newH + 'px';
      catWrap.style.height    = newH + 'px';
    }
    function onUp() {
      resizer.classList.remove('is-dragging');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      localStorage.setItem('jd_cat_h', catWrap.getBoundingClientRect().height);
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    }
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
  });
})();
