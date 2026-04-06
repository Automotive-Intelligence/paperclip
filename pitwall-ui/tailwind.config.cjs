module.exports = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        pitbg: '#0a0a0f',
        pitcard: '#12121a',
        pitborder: '#1e1e2e',
        pitgreen: '#00ff87',
        pitamber: '#ffb800',
        pitred: '#ff3366',
        pittext: '#ffffff',
        pitmuted: '#8888aa',
      },
      boxShadow: {
        pit: '0 0 24px rgba(0, 255, 135, 0.08)',
      },
      backgroundImage: {
        telemetry: 'radial-gradient(circle at 15% 0%, rgba(0,255,135,0.09), transparent 40%), radial-gradient(circle at 85% 10%, rgba(74,168,255,0.08), transparent 35%)',
      },
    },
  },
  plugins: [],
};
