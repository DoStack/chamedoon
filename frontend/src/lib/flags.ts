// Controls of flows not built yet stay hidden (BRIEF, audit P1-50). Each turns on with its phase.
export const FLAGS = {
  bookmark: true, // F14
  alerts: false, // F15, P3; also needs meta.alerts_on
  map: false, // F09, P5
} as const
