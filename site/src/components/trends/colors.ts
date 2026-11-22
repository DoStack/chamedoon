// Series colours for over-time charts (≥ 3:1 against white for lines, ≥ 4.5:1 for the bold end labels).
export const TYPE_COLOR: Record<string, string> = {
  room: '#b8410c',
  apartment: '#1d3a5c',
  basement: '#7a5c2e',
  studio: '#2e7d6b',
  house: '#6b4fa0',
};
export const COUNTRY_COLOR = { CA: '#b8410c', DE: '#1d3a5c' } as const;
export const COURIER_COLOR = { trips: '#1d3a5c', requests: '#b8410c' } as const;
export const CITY_COLOR: Record<string, string> = {
  toronto: '#b8410c',
  montreal: '#7a5c2e',
  vancouver: '#2e7d6b',
  berlin: '#1d3a5c',
  hamburg: '#6b4fa0',
  cologne: '#a3261d',
};
