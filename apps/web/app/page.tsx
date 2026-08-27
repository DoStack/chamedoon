import { HealthStatus } from "@/components/HealthStatus";

const telegramBot = process.env.NEXT_PUBLIC_TELEGRAM_BOT_USERNAME?.replace(
  /^@/,
  "",
);
const telegramAppBase = telegramBot ? `https://t.me/${telegramBot}/app` : undefined;
const demandAppUrl = telegramAppBase ? `${telegramAppBase}?startapp=demand` : undefined;
const supplyAppUrl = telegramAppBase ? `${telegramAppBase}?startapp=supply` : undefined;

export default function Home() {
  return (
    <div className="min-h-full">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-4">
          <a href="#top" className="text-lg font-semibold tracking-tight">
            KOOLBAR
          </a>
          <nav className="flex gap-4 text-sm text-slate-600">
            <a href="#how-it-works" className="hover:text-slate-900">
              How it works
            </a>
            <a href="#safety" className="hover:text-slate-900">
              Safety
            </a>
            <a href="#faq" className="hover:text-slate-900">
              FAQ
            </a>
          </nav>
        </div>
      </header>

      <main id="top" className="mx-auto max-w-3xl px-4 py-12 sm:py-16">
        <p className="text-sm font-medium uppercase tracking-[0.2em] text-slate-500">
          C2C Cross-Border Courier
        </p>
        <h1 className="mt-3 text-4xl font-semibold tracking-tight sm:text-5xl">
          KOOLBAR
        </h1>
        <p className="mt-4 max-w-xl text-lg leading-7 text-slate-600">
          Match people who need to send items with travelers who have available
          capacity.
        </p>

        <div className="mt-8 flex flex-col gap-3 sm:flex-row">
          <CtaButton href={demandAppUrl} label="I Need to Send" />
          <CtaButton href={supplyAppUrl} label="I Can Carry" variant="secondary" />
        </div>
        <p className="mt-3 text-sm text-slate-500">
          {demandAppUrl
            ? "Opens in Telegram."
            : "Telegram Mini App opens these actions. Set NEXT_PUBLIC_TELEGRAM_BOT_USERNAME."}
        </p>
        <div className="mt-6">
          <HealthStatus />
        </div>

        <section id="how-it-works" className="mt-16 scroll-mt-8">
          <h2 className="text-2xl font-semibold">How it works</h2>
          <ol className="mt-4 space-y-3 text-slate-600">
            <li>1. Create a send request or a traveler request in Telegram.</li>
            <li>2. Koolbar finds compatible opposite-side requests.</li>
            <li>3. Both sides accept the match.</li>
            <li>4. You connect on Telegram. No payments, no internal chat.</li>
          </ol>
        </section>

        <section id="safety" className="mt-12 scroll-mt-8">
          <h2 className="text-2xl font-semibold">Safety</h2>
          <p className="mt-4 leading-7 text-slate-600">
            Koolbar is a matching platform, not a courier company. You choose who
            to accept. Contact stays on Telegram. Never send prohibited items.
            Follow customs rules in both countries.
          </p>
        </section>

        <section id="faq" className="mt-12 scroll-mt-8">
          <h2 className="text-2xl font-semibold">FAQ</h2>
          <dl className="mt-4 space-y-6 text-slate-600">
            <div>
              <dt className="font-medium text-slate-900">Is this a shipping company?</dt>
              <dd className="mt-1">
                No. Koolbar matches senders with travelers. Delivery is arranged
                between people.
              </dd>
            </div>
            <div>
              <dt className="font-medium text-slate-900">Does Koolbar take payments?</dt>
              <dd className="mt-1">
                Not in the MVP. Any compensation is between the two users.
              </dd>
            </div>
            <div>
              <dt className="font-medium text-slate-900">How do I contact a match?</dt>
              <dd className="mt-1">
                After both sides accept, Koolbar shows the other person&apos;s
                Telegram username.
              </dd>
            </div>
          </dl>
        </section>
      </main>
    </div>
  );
}

function CtaButton({
  href,
  label,
  variant = "primary",
}: {
  href?: string;
  label: string;
  variant?: "primary" | "secondary";
}) {
  const className =
    variant === "primary"
      ? "inline-flex h-12 items-center justify-center rounded-xl bg-slate-900 px-5 text-base font-medium text-white"
      : "inline-flex h-12 items-center justify-center rounded-xl border border-slate-300 bg-white px-5 text-base font-medium text-slate-900";

  if (!href) {
    return <span className={className}>{label}</span>;
  }

  return (
    <a className={className} href={href}>
      {label}
    </a>
  );
}
