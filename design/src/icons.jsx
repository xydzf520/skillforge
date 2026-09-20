// Inline stroke icons — Lucide-shaped, no deps.
// Use as <I name="x" size={14} />

const ICONS = {
  search:      'M11 11l4 4 M7 12a5 5 0 1 0 0-10 5 5 0 0 0 0 10z',
  bell:        'M8 1.5v1 M3.5 7a4.5 4.5 0 1 1 9 0v3l1.2 2H2.3l1.2-2V7z M6.2 13a1.8 1.8 0 0 0 3.6 0',
  clock:       'M8 4v4l2.5 1.5 M8 14a6 6 0 1 0 0-12 6 6 0 0 0 0 12z',
  chev:        'M3 6l5 5 5-5',
  chevr:       'M6 3l5 5-5 5',
  plus:        'M3 8h10 M8 3v10',
  filter:      'M2 3h12l-4.5 6V14l-3-1.5V9L2 3z',
  grid:        'M2 2h5v5H2z M9 2h5v5H9z M2 9h5v5H2z M9 9h5v5H9z',
  list:        'M2 4h12 M2 8h12 M2 12h12',
  refresh:     'M2 8a6 6 0 0 1 10.7-3.7 M14 2v3h-3 M14 8a6 6 0 0 1-10.7 3.7 M2 14v-3h3',
  bolt:        'M9 1L3 9h4l-1 6 6-8H8l1-6z',
  spark:       'M8 1l1.6 5.4L15 8l-5.4 1.6L8 15l-1.6-5.4L1 8l5.4-1.6L8 1z',
  cube:        'M8 1l6 3.5v7L8 15 2 11.5v-7L8 1z M2 4.5L8 8l6-3.5 M8 8v7',
  beaker:      'M6 1h4 M6.5 1v5L3 13a1.5 1.5 0 0 0 1.3 2h7.4A1.5 1.5 0 0 0 13 13L9.5 6V1',
  check:       'M3 8l3 3 7-7',
  x:           'M3 3l10 10 M13 3L3 13',
  play:        'M4 2l9 6-9 6V2z',
  arrowr:      'M3 8h10 M9 4l4 4-4 4',
  arrowl:      'M13 8H3 M7 4L3 8l4 4',
  doc:         'M3 1h7l3 3v10a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V2a1 1 0 0 1 1-1z M10 1v3h3',
  folder:      'M2 4a1 1 0 0 1 1-1h3l1.5 1.5H13a1 1 0 0 1 1 1V12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V4z',
  user:        'M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M2.5 14a5.5 5.5 0 0 1 11 0',
  users:       'M5.5 8a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z M11 8a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M1.5 14a4 4 0 0 1 8 0 M10 14a4 4 0 0 1 4.5-4',
  tree:        'M3 2h10 M5 2v3 a1 1 0 0 0 1 1 h4 a1 1 0 0 0 1-1 V2 M8 6v3 M5 9h6 M5 9v3a1 1 0 0 0 1 1h1 M11 9v3a1 1 0 0 0-1 1h-1',
  inbox:       'M2 9l2-6h8l2 6 M2 9v5a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1V9 M2 9h3l1 2h4l1-2h3',
  flow:        'M3 3h3v3H3z M10 3h3v3h-3z M3 10h3v3H3z M10 10h3v3h-3z M6 4.5h4 M6 11.5h4 M4.5 6v4 M11.5 6v4',
  bot:         'M5 6V3a3 3 0 0 1 6 0v3 M3 6h10v8H3z M6 9v1 M10 9v1 M6 12h4',
  gpu:         'M2 4h12v8H2z M5 6h2v4H5z M9 6h2v4H9z M14 7h1v2h-1z',
  layers:      'M8 2l6 3-6 3-6-3 6-3z M2 8l6 3 6-3 M2 11l6 3 6-3',
  database:    'M3 3.5c0-1 2.2-1.8 5-1.8s5 .8 5 1.8-2.2 1.8-5 1.8-5-.8-5-1.8z M3 3.5v9c0 1 2.2 1.8 5 1.8s5-.8 5-1.8v-9 M3 8c0 1 2.2 1.8 5 1.8s5-.8 5-1.8',
  shield:      'M8 1l6 2v5c0 3.5-2.5 6-6 7-3.5-1-6-3.5-6-7V3l6-2z',
  star:        'M8 1.5l2 4.5 5 .5-3.7 3.3 1.1 5L8 12l-4.4 2.8 1.1-5L1 6.5l5-.5 2-4.5z',
  hist:        'M2 8a6 6 0 1 1 1.5 4 M2 14v-3h3 M8 4v4l2.5 1.5',
  dept:        'M2 13V6l6-4 6 4v7 M6 13v-4h4v4',
  link:        'M9 6a3 3 0 0 1 0 4l-1.5 1.5a3 3 0 1 1-4-4L4 7 M7 10a3 3 0 0 1 0-4l1.5-1.5a3 3 0 1 1 4 4L12 9',
  attach:      'M11 7L7 11a3 3 0 1 1-4-4l5-5a2 2 0 1 1 3 3L6 10a1 1 0 1 1-1.5-1.5L9 4',
  upload:      'M8 11V2 M4 6l4-4 4 4 M2 13h12',
  download:    'M8 2v9 M4 7l4 4 4-4 M2 13h12',
  more:        'M3 8h.01 M8 8h.01 M13 8h.01',
  send:        'M2 8l12-6-4 14-3-6-5-2z',
  warn:        'M8 2l6.5 12H1.5L8 2z M8 6v4 M8 12v.5',
  trend:       'M2 12l4-4 3 3 5-7 M10 4h4v4',
};

function I({ name, size = 14, stroke = 'currentColor', sw = 1.5, ...rest }) {
  const d = ICONS[name];
  if (!d) return null;
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={stroke}
      strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round" {...rest}>
      <path d={d} />
    </svg>
  );
}

window.I = I;
