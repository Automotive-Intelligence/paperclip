// Colors are CSS variables (space-separated RGB channels) so a single class on <html>
// swaps the whole palette, and Tailwind's alpha syntax (bg-pitcard/40) still works.
// Every colour used in the app must come from this list -- a hardcoded gray-800 does
// not follow the theme and shows up as a black hole in light mode.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;

module.exports = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        pitbg: v('pitbg'),
        pitcard: v('pitcard'),
        pitsunk: v('pitsunk'),     // recessed panels inside a card
        pitborder: v('pitborder'),
        pitgreen: v('pitgreen'),
        pitamber: v('pitamber'),
        pitred: v('pitred'),
        pittext: v('pittext'),
        pitmuted: v('pitmuted'),
        pitfaint: v('pitfaint'),   // lowest-contrast supporting text
        pitinvert: v('pitinvert'), // text on a pittext-coloured surface
      },
      boxShadow: {
        pit: '0 0 24px rgba(0, 255, 135, 0.08)',
      },
      backgroundImage: {
        telemetry:
          'radial-gradient(circle at 15% 0%, rgba(0,255,135,0.09), transparent 40%), radial-gradient(circle at 85% 10%, rgba(74,168,255,0.08), transparent 35%)',
      },
    },
  },
  plugins: [],
};
