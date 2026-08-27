import Script from "next/script";

import { MiniAppProviders } from "@/components/MiniAppProviders";

export default function MiniAppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      <Script
        src="https://telegram.org/js/telegram-web-app.js"
        strategy="afterInteractive"
      />
      <MiniAppProviders>{children}</MiniAppProviders>
    </>
  );
}
