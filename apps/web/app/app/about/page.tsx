"use client";

import { useI18n } from "@/lib/i18n";

export default function HowItWorksPage() {
  const { messages } = useI18n();
  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight">{messages.landing.howItWorksTitle}</h1>
      <div className="mt-6 space-y-5">
        {messages.landing.howItWorksSteps.map((step) => (
          <section key={step.title}>
            <p className="text-base text-slate-900">
              {step.icon} <strong>{step.title}</strong>
            </p>
            <p className="mt-1 text-sm leading-6 text-slate-600">{step.body}</p>
          </section>
        ))}
      </div>
    </div>
  );
}
