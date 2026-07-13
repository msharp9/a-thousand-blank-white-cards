import type { Metadata } from "next";
import { Permanent_Marker, Patrick_Hand, Nunito } from "next/font/google";
import "./globals.css";

import { ThemeToggle } from "@/components/theme-toggle";
import { TooltipProvider } from "@/components/ui/tooltip";

// Runs before first paint (parser-blocking inline script) so the persisted —
// or OS-preferred — theme applies without a flash of the wrong palette.
const themeInitScript = `try{var t=localStorage.getItem("tbwc_theme");if(t==="dark"||(!t&&matchMedia("(prefers-color-scheme: dark)").matches))document.documentElement.classList.add("dark")}catch(e){}`;

const fontMarker = Permanent_Marker({
  variable: "--font-marker",
  weight: "400",
  subsets: ["latin"],
});

const fontHand = Patrick_Hand({
  variable: "--font-hand",
  weight: "400",
  subsets: ["latin"],
});

const fontNunito = Nunito({
  variable: "--font-nunito",
  weight: ["400", "600", "700", "800", "900"],
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "1000 Blank White Cards",
  description: "The card game where you make the rules",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${fontMarker.variable} ${fontHand.variable} ${fontNunito.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
        <TooltipProvider>{children}</TooltipProvider>
        <ThemeToggle />
      </body>
    </html>
  );
}
