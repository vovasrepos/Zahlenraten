// Progressive Verbesserung: Nach einem Seitenwechsel liegt der Fokus sinnvoll.
// Die Formulare und das Spiel funktionieren auch ohne JavaScript.
const target = document.querySelector('[data-autofocus]');
if (target) target.focus({ preventScroll: true });
