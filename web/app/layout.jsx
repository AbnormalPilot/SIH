// Self-hosted variable fonts. No render-blocking Google Fonts link.
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";

import "./globals.css";
import SiteNav from "@/components/site-nav.jsx";
import SiteFoot from "@/components/site-foot.jsx";
import { DataProvider } from "@/lib/data.jsx";

export const metadata = {
  metadataBase: new URL("http://localhost:8080"),
  title: {
    default: "Pushpak · Real-time Airfare Price Index",
    template: "%s · Pushpak",
  },
  description:
    "A daily, route-level airfare price index for India, built on DGCA passenger weights and CPI-standard index methodology. Prototype for MoSPI's Data Informatics and Innovation Division.",
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
};

/**
 * Restores the reader's theme before first paint. Without it a reader who chose
 * dark gets a white flash on every navigation. Light is the default, so an
 * unset preference needs no stamp and no work.
 */
const PRE_PAINT = `(function(){try{
  if(localStorage.getItem("pushpak-theme")==="dark")
    document.documentElement.dataset.theme="dark";
}catch(e){}})();`;

/**
 * One frame for the whole site: a top nav, the page, a footer.
 *
 * The sidebar lives on the operations dashboard, which is one long scroll that
 * needs a map. These pages are separate documents reached from the nav, so a
 * rail listing them would sit alongside the nav saying the same thing twice.
 */
export default function RootLayout({ children }) {
  return (
    // The script above mutates <html> before React hydrates, which is the whole
    // point of it; suppressHydrationWarning stops React objecting to its own
    // instruction being carried out.
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: PRE_PAINT }} />
      </head>
      <body>
        <DataProvider>
          <div className="grain min-h-[100dvh] bg-plane">
            <a
              href="#content"
              className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[70] focus:rounded-lg focus:bg-accent focus:px-4 focus:py-2 focus:text-sm focus:font-semibold focus:text-white"
            >
              Skip to content
            </a>
            <SiteNav />
            <main id="content">{children}</main>
            <SiteFoot />
          </div>
        </DataProvider>
      </body>
    </html>
  );
}
