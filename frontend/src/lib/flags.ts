// Controls of flows not built yet stay hidden (BRIEF, audit P1-50). Each turns on with its phase.
export const FLAGS = {
  bookmark: false, // F14, P2
  share: false, // F13, P2
  alerts: false, // F15, P3; also needs meta.alerts_on
  tabs: false, // F14, F21, F18, P2 to P4
  map: false, // F09, P5
} as const
