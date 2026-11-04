// Controls of flows not built yet stay hidden (BRIEF, audit P1-50). Each turns on with its phase.
export const FLAGS = {
  bookmark: true, // F14
  alerts: false, // F15, P3; also needs meta.alerts_on
  map: true, // F09, P5
} as const

// `?tgmap=1` shows the map on Preview and in tests before its phase. __TG_MOCK__ is false in the Production build.
export const MAP_ON: boolean = FLAGS.map || (__TG_MOCK__ && new URLSearchParams(location.search).has('tgmap'))
