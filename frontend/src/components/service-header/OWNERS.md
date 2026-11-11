# Service header: owned piece

`ServiceHeader` is the top header of every Chamedoon Mini App (خانه and باربری): logotype, rule, current service, chevron,
and the dropdown card that switches between services.

- **Owner:** the "service header" session started from the CHAMEDOON CTO session, with Mohammad's approval.
- **Approved design:** variant A (dropdown card under the header), approved by Mohammad on 2026-11-10.
- **Rule:** do not edit, restyle, move or wrap this component, and do not add another header to an app. Any change goes
  through the header owner session and needs Mohammad's design approval first.
- **Adding a service:** add one entry to `SERVICES` in `service-header.tsx` (owner session only), with its SPA entry path.
- **Analytics:** `service_switch` with `{ from, to }`.
- **Tests:** `frontend/e2e/service-header.spec.ts`, and the logo placement checks in `frontend/e2e/ui-cleanup.spec.ts`.
