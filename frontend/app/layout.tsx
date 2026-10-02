import type { Metadata } from "next";
import { Source_Sans_3, JetBrains_Mono } from "next/font/google";
import Script from "next/script";
import "./globals.css";
import "./dashboard.css";
import "./glass.css";

const sourceSans = Source_Sans_3({
  subsets: ["latin", "vietnamese"],
  display: "swap",
  variable: "--font-sans",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin", "vietnamese"],
  display: "swap",
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "MasterOne — Customer Master Data Command Center",
  description: "Customer master data management, entity resolution, and survivorship workspace",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="vi" className={`${sourceSans.variable} ${jetbrainsMono.variable}`} suppressHydrationWarning>
      <body className={sourceSans.className}>
        <Script id="masterone-theme" strategy="beforeInteractive" dangerouslySetInnerHTML={{ __html: `try{var saved=localStorage.getItem('masterone.theme');document.documentElement.dataset.theme=saved==='light'||saved==='dark'?saved:(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light')}catch(e){document.documentElement.dataset.theme='light'}` }} />
        {children}
      </body>
    </html>
  );
}
